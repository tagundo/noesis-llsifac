#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_bmarc — pure-Python parser for SIFAC ``.bmarc`` / ``.bscam``
=================================================================

A Noesis-free port of the parsing half of
``plugins/python/fmt_Blade_bmarc.py``.  Where the Noesis plugin feeds Noesis'
``rpg*`` rendering API, this module decodes the same buffers into the plain
:mod:`sifac_scene` dataclasses so the converter can write FBX without launching
Noesis.

Covers:

* ``BMAR104`` archives → models (``BML``/``BMD``/``BMT``) and animations (``BMA``)
* ``BSCM`` camera animations
* embedded ``tex`` (``.btx``) textures

Skinning convention (see :mod:`sifac_fbx` for the matching cluster math):
skinned vertices are emitted in **bind-pose world space**; rigid (non-skinned)
meshes are baked into world space via their node's world matrix.  Either way a
skin cluster ties the geometry to the skeleton so animation deforms it.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sifac_mathutil import (Mat4, Quat, Vec3, half_to_float)
from sifac_reader import BinaryReader, SEEK_ABS, SEEK_REL
from sifac_scene import (AnimTrack, Animation, Bone, CameraAnim, Material,
                         Model, MorphTarget, SubMesh)


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #

@dataclass
class ParsedArchive:
    name: str
    model: Optional[Model] = None
    animations: List[Animation] = field(default_factory=list)
    camera: Optional[CameraAnim] = None

    def is_model(self) -> bool:
        return self.model is not None and self.model.has_geometry()

    def is_animation(self) -> bool:
        return bool(self.animations)


# --------------------------------------------------------------------------- #
# Magic sniffing
# --------------------------------------------------------------------------- #

def is_bmarc(data: bytes) -> bool:
    return data[:7] == b"BMAR104"


def is_bscam(data: bytes) -> bool:
    return data[:4] == b"BSCM"


# --------------------------------------------------------------------------- #
# Small buffer helpers
# --------------------------------------------------------------------------- #

def _deinterleave(buf: bytes, offset: int, width: int, stride: int) -> bytes:
    """Pull ``width`` bytes starting at ``offset`` out of every ``stride``-byte
    record.  Equivalent to Noesis' ``noesis.deinterleaveBytes``."""
    out = bytearray()
    n = len(buf)
    pos = offset
    while pos + width <= n:
        out += buf[pos:pos + width]
        pos += stride
    return bytes(out)


def _inflate_if_needed(data: bytes) -> bytes:
    """Inner ``cmp\\0`` / zlib decompression used by archive members."""
    if data[:4] == b"cmp\x00" or data[:3] == b"cmp":
        cs = BinaryReader(data)
        cs.readMagic(4)                 # "cmp"
        ctype = cs.readMagic(4)         # "zlib"
        zsize = cs.readUInt()
        size = cs.readUInt()
        blob = cs.read(zsize)
        if ctype == "zlib":
            try:
                return zlib.decompress(blob)
            except zlib.error:
                return zlib.decompressobj().decompress(blob, size)
    return data


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #

def parse_bmarc(data: bytes, name: str) -> ParsedArchive:
    """Parse a ``.bmarc`` (model and/or animation)."""
    if not is_bmarc(data):
        raise ValueError("not a BMAR104 archive")
    result = ParsedArchive(name=name)
    arc = _Arc(name, result)
    # The inner ARC's offsets are relative to the byte right after the 8-byte
    # "BMAR104" header, so we read from a slice (== the plugin's data[0x08:]).
    arc.read(BinaryReader(data[0x08:]))
    if arc.has_model:
        result.model = arc.build_model()
    result.animations = arc.animations
    return result


def parse_bscam(data: bytes, name: str) -> ParsedArchive:
    """Parse a ``.bscam`` camera animation."""
    if not is_bscam(data):
        raise ValueError("not a BSCM camera")
    result = ParsedArchive(name=name)
    result.camera = _read_bscm(BinaryReader(data), name)
    return result


def parse_any(data: bytes, name: str) -> ParsedArchive:
    if is_bmarc(data):
        return parse_bmarc(data, name)
    if is_bscam(data):
        return parse_bscam(data, name)
    raise ValueError("unrecognised file (not BMAR104 / BSCM)")


# --------------------------------------------------------------------------- #
# Raw mesh record (header fields straight out of MESH)
# --------------------------------------------------------------------------- #

