#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_fbx_bpy — build the rig in Blender and export FBX (the reliable engine)
=============================================================================

The hand-written binary FBX (``sifac_fbx.py``) carries correct data, but
Blender's importer reorients armature bones into its head/tail/roll model and
ends up mis-deforming the mesh when posed (rest looks fine, animation explodes).
Round-tripping through Blender's *own* exporter sidesteps that entirely: we
build the armature, skinned meshes, blendshapes, materials and animation with
``bpy`` and let Blender write the FBX.  The result imports cleanly into
Blender, Unity and Maya.

This needs Blender's Python module::

    pip install bpy            # matches your Python version

Used by ``sifac_convert.py`` automatically when ``bpy`` is importable (engine
``auto``/``blender``).  It also works standalone, which is how the converter
drives it — one isolated subprocess per file::

    python3 sifac_fbx_bpy.py model.bmarc out.fbx --motion mot_a.bmarc --motion mot_b.bmarc
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import bpy
    import mathutils
    HAVE_BPY = True
except Exception:
    HAVE_BPY = False

import sifac_bmarc
from sifac_mathutil import Mat4, Vec3


def available() -> bool:
    return HAVE_BPY


# --------------------------------------------------------------------------- #
# Matrix conversion: our row-vector Mat4 -> mathutils.Matrix (column-vector)
# --------------------------------------------------------------------------- #

def _M(m: Mat4):
    r = m.m
    return mathutils.Matrix((
        (r[0][0], r[1][0], r[2][0], r[3][0]),
        (r[0][1], r[1][1], r[2][1], r[3][1]),
        (r[0][2], r[1][2], r[2][2], r[3][2]),
        (r[0][3], r[1][3], r[2][3], r[3][3])))


def _scale_matrix(s: float):
    return mathutils.Matrix.Scale(s, 4)


# --------------------------------------------------------------------------- #
# Animation sampling (reuses the parsed tracks)
# --------------------------------------------------------------------------- #

def _sample(keys, frame):
    if not keys:
        return None
    if frame <= keys[0][0]:
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]
    lo, hi = 0, len(keys) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if keys[mid][0] <= frame:
            lo = mid
        else:
            hi = mid
    f0, v0 = keys[lo]
    f1, v1 = keys[hi]
    t = (frame - f0) / (f1 - f0) if f1 > f0 else 0.0
    return _interp(v0, v1, t)


def _interp(a, b, t):
    if isinstance(a, Vec3):
        return Vec3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t,
                    a.z + (b.z - a.z) * t)
    # quaternion (nlerp, shortest path)
    d = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w
    from sifac_mathutil import Quat
    bb = b if d >= 0 else Quat(-b.x, -b.y, -b.z, -b.w)
    q = Quat(a.x + (bb.x - a.x) * t, a.y + (bb.y - a.y) * t,
             a.z + (bb.z - a.z) * t, a.w + (bb.w - a.w) * t)
    return q.normalized()


def _local_at(bone, track, frame):
    if track is None:
        return bone.local_matrix
    _, _, srest = bone.local_matrix.decompose()
    trest = Vec3(bone.local_matrix.m[3][0], bone.local_matrix.m[3][1],
                 bone.local_matrix.m[3][2])
    tt = _sample(track.translation, frame) or trest
    q = _sample(track.rotation, frame) or bone.local_matrix.to_quat()
    ss = _sample(track.scale, frame) or srest
    rot = q.to_mat3()
    m = Mat4()
    sc = [ss.x, ss.y, ss.z]
    for r in range(3):
        m.m[r] = [rot[r][0] * sc[r], rot[r][1] * sc[r], rot[r][2] * sc[r], 0.0]
    m.m[3] = [tt.x, tt.y, tt.z, 1.0]
    return m


# --------------------------------------------------------------------------- #
# Scene building
# --------------------------------------------------------------------------- #

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def build_armature(model):
    ad = bpy.data.armatures.new(model.name)
    arm = bpy.data.objects.new(model.name, ad)
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    ebs = []
    for b in model.bones:
        eb = ad.edit_bones.new(b.name)
        ebs.append(eb)
    for b in model.bones:
        if b.parent_index >= 0:
            ebs[b.index].parent = ebs[b.parent_index]
    rest = []
    for b in model.bones:
        eb = ebs[b.index]
        eb.tail = (0.0, 0.05, 0.0)          # placeholder length; matrix sets orient
        eb.matrix = _M(b.world_matrix)
        rest.append(_M(b.world_matrix))
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm, rest


