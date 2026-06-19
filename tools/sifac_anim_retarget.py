#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_anim_retarget — Unity-free SIFAC -> SIFAS animation retarget
==================================================================

Converts a SIFAC (PS4/Arcade) animation FBX directly into a SIFAS
(School idol festival ALL STARS) ``.anim`` clip, **without going through
Unity's Humanoid retargeting**.

Why not Humanoid?
-----------------
The usual workflow imports the motion as a Humanoid clip and lets Unity
retarget it.  Humanoid retargeting is *lossy*: it clamps to a muscle space,
normalises bone lengths and drops every non-Humanoid bone, so the result is
only "half" the original motion.  This tool keeps the motion faithful by
copying each shared bone's **rest-relative local rotation** (Blender
``matrix_basis``) straight from the SIFAC rig onto the SIFAS rig.  Both rigs
share the same core skeleton, so the same joint bends the same way.

How it works
------------
1. Import the SIFAC animation FBX in Blender (``bpy``).
2. Rebuild the SIFAS skeleton at its rest pose (bundled prefab data, or a
   prefab you point at).  The skeleton is built in Blender space with the
   fixed coordinate change ``B = Rx(+90)`` (Unity Y-up -> Blender Z-up).
3. Per bone, aim the SIFAS bone where the SIFAC bone points in **absolute**
   world space: ``W = G . W_sifac_pose . Cconv``.  ``G`` is a global frame
   alignment (a signed permutation aligning the two rigs' body frames -- up,
   named-right and toe-forward).  ``Cconv`` is a pure-twist per-bone
   convention map (found at rest after aligning the rest bone directions), so
   the SIFAS arms match the SIFAC arms even though the two rigs rest their
   arms ~45 deg apart -- a plain rotation-from-rest copy would offset them.
4. Undo ``B`` to return to Unity space and express each bone as a
   *local-to-parent* rotation -- exactly what a Unity ``.anim`` stores.
   Root/stage translation comes from the SIFAC pelvis, on ``Hips_Position``.
5. Write a generic ``.anim`` (rotation curves + Hips_Position curve,
   ``ClipBindingConstant`` with CRC32 path hashes, member-relative paths,
   60 fps metadata) -- matched to real SIFAS game clips.

The rest round-trip (rig rebuilt and read back) reproduces the prefab's own
local quaternions to ~0.04 deg, and the retarget was validated by driving a
real SIFAS body mesh from the output and confirming a natural pose (no twist).

Requirements
------------
Blender's Python module (same one the FBX converter uses)::

    pip install bpy

Usage
-----
Single file::

    python3 sifac_anim_retarget.py --sifac mot_06_maki_0510.fbx \\
        --out 0510.anim --member ch0004_co0019_member --name "0510 Daring"

Batch a folder::

    python3 sifac_anim_retarget.py --batch ./sifac_motions \\
        --outdir ./anim_out --member ch0004_co0019_member

It also runs inside Blender (``blender -b --python sifac_anim_retarget.py --
<args>``); the same argument list is read after a lone ``--``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SKELETON = HERE / "data" / "sifas_core_skeleton.json"

try:
    import bpy
    import mathutils
    from mathutils import Matrix, Quaternion, Vector
    HAVE_BPY = True
except Exception:  # pragma: no cover - exercised only without bpy
    HAVE_BPY = False