@dataclass
class _RawMesh:
    name: str = ""
    uv_flag: int = 0
    tb_flag: int = 0
    buff_flag: int = 0
    weight_count: int = 0
    morph_count: int = 0
    face_count: int = 0
    face_size: int = 0
    stride: int = 0
    vert_count: int = 0
    bone_map_idx: int = 0
    vert_buff: bytes = b""
    face_buff: bytes = b""
    morph_names: List[str] = field(default_factory=list)
    # Computed base buffers (only for morph meshes): floats.
    morph_base_pos: Optional[List[float]] = None
    morph_base_norm: Optional[List[float]] = None
    morph_targets: List[MorphTarget] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# The archive reader
# --------------------------------------------------------------------------- #

class _Arc:
    def __init__(self, name: str, result: ParsedArchive):
        self.name = name
        self.result = result
        self.bones: List[Bone] = []
        self.bone_dict: Dict[str, int] = {}
        self.materials: List[Material] = []
        self.mat_dict: Dict[str, int] = {}
        self.mat_extra: List = []          # parallel to materials (unused extras)
        self.bone_maps: List[List[int]] = []
        self.mtx_palettes: List[List[Mat4]] = []
        self.raw_meshes: List[_RawMesh] = []
        self.draws: List = []              # (nodeIdx, meshIdx, matIdx)
        self.referenced_textures: List[str] = []
        self.embedded_textures: Dict[str, object] = {}
        self.morph_list: List = []         # [posData, normData] per morph
        self.animations: List[Animation] = []
        self.has_model = False

    # -- top-level ARC ----------------------------------------------------- #

    def read(self, bs: BinaryReader) -> None:
        bs.readMagic(4)                    # inner ARC magic
        bs.readUShort(); bs.readUShort()
        file_count = bs.readInt()
        bs.readUInt()
        bs.readFixedString(0x20)
        offsets = [bs.readUInt() for _ in range(file_count)]
        bs.align(0x20)
        types: List[str] = []
        names: List[str] = []
        for _ in range(file_count):
            types.append(bs.readMagic(4))
            bs.readUShort()                # unk1
            bs.readUShort()                # idx
            bs.readUInt()                  # hash
            bs.readUShort()                # unk2
            nsize = bs.readUShort()
            bs.readUInt()                  # unk3
            names.append(bs.readFixedString(nsize))
            bs.align(0x20)

        # Pre-load a BMT (morph) member before its BML, matching the plugin.
        if "BMT" in types:
            bi = types.index("BMT")
            self._load_member(bs, offsets[bi], "morph", names[bi])
        for i in range(file_count):
            if types[i] == "BMT":
                continue
            self._load_member(bs, offsets[i], types[i], names[i])

    def _load_member(self, bs: BinaryReader, off: int, ftype: str, name: str) -> None:
        bs.seek(off, SEEK_ABS)
        data_size = bs.readUInt()
        data_off = bs.readUShort()
        cmp_flag = bs.readUByte()
        bs.seek(off + data_off, SEEK_ABS)
        data = bs.read(data_size)
        if cmp_flag:
            data = _inflate_if_needed(data)
        if ftype == "BML":
            self.has_model = True
            self._read_bml(BinaryReader(data))
        elif ftype == "BMD":
            self._read_bmd(BinaryReader(data))
        elif ftype == "BMA":
            self._read_bma(BinaryReader(data), name)
        elif ftype == "morph":
            self._read_bmt(BinaryReader(data, 0x0C))
        elif ftype == "tex":
            self._read_embedded_tex(data)

    # -- embedded textures ------------------------------------------------- #

    def _read_embedded_tex(self, data: bytes) -> None:
        try:
            import sifac_btx
            tex = sifac_btx.decode_btx(data)
            self.embedded_textures[tex.name.rsplit(".", 1)[0]] = tex
        except Exception:
            pass

    # -- BML (model) ------------------------------------------------------- #

    def _read_bml(self, bs: BinaryReader) -> None:
        bs.readMagic(4)
        bs.readUInt()                      # chunkSize
        bs.readUInt()                      # unk
        bs.read(0x10)                      # mesh/node/mate/... counts (unused)
        self._read_chunks(bs)

    def _read_chunks(self, bs: BinaryReader) -> None:
        while bs.remaining() >= 12:
            start = bs.tell()
            magic = bs.readMagic(4)
            if magic == "":
                break
            chunk_size = bs.readUInt()
            bs.readUInt()                  # unk
            body = bs.tell()
            handler = {
                "NODE": self._read_node, "MATE": self._read_mate,
                "MESH": self._read_mesh, "MTXP": self._read_mtxp,
                "DRAW": self._read_draw, "TXTR": self._read_txtr,
            }.get(magic)
            try:
                if handler is not None:
                    handler(bs)
            except (struct.error, IndexError, ValueError):
                pass
            # Advance deterministically by the chunk size (handlers may seek
            # away to read buffers; this keeps iteration robust regardless).
            nxt = body + chunk_size
            if nxt <= start:
                break
            bs.seek(nxt, SEEK_ABS)
            bs.align(0x04)

    def _read_node(self, bs: BinaryReader) -> None:
        name = bs.stringAt(bs.readUInt64())
        if name in self.bone_dict:
            name += "_dup"
        parent = bs.stringAt(bs.readUInt64())
        pos = Vec3(bs.readFloat(), bs.readFloat(), bs.readFloat())
        scl = Vec3(bs.readFloat(), bs.readFloat(), bs.readFloat())
        euler = Vec3(bs.readFloat(), bs.readFloat(), bs.readFloat())
        local = Mat4.from_trs_noesis(pos, euler, scl)
        if name.startswith("Bip001_"):
            name = name[7:]
        if parent.startswith("Bip001_"):
            parent = parent[7:]
        bone = Bone(index=len(self.bones), name=name,
                    parent_name=parent or None, local_matrix=local)
        self.bones.append(bone)
        self.bone_dict[name] = bone.index

    def _read_mate(self, bs: BinaryReader) -> None:
        name = bs.stringAt(bs.readUInt64())
        tex = bs.stringAt(bs.readUInt64()).rsplit(".", 1)[0]
        bs.read(4)                         # unkColor
        bs.read(4)                         # modifier
        amb = tuple(bs.readUByte() / 255 for _ in range(4))
        bs.read(4)                         # emission
        bs.read(4)                         # unkColor
        mat = Material(name=name, diffuse_tex=tex, ambient=amb)  # type: ignore[arg-type]
        self.mat_dict[name] = len(self.materials)
        self.materials.append(mat)
        if tex:
            self.referenced_textures.append(tex)

    def _read_mtxp(self, bs: BinaryReader) -> None:
        index_off = bs.readUInt64()
        matrix_off = bs.readUInt64()
        bs.readUShort()
        bone_count = bs.readShort()
        bs.seek(index_off, SEEK_ABS)
        self.bone_maps.append([bs.readUInt() for _ in range(bone_count)])
        bs.seek(matrix_off, SEEK_ABS)
        mats = []
        for _ in range(bone_count):
            rows = [[bs.readFloat() for _ in range(4)] for _ in range(4)]
            m = Mat4(rows)
            # toMat43 semantics: the matrix is affine, drop the 4th column.
            m.m[0][3] = m.m[1][3] = m.m[2][3] = 0.0
            m.m[3][3] = 1.0
            mats.append(m)
        self.mtx_palettes.append(mats)

    def _read_mesh(self, bs: BinaryReader) -> None:
        m = _RawMesh()
        m.name = bs.stringAt(bs.readUInt64())
        vert_off = bs.readUInt64()
        face_off = bs.readUInt64()
        face_info_off = bs.readUInt64()
        morph_off = bs.readUInt64()
        bs.seek(0x20, SEEK_REL)
        bs.readUByte()                     # unkTypeFlag1
        m.uv_flag = bs.readUByte()
        m.tb_flag = bs.readUByte()
        bs.readUByte()                     # unkTypeFlag2
        bs.readUInt()                      # unk
        m.face_count = bs.readUInt()
        bs.readInt()                       # vertCount (recomputed below)
        m.face_size = bs.readUShort()
        m.stride = bs.readUShort()
        bs.readByte()                      # unkCount1
        m.weight_count = bs.readByte()
        bs.readByte(); bs.readByte()
        m.morph_count = bs.readUByte()
        m.buff_flag = bs.readUByte()

        bs.seek(vert_off - 0x08, SEEK_ABS)
        vsize = bs.readUInt(); bs.readUInt()
        m.vert_buff = bs.read(vsize)
        m.stride = m.stride or 1
        m.vert_count = vsize // m.stride
        bs.seek(face_off - 0x08, SEEK_ABS)
        fsize = bs.readUInt(); bs.readUInt()
        m.face_buff = bs.read(fsize)
        bs.seek(face_info_off, SEEK_ABS)
        bs.seek(0x10, SEEK_REL)
        m.bone_map_idx = bs.readUInt()

        if m.morph_count:
            try:
                self._read_mesh_morphs(bs, m, morph_off)
            except Exception:
                m.morph_count = 0
                m.morph_base_pos = None
                m.morph_targets = []
        self.raw_meshes.append(m)

    def _read_mesh_morphs(self, bs: BinaryReader, m: _RawMesh, morph_off: int) -> None:
        comp = m.vert_count * 3
        base_pos_buf = _deinterleave(m.vert_buff, 0x00, 0x0C, m.stride)
        norm_src = 0x34 if (m.buff_flag & 0x01) else 0x0C
        base_norm_buf = _deinterleave(m.vert_buff, norm_src, 0x06, m.stride)
        base_pos = list(struct.unpack_from("<%df" % comp, base_pos_buf, 0))
        base_norm = [half_to_float(h) for h in
                     struct.unpack_from("<%dH" % comp, base_norm_buf, 0)]
        if len(self.morph_list) < m.morph_count:
            raise ValueError("morph data missing")

        new_base_pos: List[float] = []
        bs.seek(morph_off, SEEK_ABS)
        for i in range(m.morph_count):
            mname = bs.stringAt(bs.readUInt64())
            bs.seek(0x08, SEEK_REL)
            m.morph_names.append(mname)
            mp_buf = _deinterleave(self.morph_list[i][0], 0x00, 0x06, 0x08)
            mn_buf = _deinterleave(self.morph_list[i][1], 0x00, 0x06, 0x08)
            mp = [half_to_float(h) for h in struct.unpack_from("<%dH" % comp, mp_buf, 0)]
            mn = [half_to_float(h) for h in struct.unpack_from("<%dH" % comp, mn_buf, 0)]
            if i == 0:
                new_base_pos = [base_pos[a] + mp[a] for a in range(comp)]
                new_base_norm = [base_norm[a] + mn[a] for a in range(comp)]
                m.morph_base_pos = new_base_pos
                m.morph_base_norm = new_base_norm
            else:
                eye = ("_eye_" in mname or "_eyebrow_" in mname)
                deltas_pos: List[Vec3] = []
                deltas_norm: List[Vec3] = []
                for v in range(m.vert_count):
                    a = v * 3
                    if eye:
                        tx = new_base_pos[a] + mp[a]
                        ty = new_base_pos[a + 1] + mp[a + 1]
                        tz = new_base_pos[a + 2] + mp[a + 2]
                    else:
                        tx = base_pos[a] + mp[a]
                        ty = base_pos[a + 1] + mp[a + 1]
                        tz = base_pos[a + 2] + mp[a + 2]
                    deltas_pos.append(Vec3(tx - new_base_pos[a],
                                           ty - new_base_pos[a + 1],
                                           tz - new_base_pos[a + 2]))
                    deltas_norm.append(Vec3(mn[a], mn[a + 1], mn[a + 2]))
                m.morph_targets.append(
                    MorphTarget(name=mname, position_deltas=deltas_pos,
                                normal_deltas=deltas_norm))

    def _read_draw(self, bs: BinaryReader) -> None:
        node_idx = bs.readUInt()
        mesh_idx = bs.readUInt()
        mat_idx = bs.readUInt()
        bs.readUInt()
        self.draws.append((node_idx, mesh_idx, mat_idx))

    def _read_txtr(self, bs: BinaryReader) -> None:
        name = bs.stringAt(bs.readUInt64()).rsplit(".", 1)[0]
        if name:
            self.referenced_textures.append(name)

    # -- BMD (extra material textures) ------------------------------------ #

    def _read_bmd(self, bs: BinaryReader) -> None:
        bs.readMagic(4)                    # magic
        bs.readMagic(4)                    # version
        bs.readUShort()
        mat_count = bs.readShort()
        bs.read(12)                        # unk x3
        mat_off = bs.readUInt()
        bs.readUInt()                      # stringOff
        bs.read(8)                         # unk x2
        tex_prop_count = bs.readShort()
        shader_prop_count = bs.readShort()
        mat_prop_count = bs.readShort()
        bs.readUShort()
        tex_prop_off = bs.readUInt()
        shader_prop_off = bs.readUInt()
        mat_prop_off = bs.readUInt()

        bs.seek(tex_prop_off, SEEK_ABS)
        tex_prop = []
        for _ in range(tex_prop_count):
            tex_hash = bs.readUInt()
            if tex_hash:
                tname = bs.stringAt(bs.readUInt()).rsplit(".", 1)[0]
            else:
                bs.read(4); tname = ""
            bs.readUInt()                  # texTypeHash
            ttype = bs.stringAt(bs.readUInt())
            bs.read(0x20)
            tex_prop.append((ttype, tname))

        bs.seek(shader_prop_off, SEEK_ABS)
        shader_prop = []
        for _ in range(shader_prop_count):
            bs.readUInt()                  # shaderHash
            sname = bs.stringAt(bs.readUInt())
            bs.read(8)
            mp_idx = bs.readShort()
            tp_idx = bs.readShort()
            bs.readByte()
            mp_cnt = bs.readByte()
            tp_cnt = bs.readByte()
            bs.read(0x19)
            shader_prop.append((sname, mp_idx, mp_cnt, tp_idx, tp_cnt))

        bs.seek(mat_prop_off, SEEK_ABS)
        for _ in range(mat_prop_count):
            bs.stringAt(bs.readUInt())     # matPropName
            bs.readUInt()
            bs.readUShort()
            bs.read(0x06)
            bs.read(0x10)                  # vec4

        bs.seek(mat_off, SEEK_ABS)
        for _ in range(mat_count):
            bs.readUInt()                  # matHash
            mname = bs.stringAt(bs.readUInt())
            bs.read(0x1C)
            shader_idx = bs.readUShort() + 1
            shader_cnt = bs.readByte()
            bs.read(0x39)
            if mname in self.mat_dict and shader_cnt == 2 \
                    and 0 <= shader_idx < len(shader_prop):
                mat = self.materials[self.mat_dict[mname]]
                _, _, _, tp_idx, tp_cnt = shader_prop[shader_idx]
                for a in range(tp_idx, tp_idx + tp_cnt):
                    if not (0 <= a < len(tex_prop)):
                        continue
                    ttype, tname = tex_prop[a]
                    if not tname:
                        continue
                    if ttype == "tSpecularMap":
                        mat.specular_tex = tname
                    elif ttype == "tNormalMap":
                        mat.normal_tex = tname
                    elif ttype == "tLightMap":
                        mat.light_tex = tname
                    elif ttype == "tEnvMap":
                        mat.env_tex = tname
                    self.referenced_textures.append(tname)

    # -- BMA (skeletal animation) ----------------------------------------- #

    def _read_bma(self, bs: BinaryReader, name: str) -> None:
        bs.readMagic(4)
        bs.readUInt(); bs.readUInt()
        bs.readMagic(4)                    # version
        fps = bs.readFloat()
        end_frame = int(bs.readFloat())
        if end_frame == 0:
            return
        magic = bs.readMagic(4)
        bs.readUInt(); bs.readUInt()
        if magic != "ANSK":
            return
        anim = Animation(name=name, end_frame=end_frame, fps=fps or 60.0)
        bone_off = bs.readUInt64()
        bone_count = bs.readUShort()
        bs.readUShort()
        bs.readUInt()
        bs.seek(bone_off, SEEK_ABS)
        entries = []
        for _ in range(bone_count):
            bname = bs.stringAt(bs.readUInt64())
            if bname.startswith("Bip001_"):
                bname = bname[7:]
            data_off = bs.readUInt64()
            bs.readUInt()                  # hash
            data_count = bs.readUShort()
            bs.readUShort()                # facialId
            entries.append((bname, data_off, data_count))
        for bname, data_off, data_count in entries:
            track = AnimTrack(bone_name=bname)
            bs.seek(data_off, SEEK_ABS)
            self._read_anim_data(bs, track, data_count)
            if track.translation or track.rotation or track.scale:
                anim.tracks.append(track)
        if anim.tracks:
            self.animations.append(anim)

    def _read_anim_data(self, bs: BinaryReader, track: AnimTrack, count: int) -> None:
        headers = []
        for _ in range(count):
            frame_off = bs.readUInt64()
            key_off = bs.readUInt64()
            frame_count = bs.readUInt()
            data_type = bs.readUInt()
            headers.append((frame_off, key_off, frame_count, data_type))
        for frame_off, key_off, frame_count, data_type in headers:
            bs.seek(frame_off, SEEK_ABS)
            frames = []
            for _ in range(frame_count):
                frames.append(int(bs.readFloat()))
                bs.readUInt64()            # per-key data offset (unused)
            bs.seek(key_off, SEEK_ABS)
            if data_type == 0x00:
                for i in range(frame_count):
                    v = Vec3(bs.readFloat(), bs.readFloat(), bs.readFloat())
                    track.translation.append((frames[i], v))
            elif data_type == 0x02:
                for i in range(frame_count):
                    v = Vec3(bs.readFloat(), bs.readFloat(), bs.readFloat())
                    track.scale.append((frames[i], v))
            elif data_type == 0x03:
                for i in range(frame_count):
                    # The plugin negates w for Noesis' quaternion->matrix
                    # convention; our math uses the raw file quaternion.
                    q = Quat(bs.readFloat(), bs.readFloat(), bs.readFloat(),
                             bs.readFloat())
                    track.rotation.append((frames[i], q))

    # -- BMT (morph source) ----------------------------------------------- #

    def _read_bmt(self, bs: BinaryReader) -> None:
        bs.readMagic(4)
        bs.readUShort(); bs.readUShort()
        file_count = bs.readInt()
        bs.readUInt()
        bs.readFixedString(0x20)
        offsets = [bs.readUInt() for _ in range(file_count)]
        bs.align(0x20)
        types = []
        for _ in range(file_count):
            types.append(bs.readMagic(4))
            bs.readUShort(); bs.readUShort(); bs.readUInt()
            bs.readUShort()
            nsize = bs.readUShort()
            bs.readUInt()
            bs.readFixedString(nsize)
            bs.align(0x20)
        for i in range(file_count):
            if types[i] != "tex":
                continue
            bs.seek(offsets[i], SEEK_ABS)
            data_size = bs.readUInt()
            data_off = bs.readUShort()
            cmp_flag = bs.readUByte()
            bs.seek(offsets[i] + data_off, SEEK_ABS)
            data = bs.read(data_size)
            if cmp_flag:
                data = _inflate_if_needed(data)
            self._read_morph_tex(BinaryReader(data))

    def _read_morph_tex(self, bs: BinaryReader) -> None:
        bs.seek(0x08, SEEK_REL)
        width = bs.readUShort()
        height = bs.readUShort()
        bs.readUInt()
        byte_format = bs.readUByte()
        bs.seek(0x07, SEEK_REL)
        data_off = bs.readUInt()
        bs.seek(data_off, SEEK_ABS)
        if byte_format == 0x1B:
            pos_data = bs.read(width * height * 0x04)
            norm_data = bs.read(width * height * 0x04)
            self.morph_list.append([pos_data, norm_data])

    # -- assemble the final Model ----------------------------------------- #

    def build_model(self) -> Model:
        self._resolve_skeleton()
        model = Model(name=self.name, bones=self.bones, materials=self.materials)
        model.embedded_textures = self.embedded_textures
        model.referenced_textures = list(dict.fromkeys(self.referenced_textures))
        for node_idx, mesh_idx, mat_idx in self.draws:
            if not (0 <= mesh_idx < len(self.raw_meshes)):
                continue
            mesh = self.raw_meshes[mesh_idx]
            if mesh.name == "unified_mesh":
                continue
            sub = self._build_submesh(mesh, node_idx, mat_idx)
            if sub is not None and sub.positions:
                model.submeshes.append(sub)
        return model

    def _resolve_skeleton(self) -> None:
        for b in self.bones:
            b.parent_index = self.bone_dict.get(b.parent_name, -1) \
                if b.parent_name else -1
        # World matrices via hierarchy (== Noesis rapi.multiplyBones).
        for b in self.bones:
            if b.parent_index >= 0 and self.bones[b.parent_index].world_matrix:
                b.world_matrix = b.local_matrix * self.bones[b.parent_index].world_matrix
            elif b.parent_index >= 0:
                # parent appears later; compute lazily below
                b.world_matrix = None
            else:
                b.world_matrix = b.local_matrix
        # Second pass for any out-of-order parents.
        for _ in range(len(self.bones)):
            changed = False
            for b in self.bones:
                if b.world_matrix is None and b.parent_index >= 0:
                    pw = self.bones[b.parent_index].world_matrix
                    if pw is not None:
                        b.world_matrix = b.local_matrix * pw
                        changed = True
            if not changed:
                break
        for b in self.bones:
            if b.world_matrix is None:
                b.world_matrix = b.local_matrix

    def _palette_pretransform(self, mesh: _RawMesh, vbuf: bytes, stride: int) -> Mat4:
        """MTXP-based pre-transform for non-model skinned meshes.

        ``rpgSetTransform(mtxList[boneMapIdx][idx])`` where ``idx`` is vertex
        0's first bone index, and ``mtxList[k] = fileMtx_k · boneWorld[bm[k]]``.
        """
        bmi = mesh.bone_map_idx
        if not (0 <= bmi < len(self.bone_maps)):
            return Mat4.identity()
        bone_map = self.bone_maps[bmi]
        file_mtx = self.mtx_palettes[bmi] if bmi < len(self.mtx_palettes) else []
        idx0 = struct.unpack_from("<B", vbuf, 0x1C)[0] if len(vbuf) > 0x1C else 0
        if idx0 >= len(bone_map):
            idx0 = 0
        gb = bone_map[idx0] if bone_map else 0
        bw = self.bones[gb].world_matrix if 0 <= gb < len(self.bones) else Mat4.identity()
        fm = file_mtx[idx0] if idx0 < len(file_mtx) else Mat4.identity()
        return fm * bw

    def _build_submesh(self, mesh: _RawMesh, node_idx: int, mat_idx: int):
        stride = mesh.stride
        vbuf = mesh.vert_buff
        n = mesh.vert_count
        if n == 0 or stride == 0:
            return None
        skinned = bool(mesh.buff_flag & 0x01)

        # --- attribute offsets within the stride (mirrors readDraw) ---------
        off = 0x0C
        if skinned:
            off = 0x34
        norm_off = off
        off += 0x08
        col_off = off
        off += 0x04
        uv1_off = uv1_half = uv2_off = uv2_half = None
        if mesh.uv_flag & 0x01:
            uv1_off = off; off += 0x08
        elif mesh.uv_flag & 0x04:
            uv1_off = off; uv1_half = True; off += 0x04
        if mesh.buff_flag & 0x04:
            if mesh.uv_flag & 0x10:
                uv2_off = off; off += 0x08
            elif mesh.uv_flag & 0x40:
                uv2_off = off; uv2_half = True; off += 0x04

        material_name = self.materials[mat_idx].name \
            if 0 <= mat_idx < len(self.materials) else ""
        sub = SubMesh(name=mesh.name or f"mesh_{node_idx}",
                      material_name=material_name)

        # --- per-mesh pre-transform into bind-pose world space --------------
        # Mirrors the plugin's rpgSetTransform:
        #   skinned model mesh  -> bone[0] (the bml_axisNode, e.g. -90deg X),
        #   other skinned mesh  -> MTXP matrix picked by vertex 0's bone index,
        #   rigid mesh          -> the draw node's world matrix.
        ident = Mat4.identity()
        if skinned:
            if self.name.startswith("mod_") or self.name.startswith("stage"):
                pre = self.bones[0].world_matrix if self.bones else ident
            else:
                pre = self._palette_pretransform(mesh, vbuf, stride)
        else:
            pre = self.bones[node_idx].world_matrix \
                if 0 <= node_idx < len(self.bones) else ident
        pre = pre or ident
        sub.bind_transform = pre        # maps rest verts -> world bind

        use_morph_base = mesh.morph_count and mesh.morph_base_pos is not None
        for v in range(n):
            base = v * stride
            if use_morph_base:
                a = v * 3
                p = Vec3(mesh.morph_base_pos[a], mesh.morph_base_pos[a + 1],
                         mesh.morph_base_pos[a + 2])
                nrm = Vec3(mesh.morph_base_norm[a], mesh.morph_base_norm[a + 1],
                           mesh.morph_base_norm[a + 2])
            else:
                px, py, pz = struct.unpack_from("<3f", vbuf, base)
                p = Vec3(px, py, pz)
                nx, ny, nz = struct.unpack_from("<3H", vbuf, base + norm_off)
                nrm = Vec3(half_to_float(nx), half_to_float(ny), half_to_float(nz))
            # Bake into bind-pose world space (robust for Blender's importer).
            sub.positions.append(pre.transform_point(p))
            sub.normals.append(pre.transform_vector(nrm).normalized())

            r, g, b, al = struct.unpack_from("<4B", vbuf, base + col_off)
            sub.colors.append((r, g, b, al))

            if uv1_off is not None:
                if uv1_half:
                    u, w = struct.unpack_from("<2H", vbuf, base + uv1_off)
                    sub.uvs.append((half_to_float(u), half_to_float(w)))
                else:
                    u, w = struct.unpack_from("<2f", vbuf, base + uv1_off)
                    sub.uvs.append((u, w))
            else:
                sub.uvs.append((0.0, 0.0))
            if uv2_off is not None:
                if uv2_half:
                    u, w = struct.unpack_from("<2H", vbuf, base + uv2_off)
                    sub.uvs2.append((half_to_float(u), half_to_float(w)))
                else:
                    u, w = struct.unpack_from("<2f", vbuf, base + uv2_off)
                    sub.uvs2.append((u, w))

        # --- skin weights (global bone index, weight) ------------------------
        bone_map = self.bone_maps[mesh.bone_map_idx] \
            if 0 <= mesh.bone_map_idx < len(self.bone_maps) else []
        if skinned and bone_map:
            for v in range(n):
                base = v * stride
                w1 = struct.unpack_from("<4f", vbuf, base + 0x0C)
                i1 = struct.unpack_from("<4B", vbuf, base + 0x1C)
                pairs = list(zip(i1, w1))
                if mesh.weight_count > 4:
                    w2 = struct.unpack_from("<4f", vbuf, base + 0x20)
                    i2 = struct.unpack_from("<4B", vbuf, base + 0x30)
                    pairs += list(zip(i2, w2))
                inf = [(bone_map[li], float(w)) for li, w in pairs
                       if w > 0.0 and li < len(bone_map)]
                sub.skin.append(inf or [(bone_map[0], 1.0)])
        else:
            gb = node_idx if 0 <= node_idx < len(self.bones) else 0
            for _ in range(n):
                sub.skin.append([(gb, 1.0)])

        # --- faces (reverse winding to match the plugin's TRIWINDBACKWARD) ---
        fs = mesh.face_size or 2
        fmt = {1: "<B", 2: "<H", 4: "<I"}.get(fs, "<H")
        idx = list(struct.iter_unpack(fmt, mesh.face_buff[:mesh.face_count * fs]))
        idx = [i[0] for i in idx]
        for t in range(0, len(idx) - 2, 3):
            a, b, c = idx[t], idx[t + 1], idx[t + 2]
            if a < n and b < n and c < n:
                sub.triangles.append((a, c, b))

        # --- morph targets ---------------------------------------------------
        if mesh.morph_targets:
            sub.morphs = mesh.morph_targets
        return sub


