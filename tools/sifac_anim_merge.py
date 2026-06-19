#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_anim_merge -- combine two SIFAS .anim clips by bone group
===============================================================

A direct (Unity-free) retarget reproduces the SIFAC motion *faithfully*, which
is ideal for the body, legs, fingers and root motion.  But the ARMS of a SIFAC
motion, placed unchanged on a SIFAS rig, can look off -- the SIFAS character is
meant to carry its arms in its own natural range, which is exactly what Unity's
Humanoid retarget produces.

This tool takes the best of both: it keeps every curve from a FAITHFUL clip
(``sifac_anim_retarget`` output) except the arm group, which it pulls from a
NATURAL clip (your Humanoid retarget).  The two clips must be the same motion on
the same SIFAS skeleton (member-relative paths), time-aligned.

    python3 sifac_anim_merge.py faithful.anim natural.anim out.anim

By default the arm group taken from NATURAL is::

    LeftShoulder RightShoulder LeftArm RightArm LeftForeArm RightForeArm
    LeftArmRoll RightArmRoll LeftForeArmRoll RightForeArmRoll
    LeftHand RightHand Neck Head

Everything else (hips, spine, legs, feet, toes, FINGERS, root translation)
comes from FAITHFUL.  Override the set with ``--natural-bones A,B,C`` (matched
by the path's last segment) or add to it with ``--also-natural A,B``.
"""

from __future__ import annotations

import argparse
import re
import sys
import zlib
from pathlib import Path

DEFAULT_NATURAL = [
    "LeftShoulder", "RightShoulder", "LeftArm", "RightArm",
    "LeftForeArm", "RightForeArm", "LeftArmRoll", "RightArmRoll",
    "LeftForeArmRoll", "RightForeArmRoll", "LeftHand", "RightHand",
    "Neck", "Head",
]


def _split_curves(text, section_header):
    """Return (list of (path, block_text)) for one curve section, and whether
    the section was present."""
    lines = text.splitlines(keepends=True)
    out = []
    i = 0
    n = len(lines)
    # find the section header line
    while i < n and not lines[i].startswith(section_header):
        i += 1
    if i >= n:
        return out
    i += 1
    block = []
    while i < n:
        ln = lines[i]
        # a new top-level section (2-space key) ends this one
        if re.match(r'^  m_[A-Za-z]', ln) or re.match(r'^  [A-Za-z]', ln):
            break
        if ln.startswith("  - curve:") and block:
            # shouldn't happen (path closes a block), but guard
            block = []
        block.append(ln)
        m = re.match(r'^    path: (.+?)\s*$', ln)
        if m:
            out.append((m.group(1), "".join(block)))
            block = []
        i += 1
    return out


def _binding(path, attribute):
    h = zlib.crc32(path.encode("utf-8")) & 0xffffffff
    return ("    - serializedVersion: 2\n"
            "      path: %d\n"
            "      attribute: %d\n"
            "      script: {fileID: 0}\n"
            "      typeID: 4\n"
            "      customType: 0\n"
            "      isPPtrCurve: 0\n" % (h, attribute))


def _stop_time(rot_blocks):
    """Largest keyframe time across the given curve blocks."""
    stop = 0.0
    for _p, b in rot_blocks:
        for m in re.finditer(r'^\s+time: ([-\d.eE]+)', b, re.M):
            stop = max(stop, float(m.group(1)))
    return stop


def merge(faithful_path, natural_path, out_path, natural_bones, clip_name=None):
    faithful = Path(faithful_path).read_text(encoding="utf-8", errors="replace")
    natural = Path(natural_path).read_text(encoding="utf-8", errors="replace")

    f_rot = _split_curves(faithful, "  m_RotationCurves:")
    f_pos = _split_curves(faithful, "  m_PositionCurves:")
    n_rot = dict(_split_curves(natural, "  m_RotationCurves:"))

    natset = set(natural_bones)

    def leaf(p):
        return p.rsplit("/", 1)[-1]

    out_rot = []
    swapped = []
    missing = []
    for path, block in f_rot:
        if leaf(path) in natset:
            if path in n_rot:
                out_rot.append((path, n_rot[path]))
                swapped.append(leaf(path))
            else:
                # natural clip lacks it -- keep faithful, note it
                out_rot.append((path, block))
                missing.append(leaf(path))
        else:
            out_rot.append((path, block))
    # also bring in natural arm bones that the faithful clip doesn't have
    f_rot_paths = {p for p, _ in f_rot}
    for path, block in n_rot.items():
        if leaf(path) in natset and path not in f_rot_paths:
            out_rot.append((path, block))
            swapped.append(leaf(path) + "(+)")

    if clip_name is None:
        m = re.search(r'^  m_Name: (.+?)\s*$', faithful, re.M)
        clip_name = (m.group(1) if m else "merged")

    stop = _stop_time(out_rot)
    out = []
    out.append("%YAML 1.1")
    out.append("%TAG !u! tag:unity3d.com,2011:")
    out.append("--- !u!74 &7400000")
    out.append("AnimationClip:")
    out.append("  m_ObjectHideFlags: 0")
    out.append("  m_CorrespondingSourceObject: {fileID: 0}")
    out.append("  m_PrefabInstance: {fileID: 0}")
    out.append("  m_PrefabAsset: {fileID: 0}")
    out.append("  m_Name: %s" % clip_name)
    out.append("  serializedVersion: 6")
    out.append("  m_Legacy: 0")
    out.append("  m_Compressed: 0")
    out.append("  m_UseHighQualityCurve: 1")
    out.append("  m_RotationCurves:")
    body = "".join(b for _p, b in out_rot)
    out.append(body.rstrip("\n"))
    out.append("  m_CompressedRotationCurves: []")
    out.append("  m_EulerCurves: []")
    out.append("  m_PositionCurves:")
    if f_pos:
        out.append("".join(b for _p, b in f_pos).rstrip("\n"))
    out.append("  m_ScaleCurves: []")
    out.append("  m_FloatCurves: []")
    out.append("  m_PPtrCurves: []")
    out.append("  m_SampleRate: 60")
    out.append("  m_WrapMode: 0")
    out.append("  m_Bounds:")
    out.append("    m_Center: {x: 0, y: 0, z: 0}")
    out.append("    m_Extent: {x: 0, y: 0, z: 0}")
    out.append("  m_ClipBindingConstant:")
    out.append("    genericBindings:")
    binds = []
    for p, _b in out_rot:
        binds.append(_binding(p, 2))
    for p, _b in f_pos:
        binds.append(_binding(p, 1))
    out.append("".join(binds).rstrip("\n"))
    out.append("    pptrCurveMapping: []")
    out.append("  m_AnimationClipSettings:")
    out.append("    serializedVersion: 2")
    out.append("    m_AdditiveReferencePoseClip: {fileID: 0}")
    out.append("    m_AdditiveReferencePoseTime: 0")
    out.append("    m_StartTime: 0")
    out.append("    m_StopTime: %g" % stop)
    out.append("    m_OrientationOffsetY: 0")
    out.append("    m_Level: 0")
    out.append("    m_CycleOffset: 0")
    out.append("    m_HasAdditiveReferencePose: 0")
    out.append("    m_LoopTime: 0")
    out.append("    m_LoopBlend: 0")
    out.append("    m_LoopBlendOrientation: 0")
    out.append("    m_LoopBlendPositionY: 0")
    out.append("    m_LoopBlendPositionXZ: 0")
    out.append("    m_KeepOriginalOrientation: 0")
    out.append("    m_KeepOriginalPositionY: 1")
    out.append("    m_KeepOriginalPositionXZ: 0")
    out.append("    m_HeightFromFeet: 0")
    out.append("    m_Mirror: 0")
    out.append("  m_EditorCurves: []")
    out.append("  m_EulerEditorCurves: []")
    out.append("  m_HasGenericRootTransform: 0")
    out.append("  m_HasMotionFloatCurves: 0")
    out.append("  m_Events: []")
    out.append("")
    Path(out_path).write_text("\n".join(out), encoding="utf-8")
    print("[ok] %s" % out_path)
    print("  arms from NATURAL: %s" % " ".join(sorted(set(swapped))))
    if missing:
        print("  [warn] natural clip had no curve for: %s (kept faithful)"
              % " ".join(sorted(set(missing))))
    print("  everything else (body, legs, fingers, root) from FAITHFUL")


def main():
    ap = argparse.ArgumentParser(description="Merge a faithful + a natural SIFAS .anim by bone group")
    ap.add_argument("faithful", help="faithful clip (sifac_anim_retarget output)")
    ap.add_argument("natural", help="natural clip (e.g. Unity Humanoid retarget)")
    ap.add_argument("out", help="output .anim")
    ap.add_argument("--name", default=None, help="clip name (default: faithful's)")
    ap.add_argument("--natural-bones", default=None,
                    help="comma list of bone leaf names to take from NATURAL "
                         "(default: the arm group)")
    ap.add_argument("--also-natural", default=None,
                    help="comma list to ADD to the default natural group")
    args = ap.parse_args()
    if args.natural_bones:
        nb = [b.strip() for b in args.natural_bones.split(",") if b.strip()]
    else:
        nb = list(DEFAULT_NATURAL)
        if args.also_natural:
            nb += [b.strip() for b in args.also_natural.split(",") if b.strip()]
    merge(args.faithful, args.natural, args.out, nb, clip_name=args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
