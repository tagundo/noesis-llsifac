#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_fbx — build a binary FBX scene from parsed SIFAC data
===========================================================

Turns the :mod:`sifac_scene` dataclasses into a complete FBX 7400 document:
a skeleton (LimbNodes), skinned/baked geometry with materials and texture
references, blendshape morphs, one AnimationStack per motion, and an optional
animated camera.

Skinning is set up so the mesh appears in bind pose and deforms with the
skeleton (see the cluster derivation in the code): control points are baked to
**bind-pose world space**, the mesh node sits at identity, and each cluster
uses ``TransformMatrix = identity`` with ``TransformLink = bone bind world``.
"""

from __future__ import annotations

import math
import os
from typing import Dict, List, Optional, Sequence

from sifac_fbx_encode import FBXNode, write_fbx
from sifac_mathutil import (Mat4, Quat, Vec3, euler_unwrap, euler_xyz_to_mat3,
                            mat3_row_to_quat, mat3_to_euler_xyz,
                            quat_to_euler_xyz)
from sifac_scene import Animation, CameraAnim, Model

# FBX time: 1 second == this many "ktime" ticks.
KTIME_SECOND = 46186158000
# Game UVs use a top-left origin; FBX UV space is bottom-left -> flip V.
FLIP_V = True


# --------------------------------------------------------------------------- #
# Property70 helpers
# --------------------------------------------------------------------------- #

def _p(name, ptype, sub, flag, *values) -> FBXNode:
    node = FBXNode("P")
    node.prop(name); node.prop(ptype); node.prop(sub); node.prop(flag)
    for v in values:
        if isinstance(v, float):
            node.prop_f64(v)
        elif isinstance(v, int):
            node.prop_i32(v)
        else:
            node.prop(v)
    return node


def _props70(*ps: FBXNode) -> FBXNode:
    n = FBXNode("Properties70")
    for p in ps:
        n.children.append(p)
    return n


def _ktime_prop(name: str, ktime: int) -> FBXNode:
    """A Properties70 entry whose value is a 64-bit KTime."""
    node = FBXNode("P")
    node.prop(name); node.prop("KTime"); node.prop("Time"); node.prop("")
    node.prop_i64(ktime)
    return node


# --------------------------------------------------------------------------- #
# Scene assembler
# --------------------------------------------------------------------------- #

class _FbxScene:
    def __init__(self, creator: str = "sifac_convert", up_axis: str = "y"):
        self.creator = creator
        self.up_axis = up_axis
        self._id = 1_000_000
        self.objects: List[FBXNode] = []
        self.connections: List[FBXNode] = []
        self.counts: Dict[str, int] = {}

    def new_id(self) -> int:
        self._id += 1
        return self._id

    def add(self, node: FBXNode, otype: str) -> None:
        self.objects.append(node)
        self.counts[otype] = self.counts.get(otype, 0) + 1

    def oo(self, child: int, parent: int) -> None:
        c = FBXNode("C"); c.prop("OO"); c.prop_i64(child); c.prop_i64(parent)
        self.connections.append(c)

    def op(self, child: int, parent: int, prop: str) -> None:
        c = FBXNode("C"); c.prop("OP"); c.prop_i64(child); c.prop_i64(parent)
        c.prop(prop)
        self.connections.append(c)

    # -- document roots ---------------------------------------------------- #

    def document(self) -> List[FBXNode]:
        roots: List[FBXNode] = []
        roots.append(self._header_extension())
        fid = FBXNode("FileId"); fid.prop_raw(b"sifac-converter-0001"[:16].ljust(16, b"\0"))
        roots.append(fid)
        ct = FBXNode("CreationTime"); ct.prop("1970-01-01 00:00:00:000")
        roots.append(ct)
        cr = FBXNode("Creator"); cr.prop(self.creator)
        roots.append(cr)
        roots.append(self._global_settings())
        roots.append(self._documents())
        roots.append(FBXNode("References"))
        roots.append(self._definitions())
        objs = FBXNode("Objects")
        objs.children = self.objects
        roots.append(objs)
        conns = FBXNode("Connections")
        conns.children = self.connections
        roots.append(conns)
        takes = FBXNode("Takes")
        takes.child("Current", "")
        roots.append(takes)
        return roots

    def _header_extension(self) -> FBXNode:
        h = FBXNode("FBXHeaderExtension")
        h.child("FBXHeaderVersion", 1003)
        h.child("FBXVersion", 7400)
        h.child("EncryptionType", 0)
        ts = h.child("CreationTimeStamp")
        ts.child("Version", 1000)
        for k, v in (("Year", 1970), ("Month", 1), ("Day", 1), ("Hour", 0),
                     ("Minute", 0), ("Second", 0), ("Millisecond", 0)):
            ts.child(k, v)
        h.child("Creator", self.creator)
        return h

    def _global_settings(self) -> FBXNode:
        g = FBXNode("GlobalSettings")
        g.child("Version", 1000)
        # Y-up (default, matches SIFAS / most DCC tools): up=Y, front=Z.
        # Z-up: up=Z, front=-Y (the data is rotated to match in _apply_up_axis).
        if self.up_axis == "z":
            up_ax, up_sgn, fr_ax, fr_sgn = 2, 1, 1, -1
        else:
            up_ax, up_sgn, fr_ax, fr_sgn = 1, 1, 2, 1
        g.children.append(_props70(
            _p("UpAxis", "int", "Integer", "", up_ax),
            _p("UpAxisSign", "int", "Integer", "", up_sgn),
            _p("FrontAxis", "int", "Integer", "", fr_ax),
            _p("FrontAxisSign", "int", "Integer", "", fr_sgn),
            _p("CoordAxis", "int", "Integer", "", 0),
            _p("CoordAxisSign", "int", "Integer", "", 1),
            _p("OriginalUpAxis", "int", "Integer", "", up_ax),
            _p("OriginalUpAxisSign", "int", "Integer", "", up_sgn),
            # Our coordinates are in metres.  FBX's base unit is the centimetre,
            # so "100 cm per unit" tells importers each unit is one metre.
            # Blender computes its import scale as UnitScaleFactor/100, so 100
            # here imports 1:1 (a ~1.5 m character) instead of shrinking ×100.
            _p("UnitScaleFactor", "double", "Number", "", 100.0),
            _p("OriginalUnitScaleFactor", "double", "Number", "", 100.0),
            _p("TimeMode", "enum", "", "", 14),
            _p("CustomFrameRate", "double", "Number", "", 60.0),
        ))
        return g

    def _documents(self) -> FBXNode:
        d = FBXNode("Documents")
        d.child("Count", 1)
        doc = FBXNode("Document")
        doc.prop_i64(1); doc.prop("Scene"); doc.prop("Scene")
        doc.children.append(_props70(
            _p("SourceObject", "object", "", ""),
            _p("ActiveAnimStackName", "KString", "", "", "")))
        doc.child("RootNode", 0)
        d.children.append(doc)
        return d

    def _definitions(self) -> FBXNode:
        d = FBXNode("Definitions")
        d.child("Version", 100)
        total = 1 + sum(self.counts.values())   # +1 GlobalSettings
        d.child("Count", total)
        gs = FBXNode("ObjectType"); gs.prop("GlobalSettings")
        gs.child("Count", 1)
        d.children.append(gs)
        for otype, count in self.counts.items():
            ot = FBXNode("ObjectType"); ot.prop(otype)
            ot.child("Count", count)
            d.children.append(ot)
        return d

    def write(self, path) -> None:
        write_fbx(path, self.document())


# --------------------------------------------------------------------------- #
# Object builders
# --------------------------------------------------------------------------- #

def _obj_name(name: str, cls: str) -> str:
    return f"{name}\x00\x01{cls}"


def _ktime(frame: float, fps: float) -> int:
    return int(round(frame * KTIME_SECOND / max(1e-6, fps)))


class _Builder:
    """Builds the FBX objects/connections for one model + its animations."""

    def __init__(self, scene: _FbxScene, model: Optional[Model],
                 texture_dir: str = "", scale: float = 1.0,
                 include_morphs: bool = True):
        self.s = scene
        self.model = model
        self.texture_dir = texture_dir
        self.scale = scale
        self.include_morphs = include_morphs
        self.bone_ids: List[int] = []
        self.bone_by_name: Dict[str, int] = {}
        self.bone_rest_euler: Dict[str, Vec3] = {}
        self.tex_ids: Dict[str, int] = {}
        self.mesh_model_ids: List[int] = []

    # -- skeleton ---------------------------------------------------------- #

    def build_skeleton(self) -> None:
        if not self.model or not self.model.bones:
            return
        s = self.s
        for bone in self.model.bones:
            mid = s.new_id()
            self.bone_ids.append(mid)
            self.bone_by_name[bone.name] = mid
            t, r, sc = bone.local_matrix.decompose()
            self.bone_rest_euler[bone.name] = r
            m = FBXNode("Model")
            m.prop_i64(mid); m.prop(_obj_name(bone.name, "Model")); m.prop("LimbNode")
            m.child("Version", 232)
            m.children.append(_props70(
                _p("RotationActive", "bool", "", "", 1),
                _p("InheritType", "enum", "", "", 1),
                _p("ScalingMax", "Vector3D", "Vector", "", 0.0, 0.0, 0.0),
                _p("DefaultAttributeIndex", "int", "Integer", "", 0),
                _p("Lcl Translation", "Lcl Translation", "", "A",
                   t.x * self.scale, t.y * self.scale, t.z * self.scale),
                _p("Lcl Rotation", "Lcl Rotation", "", "A", r.x, r.y, r.z),
                _p("Lcl Scaling", "Lcl Scaling", "", "A", sc.x, sc.y, sc.z),
            ))
            s.add(m, "Model")
            # Skeleton node attribute.
            aid = s.new_id()
            attr = FBXNode("NodeAttribute")
            attr.prop_i64(aid); attr.prop(_obj_name(bone.name, "NodeAttribute"))
            attr.prop("LimbNode")
            attr.children.append(_props70(
                _p("Size", "double", "Number", "", 1.0)))
            attr.child("TypeFlags", "Skeleton")
            s.add(attr, "NodeAttribute")
            s.oo(aid, mid)
        # Parent links.
        for bone in self.model.bones:
            mid = self.bone_ids[bone.index]
            if bone.parent_index >= 0:
                s.oo(mid, self.bone_ids[bone.parent_index])
            else:
                s.oo(mid, 0)

    def build_bind_pose(self) -> None:
        """Emit a ``BindPose`` recording every bone's (and skinned mesh's)
        global transform at bind time.

        Blender's FBX importer derives armature rest matrices from the BindPose
        when present; without it, it falls back to the skin-cluster TransformLink
        and (for hand-written files) tends to collapse all bones onto the origin.
        Writing the BindPose — exactly what Blender's own exporter does — makes
        the skeleton import in the correct rest pose, and Unity/Maya use it too.
        Bones list their bind world; meshes are identity (control points are
        already baked to bind-pose world)."""
        if not self.bone_ids or not self.model:
            return
        s = self.s
        pose_id = s.new_id()
        pose = FBXNode("Pose")
        pose.prop_i64(pose_id)
        pose.prop(_obj_name("sifac_bind", "Pose"))
        pose.prop("BindPose")
        pose.child("Type", "BindPose")
        pose.child("Version", 100)
        entries: List = []
        for bone in self.model.bones:
            world = bone.world_matrix or Mat4.identity()
            entries.append((self.bone_ids[bone.index],
                            _scaled(world, self.scale)))
        for mid in self.mesh_model_ids:
            entries.append((mid, Mat4.identity()))
        pose.child("NbPoseNodes", len(entries))
        for node_id, mat in entries:
            pn = FBXNode("PoseNode")
            pn.child("Node").prop_i64(node_id)
            pn.child("Matrix").prop_f64_array(mat.fbx_array())
            pose.children.append(pn)
        s.add(pose, "Pose")

    # -- textures / materials --------------------------------------------- #

    def _texture(self, base_name: str) -> int:
        if not base_name:
            return 0
        if base_name in self.tex_ids:
            return self.tex_ids[base_name]
        s = self.s
        rel = base_name + ".png"
        abspath = os.path.join(self.texture_dir, rel) if self.texture_dir else rel
        vid = s.new_id()
        video = FBXNode("Video")
        video.prop_i64(vid); video.prop(_obj_name(base_name, "Video")); video.prop("Clip")
        video.children.append(_props70(_p("Path", "KString", "XRefUrl", "", abspath)))
        video.child("UseMipMap", 0)
        video.child("Filename", abspath)
        video.child("RelativeFilename", rel)
        s.add(video, "Video")

        tid = s.new_id()
        tex = FBXNode("Texture")
        tex.prop_i64(tid); tex.prop(_obj_name(base_name, "Texture")); tex.prop("")
        tex.child("Type", "TextureVideoClip")
        tex.child("Version", 202)
        tex.child("TextureName", _obj_name(base_name, "Texture"))
        tex.children.append(_props70(
            _p("UVSet", "KString", "", "", "map1"),
            _p("UseMaterial", "bool", "", "", 1)))
        tex.child("Media", _obj_name(base_name, "Video"))
        tex.child("FileName", abspath)
        tex.child("RelativeFilename", rel)
        s.add(tex, "Texture")
        s.oo(vid, tid)
        self.tex_ids[base_name] = tid
        return tid

    def _material(self, mat) -> int:
        s = self.s
        mid = s.new_id()
        node = FBXNode("Material")
        node.prop_i64(mid); node.prop(_obj_name(mat.name, "Material")); node.prop("")
        node.child("Version", 102)
        node.child("ShadingModel", "phong")
        node.child("MultiLayer", 0)
        amb = mat.ambient
        node.children.append(_props70(
            _p("ShadingModel", "KString", "", "", "Phong"),
            _p("AmbientColor", "Color", "", "A", amb[0], amb[1], amb[2]),
            _p("DiffuseColor", "Color", "", "A", 1.0, 1.0, 1.0),
            _p("SpecularColor", "Color", "", "A", 0.2, 0.2, 0.2),
            _p("Shininess", "double", "Number", "", 20.0),
            _p("Opacity", "double", "Number", "", 1.0),
        ))
        s.add(node, "Material")
        # Connect textures to material slots.
        slot_map = [
            (mat.diffuse_tex, "DiffuseColor"),
            (mat.normal_tex, "NormalMap"),
            (mat.specular_tex, "SpecularColor"),
            (mat.light_tex, "AmbientColor"),
        ]
        for tex_name, slot in slot_map:
            if tex_name:
                tid = self._texture(tex_name)
                if tid:
                    s.op(tid, mid, slot)
        return mid

    # -- geometry ---------------------------------------------------------- #

    def build_meshes(self) -> None:
        if not self.model:
            return
        s = self.s
        mat_ids: Dict[str, int] = {}
        for sub in self.model.submeshes:
            if not sub.triangles or not sub.positions:
                continue
            geo_id = s.new_id()
            model_id = s.new_id()
            geo = self._geometry(sub, geo_id)
            s.add(geo, "Geometry")

            # Control points are baked to world; the mesh node stays at identity.
            mesh = FBXNode("Model")
            mesh.prop_i64(model_id); mesh.prop(_obj_name(sub.name, "Model"))
            mesh.prop("Mesh")
            mesh.child("Version", 232)
            mesh.children.append(_props70(
                _p("Lcl Translation", "Lcl Translation", "", "A", 0.0, 0.0, 0.0),
                _p("Lcl Rotation", "Lcl Rotation", "", "A", 0.0, 0.0, 0.0),
                _p("Lcl Scaling", "Lcl Scaling", "", "A", 1.0, 1.0, 1.0),
            ))
            s.add(mesh, "Model")
            self.mesh_model_ids.append(model_id)
            s.oo(geo_id, model_id)
            s.oo(model_id, 0)

            # Material.
            if sub.material_name:
                if sub.material_name not in mat_ids:
                    mat = next((m for m in self.model.materials
                                if m.name == sub.material_name), None)
                    if mat is not None:
                        mat_ids[sub.material_name] = self._material(mat)
                if sub.material_name in mat_ids:
                    s.oo(mat_ids[sub.material_name], model_id)

            # Skin.
            if self.bone_ids and any(sub.skin):
                self._skin(sub, geo_id)
            # Morphs.
            if self.include_morphs and sub.morphs:
                self._blendshapes(sub, geo_id)

    def _geometry(self, sub, geo_id: int) -> FBXNode:
        scale = self.scale
        verts: List[float] = []
        for p in sub.positions:
            verts += [p.x * scale, p.y * scale, p.z * scale]
        poly_idx: List[int] = []
        normals: List[float] = []
        uvs: List[float] = []
        colors: List[float] = []
        for (a, b, c) in sub.triangles:
            poly_idx += [a, b, ~c]
            for vi in (a, b, c):
                n = sub.normals[vi]
                normals += [n.x, n.y, n.z]
                if sub.uvs:
                    u, v = sub.uvs[vi]
                    uvs += [u, (1.0 - v) if FLIP_V else v]
                else:
                    uvs += [0.0, 0.0]
                if sub.colors:
                    r, g, bl, al = sub.colors[vi]
                    colors += [r / 255.0, g / 255.0, bl / 255.0, al / 255.0]
                else:
                    colors += [1.0, 1.0, 1.0, 1.0]

        geo = FBXNode("Geometry")
        geo.prop_i64(geo_id); geo.prop(_obj_name(sub.name, "Geometry")); geo.prop("Mesh")
        geo.child("GeometryVersion", 124)
        geo.child("Vertices").prop_f64_array(verts)
        geo.child("PolygonVertexIndex").prop_i32_array(poly_idx)

        ln = FBXNode("LayerElementNormal")
        ln.prop_i32(0)
        ln.child("Version", 101)
        ln.child("Name", "")
        ln.child("MappingInformationType", "ByPolygonVertex")
        ln.child("ReferenceInformationType", "Direct")
        ln.child("Normals").prop_f64_array(normals)
        geo.children.append(ln)

        lu = FBXNode("LayerElementUV")
        lu.prop_i32(0)
        lu.child("Version", 101)
        lu.child("Name", "map1")
        lu.child("MappingInformationType", "ByPolygonVertex")
        lu.child("ReferenceInformationType", "Direct")
        lu.child("UV").prop_f64_array(uvs)
        geo.children.append(lu)

        lc = FBXNode("LayerElementColor")
        lc.prop_i32(0)
        lc.child("Version", 101)
        lc.child("Name", "color")
        lc.child("MappingInformationType", "ByPolygonVertex")
        lc.child("ReferenceInformationType", "Direct")
        lc.child("Colors").prop_f64_array(colors)
        geo.children.append(lc)

        lm = FBXNode("LayerElementMaterial")
        lm.prop_i32(0)
        lm.child("Version", 101)
        lm.child("Name", "")
        lm.child("MappingInformationType", "AllSame")
        lm.child("ReferenceInformationType", "IndexToDirect")
        lm.child("Materials").prop_i32_array([0])
        geo.children.append(lm)

        layer = FBXNode("Layer"); layer.prop_i32(0)
        layer.child("Version", 100)
        for etype in ("LayerElementNormal", "LayerElementColor",
                      "LayerElementMaterial", "LayerElementUV"):
            le = FBXNode("LayerElement")
            le.child("Type", etype)
            le.child("TypedIndex", 0)
            layer.children.append(le)
        geo.children.append(layer)
        return geo

    # -- skinning ---------------------------------------------------------- #

    def _skin(self, sub, geo_id: int) -> None:
        s = self.s
        skin_id = s.new_id()
        skin = FBXNode("Deformer")
        skin.prop_i64(skin_id); skin.prop(_obj_name(sub.name, "Deformer")); skin.prop("Skin")
        skin.child("Version", 101)
        skin.children.append(_props70())
        skin.child("Link_DeformAcuracy", 50.0)
        s.add(skin, "Deformer")
        s.oo(skin_id, geo_id)

        # Control points are baked to bind-pose world (mesh global bind = I).
        # Per the FBX skin convention, a cluster stores:
        #   TransformLink = bone global bind         (boneBind)
        #   Transform     = geometry→bone-space bind = boneBind⁻¹ · meshBind
        # Here meshBind = I, so Transform = boneBind⁻¹.  This matters: Blender's
        # importer reconstructs the mesh's bind as TransformLink · Transform and
        # *assumes it is identical for every cluster of the mesh* — with the old
        # Transform = I it instead got boneBind (different per bone), so the mesh
        # snapped to whichever bone was processed last and the whole thing
        # scattered into a blob.  With Transform = boneBind⁻¹ the product is I
        # for every cluster, so the mesh stays put and deforms correctly.
        by_bone: Dict[int, List] = {}
        for vi, influences in enumerate(sub.skin):
            for gb, w in influences:
                by_bone.setdefault(gb, []).append((vi, w))
        for gb, items in by_bone.items():
            if not (0 <= gb < len(self.bone_ids)):
                continue
            cluster_id = s.new_id()
            bone = self.model.bones[gb]
            world = _scaled(bone.world_matrix or Mat4.identity(), self.scale)
            cl = FBXNode("Deformer")
            cl.prop_i64(cluster_id); cl.prop(_obj_name(bone.name, "SubDeformer"))
            cl.prop("Cluster")
            cl.child("Version", 100)
            cl.children.append(_props70())
            cl.child("UserData", "", "")
            cl.child("Indexes").prop_i32_array([vi for vi, _ in items])
            cl.child("Weights").prop_f64_array([float(w) for _, w in items])
            cl.child("Transform").prop_f64_array(world.inverse().fbx_array())
            cl.child("TransformLink").prop_f64_array(world.fbx_array())
            s.add(cl, "Deformer")
            s.oo(cluster_id, skin_id)
            s.oo(self.bone_ids[gb], cluster_id)

    # -- morphs ------------------------------------------------------------ #

    def _blendshapes(self, sub, geo_id: int) -> None:
        s = self.s
        bs_id = s.new_id()
        bsdef = FBXNode("Deformer")
        bsdef.prop_i64(bs_id); bsdef.prop(_obj_name(sub.name, "Deformer"))
        bsdef.prop("BlendShape")
        bsdef.child("Version", 100)
        s.add(bsdef, "Deformer")
        s.oo(bs_id, geo_id)
        for target in sub.morphs:
            indices = []
            deltas = []
            normals = []
            for i, d in enumerate(target.position_deltas):
                if abs(d.x) + abs(d.y) + abs(d.z) < 1e-6:
                    continue
                indices.append(i)
                deltas += [d.x * self.scale, d.y * self.scale, d.z * self.scale]
                if i < len(target.normal_deltas):
                    nd = target.normal_deltas[i]
                    normals += [nd.x, nd.y, nd.z]
                else:
                    normals += [0.0, 0.0, 0.0]
            if not indices:
                continue
            chan_id = s.new_id()
            chan = FBXNode("Deformer")
            chan.prop_i64(chan_id); chan.prop(_obj_name(target.name, "SubDeformer"))
            chan.prop("BlendShapeChannel")
            chan.child("Version", 100)
            chan.child("DeformPercent", 0.0)
            chan.child("FullWeights").prop_f64_array([100.0])
            chan.children.append(_props70())
            s.add(chan, "Deformer")
            s.oo(chan_id, bs_id)

            shape_id = s.new_id()
            shape = FBXNode("Geometry")
            shape.prop_i64(shape_id); shape.prop(_obj_name(target.name, "Geometry"))
            shape.prop("Shape")
            shape.child("Version", 100)
            shape.child("Indexes").prop_i32_array(indices)
            shape.child("Vertices").prop_f64_array(deltas)
            shape.child("Normals").prop_f64_array(normals)
            s.add(shape, "Geometry")
            s.oo(shape_id, chan_id)

    # -- animation --------------------------------------------------------- #

    def build_animation(self, anim: Animation) -> None:
        if not self.bone_by_name:
            return
        s = self.s
        fps = anim.fps or 60.0
        stack_id = s.new_id()
        stop = _ktime(anim.end_frame, fps)
        stack = FBXNode("AnimationStack")
        stack.prop_i64(stack_id); stack.prop(_obj_name(anim.name, "AnimStack")); stack.prop("")
        stack.children.append(_props70(
            _ktime_prop("LocalStart", 0),
            _ktime_prop("LocalStop", stop),
            _ktime_prop("ReferenceStart", 0),
            _ktime_prop("ReferenceStop", stop)))
        s.add(stack, "AnimationStack")

        layer_id = s.new_id()
        layer = FBXNode("AnimationLayer")
        layer.prop_i64(layer_id); layer.prop(_obj_name(anim.name, "AnimLayer")); layer.prop("")
        s.add(layer, "AnimationLayer")
        s.oo(layer_id, stack_id)

        for track in anim.tracks:
            mid = self.bone_by_name.get(track.bone_name)
            if mid is None:
                continue
            if track.translation:
                self._curve_node(
                    layer_id, mid, "Lcl Translation",
                    [(f, v.x * self.scale, v.y * self.scale, v.z * self.scale)
                     for f, v in track.translation], fps,
                    defaults=(0.0, 0.0, 0.0))
            if track.rotation:
                eulers = _quat_track_to_euler(track.rotation)
                self._curve_node(layer_id, mid, "Lcl Rotation", eulers, fps,
                                 defaults=(0.0, 0.0, 0.0))
            if track.scale:
                self._curve_node(
                    layer_id, mid, "Lcl Scaling",
                    [(f, v.x, v.y, v.z) for f, v in track.scale], fps,
                    defaults=(1.0, 1.0, 1.0))

    def _curve_node(self, layer_id: int, target_id: int, prop: str,
                    samples: Sequence, fps: float, defaults) -> None:
        s = self.s
        cn_id = s.new_id()
        cn = FBXNode("AnimationCurveNode")
        cn.prop_i64(cn_id); cn.prop(_obj_name(prop, "AnimCurveNode")); cn.prop("")
        cn.children.append(_props70(
            _p("d|X", "Number", "", "A", defaults[0]),
            _p("d|Y", "Number", "", "A", defaults[1]),
            _p("d|Z", "Number", "", "A", defaults[2])))
        s.add(cn, "AnimationCurveNode")
        s.oo(cn_id, layer_id)
        s.op(cn_id, target_id, prop)
        for ch, comp in (("d|X", 1), ("d|Y", 2), ("d|Z", 3)):
            times = [_ktime(row[0], fps) for row in samples]
            values = [row[comp] for row in samples]
            self._curve(cn_id, ch, times, values)

    def _curve(self, curve_node_id: int, channel: str,
               times: Sequence[int], values: Sequence[float]) -> None:
        s = self.s
        c_id = s.new_id()
        c = FBXNode("AnimationCurve")
        c.prop_i64(c_id); c.prop(_obj_name("", "AnimCurve")); c.prop("")
        c.child("Default", float(values[0]) if values else 0.0)
        c.child("KeyVer", 4008)
        c.child("KeyTime").prop_i64_array(list(times))
        c.child("KeyValueFloat").prop_f32_array([float(v) for v in values])
        # Match Blender's own export encoding for linear keys exactly so its
        # importer parses the curve identically.
        c.child("KeyAttrFlags").prop_i32_array([24836])
        c.child("KeyAttrDataFloat").prop_f32_array([0.0, 0.0, 9.419963346924634e-30, 0.0])
        c.child("KeyAttrRefCount").prop_i32_array([len(times)])
        s.add(c, "AnimationCurve")
        s.oo(c_id, curve_node_id)
        s.op(c_id, curve_node_id, channel)


def _scaled(m: Mat4, scale: float) -> Mat4:
    """Return ``m`` with its translation row multiplied by ``scale``."""
    out = Mat4([row[:] for row in m.m])
    out.m[3][0] *= scale; out.m[3][1] *= scale; out.m[3][2] *= scale
    return out


def _quat_track_to_euler(rot_track):
    """Convert a quaternion track to continuous Euler XYZ degrees (frame, x,y,z)."""
    out = []
    prev = Vec3(0.0, 0.0, 0.0)
    for i, (frame, q) in enumerate(rot_track):
        e = quat_to_euler_xyz(q)
        if i > 0:
            e = euler_unwrap(prev, e)
        prev = e
        out.append((frame, e.x, e.y, e.z))
    return out


def _mat3t(m):
    return [[m[0][0], m[1][0], m[2][0]],
            [m[0][1], m[1][1], m[2][1]],
            [m[0][2], m[1][2], m[2][2]]]


def _mat3mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _quat_track_to_euler_delta(rot_track, rest_euler: Vec3):
    """Animation rotation as a delta from the rest (PreRotation) orientation.

    With ``full = PreRotation · Lcl`` (row-vector ``full = Lcl · Pre``), the
    animated ``Lcl`` rotation is ``anim · restᵀ``.  Emitted as continuous Euler
    XYZ degrees so Blender's reoriented bones still pose correctly."""
    import math
    rest = euler_xyz_to_mat3(Vec3(math.radians(rest_euler.x),
                                  math.radians(rest_euler.y),
                                  math.radians(rest_euler.z)))
    rest_t = _mat3t(rest)
    out = []
    prev = Vec3(0.0, 0.0, 0.0)
    for i, (frame, q) in enumerate(rot_track):
        delta = _mat3mul(q.to_mat3(), rest_t)
        e = mat3_to_euler_xyz(delta)
        if i > 0:
            e = euler_unwrap(prev, e)
        prev = e
        out.append((frame, e.x, e.y, e.z))
    return out


