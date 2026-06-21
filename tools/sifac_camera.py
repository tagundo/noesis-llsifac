#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_camera — SIFAC ``.bscam`` camera -> portable SIFAS-space camera track
===========================================================================

Body motion has ``sifac_anim_retarget.py`` (SIFAC dance -> SIFAS ``.anim``).
This is the camera sibling: it turns a SIFAC ``.bscam`` (the ``BSCM`` camera the
extractor pulls out of a live) into a **dense, per-frame camera track** —
position, rotation (quaternion) and field-of-view at a fixed frame rate, in
SIFAS / Unity **Y-up** space — written as a small, well-defined JSON file.

Why a track and not just the FBX?
---------------------------------
``sifac_convert.py`` already writes an *animated camera FBX* for DCC tools.  But
in SIFAS the camera does **not** live as a standalone asset — it is a set of
keyframes **inside the live timeline** (``LiveTimelineData``).  So to bring a
SIFAC camera into SIFAS you need the camera as **plain keyframe values** you can
drop onto the timeline's camera keys, not as an FBX node.  This tool produces
exactly those values:

    .bscam  --(parse: sifac_bmarc)-->  CameraAnim (sparse keys + base)
            --(sample + Y-up)------->  dense per-frame [pos, rot, fov]
            --(write)--------------->  camera.json   (ready to inject)

The **injection** step (writing these values onto a real SIFAS timeline's camera
keys) needs the exact ``LiveTimelineData`` camera-key field layout, which is
pinned from a real timeline asset the same way the mouth workflow pins it from a
UABEA dump — see the notes at the bottom of this file and the SIFAS-MODDING
``live_timeline`` tools.  This module deliberately stops at the verifiable,
engine-neutral camera track so its output is correct regardless of that step.

Usage
-----
::

    python3 sifac_camera.py --in cam_0510.bscam --out 0510_camera.json
    python3 sifac_camera.py --batch ./cams --outdir ./camera_json --fps 60
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


# --------------------------------------------------------------------------- #
# Sampling helpers (work on plain (frame, value) key lists)
# --------------------------------------------------------------------------- #

def _lerp(a, b, t):
    return a + (b - a) * t


def _sample_scalar(keys, frame, base):
    """Linear-interpolate a sparse [(idx, value)] list at integer ``frame``.

    Holds the end values past the first/last key; returns ``base`` if empty.
    """
    if not keys:
        return base
    if frame <= keys[0][0]:
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]
    for i in range(len(keys) - 1):
        f0, v0 = keys[i]
        f1, v1 = keys[i + 1]
        if f0 <= frame <= f1:
            t = 0.0 if f1 == f0 else (frame - f0) / (f1 - f0)
            return _lerp(v0, v1, t)
    return keys[-1][1]


def _sample_vec3(keys, frame, base):
    if not keys:
        return [base[0], base[1], base[2]]
    if frame <= keys[0][0]:
        return list(keys[0][1])
    if frame >= keys[-1][0]:
        return list(keys[-1][1])
    for i in range(len(keys) - 1):
        f0, v0 = keys[i]
        f1, v1 = keys[i + 1]
        if f0 <= frame <= f1:
            t = 0.0 if f1 == f0 else (frame - f0) / (f1 - f0)
            return [_lerp(v0[c], v1[c], t) for c in range(3)]
    return list(keys[-1][1])


def _norm_quat(q):
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]) or 1.0
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def _nlerp_quat(a, b, t):
    """Normalised lerp with hemisphere correction — smooth, cheap, no flips."""
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    if d < 0.0:
        b = (-b[0], -b[1], -b[2], -b[3])
    q = (_lerp(a[0], b[0], t), _lerp(a[1], b[1], t),
         _lerp(a[2], b[2], t), _lerp(a[3], b[3], t))
    return _norm_quat(q)


def _sample_quat(keys, frame, base):
    if not keys:
        return list(_norm_quat(base))
    if frame <= keys[0][0]:
        return list(_norm_quat(keys[0][1]))
    if frame >= keys[-1][0]:
        return list(_norm_quat(keys[-1][1]))
    for i in range(len(keys) - 1):
        f0, v0 = keys[i]
        f1, v1 = keys[i + 1]
        if f0 <= frame <= f1:
            t = 0.0 if f1 == f0 else (frame - f0) / (f1 - f0)
            return list(_nlerp_quat(v0, v1, t))
    return list(_norm_quat(keys[-1][1]))


# --------------------------------------------------------------------------- #
# CameraAnim -> dense track
# --------------------------------------------------------------------------- #

def _as_tuple3(v):
    return (float(v.x), float(v.y), float(v.z))


def _as_tuple4(q):
    return (float(q.x), float(q.y), float(q.z), float(q.w))


