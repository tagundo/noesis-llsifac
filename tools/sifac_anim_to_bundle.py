#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_anim_to_bundle — put a retargeted clip into a SIFAS Unity AssetBundle
===========================================================================

Unity ``.anim`` files (what ``sifac_anim_retarget.py`` writes) are *editor*
text assets.  The game loads **AssetBundles** (binary ``UnityFS``), so to use a
clip in-game you must get it into a bundle.  Building a bundle from scratch
without Unity is unreliable; the robust, Unity-free way is to take a **template
bundle** -- a real SIFAS animation bundle you extracted from the game -- and
*replace* the ``AnimationClip`` inside it with ours, then repack.  That is what
this tool does, on top of `UnityPy`_.

.. _UnityPy: https://pypi.org/project/UnityPy/

Two modes
---------
``--inspect BUNDLE``
    Print what's inside a bundle: its Unity version, every ``AnimationClip``
    and -- crucially -- *how its curves are stored*.  A built clip usually
    keeps the runtime ``m_MuscleClip`` / ``m_Clip`` (Streamed/Dense/Constant)
    and **drops** the editor ``m_RotationCurves``.  Which one your SIFAS
    bundles use decides how injection must write the data, so run this first on
    a sample and share the output.

``--template BUNDLE --anim CLIP.anim --out OUT.bundle``
    Inject: parse the ``.anim`` and bake its curves into the template clip's
    **runtime** data, then repack the bundle.  SIFAS clips (Unity 2018.4) are
    generic and keep their motion in ``m_MuscleClip`` (the editor curves are
    stripped), so we write a single ``DenseClip`` -- every curve evenly sampled
    -- with matching ``genericBindings`` (CRC32 path hash, attribute 1=pos /
    2=rot, typeID 4).  The clip's name is **kept** by default so the game loads
    your motion in its place; the rest of the bundle (Avatar, Animator, the
    transform hierarchy, other assets) is untouched.

    Validated headless: the written DenseClip decodes back to the source
    ``.anim`` values to ~1e-7 and the bundle re-packs/re-loads cleanly.  Final
    in-game playback is the one thing that needs Unity, so test it there.

Requirements
------------
``pip install UnityPy`` (pure Python; no Unity, no bpy).

Usage
-----
::

    python3 sifac_anim_to_bundle.py --inspect ch0202_so2003.bundle
    python3 sifac_anim_to_bundle.py --inspect ch0202_so2003.bundle --dump-tree
    python3 sifac_anim_to_bundle.py --template ch0202_so2003.bundle \\
        --anim 0510.anim --out 0510_so.bundle
"""

from __future__ import annotations

import argparse
import re
import sys
import zlib
from pathlib import Path

try:
    import UnityPy
    HAVE_UNITYPY = True
except Exception:  # pragma: no cover - only exercised without UnityPy
    HAVE_UNITYPY = False


# --------------------------------------------------------------------------- #
# .anim (editor YAML) parsing
# --------------------------------------------------------------------------- #

def parse_anim(path):
    """Parse a Unity editor ``.anim`` into a small dict.

    Returns ``{"name", "sample_rate", "stop", "rot", "pos"}`` where ``rot`` is
    ``{leaf_path: [(time, (x,y,z,w)), ...]}`` and ``pos`` likewise with xyz.
    ``leaf_path`` is the full member-relative transform path string.
    """
    name = None
    sample_rate = 60.0
    stop = 0.0
    rot, pos = {}, {}
    section = None          # 'rot' | 'pos' | None
    cur_keys = []
    cur_path = None
    t = None
    comps = None

    def flush():
        nonlocal cur_keys, cur_path
        if cur_path is not None and section in ("rot", "pos"):
            (rot if section == "rot" else pos)[cur_path] = cur_keys
        cur_keys = []
        cur_path = None

    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("  m_Name:"):
            name = line.split(":", 1)[1].strip()
            continue
        if line.startswith("  m_SampleRate:"):
            try:
                sample_rate = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
            continue
        if line.startswith("    m_StopTime:"):
            try:
                stop = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
            continue
        if line.startswith("  m_RotationCurves"):
            flush(); section = "rot"; comps = "xyzw"; continue
        if line.startswith("  m_PositionCurves"):
            flush(); section = "pos"; comps = "xyz"; continue
        if (line.startswith("  m_CompressedRotationCurves") or
                line.startswith("  m_EulerCurves") or
                line.startswith("  m_ScaleCurves") or
                line.startswith("  m_FloatCurves") or
                line.startswith("  m_PPtrCurves")):
            flush(); section = None; continue
        if section not in ("rot", "pos"):
            continue
        if line.startswith("  - curve:"):
            flush(); continue
        m = re.match(r'\s+time: ([-+\d.eE]+)', line)
        if m:
            t = float(m.group(1)); continue
        m = re.match(r'\s+value: \{x: ([-+\d.eE]+), y: ([-+\d.eE]+), z: ([-+\d.eE]+)'
                     r'(?:, w: ([-+\d.eE]+))?\}', line)
        if m and t is not None:
            vals = [float(m.group(i)) for i in range(1, 5) if m.group(i) is not None]
            cur_keys.append((t, tuple(vals)))
            t = None
            continue
        m = re.match(r'    path: (.+)$', line)
        if m:
            cur_path = m.group(1).strip()
    flush()
    return {"name": name, "sample_rate": sample_rate, "stop": stop,
            "rot": rot, "pos": pos}


def crc32_path(path_str):
    """Unity's transform-path hash used in genericBindings / binary curves."""
    return zlib.crc32(path_str.encode("utf-8")) & 0xffffffff