# --------------------------------------------------------------------------- #
# Orientation
# --------------------------------------------------------------------------- #

# Row-vector +90° about X: maps Y-up data to Z-up (head +Y -> +Z).
_ROT_Y_TO_Z = Mat4([[1.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, -1.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0]])
_ROT3_Y_TO_Z = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]


def _apply_up_axis(model: Model, animations, up_axis: str) -> None:
    """Re-orient the whole scene in place for the requested up axis.

    The SIFAC data is Y-up (matching SIFAS), which is the default.  For
    ``up_axis == "z"`` we rotate everything by +90° about X so the character
    stands along +Z (Blender / Z-up DCC convention).  A global world rotation R
    leaves parent-relative transforms untouched, so only world matrices, the
    root bones' local matrix, the geometry, the morph deltas and the *root*
    animation tracks need rotating."""
    if up_axis != "z":
        return
    R, R3 = _ROT_Y_TO_Z, _ROT3_Y_TO_Z
    if model is not None:
        for sub in model.submeshes:
            sub.positions = [R.transform_point(p) for p in sub.positions]
            sub.normals = [R.transform_vector(n) for n in sub.normals]
            for tgt in sub.morphs:
                tgt.position_deltas = [R.transform_vector(d)
                                       for d in tgt.position_deltas]
                tgt.normal_deltas = [R.transform_vector(d)
                                     for d in tgt.normal_deltas]
        roots = set()
        for b in model.bones:
            b.world_matrix = (b.world_matrix or Mat4.identity()) * R
            if b.parent_index < 0:
                b.local_matrix = b.local_matrix * R
                roots.add(b.name)
    else:
        roots = set()
    for anim in (animations or []):
        for tr in anim.tracks:
            if tr.bone_name not in roots:
                continue
            tr.translation = [(f, R.transform_vector(v)) for f, v in tr.translation]
            tr.rotation = [(f, mat3_row_to_quat(_mat3mul(q.to_mat3(), R3)))
                           for f, q in tr.rotation]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def write_model_fbx(path, model: Model, animations: Optional[List[Animation]] = None,
                    texture_dir: str = "", scale: float = 1.0,
                    include_morphs: bool = True, up_axis: str = "y") -> None:
    """Write a model (optionally with animation takes) to a binary FBX file."""
    _apply_up_axis(model, animations, up_axis)
    scene = _FbxScene(up_axis=up_axis)
    builder = _Builder(scene, model, texture_dir=texture_dir, scale=scale,
                       include_morphs=include_morphs)
    builder.build_skeleton()
    builder.build_meshes()
    builder.build_bind_pose()
    for anim in (animations or []):
        builder.build_animation(anim)
    scene.write(path)