# Unity Y-up (left handed) -> Blender Z-up (right handed); pure rotation, det +1.
def _B():
    return Matrix(((1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


# --------------------------------------------------------------------------- #
# Skeleton source: bundled JSON, or a SIFAS member .prefab
# --------------------------------------------------------------------------- #

def _parse_prefab(prefab_path):
    """Return name -> (parent_name, rot_xyzw, pos_xyz) and name -> rel_path."""
    import re
    txt = Path(prefab_path).read_text(encoding="utf-8", errors="replace")
    docs = re.split(r'^--- !u!(\d+) &(-?\d+).*$', txt, flags=re.M)
    gos, trs = {}, {}
    i = 1
    while i + 2 < len(docs):
        cls, fid, body = docs[i], docs[i + 1], docs[i + 2]
        if cls == "1":
            m = re.search(r'm_Name:\s*"?([^"\n]+?)"?\s*$', body, re.M)
            gos[fid] = m.group(1).strip() if m else "?"
        elif cls == "4":
            go = re.search(r'm_GameObject:\s*\{fileID:\s*(-?\d+)\}', body)
            rot = re.search(r'm_LocalRotation:.*?x:\s*([-\d.eE]+),\s*y:\s*([-\d.eE]+),'
                            r'\s*z:\s*([-\d.eE]+),\s*w:\s*([-\d.eE]+)', body, re.S)
            pos = re.search(r'm_LocalPosition:.*?x:\s*([-\d.eE]+),\s*y:\s*([-\d.eE]+),'
                            r'\s*z:\s*([-\d.eE]+)', body, re.S)
            fa = re.search(r'm_Father:\s*\{fileID:\s*(-?\d+)\}', body)
            trs[fid] = {"go": go.group(1) if go else None,
                        "rot": tuple(float(rot.group(k)) for k in range(1, 5)) if rot else (0, 0, 0, 1),
                        "pos": tuple(float(pos.group(k)) for k in range(1, 4)) if pos else (0, 0, 0),
                        "father": fa.group(1) if fa else "0"}
        i += 3
    name2fid = {gos.get(t["go"]): fid for fid, t in trs.items() if t["go"] in gos}
    tbl, relpath = {}, {}
    for fid, t in trs.items():
        name = gos.get(t["go"], "?")
        pfid = t["father"]
        pname = gos.get(trs[pfid]["go"]) if pfid in trs else None
        tbl[name] = (pname, t["rot"], t["pos"])
    # member = the bone whose father is 0 / outside
    member = None
    for name, (pname, _, _) in tbl.items():
        if pname is None:
            member = name
            break

    def path(name):
        parts = [name]
        cur = name
        while True:
            p = tbl.get(cur, (None,))[0]
            if not p or p not in tbl:
                break
            parts.append(p)
            cur = p
        return "/".join(reversed(parts))

    for name in tbl:
        full = path(name)
        relpath[name] = full[len(member) + 1:] if member and full.startswith(member + "/") else (
            "" if name == member else full)
    return tbl, relpath, member


def load_skeleton(prefab=None, skeleton_json=None):
    """
    Returns (tbl, relpath, core, member_example) where
      tbl[name]   = (parent_name, rot_xyzw, pos_xyz)
      relpath[name] = path from the member object, e.g. "Reference/Move/.../Hips"
      core        = list of bone names shared by SIFAC and SIFAS (to retarget)
    """
    if prefab:
        tbl, relpath, member = _parse_prefab(prefab)
        data = json.loads(Path(skeleton_json or DEFAULT_SKELETON).read_text(encoding="utf-8"))
        core = [c for c in data["core"] if c in tbl]
        return tbl, relpath, core, member
    data = json.loads(Path(skeleton_json or DEFAULT_SKELETON).read_text(encoding="utf-8"))
    tbl, relpath = {}, {}
    for b in data["bones"]:
        tbl[b["name"]] = (b["parent"], tuple(b["rot"]), tuple(b["pos"]))
        relpath[b["name"]] = b["rel_path"]
    return tbl, relpath, list(data["core"]), data.get("member_example", "ch0000_co0000_member")


# --------------------------------------------------------------------------- #
# Build the SIFAS rest skeleton in Blender
# --------------------------------------------------------------------------- #

def _depth(tbl, n):
    d, p = 0, tbl[n][0]
    while p is not None and p in tbl:
        d += 1
        p = tbl[p][0]
    return d


def build_sifas_armature(tbl, name="SIFAS_target"):
    B = _B()

    def u_local(n):
        _, q, pos = tbl[n]
        return Matrix.Translation(Vector(pos)) @ Quaternion((q[3], q[0], q[1], q[2])).to_matrix().to_4x4()

    cache = {}

    def u_world(n):
        if n in cache:
            return cache[n]
        p = tbl[n][0]
        W = u_local(n) if (p is None or p not in tbl) else u_world(p) @ u_local(n)
        cache[n] = W
        return W

    order = sorted(tbl.keys(), key=lambda n: _depth(tbl, n))
    ad = bpy.data.armatures.new(name)
    ob = bpy.data.objects.new(name, ad)
    bpy.context.collection.objects.link(ob)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode='EDIT')
    for n in order:
        eb = ad.edit_bones.new(n)
        Wb = B @ u_world(n)
        eb.head = Wb.translation
        eb.tail = Wb.translation + (Wb.to_3x3() @ Vector((0, 0.06, 0)))
        eb.matrix = Wb
        pn = tbl[n][0]
        if pn is not None and pn in ad.edit_bones:
            eb.parent = ad.edit_bones[pn]
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.update()
    return ob, order


# --------------------------------------------------------------------------- #
# Unity-space extraction
# --------------------------------------------------------------------------- #

class _Extractor:
    """Pull Unity local rotation/position out of a posed Blender armature."""

    def __init__(self, ob, tbl):
        self.ob = ob
        self.tbl = tbl
        self.Binv = _B().inverted()

    def world(self, n):
        # full 4x4 in Unity space
        return self.Binv @ (self.ob.matrix_world @ self.ob.pose.bones[n].matrix)

    def local(self, n):
        """Return (rot_xyzw, pos_xyz) local to the Unity parent."""
        Wn = self.world(n)
        p = self.tbl[n][0]
        if p is not None and p in self.ob.pose.bones:
            L = self.world(p).inverted() @ Wn
        else:
            L = Wn
        q = L.to_quaternion()
        t = L.translation
        return (q.x, q.y, q.z, q.w), (t.x, t.y, t.z)


def self_test(ob, tbl):
    """Rest round-trip: extracted locals must match the prefab's own quats."""
    ex = _Extractor(ob, tbl)
    worst, wn = 0.0, None
    for n, (p, q, _) in tbl.items():
        if p is None or p not in ob.pose.bones:
            continue
        got, _pos = ex.local(n)
        d = abs(got[0] * q[0] + got[1] * q[1] + got[2] * q[2] + got[3] * q[3])
        d = math.degrees(2 * math.acos(min(1.0, d)))
        if d > worst:
            worst, wn = d, n
    return worst, wn


# --------------------------------------------------------------------------- #
# .anim writing
# --------------------------------------------------------------------------- #

def _g(v):
    """Compact float formatting Unity is happy to parse."""
    if v == 0:
        return "0"
    s = "%.9g" % v
    return s


def _finite_diff_tangents(times, vals):
    """Catmull-Rom-ish tangents per component for smooth C1 playback."""
    n = len(times)
    ncomp = len(vals[0])
    tin = [[0.0] * ncomp for _ in range(n)]
    tout = [[0.0] * ncomp for _ in range(n)]
    for i in range(n):
        for c in range(ncomp):
            if 0 < i < n - 1:
                dt = times[i + 1] - times[i - 1]
                s = (vals[i + 1][c] - vals[i - 1][c]) / dt if dt else 0.0
            elif i == 0 and n > 1:
                dt = times[1] - times[0]
                s = (vals[1][c] - vals[0][c]) / dt if dt else 0.0
            elif i == n - 1 and n > 1:
                dt = times[-1] - times[-2]
                s = (vals[-1][c] - vals[-2][c]) / dt if dt else 0.0
            else:
                s = 0.0
            tin[i][c] = s
            tout[i][c] = s
    return tin, tout


def _write_curve(out, path, times, vals, comps):
    """Write one rotation (comps='xyzw') or position (comps='xyz') curve."""
    tin, tout = _finite_diff_tangents(times, vals)
    out.append("  - curve:")
    out.append("      serializedVersion: 2")
    out.append("      m_Curve:")
    third = "0.33333334"
    for i, t in enumerate(times):
        v = vals[i]
        vk = ", ".join("%s: %s" % (comps[c], _g(v[c])) for c in range(len(comps)))
        ik = ", ".join("%s: %s" % (comps[c], _g(tin[i][c])) for c in range(len(comps)))
        ok = ", ".join("%s: %s" % (comps[c], _g(tout[i][c])) for c in range(len(comps)))
        w = ", ".join("%s: %s" % (comps[c], third) for c in range(len(comps)))
        out.append("      - serializedVersion: 3")
        out.append("        time: %s" % _g(t))
        out.append("        value: {%s}" % vk)
        out.append("        inSlope: {%s}" % ik)
        out.append("        outSlope: {%s}" % ok)
        out.append("        tangentMode: 0")
        out.append("        weightedMode: 0")
        out.append("        inWeight: {%s}" % w)
        out.append("        outWeight: {%s}" % w)
    out.append("      m_PreInfinity: 2")
    out.append("      m_PostInfinity: 2")
    out.append("      m_RotationOrder: 4")
    out.append("    path: %s" % path)


def write_anim(path_out, clip_name, times, rot_curves, pos_curves, fps):
    """
    rot_curves: list of (full_path, [ (x,y,z,w) per frame ])
    pos_curves: list of (full_path, [ (x,y,z)   per frame ])
    """
    stop = times[-1] if times else 0.0
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
    for p, vals in rot_curves:
        _write_curve(out, p, times, vals, "xyzw")
    out.append("  m_CompressedRotationCurves: []")
    out.append("  m_EulerCurves: []")
    out.append("  m_PositionCurves:")
    for p, vals in pos_curves:
        _write_curve(out, p, times, vals, "xyz")
    out.append("  m_ScaleCurves: []")
    out.append("  m_FloatCurves: []")
    out.append("  m_PPtrCurves: []")
    out.append("  m_SampleRate: %d" % int(round(fps)))
    out.append("  m_WrapMode: 0")
    out.append("  m_Bounds:")
    out.append("    m_Center: {x: 0, y: 0, z: 0}")
    out.append("    m_Extent: {x: 0, y: 0, z: 0}")
    out.append("  m_ClipBindingConstant:")
    out.append("    genericBindings:")
    # attribute: 1=position, 2=rotation, 3=scale ; typeID 4 = Transform
    for p, _ in rot_curves:
        _binding(out, p, 2)
    for p, _ in pos_curves:
        _binding(out, p, 1)
    out.append("    pptrCurveMapping: []")
    out.append("  m_AnimationClipSettings:")
    out.append("    serializedVersion: 2")
    out.append("    m_AdditiveReferencePoseClip: {fileID: 0}")
    out.append("    m_AdditiveReferencePoseTime: 0")
    out.append("    m_StartTime: 0")
    out.append("    m_StopTime: %s" % _g(stop))
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
    Path(path_out).parent.mkdir(parents=True, exist_ok=True)
    Path(path_out).write_text("\n".join(out), encoding="utf-8")


def _binding(out, path, attribute):
    h = zlib.crc32(path.encode("utf-8")) & 0xffffffff
    out.append("    - serializedVersion: 2")
    out.append("      path: %d" % h)
    out.append("      attribute: %d" % attribute)
    out.append("      script: {fileID: 0}")
    out.append("      typeID: 4")
    out.append("      customType: 0")
    out.append("      isPPtrCurve: 0")


# --------------------------------------------------------------------------- #
# Retarget driver
# --------------------------------------------------------------------------- #

def _ensure_sign_continuity(seq):
    """Flip quaternion signs so neighbours stay on the same hemisphere."""
    for i in range(1, len(seq)):
        a, b = seq[i - 1], seq[i]
        if a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3] < 0:
            seq[i] = (-b[0], -b[1], -b[2], -b[3])


# SIFAS roll/twist bones, and how much of which driver bone's twist they take.
# These bones do not exist in SIFAC, so a plain retarget leaves them at rest
# and the shoulder/elbow skin pinches ("candy-wrapper") at extreme twists.
# Real SIFAS game clips drive them: each roll bone rotates about the limb's
# own long axis (local +X; the roll bones rest at identity with the bone laid
# along -X) by a fixed fraction of its driver bone's twist about that same
# axis.  Measured from a shipped clip (ch0202): ArmRoll = -1/2 . twist(Arm)
# and ForeArmRoll = +1/2 . twist(Hand) -- the textbook half-twist split.
# Reproducing the rig's own formula spreads the twist and removes the pinch.
_TWIST_BONES = [
    ("LeftArmRoll", "LeftArm", -0.5),
    ("RightArmRoll", "RightArm", -0.5),
    ("LeftForeArmRoll", "LeftHand", 0.5),
    ("RightForeArmRoll", "RightHand", 0.5),
]


def _twist_about_x(q):
    """Signed twist angle (radians) of quaternion q=(x,y,z,w) about +X.

    Swing-twist decomposition: the twist part is the quaternion (x,0,0,w)
    normalised, whose angle is 2*atan2(x, w).
    """
    return 2.0 * math.atan2(q[0], q[3])


def _smooth_quat_seq(seq, window):
    """Light low-pass on a quaternion sequence.

    Hemisphere-aligned, triangular-weighted normalised average over a
    ``+/- window//2`` neighbourhood.  Softens genuinely fast motion (e.g. the
    arms, which a faithful retarget keeps sharp where Humanoid would clamp it)
    toward a smoother look, at a small cost in fidelity.  ``window`` is in
    frames; 0/1/2 are no-ops.
    """
    n = len(seq)
    if window < 3 or n < 3:
        return seq
    h = window // 2
    out = []
    for i in range(n):
        ref = seq[i]
        ax = ay = az = aw = 0.0
        for j in range(max(0, i - h), min(n, i + h + 1)):
            q = seq[j]
            s = 1.0 if (q[0] * ref[0] + q[1] * ref[1] + q[2] * ref[2] + q[3] * ref[3]) >= 0 else -1.0
            wt = 1.0 - abs(i - j) / (h + 1.0)
            ax += s * q[0] * wt; ay += s * q[1] * wt; az += s * q[2] * wt; aw += s * q[3] * wt
        nrm = math.sqrt(ax * ax + ay * ay + az * az + aw * aw) or 1.0
        out.append((ax / nrm, ay / nrm, az / nrm, aw / nrm))
    return out


def _twist_curves(rot_seq, full_path, tbl, strength=1.0):
    """Build roll-bone rotation curves from the driver bones' local twist.

    ``strength`` scales the measured half-twist factor: 1.0 = the game's own
    amount (ArmRoll = -1/2 . Arm, ForeArmRoll = +1/2 . Hand), 0.0 = none,
    >1.0 = exaggerated.  Lets you dial how strongly the shoulder/elbow skin is
    un-pinched.
    """
    curves = []
    for roll, drv, factor in _TWIST_BONES:
        if roll not in tbl or drv not in rot_seq:
            continue
        seq = []
        for q in rot_seq[drv]:
            half = 0.5 * factor * strength * _twist_about_x(q)
            seq.append((math.sin(half), 0.0, 0.0, math.cos(half)))
        _ensure_sign_continuity(seq)
        curves.append((full_path(roll), seq))
    return curves


# --------------------------------------------------------------------------- #
# Multi-format export: bake the retargeted clip onto the SIFAS rig in Blender
# and hand it to Blender's own exporters (FBX / glTF / BVH).  The .anim path
# does not need this -- it writes Unity curves straight out -- but DCC formats
# want the motion as a real Action on the armature.
# --------------------------------------------------------------------------- #

# file extension -> human label, used only for messages/validation
EXPORT_FORMATS = ("anim", "fbx", "glb", "gltf", "bvh")


def _quat_wxyz(xyzw):
    return Quaternion((xyzw[3], xyzw[0], xyzw[1], xyzw[2]))


def _bake_prefab_action(ob, order, tbl, local_by_name, root_delta, times, fps):
    """Keyframe ``ob`` (the rebuilt SIFAS rig) from the per-frame Unity-local
    quaternions in ``local_by_name`` (bone name -> [(x,y,z,w), ...]).  Only the
    animated bones are keyed; the rest stay at rest, which is what the motion
    assumes.  ``root_delta`` (or None) is a per-frame prefab-space Vector added
    to the object location so root/stage motion travels too.
    """
    B3 = _B().to_3x3()
    pr = {n: (ob.matrix_world @ ob.data.bones[n].matrix_local).to_3x3() for n in order}
    rest_local = {n: _quat_wxyz(tbl[n][1]) for n in order}
    for pb in ob.pose.bones:
        pb.rotation_mode = 'QUATERNION'
    anim_bones = [n for n in order if n in local_by_name]
    scene = bpy.context.scene
    last_kf = 0
    for fi in range(len(times)):
        kf = int(round(times[fi] * fps))
        last_kf = kf
        Wu = {}

        def uw(n):
            if n in Wu:
                return Wu[n]
            p = tbl[n][0]
            seq = local_by_name.get(n)
            L = (_quat_wxyz(seq[fi]) if seq else rest_local[n]).to_matrix()
            Wu[n] = L if (p is None or p not in tbl) else uw(p) @ L
            return Wu[n]

        Wp = {n: B3 @ uw(n) for n in order}
        for n in anim_bones:
            par = ob.data.bones[n].parent
            pn = par.name if par else None
            Rpar = Wp[pn] if pn in Wp else Matrix.Identity(3)
            restp = pr[pn] if pn in pr else Matrix.Identity(3)
            rl = restp.inverted() @ pr[n]
            pb = ob.pose.bones[n]
            pb.rotation_quaternion = (rl.inverted() @ (Rpar.inverted() @ Wp[n])).to_quaternion()
            pb.keyframe_insert("rotation_quaternion", frame=kf)
        if root_delta is not None:
            ob.location = root_delta[fi]
            ob.keyframe_insert("location", frame=kf)
    scene.frame_start = 0
    scene.frame_end = max(1, last_kf)
    scene.render.fps = int(round(fps))
    scene.render.fps_base = 1.0


def _export_formats(ob, out_base, formats, verbose=True):
    """Export the baked armature ``ob`` to each DCC format requested."""
    scene = bpy.context.scene
    for o in bpy.data.objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    os.makedirs(os.path.dirname(os.path.abspath(out_base)) or ".", exist_ok=True)
    written = []
    for fmt in formats:
        if fmt == "anim":
            continue
        path = "%s.%s" % (out_base, fmt)
        try:
            if fmt == "fbx":
                bpy.ops.export_scene.fbx(
                    filepath=path, use_selection=True, object_types={'ARMATURE'},
                    add_leaf_bones=False, bake_anim=True,
                    bake_anim_use_all_bones=True, bake_anim_use_nla_strips=False,
                    bake_anim_use_all_actions=False, bake_anim_simplify_factor=0.0,
                    axis_forward='-Z', axis_up='Y')
            elif fmt in ("glb", "gltf"):
                bpy.ops.export_scene.gltf(
                    filepath=path,
                    export_format='GLB' if fmt == "glb" else 'GLTF_SEPARATE',
                    use_selection=True, export_animations=True,
                    export_frame_range=True, export_yup=True)
            elif fmt == "bvh":
                bpy.ops.export_anim.bvh(
                    filepath=path, frame_start=scene.frame_start,
                    frame_end=scene.frame_end, root_transform_only=False)
            else:
                print("[warn] unknown export format: %s" % fmt)
                continue
            written.append(path)
            if verbose:
                print("[ok] %s" % path)
        except Exception as e:  # one bad exporter shouldn't sink the rest
            print("[fail] export %s: %s" % (fmt, e))
    return written



# Bones used to calibrate the global frame G.
_G_BONES = ["Hips", "Head", "RightUpLeg", "LeftUpLeg", "RightFoot", "RightToeBase"]


def _snap_signed_permutation(M):
    """Snap a near-orthogonal 3x3 to the closest signed permutation matrix."""
    cells = sorted(((abs(M[i][j]), i, j) for i in range(3) for j in range(3)),
                   reverse=True)
    out = [[0.0] * 3 for _ in range(3)]
    rows, cols = set(), set()
    for _v, i, j in cells:
        if i in rows or j in cols:
            continue
        out[i][j] = 1.0 if M[i][j] >= 0 else -1.0
        rows.add(i); cols.add(j)
    return Matrix(out)


def _body_frame(o):
    """Orthonormal body frame [right | up | forward] (columns) from landmarks.

    ``up`` = Hips->Head, ``right`` = Left->Right hip (named, so it carries the
    rig's actual left/right -- including any mirror), ``forward`` = the toe
    direction (resolves the front/back ambiguity a roughly planar standing
    skeleton has).  Aligning these two frames pins facing AND handedness, which
    position-only Kabsch cannot: it can pick a 180 deg yaw that walks the
    character backwards.
    """
    def hd(n):
        return (o.matrix_world @ o.data.bones[n].matrix_local).translation
    up = (hd("Head") - hd("Hips")).normalized()
    rt = hd("RightUpLeg") - hd("LeftUpLeg")
    rt = (rt - up * rt.dot(up)).normalized()
    toe = hd("RightToeBase") - hd("RightFoot")
    toe = toe - up * toe.dot(up)
    fwd = toe.normalized() if toe.length > 1e-9 else up.cross(rt).normalized()
    rt = (rt - fwd * rt.dot(fwd)).normalized()
    return Matrix((rt, up, fwd)).transposed()


def _compute_G(src, ob, bones=_G_BONES):
    """
    Global frame alignment between the Blender-imported SIFAC space and the
    prefab-built SIFAS (B) space, as a signed-permutation rotation, from the
    two rigs' body frames (up / named-right / toe-forward).  Snapping cleans it
    to an axis convention.  Falls back to nothing fancy -- the landmark bones
    are all core body bones present in both rigs.
    """
    G = _body_frame(ob) @ _body_frame(src).inverted()
    return _snap_signed_permutation([list(r) for r in G])


def retarget_file(sifac_fbx, out_anim, member, clip_name=None,
                  prefab=None, skeleton_json=None,
                  frame_start=None, frame_end=None, step=1,
                  root_motion=True, twist_bones=True, twist_strength=1.0,
                  smooth=0, formats=("anim",), verbose=True):
    if not HAVE_BPY:
        raise RuntimeError("Blender's bpy module is required (pip install bpy).")
    formats = tuple(formats) if formats else ("anim",)
    bad = [f for f in formats if f not in EXPORT_FORMATS]
    if bad:
        raise ValueError("unknown export format(s): %s (known: %s)"
                         % (", ".join(bad), ", ".join(EXPORT_FORMATS)))

    tbl, relpath, core, member_example = load_skeleton(prefab, skeleton_json)
    # Real SIFAS game clips bind transforms by paths RELATIVE TO THE MEMBER
    # object (the one that carries the Animator): "Reference/Move/.../Hips".
    # So no member prefix by default -- that was why the clip drove nothing.
    # `--member NAME` is only for setups whose Animator sits ABOVE the member.
    member = member or ""

    def full_path(n):
        rp = relpath.get(n, n) or n
        return "%s/%s" % (member, rp) if member else rp

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(sifac_fbx))
    src = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    if src is None:
        raise RuntimeError("No armature found in %s" % sifac_fbx)
    act = src.animation_data.action if src.animation_data else None
    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base

    if act is not None:
        f0, f1 = act.frame_range
    else:
        f0, f1 = scene.frame_start, scene.frame_end
    f0 = int(math.ceil(f0)) if frame_start is None else int(frame_start)
    f1 = int(math.floor(f1)) if frame_end is None else int(frame_end)

    ob, order = build_sifas_armature(tbl)
    shared = [n for n in core if n in ob.pose.bones and n in src.pose.bones]

    worst, wn = self_test(ob, tbl)
    if verbose:
        print("[selftest] rest round-trip max error %.4f deg (worst %s)" % (worst, wn))
    if worst > 1.0:
        print("[warn] rest round-trip error is high (%.3f deg) -- check the skeleton." % worst)

    # --- retarget by ABSOLUTE world orientation (aim where SIFAC aims) ---
    # The two rigs rest their ARMS in different poses (T- vs A-like): ~45 deg
    # apart, while legs rest the same.  Transferring rotation-from-rest then
    # offsets the arms by that 45 deg.  So instead we make each SIFAS bone point
    # exactly where the (G-mapped) SIFAC bone points -- W = G . W_sifac_pose .
    # Cconv -- where Cconv is a pure-twist convention map per bone.  Cconv is
    # found at rest AFTER aligning the two rest bone directions, so it carries
    # only the local-axis (roll) difference, not the rest-pose difference.
    G = _compute_G(src, ob, _G_BONES)
    G3 = G.to_3x3(); Gi3 = G3.inverted()
    Binv3 = _B().to_3x3().inverted()
    if verbose:
        print("[align] global frame G = %s" % [[int(x) for x in r] for r in G3])

    def src_rest(n):
        return (src.matrix_world @ src.data.bones[n].matrix_local).to_3x3()

    def pf_rest(n):
        return (ob.matrix_world @ ob.data.bones[n].matrix_local).to_3x3()

    src_rest_c = {n: src_rest(n) for n in shared}
    pf_rest_c = {n: pf_rest(n) for n in order}

    # Cconv aligns the two rests by their PHYSICAL bone direction -- the vector
    # from a bone's head to its (longest) child's head.  We must NOT use the
    # bone's local +Y for this: the prefab bones carry the Unity local axes,
    # whose +Y runs sideways on the legs, so aligning +Y corrupts them.  The
    # child direction is physical and corresponds between the two rigs.
    def _head(o, n):
        return (o.matrix_world @ o.data.bones[n].matrix_local).translation

    primary_child = {}
    for n in shared:
        kids = [c for c in shared if tbl[c][0] == n]
        primary_child[n] = (max(kids, key=lambda c: (_head(ob, c) - _head(ob, n)).length)
                            if kids else None)

    cconv = {}
    for n in shared:
        c = primary_child[n]
        r_align = Matrix.Identity(3)
        if c is not None:
            us = _head(src, c) - _head(src, n)
            up = _head(ob, c) - _head(ob, n)
            if us.length > 1e-9 and up.length > 1e-9:
                r_align = (G3 @ us.normalized()).rotation_difference(up.normalized()).to_matrix()
        cconv[n] = (r_align @ G3 @ src_rest_c[n]).inverted() @ pf_rest_c[n]

    rot_bones = [n for n in shared if tbl[n][0] is not None]
    rot_seq = {n: [] for n in rot_bones}
    times = []

    # Root (stage) motion is carried by Hips_Position in real SIFAS clips, not
    # by Hips.  We transfer it from the SIFAC pelvis trajectory: per frame, the
    # SIFAC Hips world delta from frame 0, moved into the prefab frame (G) then
    # Unity space, dropped into Hips_Position's local frame (height-scaled).
    hp = "Hips_Position"
    do_root = (root_motion and hp in tbl and "Hips" in src.pose.bones)
    if do_root:
        hp_parent = tbl[hp][0]
        hp_rest = Vector(tbl[hp][2])
        ex0 = _Extractor(ob, tbl)
        hp_par_rot = ex0.world(hp_parent).to_3x3() if hp_parent in ob.pose.bones else Matrix.Identity(3)
        # Scale the SIFAC hips travel into SIFAS proportions by the ratio of the
        # two rigs' actual SIZE (Hips->Head length), which is axis-independent.
        # The old proxy -- the hips' rest Z coordinate -- breaks when the
        # imported SIFAC rig stands along a different axis: its Hips-Z is then
        # ~0 (or negative), so sifas_z/sifac_z blows up (e.g. -52) and any clip
        # that actually travels flings the character hundreds of units across
        # the stage (and flips its direction).  Hips->Head length is ~the same
        # on both rigs (so rscale ~= 1) and stays correct whatever the import
        # scale or orientation.
        def _rig_size(o):
            if "Head" not in o.data.bones or "Hips" not in o.data.bones:
                return 0.0
            head = (o.matrix_world @ o.data.bones["Head"].matrix_local).translation
            hips = (o.matrix_world @ o.data.bones["Hips"].matrix_local).translation
            return (head - hips).length
        sifac_size = _rig_size(src)
        sifas_size = _rig_size(ob)
        rscale = (sifas_size / sifac_size) if sifac_size > 1e-6 else 1.0
        baseline = None
        hp_pos = []
        root_delta = []  # per-frame prefab-space Vector, for DCC bakes

    frames = list(range(f0, f1 + 1, max(1, step)))
    for f in frames:
        scene.frame_set(f)
        bpy.context.view_layer.update()
        times.append((f - f0) / fps)
        # prefab-frame world rotation per bone.  Absolute aiming for the arm
        # chain (Arm/ForeArm/Hand/fingers) -- those rest ~45 deg apart and must
        # land where SIFAC points.  Everything else (shoulders, chest, spine,
        # neck, head, legs) uses the relative world-delta and keeps its natural
        # SIFAS rest: absolute there would bake in SIFAC's T-pose baseline and
        # hunch the shoulders / over-move the chest.
        Wp = {}
        for n in order:
            if n in src_rest_c:
                src_p = (src.matrix_world @ src.pose.bones[n].matrix).to_3x3()
                if ("Arm" in n) or ("Hand" in n):
                    Wp[n] = (G3 @ src_p) @ cconv[n]                 # absolute
                else:
                    dW = src_p @ src_rest_c[n].inverted()
                    Wp[n] = (G3 @ dW @ Gi3) @ pf_rest_c[n]          # relative
            else:
                # Non-shared bone (e.g. SIFAS-only Spine2): it has no SIFAC
                # source, so it stays at its REST relative to its parent -- but
                # it must still FOLLOW that parent through the hierarchy.  If we
                # froze it at the static rest world instead, a spinning Spine1
                # would leave Spine2 behind, and its children (Neck, shoulders)
                # would wind a full counter-turn on every body rotation.
                p = tbl[n][0]
                if p is not None and p in Wp:
                    Wp[n] = Wp[p] @ (pf_rest_c[p].inverted() @ pf_rest_c[n])
                else:
                    Wp[n] = pf_rest_c[n]
        # Unity world then local-to-parent rotation
        Wu = {n: Binv3 @ Wp[n] for n in order}
        for n in rot_bones:
            p = tbl[n][0]
            L = (Wu[p].inverted() @ Wu[n]) if p in Wu else Wu[n]
            q = L.to_quaternion()
            rot_seq[n].append((q.x, q.y, q.z, q.w))
        if do_root:
            w = src.matrix_world @ src.pose.bones["Hips"].head
            if baseline is None:
                baseline = w.copy()
            d_prefab = G3 @ ((w - baseline) * rscale)   # prefab (Blender) space
            d_unity = Binv3 @ d_prefab
            local = hp_rest + (hp_par_rot.inverted() @ d_unity)
            hp_pos.append((local.x, local.y, local.z))
            root_delta.append(d_prefab)

    for n in rot_bones:
        _ensure_sign_continuity(rot_seq[n])

    if smooth and smooth >= 3:
        for n in rot_bones:
            rot_seq[n] = _smooth_quat_seq(rot_seq[n], smooth)
        if verbose:
            print("[smooth] low-pass window %d frames applied to %d curves"
                  % (smooth, len(rot_bones)))

    # name-keyed locals (for the .anim *and* the DCC bake)
    local_by_name = {n: rot_seq[n] for n in rot_bones}
    rot_curves = [(full_path(n), rot_seq[n]) for n in rot_bones]
    if twist_bones and twist_strength != 0.0:
        roll_curves = _twist_curves(rot_seq, full_path, tbl, twist_strength)
        rot_curves.extend(roll_curves)
        for p, seq in roll_curves:
            local_by_name[p.split("/")[-1]] = seq
        if verbose and roll_curves:
            print("[twist] added %d roll-bone curves x%.2f (%s)"
                  % (len(roll_curves), twist_strength,
                     ", ".join(p.split("/")[-1] for p, _ in roll_curves)))
    pos_curves = []
    if do_root:
        pos_curves.append((full_path(hp), hp_pos))

    clip_name = clip_name or Path(sifac_fbx).stem
    out_base = os.path.splitext(str(out_anim))[0]
    written = []
    if "anim" in formats:
        anim_path = out_anim if str(out_anim).lower().endswith(".anim") else out_base + ".anim"
        write_anim(anim_path, clip_name, times, rot_curves, pos_curves, fps=60)
        written.append(anim_path)
        if verbose:
            print("[ok] %s  (%d frames, %d rotation curves, %d position curves)"
                  % (anim_path, len(frames), len(rot_curves), len(pos_curves)))

    # Other formats: bake the motion onto the rebuilt SIFAS rig, export via Blender.
    dcc = [f for f in formats if f != "anim"]
    if dcc:
        _bake_prefab_action(ob, order, tbl, local_by_name,
                            root_delta if do_root else None, times, fps)
        written += _export_formats(ob, out_base, dcc, verbose=verbose)

    return written if len(written) != 1 else written[0]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _argv():
    # support `blender -b --python this.py -- <args>`
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return sys.argv[1:]