# --------------------------------------------------------------------------- #
# Bundle inspection
# --------------------------------------------------------------------------- #

def _summary_of_tree(tree):
    """Describe how an AnimationClip typetree stores its data."""
    def n(key):
        v = tree.get(key)
        return len(v) if isinstance(v, (list, tuple)) else (0 if v is None else "?")

    info = {
        "m_Name": tree.get("m_Name"),
        "m_Legacy": tree.get("m_Legacy"),
        "m_Compressed": tree.get("m_Compressed"),
        "m_SampleRate": tree.get("m_SampleRate"),
        "m_RotationCurves": n("m_RotationCurves"),
        "m_CompressedRotationCurves": n("m_CompressedRotationCurves"),
        "m_PositionCurves": n("m_PositionCurves"),
        "m_EulerCurves": n("m_EulerCurves"),
        "m_ScaleCurves": n("m_ScaleCurves"),
        "m_FloatCurves": n("m_FloatCurves"),
    }
    muscle = tree.get("m_MuscleClip") or tree.get("m_Clip")
    if isinstance(muscle, dict):
        clip = muscle.get("m_Clip", muscle)
        if isinstance(clip, dict):
            sc = clip.get("m_StreamedClip", {})
            dc = clip.get("m_DenseClip", {})
            cc = clip.get("m_ConstantClip", {})
            binds = clip.get("m_Bindings")
            info["runtime_clip"] = {
                "StreamedClip.data": (len(sc.get("data", [])) if isinstance(sc, dict) else "?"),
                "DenseClip.m_SampleArray": (len(dc.get("m_SampleArray", [])) if isinstance(dc, dict) else "?"),
                "DenseClip.m_CurveCount": (dc.get("m_CurveCount") if isinstance(dc, dict) else "?"),
                "DenseClip.m_FrameCount": (dc.get("m_FrameCount") if isinstance(dc, dict) else "?"),
                "ConstantClip.data": (len(cc.get("data", [])) if isinstance(cc, dict) else "?"),
                "m_Bindings": (len(binds) if isinstance(binds, list) else "?"),
            }
    cbind = tree.get("m_ClipBindingConstant")
    if isinstance(cbind, dict):
        gb = cbind.get("genericBindings")
        info["genericBindings"] = len(gb) if isinstance(gb, list) else "?"
    return info


def inspect_bundle(path, dump_tree=False):
    env = UnityPy.load(str(path))
    print("bundle: %s" % path)
    ver = getattr(env, "assets", None)
    try:
        uv = next(iter(env.assets)).unity_version
        print("unity_version: %s" % uv)
    except Exception:
        pass
    n_clip = 0
    for obj in env.objects:
        tname = obj.type.name if hasattr(obj.type, "name") else str(obj.type)
        if tname != "AnimationClip":
            continue
        n_clip += 1
        try:
            tree = obj.read_typetree()
        except Exception as e:
            print("  [AnimationClip] (typetree read failed: %s)" % e)
            continue
        info = _summary_of_tree(tree)
        print("  AnimationClip #%d:" % n_clip)
        for k, v in info.items():
            print("      %-28s %s" % (k, v))
        if dump_tree:
            print("      --- top-level keys ---")
            for k in tree.keys():
                tv = type(tree[k]).__name__
                extra = ""
                if isinstance(tree[k], list) and tree[k]:
                    extra = " e.g. %r" % (tree[k][0],)
                    if len(str(extra)) > 200:
                        extra = " e.g. <%s of %d>" % (type(tree[k][0]).__name__, len(tree[k]))
                print("        %-30s : %s%s" % (k, tv, extra))
            dump_tree = False  # only the first clip, to keep output sane
    if n_clip == 0:
        print("  (no AnimationClip objects found -- is this an animation bundle?)")
        print("  object types present:",
              sorted({(o.type.name if hasattr(o.type, 'name') else str(o.type))
                      for o in env.objects}))
    return n_clip