def write_animation_fbx(path, model: Model, animations: List[Animation],
                        texture_dir: str = "", scale: float = 1.0,
                        up_axis: str = "y") -> None:
    """Write a rigged model with one or more animation takes (for motions)."""
    write_model_fbx(path, model, animations, texture_dir=texture_dir,
                    scale=scale, include_morphs=False, up_axis=up_axis)


def write_camera_fbx(path, camera: CameraAnim, scale: float = 1.0,
                     up_axis: str = "y") -> None:
    """Write a camera with its animated transform + field of view."""
    if up_axis == "z":
        R, R3 = _ROT_Y_TO_Z, _ROT3_Y_TO_Z
        camera.base_pos = R.transform_point(camera.base_pos)
        camera.base_rot = mat3_row_to_quat(_mat3mul(camera.base_rot.to_mat3(), R3))
        camera.translation = [(f, R.transform_point(v)) for f, v in camera.translation]
        camera.rotation = [(f, mat3_row_to_quat(_mat3mul(q.to_mat3(), R3)))
                           for f, q in camera.rotation]
    scene = _FbxScene(up_axis=up_axis)
    s = scene
    fps = camera.fps or 60.0

    cam_attr_id = s.new_id()
    attr = FBXNode("NodeAttribute")
    attr.prop_i64(cam_attr_id); attr.prop(_obj_name(camera.name, "NodeAttribute"))
    attr.prop("Camera")
    attr.children.append(_props70(
        _p("FieldOfView", "FieldOfView", "", "A", camera.base_fov),
        _p("AspectWidth", "double", "Number", "", 1920.0),
        _p("AspectHeight", "double", "Number", "", 1080.0)))
    attr.child("TypeFlags", "Camera")
    s.add(attr, "NodeAttribute")

    cam_id = s.new_id()
    cam = FBXNode("Model")
    cam.prop_i64(cam_id); cam.prop(_obj_name(camera.name, "Model")); cam.prop("Camera")
    cam.child("Version", 232)
    t = camera.base_pos
    r = quat_to_euler_xyz(camera.base_rot)
    cam.children.append(_props70(
        _p("Lcl Translation", "Lcl Translation", "", "A",
           t.x * scale, t.y * scale, t.z * scale),
        _p("Lcl Rotation", "Lcl Rotation", "", "A", r.x, r.y, r.z),
        _p("Lcl Scaling", "Lcl Scaling", "", "A", 1.0, 1.0, 1.0)))
    s.add(cam, "Model")
    s.oo(cam_attr_id, cam_id)
    s.oo(cam_id, 0)

    # Animate the camera transform.
    builder = _Builder(scene, None, scale=scale)
    builder.bone_by_name = {camera.name: cam_id}
    anim = Animation(name=camera.name, end_frame=camera.end_frame, fps=fps)
    from sifac_scene import AnimTrack
    track = AnimTrack(bone_name=camera.name)
    track.translation = list(camera.translation)
    track.rotation = list(camera.rotation)
    anim.tracks.append(track)
    builder.build_animation(anim)

    # Best-effort animated field of view on the camera node attribute.
    if camera.fov:
        cn_id = s.new_id()
        cn = FBXNode("AnimationCurveNode")
        cn.prop_i64(cn_id); cn.prop(_obj_name("FieldOfView", "AnimCurveNode"))
        cn.prop("")
        cn.children.append(_props70(_p("d|X", "Number", "", "A", camera.base_fov)))
        s.add(cn, "AnimationCurveNode")
        # Connect to the most recent AnimationLayer.
        for obj in reversed(scene.objects):
            if obj.name == "AnimationLayer":
                s.oo(cn_id, obj.props[0]); break
        s.op(cn_id, cam_attr_id, "FieldOfView")
        builder._curve(cn_id, "d|X", [_ktime(f, fps) for f, _ in camera.fov],
                       [v for _, v in camera.fov])
    scene.write(path)