def main():
    ap = argparse.ArgumentParser(description="Unity-free SIFAC -> SIFAS .anim retarget")
    ap.add_argument("--sifac", help="source SIFAC animation FBX")
    ap.add_argument("--out", help="output .anim path")
    ap.add_argument("--batch", help="folder of SIFAC FBX files to convert")
    ap.add_argument("--outdir", help="output folder for --batch")
    ap.add_argument("--member", default=None,
                    help="optional path prefix. Real SIFAS clips bind relative "
                         "to the member object (Animator on the member), so the "
                         "default is NO prefix. Only set this (e.g. "
                         "ch0004_co0019_member) if your Animator sits above it.")
    ap.add_argument("--name", default=None, help="clip name (default: file stem)")
    ap.add_argument("--prefab", default=None,
                    help="optional SIFAS member .prefab to use instead of bundled skeleton")
    ap.add_argument("--skeleton", default=None, help="override bundled skeleton JSON")
    ap.add_argument("--start", type=int, default=None, help="first frame (source)")
    ap.add_argument("--end", type=int, default=None, help="last frame (source)")
    ap.add_argument("--step", type=int, default=1, help="frame step (decimate)")
    ap.add_argument("--no-root-motion", action="store_true", help="skip Hips translation")
    ap.add_argument("--no-twist-bones", action="store_true",
                    help="skip driving the roll/twist bones (ArmRoll/ForeArmRoll); "
                         "leaving them at rest can pinch the shoulder/elbow skin")
    ap.add_argument("--twist-strength", type=float, default=1.0, metavar="X",
                    help="how strongly to drive the roll bones: 1.0 = the game's "
                         "own half-twist amount (default), 0 = none, >1 = stronger.")
    ap.add_argument("--smooth", type=int, default=0, metavar="FRAMES",
                    help="optional low-pass window (frames) to soften fast motion "
                         "(mostly the arms) toward a Humanoid-like feel; 0 = off "
                         "(faithful). Try 3 or 5 for a light touch.")
    ap.add_argument("--format", default="anim", metavar="LIST",
                    help="comma-separated output formats: %s (default: anim). "
                         "e.g. --format anim,fbx,glb" % ",".join(EXPORT_FORMATS))
    args = ap.parse_args(_argv())
    formats = [f.strip().lower() for f in args.format.split(",") if f.strip()]

    if args.batch:
        outdir = Path(args.outdir or "anim_out")
        outdir.mkdir(parents=True, exist_ok=True)
        files = sorted([p for p in Path(args.batch).iterdir()
                        if p.suffix.lower() == ".fbx"])
        if not files:
            print("no .fbx files in", args.batch)
            return 1
        for fb in files:
            out = outdir / (fb.stem + ".anim")
            try:
                retarget_file(fb, out, args.member, clip_name=fb.stem,
                              prefab=args.prefab, skeleton_json=args.skeleton,
                              frame_start=args.start, frame_end=args.end,
                              step=args.step, root_motion=not args.no_root_motion,
                              twist_bones=not args.no_twist_bones,
                              twist_strength=args.twist_strength,
                              smooth=args.smooth, formats=formats)
            except Exception as e:  # keep going through the batch
                print("[fail] %s: %s" % (fb.name, e))
        return 0

    if not args.sifac or not args.out:
        ap.error("--sifac and --out are required (or use --batch/--outdir)")
    retarget_file(args.sifac, args.out, args.member, clip_name=args.name,
                  prefab=args.prefab, skeleton_json=args.skeleton,
                  frame_start=args.start, frame_end=args.end,
                  step=args.step, root_motion=not args.no_root_motion,
                  twist_bones=not args.no_twist_bones,
                  twist_strength=args.twist_strength,
                  smooth=args.smooth, formats=formats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