# --------------------------------------------------------------------------- #
# Injection (format pinned from --inspect on a real sample)
# --------------------------------------------------------------------------- #

def _resample(keys, grid):
    """Sample a curve (keys = [(time, (v0,v1,...)), ...], sorted) onto the
    uniform ``grid`` of times by linear interpolation (hold past the ends).
    Returns a list of value-tuples, one per grid time."""
    out = []
    if not keys:
        return out
    ts = [k[0] for k in keys]
    ncomp = len(keys[0][1])
    j = 0
    n = len(keys)
    for g in grid:
        while j + 1 < n and ts[j + 1] <= g:
            j += 1
        if j + 1 >= n or g <= ts[0]:
            i = 0 if g <= ts[0] else n - 1
            out.append(tuple(keys[i][1]))
            continue
        t0, v0 = keys[j]
        t1, v1 = keys[j + 1]
        a = 0.0 if t1 == t0 else (g - t0) / (t1 - t0)
        out.append(tuple(v0[c] + (v1[c] - v0[c]) * a for c in range(ncomp)))
    return out


def _normalize_quat_rows(rows):
    """Renormalise 4-tuples (interpolated quaternions) to unit length."""
    out = []
    for q in rows:
        m = (q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]) ** 0.5 or 1.0
        out.append((q[0] / m, q[1] / m, q[2] / m, q[3] / m))
    return out


# attribute -> number of float curves it expands to
_ATTR_WIDTH = {1: 3, 2: 4, 3: 3, 4: 3, 0: 1}


def build_dense_clip(tree, anim, clip_name=None, sample_rate=60.0):
    """Rewrite a template AnimationClip typetree so its motion is OUR clip,
    stored as a single DenseClip (evenly sampled), with matching bindings.

    Generic clips keep the runtime data in ``m_MuscleClip``; the editor curves
    stay empty.  We put every curve in the DenseClip (StreamedClip and
    ConstantClip emptied), which makes the clip's curve-index space contiguous,
    so the GenericBinding order is exactly the DenseClip curve order.  The
    template's identity pose blocks (m_DeltaPose / m_StartX / ...) and the
    all-``-1`` m_IndexArray are kept as-is.
    """
    # frame grid covering [0, stop] at sample_rate
    stop = anim.get("stop") or 0.0
    if stop <= 0.0:
        for d in (anim["rot"], anim["pos"]):
            for keys in d.values():
                if keys:
                    stop = max(stop, keys[-1][0])
    frames = int(round(stop * sample_rate)) + 1
    grid = [i / sample_rate for i in range(frames)]

    # ordered bindings: positions (attr 1, 3 floats) then rotations (attr 2, 4).
    # The per-frame samples are laid out in this same order.
    columns = []   # (path, attribute, resampled rows)
    for path, keys in anim["pos"].items():
        columns.append((path, 1, _resample(keys, grid)))
    for path, keys in anim["rot"].items():
        columns.append((path, 2, _normalize_quat_rows(_resample(keys, grid))))

    # Drop any curve that resampled to nothing (a malformed/keyless source
    # curve); keeping it would crash the sample loop and desync the curve
    # count, bindings and value-delta arrays.
    dropped = [p for p, _a, rows in columns if not rows]
    if dropped:
        print("[warn] skipping %d curve(s) with no keyframes: %s"
              % (len(dropped), ", ".join(p.split("/")[-1] for p in dropped)))
    columns = [c for c in columns if c[2]]

    curve_count = sum(_ATTR_WIDTH[a] for _, a, _ in columns)

    # DenseClip sample array, frame-major: [f0c0 f0c1 ... f1c0 ...]
    sample_array = []
    for fi in range(frames):
        for _path, _attr, rows in columns:
            sample_array.extend(rows[fi] if fi < len(rows) else rows[-1])

    # per-float-curve min/max, in the same order
    value_delta = []
    for _path, _attr, rows in columns:
        width = len(rows[0]) if rows else 0
        for c in range(width):
            col = [r[c] for r in rows]
            value_delta.append({"m_Start": min(col), "m_Stop": max(col)})

    generic = [{"path": crc32_path(p), "attribute": a,
                "script": {"m_FileID": 0, "m_PathID": 0},
                "typeID": 4, "customType": 0, "isPPtrCurve": 0}
               for p, a, _ in columns]

    mc = tree["m_MuscleClip"]
    clip = mc["m_Clip"]["data"]
    clip["m_StreamedClip"] = {"data": [], "curveCount": 0}
    clip["m_DenseClip"] = {"m_FrameCount": frames, "m_CurveCount": curve_count,
                           "m_SampleRate": float(sample_rate), "m_BeginTime": 0.0,
                           "m_SampleArray": sample_array}
    clip["m_ConstantClip"] = {"data": []}
    mc["m_ValueArrayDelta"] = value_delta
    # no additive reference pose for our clips: keep this parallel-to-curves
    # array empty so it can't disagree with the new curve count.
    mc["m_ValueArrayReferencePose"] = []
    mc["m_StartTime"] = 0.0
    mc["m_StopTime"] = (frames - 1) / sample_rate if frames > 1 else 0.0

    tree["m_ClipBindingConstant"]["genericBindings"] = generic
    tree["m_ClipBindingConstant"]["pptrCurveMapping"] = []
    tree["m_SampleRate"] = float(sample_rate)
    tree["m_UseHighQualityCurve"] = False
    tree["m_Legacy"] = False
    tree["m_Compressed"] = False
    # the runtime allocates from this size hint; cover the dense blob generously
    tree["m_MuscleClipSize"] = len(sample_array) * 4 + len(value_delta) * 8 + 8192
    if clip_name:
        tree["m_Name"] = clip_name
    return tree, frames, curve_count, len(columns)