def build_meshes(model, arm, texture_dir, out_dir, include_morphs):
    blobs = None
    materials = {}
    for sub in model.submeshes:
        if not sub.triangles or not sub.positions:
            continue
        me = bpy.data.meshes.new(sub.name)
        verts = [(p.x, p.y, p.z) for p in sub.positions]
        faces = [(t[0], t[1], t[2]) for t in sub.triangles]
        me.from_pydata(verts, [], faces)
        me.update()
        me.validate(verbose=False)
        # Smooth shading. NOTE: we deliberately do NOT call
        # normals_split_custom_set_from_vertices — it segfaults inside Blender
        # on some builds (notably Apple Silicon). Blender recomputes good
        # vertex normals from the smooth-shaded geometry instead.
        if len(me.polygons):
            me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
            me.update()
        # UVs
        if sub.uvs:
            uvl = me.uv_layers.new(name="map1")
            for poly in me.polygons:
                for li in poly.loop_indices:
                    vi = me.loops[li].vertex_index
                    u, v = sub.uvs[vi]
                    uvl.data[li].uv = (u, 1.0 - v)
        # vertex colors
        if sub.colors:
            try:
                col = me.color_attributes.new("color", 'BYTE_COLOR', 'CORNER')
                for li, loop in enumerate(me.loops):
                    r, g, b, a = sub.colors[loop.vertex_index]
                    col.data[li].color = (r / 255, g / 255, b / 255, a / 255)
            except Exception:
                pass
        obj = bpy.data.objects.new(sub.name, me)
        bpy.context.collection.objects.link(obj)

        # vertex groups / weights
        vgs = {}
        for vi, inf in enumerate(sub.skin):
            for gb, w in inf:
                if not (0 <= gb < len(model.bones)):
                    continue
                nm = model.bones[gb].name
                vg = vgs.get(nm) or obj.vertex_groups.new(name=nm)
                vgs[nm] = vg
                vg.add([vi], float(w), 'REPLACE')
        mod = obj.modifiers.new("Armature", 'ARMATURE')
        mod.object = arm
        obj.parent = arm

        # material
        mat = next((m for m in model.materials if m.name == sub.material_name),
                   None)
        if mat is not None:
            blobs = _texture_blobs(texture_dir) if blobs is None else blobs
            bmat = materials.get(mat.name) or _make_material(
                mat, blobs, out_dir)
            materials[mat.name] = bmat
            me.materials.append(bmat)

        # blendshapes (morphs)
        if include_morphs and sub.morphs:
            _add_shape_keys(obj, sub)
    return arm


def _add_shape_keys(obj, sub):
    obj.shape_key_add(name="Basis", from_mix=False)
    base = obj.data.shape_keys.key_blocks["Basis"]
    for target in sub.morphs:
        sk = obj.shape_key_add(name=target.name, from_mix=False)
        for i, d in enumerate(target.position_deltas):
            if i < len(sk.data) and (abs(d.x) + abs(d.y) + abs(d.z)) > 1e-6:
                bp = base.data[i].co
                sk.data[i].co = (bp[0] + d.x, bp[1] + d.y, bp[2] + d.z)