# --------------------------------------------------------------------------- #
# BSCM camera
# --------------------------------------------------------------------------- #

def _read_bscm(bs: BinaryReader, name: str) -> CameraAnim:
    bs.readMagic(4)
    bs.readUInt(); bs.readUInt()
    bs.readMagic(4)                        # version
    fps = bs.readFloat()
    end_frame = int(bs.readFloat())
    tran_count = bs.readInt()
    rot_count = bs.readInt()
    scl_count = bs.readInt()
    fov_count = bs.readInt()
    base_pos = Vec3(bs.readFloat(), bs.readFloat(), bs.readFloat())
    brx, bry, brz = bs.readFloat(), bs.readFloat(), bs.readFloat()
    from sifac_mathutil import euler_xyz_to_mat3, mat3_row_to_quat
    base_rot = mat3_row_to_quat(euler_xyz_to_mat3(Vec3(brx, bry, brz)))
    bs.readFloat(); bs.readFloat(); bs.readFloat()   # base scale
    base_fov = bs.readFloat()
    bs.readUInt()
    tran_off = bs.readUInt64()
    rot_off = bs.readUInt64()
    bs.readUInt64()                        # scale offset (unused for camera)
    fov_off = bs.readUInt64()

    cam = CameraAnim(name=name, end_frame=end_frame, fps=fps or 60.0,
                     base_pos=base_pos, base_rot=base_rot, base_fov=base_fov)
    if end_frame == 0:
        return cam
    bs.seek(tran_off, SEEK_ABS)
    for _ in range(tran_count):
        if bs.remaining() < 20:           # idx + pad + 3 floats
            break
        idx = bs.readUInt(); bs.readUInt()
        cam.translation.append((idx, Vec3(bs.readFloat(), bs.readFloat(), bs.readFloat())))
    bs.seek(rot_off, SEEK_ABS)
    for _ in range(rot_count):
        if bs.remaining() < 24:           # idx + pad + 4 floats
            break
        idx = bs.readUInt(); bs.readUInt()
        q = Quat(bs.readFloat(), bs.readFloat(), bs.readFloat(), bs.readFloat())
        cam.rotation.append((idx, q))
    bs.seek(fov_off, SEEK_ABS)
    for _ in range(fov_count):
        if bs.remaining() < 12:           # idx + pad + fov
            break
        idx = bs.readUInt(); bs.readUInt()
        cam.fov.append((idx, bs.readFloat()))
        # Two trailing tangent/padding floats follow each key, but the final
        # key omits them in some files — read only what is actually there so we
        # don't run 4 bytes off the end of the buffer.
        for _ in range(2):
            if bs.remaining() < 4:
                break
            bs.readFloat()
    return cam