def inject(template, anim, out, clip_name=None, sample_rate=60.0,
           target=None, verbose=True):
    env = UnityPy.load(str(template))
    parsed = parse_anim(anim)
    targets = [o for o in env.objects
               if (o.type.name if hasattr(o.type, "name") else "") == "AnimationClip"]
    if not targets:
        raise SystemExit("no AnimationClip in template bundle %s" % template)
    names = [o.read_typetree().get("m_Name") for o in targets]
    if target is not None:
        picks = [o for o, nm in zip(targets, names) if nm == target]
        if not picks:
            raise SystemExit("no clip named %r in bundle (have: %s)"
                             % (target, ", ".join(map(str, names))))
        reader = picks[0]
    else:
        reader = targets[0]
        if len(targets) > 1:
            print("[warn] bundle has %d AnimationClips %s; replacing only '%s'. "
                  "Use --target NAME to pick another."
                  % (len(targets), names, names[0]))
    tree = reader.read_typetree()

    if not isinstance(tree.get("m_MuscleClip"), dict):
        raise SystemExit(
            "template clip has no m_MuscleClip (runtime clip).  Run --inspect "
            "and share the output; this format isn't handled yet.")

    if verbose:
        print("[inject] template clip   : '%s'  (%d bindings)"
              % (tree.get("m_Name"), len(tree["m_ClipBindingConstant"]["genericBindings"])))
        print("[inject] our clip '%s' : %d rotation + %d position curves"
              % (parsed["name"], len(parsed["rot"]), len(parsed["pos"])))

    tree, frames, ccount, nbind = build_dense_clip(
        tree, parsed, clip_name=clip_name, sample_rate=sample_rate)
    reader.save_typetree(tree)
    data = env.file.save()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(data)
    if verbose:
        print("[ok] %s  (DenseClip %d frames x %d curves, %d bindings, name '%s')"
              % (out, frames, ccount, nbind, tree.get("m_Name")))
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(
        description="Inspect a SIFAS animation bundle, or inject a retargeted "
                    ".anim into one (Unity-free, via UnityPy).")
    ap.add_argument("--inspect", metavar="BUNDLE",
                    help="print a bundle's AnimationClips and how they store curves")
    ap.add_argument("--dump-tree", action="store_true",
                    help="with --inspect: also dump the first clip's typetree keys")
    ap.add_argument("--template", metavar="BUNDLE", help="bundle to inject into")
    ap.add_argument("--anim", metavar="CLIP.anim", help="retargeted .anim to inject")
    ap.add_argument("--out", metavar="OUT.bundle", help="output bundle path")
    ap.add_argument("--clip-name", default=None,
                    help="rename the clip (default: KEEP the template's name, so "
                         "the game loads your motion in its place)")
    ap.add_argument("--fps", type=float, default=60.0,
                    help="DenseClip sample rate (default 60)")
    ap.add_argument("--target", default=None,
                    help="for a multi-clip bundle, the name of the clip to "
                         "replace (default: the first)")
    args = ap.parse_args()

    if not HAVE_UNITYPY:
        sys.stderr.write("UnityPy is required:  pip install UnityPy\n")
        return 2

    if args.inspect:
        inspect_bundle(args.inspect, dump_tree=args.dump_tree)
        return 0
    if args.template and args.anim and args.out:
        inject(args.template, args.anim, args.out,
               clip_name=args.clip_name, sample_rate=args.fps, target=args.target)
        return 0
    ap.error("use --inspect BUNDLE, or --template/--anim/--out together")


if __name__ == "__main__":
    sys.exit(main())