def build_animation(arm, model, animation, fps):
    rest = [_M(b.world_matrix) for b in model.bones]
    tracks = {t.bone_name: t for t in animation.tracks}
    animated = [b for b in model.bones if b.name in tracks]
    if not animated:
        return
    # Union of all key frames keeps every bone's pose exact at sampled frames.
    frameset = set()
    for t in animation.tracks:
        for keys in (t.translation, t.rotation, t.scale):
            for f, _ in keys:
                frameset.add(f)
    frames = sorted(frameset) or [0]

    sc = bpy.context.scene
    sc.render.fps = int(round(fps)) or 60
    sc.frame_start = 1
    sc.frame_end = animation.end_frame + 1

    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    for pb in arm.pose.bones:
        pb.rotation_mode = 'QUATERNION'
    bpy.ops.object.mode_set(mode="OBJECT")

    # Sample each animated bone's local pose (matrix_basis) at every key frame.
    # We deliberately do NOT use pb.keyframe_insert in the loop: it tags the
    # depsgraph on every call and turns into ~bones*frames*3 evaluations
    # (seconds → minutes, CPU pegged at 100%). Instead we collect the samples
    # here and write the F-curves in one bulk pass below.
    ident = mathutils.Matrix.Identity(4)
    samples = {b.index: ([], [], []) for b in animated}  # loc, quat, scale
    for fr in frames:
        # World pose for every bone (parent before child, file order works).
        P = {}
        for b in model.bones:
            loc = _local_at(b, tracks.get(b.name), fr)
            P[b.index] = (_M(loc) if b.parent_index < 0
                          else P[b.parent_index] @ _M(loc))
        for b in animated:
            pr = rest[b.parent_index] if b.parent_index >= 0 else ident
            pp = P[b.parent_index] if b.parent_index >= 0 else ident
            basis = rest[b.index].inverted() @ pr @ pp.inverted() @ P[b.index]
            l, q, s = basis.decompose()
            L, Q, S = samples[b.index]
            L.append(l)
            Q.append(q)
            S.append(s)

    fcurves = _action_fcurves(arm, animation.name)
    fnums = [float(fr + 1) for fr in frames]
    n = len(fnums)

    def _curve(data_path, index, values):
        fc = fcurves.new(data_path=data_path, index=index)
        kp = fc.keyframe_points
        kp.add(n)
        co = [0.0] * (2 * n)
        for i in range(n):
            co[2 * i] = fnums[i]
            co[2 * i + 1] = values[i]
        kp.foreach_set("co", co)
        kp.foreach_set("interpolation", [1] * n)  # 1 == LINEAR
        fc.update()

    for b in animated:
        L, Q, S = samples[b.index]
        # Keep quaternions on one hemisphere so linear interpolation between
        # keys doesn't take the long way round (no spurious flips).
        for i in range(1, len(Q)):
            if Q[i].dot(Q[i - 1]) < 0.0:
                Q[i].negate()
        dp = 'pose.bones["%s"]' % b.name
        for idx in range(3):
            _curve(dp + ".location", idx, [v[idx] for v in L])
        for idx in range(4):
            _curve(dp + ".rotation_quaternion", idx, [v[idx] for v in Q])
        for idx in range(3):
            _curve(dp + ".scale", idx, [v[idx] for v in S])


def _action_fcurves(arm, name):
    """Create an action on ``arm`` and return an F-curve collection whose
    ``.new(data_path=, index=)`` works across both the legacy action API
    (Blender <= 4.3) and the layered/slotted API (Blender 4.4+/5.x)."""
    if arm.animation_data is None:
        arm.animation_data_create()
    act = bpy.data.actions.new(name=name or "Take")
    arm.animation_data.action = act
    if hasattr(act, "fcurves"):  # legacy
        return act.fcurves
    # Layered actions: action -> slot -> layer -> strip -> channelbag -> fcurves
    slot = act.slots.new(id_type='OBJECT', name="Anim")
    try:
        arm.animation_data.action_slot = slot
    except Exception:
        pass
    layer = act.layers.new("Layer")
    strip = layer.strips.new(type='KEYFRAME')
    return strip.channelbag(slot, ensure=True).fcurves


# --------------------------------------------------------------------------- #
# Textures / materials
# --------------------------------------------------------------------------- #

def _texture_blobs(texture_dir):
    import sifac_native
    blobs = {}
    if not texture_dir:
        return blobs
    td = Path(texture_dir)
    for d in (td, td.parent):
        pac = d / "texture.pac"
        if pac.is_file():
            try:
                data = pac.read_bytes()
                if sifac_native.is_arc_bytes(data):
                    for e in sifac_native.parse_arc(data):
                        base = os.path.splitext(os.path.basename(e.name))[0]
                        blob = data[e.offset:e.offset + e.size]
                        if blob[:3] == b"btx":
                            blobs.setdefault(base, blob)
            except Exception:
                pass
    if td.is_dir():
        for f in td.glob("*.btx"):
            blobs.setdefault(f.stem, f.read_bytes())
    return blobs


def _decode_png(name, blobs, out_dir):
    """Decode a referenced texture to a PNG next to the FBX; return its path."""
    dest = Path(out_dir) / f"{name}.png"
    if dest.exists():
        return str(dest)
    blob = blobs.get(name)
    if blob is None:
        return None
    try:
        import sifac_btx
        sifac_btx.save_texture(sifac_btx.decode_btx(blob), dest)
        return str(dest)
    except Exception:
        return None