def camera_to_track(cam, fps=None, scale=1.0):
    """Turn a :class:`CameraAnim` into a dense per-frame camera track dict.

    Output is in SIFAS / Unity **Y-up** space (the ``.bscam`` already stands
    upright there, the same as ``sifac_convert``'s default ``--up-axis y``):

        {"format": "sifac-camera/1", "name", "fps", "frame_count", "up_axis",
         "frames": [{"t", "pos":[x,y,z], "rot":[x,y,z,w], "fov"}, ...]}
    """
    out_fps = float(fps or cam.fps or 60.0)
    end = int(cam.end_frame)
    tran = [(int(i), _as_tuple3(v)) for i, v in cam.translation]
    rot = [(int(i), _as_tuple4(q)) for i, q in cam.rotation]
    fov = [(int(i), float(f)) for i, f in cam.fov]
    base_pos = _as_tuple3(cam.base_pos)
    base_rot = _as_tuple4(cam.base_rot)
    base_fov = float(cam.base_fov)

    frames = []
    for f in range(end + 1):
        p = _sample_vec3(tran, f, base_pos)
        frames.append({
            "t": round(f / out_fps, 6),
            "pos": [round(p[0] * scale, 6), round(p[1] * scale, 6), round(p[2] * scale, 6)],
            "rot": [round(c, 6) for c in _sample_quat(rot, f, base_rot)],
            "fov": round(_sample_scalar(fov, f, base_fov), 4),
        })
    return {
        "format": "sifac-camera/1",
        "name": cam.name,
        "fps": out_fps,
        "frame_count": end + 1,
        "up_axis": "y",
        "frames": frames,
    }


def convert_file(bscam_path, out_json, fps=None, scale=1.0, verbose=True):
    import sifac_bmarc
    data = Path(bscam_path).read_bytes()
    parsed = sifac_bmarc.parse_bscam(data, Path(bscam_path).stem)
    if parsed.camera is None:
        raise ValueError("no camera data in %s" % bscam_path)
    track = camera_to_track(parsed.camera, fps=fps, scale=scale)
    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_json).write_text(json.dumps(track, indent=1), encoding="utf-8")
    if verbose:
        print("[ok] %s  (%d frames @ %g fps, fov %.1f..%.1f)"
              % (out_json, track["frame_count"], track["fps"],
                 min(f["fov"] for f in track["frames"]),
                 max(f["fov"] for f in track["frames"])))
    return out_json


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a SIFAC .bscam camera into a dense SIFAS-space "
                    "camera track (JSON) ready for live-timeline injection.")
    ap.add_argument("--in", dest="inp", help="source .bscam file")
    ap.add_argument("--out", help="output .json path")
    ap.add_argument("--batch", help="folder of .bscam files")
    ap.add_argument("--outdir", help="output folder for --batch")
    ap.add_argument("--fps", type=float, default=None, help="resample rate (default: source fps)")
    ap.add_argument("--scale", type=float, default=1.0, help="position scale")
    args = ap.parse_args(argv)

    if args.batch:
        base = Path(args.batch)
        outdir = Path(args.outdir or "camera_json")
        files = sorted(p for p in base.rglob("*") if p.suffix.lower() == ".bscam")
        if not files:
            print("no .bscam files under", base)
            return 1
        for fb in files:
            out = outdir / (fb.stem + ".json")
            try:
                convert_file(fb, out, fps=args.fps, scale=args.scale)
            except Exception as exc:  # keep going through the batch
                print("[fail] %s: %s" % (fb.name, exc))
        return 0

    if not args.inp or not args.out:
        ap.error("--in and --out are required (or use --batch/--outdir)")
    convert_file(args.inp, args.out, fps=args.fps, scale=args.scale)
    return 0


# --------------------------------------------------------------------------- #
# Injecting into a SIFAS live timeline (notes)
# --------------------------------------------------------------------------- #
#
# In SIFAS the camera is a set of keyframes inside the live timeline
# (LiveTimelineData), not a standalone asset.  To inject this track you overwrite
# the timeline's camera keys with these per-frame pos / rot / fov values, the
# same way the SIFAS-MODDING live_timeline/ tools overwrite the mouth keys from a
# UABEA dump (and the same template-replacement idea as sifac_anim_to_bundle.py):
#
#   1. Dump the target timeline (UABEA text or UnityPy typetree) and locate its
#      camera key list (position / lookAt-or-rotation / fov over time).
#   2. Resample THIS track onto the timeline's key times (it is already dense,
#      so this is a lookup) and write the values back.
#   3. Repack the bundle and install it with elichika's live_dance_installer.py.
#
# The exact camera-key field names differ per timeline build, so that step is
# pinned from a real sample rather than guessed here — keeping this converter's
# output (the camera track itself) correct on its own.

if __name__ == "__main__":
    sys.exit(main())
