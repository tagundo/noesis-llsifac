#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_btx — decode SIFAC ``.btx`` textures to RGBA / PNG
========================================================

A faithful, Noesis-free port of ``Btx.readBtx`` from
``plugins/python/fmt_Blade_bmarc.py``.  Produces a :class:`Texture` (raw RGBA8)
that the converter saves as a PNG next to the FBX.
"""

from __future__ import annotations

from dataclasses import dataclass

import sifac_bcn as bcn
from sifac_png import write_png
from sifac_reader import BinaryReader, SEEK_ABS


@dataclass
class Texture:
    name: str
    width: int
    height: int
    rgba: bytes            # width*height*4, RGBA8, top row first
    cube: bool = False


# texFormat byte -> (kind, raw-format-string-or-None, bytes-per-pixel-or-None)
_RAW_FORMATS = {
    0x00: ("b8g8r8a8", 4),
    0x01: ("b8g8r8", 3),
    0x02: ("b5g6r5", 2),
    0x03: ("b5g5r5a1", 2),
    0x04: ("b4g4r4a4", 2),
    0x10: ("r8", 1),
}


def decode_btx(data: bytes) -> Texture:
    bs = BinaryReader(data)
    magic = bs.readMagic(4)
    if magic != "btx":
        raise ValueError("not a btx texture")
    bs.readUInt()                       # 0x04 unk1
    width = bs.readUShort()             # 0x08
    height = bs.readUShort()            # 0x0A
    bs.readUInt()                       # 0x0C unk2
    tex_format = bs.readUByte()         # 0x10
    bs.readUByte()                      # 0x11 unkFlag2
    bs.readByte()                       # 0x12 mipCount
    bs.read(4)                          # 0x13 unkFlag3..6
    cube_flag = bs.readUByte()          # 0x17 cubeFlag
    data_off = bs.readUInt()            # 0x18
    bs.readUInt()                       # 0x1C unk
    bs.readUInt()                       # 0x20 unk3
    name = bs.stringAt(bs.readUInt())   # 0x24 name offset
    bs.seek(data_off, SEEK_ABS)
    payload = data[data_off:]

    if cube_flag:
        # 6 BC7 faces; decode face 0 as the representative 2D image.
        rgba = bcn.decode_bc7(payload[:width * height], width, height)
        return Texture(name or "cube", width, height, rgba, cube=True)

    rgba = _decode_2d(payload, width, height, tex_format)
    return Texture(name or "tex", width, height, rgba)


def _decode_2d(payload: bytes, width: int, height: int, fmt: int) -> bytes:
    if fmt in _RAW_FORMATS:
        rawfmt, bpp = _RAW_FORMATS[fmt]
        return bcn.decode_raw(payload[:width * height * bpp], width, height, rawfmt)
    if fmt == 0x07:
        return bcn.decode_bc1(payload, width, height)
    if fmt == 0x08:
        return bcn.decode_bc2(payload, width, height)
    if fmt == 0x09:
        return bcn.decode_bc3(payload, width, height)
    if fmt == 0x0A:
        return bcn.decode_bc4(payload, width, height)
    if fmt == 0x0B:
        return bcn.decode_bc5(payload, width, height)
    if fmt == 0x0C:
        return bcn.decode_bc6h(payload, width, height)
    if fmt == 0x0D:
        return bcn.decode_bc7(payload, width, height)
    raise bcn.UnsupportedTextureFormat(f"btx texFormat 0x{fmt:02X}")


def save_texture(tex: Texture, path) -> None:
    write_png(path, tex.rgba, tex.width, tex.height, channels=4)