def _make_material(mat, blobs, out_dir):
    bmat = bpy.data.materials.new(mat.name)
    bmat.use_nodes = True
    nt = bmat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    def hook(tex_name, slot, noncolor=False):
        if not tex_name or bsdf is None:
            return
        png = _decode_png(tex_name, blobs, out_dir)
        if not png:
            return
        try:
            img = bpy.data.images.load(png, check_existing=True)
        except Exception:
            return
        tn = nt.nodes.new('ShaderNodeTexImage')
        tn.image = img
        if noncolor:
            try: img.colorspace_settings.name = 'Non-Color'
            except Exception: pass
        if slot == "base":
            nt.links.new(tn.outputs['Color'], bsdf.inputs['Base Color'])
            nt.links.new(tn.outputs['Alpha'], bsdf.inputs['Alpha'])
        elif slot == "normal":
            nm = nt.nodes.new('ShaderNodeNormalMap')
            nt.links.new(tn.outputs['Color'], nm.inputs['Color'])
            nt.links.new(nm.outputs['Normal'], bsdf.inputs['Normal'])
    hook(mat.diffuse_tex, "base")
    hook(mat.normal_tex, "normal", noncolor=True)
    return bmat


# --------------------------------------------------------------------------- #
# Top-level export
# --------------------------------------------------------------------------- #

def export_model(model, animations, out_path, texture_dir="", scale=1.0,
                 include_morphs=True, up_axis="y"):
    reset_scene()
    arm, _rest = build_armature(model)
    build_meshes(model, arm, texture_dir, Path(out_path).parent, include_morphs)
    if animations:
        # one combined take (first animation); multiple takes need NLA — keep
        # it simple and reliable: export the first/most-relevant motion.
        build_animation(arm, model, animations[0], animations[0].fps)
    _apply_scale(scale)
    # The SIFAC data is Y-up, so the rig is built lying along Y in Blender's
    # Z-up world.  Stand it upright (+90° X) so it exports cleanly and lands the
    # same way a SIFAS FBX does instead of on its side.  ``up_axis`` then only
    # picks the file's declared up axis: 'y' (SIFAS / DCC default) or 'z'.
    import math
    for o in bpy.data.objects:
        if o.parent is None and o.type in {"ARMATURE", "MESH", "EMPTY"}:
            o.rotation_euler = (o.rotation_euler[0] + math.radians(90),
                                o.rotation_euler[1], o.rotation_euler[2])
    bpy.context.view_layer.update()
    bpy.ops.export_scene.fbx(
        filepath=str(out_path), use_selection=False, add_leaf_bones=False,
        bake_anim=bool(animations), mesh_smooth_type='FACE',
        path_mode='COPY', embed_textures=False,
        axis_forward='-Z', axis_up=('Z' if up_axis == "z" else 'Y'))


def _apply_scale(scale):
    if abs(scale - 1.0) < 1e-9:
        return
    for o in bpy.data.objects:
        if o.parent is None:
            o.scale = (scale, scale, scale)


# --------------------------------------------------------------------------- #
# Standalone CLI (so the converter can run us in an isolated subprocess)
# --------------------------------------------------------------------------- #

def _main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="sifac_fbx_bpy")
    p.add_argument("model")
    p.add_argument("out")
    p.add_argument("--motion", action="append", default=[])
    p.add_argument("--texture-dir", default="")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--up-axis", choices=("y", "z"), default="y")
    p.add_argument("--no-morphs", action="store_true")
    p.add_argument("--anim-only", action="store_true")
    a = p.parse_args(argv)
    if not HAVE_BPY:
        sys.stderr.write("bpy is not available\n")
        return 3

    parsed = sifac_bmarc.parse_bmarc(Path(a.model).read_bytes(), Path(a.model).stem)
    model = parsed.model
    if model is None or not model.has_geometry():
        sys.stderr.write("no geometry in model\n")
        return 1
    anims = list(parsed.animations)
    for mp in a.motion:
        try:
            anims.extend(sifac_bmarc.parse_bmarc(
                Path(mp).read_bytes(), Path(mp).stem).animations)
        except Exception as exc:
            sys.stderr.write(f"motion {mp}: {exc}\n")
    if a.anim_only:
        model.submeshes = []
    export_model(model, anims, a.out, texture_dir=a.texture_dir,
                 scale=a.scale, include_morphs=not a.no_morphs,
                 up_axis=a.up_axis)
    return 0


if __name__ == "__main__":
    # Strip Blender's own args if launched via `blender -P`.
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    raise SystemExit(_main(argv))
