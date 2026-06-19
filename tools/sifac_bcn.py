#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_bcn — pure-Python texture decoders for SIFAC ``.btx``
===========================================================

Decodes the pixel formats the Noesis plugin handles in
``Btx.readBtx`` to straight RGBA8 (top row first), with no third-party
dependencies for the common cases:

* raw formats  : BGRA8, BGR8, BGR565, BGR5A1, BGRA4, R8
* block formats: BC1 (DXT1), BC2 (DXT3), BC3 (DXT5), BC4, BC5

BC6H/BC7 are intricate (eight modes, big partition tables) and impossible to
verify here without sample assets, so rather than ship an unvetted decoder we
route them through an *optional* accelerator if the user has one installed
(``texture2ddecoder`` — the de-facto package the asset-ripping community uses,
``pip install texture2ddecoder``).  When it is absent we raise
:class:`UnsupportedTextureFormat`; the converter treats that as non-fatal,
keeps the material's texture reference, and moves on.

All decoders return ``bytes`` of length ``width*height*4`` (RGBA8).
"""

from __future__ import annotations

import struct


class UnsupportedTextureFormat(Exception):
    """Raised when a format needs an optional decoder that is not installed."""


# --------------------------------------------------------------------------- #
# Raw (uncompressed) formats
# --------------------------------------------------------------------------- #

def decode_raw(data: bytes, width: int, height: int, fmt: str) -> bytes:
    """Decode an uncompressed buffer to RGBA8.

    ``fmt`` mirrors the channel-order strings the Noesis plugin passes to
    ``rapi.imageDecodeRaw``."""
    n = width * height
    out = bytearray(n * 4)
    if fmt == "b8g8r8a8":
        for i in range(n):
            b, g, r, a = data[i * 4:i * 4 + 4]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
    elif fmt == "b8g8r8":
        for i in range(n):
            b, g, r = data[i * 3:i * 3 + 3]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, 255))
    elif fmt == "b5g6r5":
        for i in range(n):
            v = data[i * 2] | (data[i * 2 + 1] << 8)
            r = (v >> 11) & 0x1F; g = (v >> 5) & 0x3F; b = v & 0x1F
            out[i * 4:i * 4 + 4] = bytes((
                (r << 3) | (r >> 2), (g << 2) | (g >> 4),
                (b << 3) | (b >> 2), 255))
    elif fmt in ("b5g5r5a1", "rbg5r5a1"):
        # The plugin's 0x03 case; treat as 5/5/5/1 little-endian.
        for i in range(n):
            v = data[i * 2] | (data[i * 2 + 1] << 8)
            r = (v >> 10) & 0x1F; g = (v >> 5) & 0x1F
            b = v & 0x1F; a = (v >> 15) & 0x1
            out[i * 4:i * 4 + 4] = bytes((
                (r << 3) | (r >> 2), (g << 3) | (g >> 2),
                (b << 3) | (b >> 2), 255 if a else 0))
    elif fmt == "b4g4r4a4":
        for i in range(n):
            v = data[i * 2] | (data[i * 2 + 1] << 8)
            b = v & 0xF; g = (v >> 4) & 0xF
            r = (v >> 8) & 0xF; a = (v >> 12) & 0xF
            out[i * 4:i * 4 + 4] = bytes((
                r * 17, g * 17, b * 17, a * 17))
    elif fmt == "r8":
        for i in range(n):
            r = data[i]
            out[i * 4:i * 4 + 4] = bytes((r, r, r, 255))
    else:
        raise UnsupportedTextureFormat(f"raw format {fmt!r}")
    return bytes(out)


# --------------------------------------------------------------------------- #
# Block helpers
# --------------------------------------------------------------------------- #

def _expand565(c: int):
    r = (c >> 11) & 0x1F; g = (c >> 5) & 0x3F; b = c & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _color_block(block: bytes, dxt1: bool):
    """Decode the 8-byte BC1-style color part into 16 (r,g,b,a) tuples."""
    c0 = block[0] | (block[1] << 8)
    c1 = block[2] | (block[3] << 8)
    r0, g0, b0 = _expand565(c0)
    r1, g1, b1 = _expand565(c1)
    pal = [(r0, g0, b0, 255), (r1, g1, b1, 255), None, None]
    if (not dxt1) or c0 > c1:
        pal[2] = ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255)
        pal[3] = ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255)
    else:
        pal[2] = ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255)
        pal[3] = (0, 0, 0, 0)        # transparent black in 1-bit-alpha mode
    bits = struct.unpack("<I", block[4:8])[0]
    texels = []
    for i in range(16):
        texels.append(pal[(bits >> (2 * i)) & 0x3])
    return texels


def _alpha_block(block: bytes):
    """Decode an 8-byte BC3/BC4-style interpolated alpha block -> 16 values."""
    a0 = block[0]; a1 = block[1]
    if a0 > a1:
        a = [a0, a1,
             (6 * a0 + 1 * a1) // 7, (5 * a0 + 2 * a1) // 7,
             (4 * a0 + 3 * a1) // 7, (3 * a0 + 4 * a1) // 7,
             (2 * a0 + 5 * a1) // 7, (1 * a0 + 6 * a1) // 7]
    else:
        a = [a0, a1,
             (4 * a0 + 1 * a1) // 5, (3 * a0 + 2 * a1) // 5,
             (2 * a0 + 3 * a1) // 5, (1 * a0 + 4 * a1) // 5,
             0, 255]
    idx_bits = int.from_bytes(block[2:8], "little")
    out = []
    for i in range(16):
        out.append(a[(idx_bits >> (3 * i)) & 0x7])
    return out


def _blit(out: bytearray, width: int, height: int, bx: int, by: int, texels):
    """Write a 4x4 decoded block of (r,g,b,a) into the output image."""
    for ty in range(4):
        y = by + ty
        if y >= height:
            break
        row = (y * width + bx) * 4
        for tx in range(4):
            x = bx + tx
            if x >= width:
                continue
            r, g, b, a = texels[ty * 4 + tx]
            o = row + tx * 4
            out[o] = r; out[o + 1] = g; out[o + 2] = b; out[o + 3] = a


def _iter_blocks(width: int, height: int):
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            yield bx, by


def decode_bc1(data: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    p = 0
    for bx, by in _iter_blocks(width, height):
        _blit(out, width, height, bx, by, _color_block(data[p:p + 8], True))
        p += 8
    return bytes(out)


def decode_bc2(data: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    p = 0
    for bx, by in _iter_blocks(width, height):
        alpha = data[p:p + 8]
        color = _color_block(data[p + 8:p + 16], False)
        abits = int.from_bytes(alpha, "little")
        texels = []
        for i in range(16):
            r, g, b, _ = color[i]
            a = (abits >> (4 * i)) & 0xF
            texels.append((r, g, b, a * 17))
        _blit(out, width, height, bx, by, texels)
        p += 16
    return bytes(out)


def decode_bc3(data: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 4)
    p = 0
    for bx, by in _iter_blocks(width, height):
        alpha = _alpha_block(data[p:p + 8])
        color = _color_block(data[p + 8:p + 16], False)
        texels = [(color[i][0], color[i][1], color[i][2], alpha[i])
                  for i in range(16)]
        _blit(out, width, height, bx, by, texels)
        p += 16
    return bytes(out)


def decode_bc4(data: bytes, width: int, height: int) -> bytes:
    """Single-channel block -> grayscale RGBA."""
    out = bytearray(width * height * 4)
    p = 0
    for bx, by in _iter_blocks(width, height):
        vals = _alpha_block(data[p:p + 8])
        texels = [(v, v, v, 255) for v in vals]
        _blit(out, width, height, bx, by, texels)
        p += 8
    return bytes(out)


def decode_bc5(data: bytes, width: int, height: int,
               reconstruct_z: bool = True) -> bytes:
    """Two-channel block (R,G) -> RGBA, reconstructing B as a normal's Z."""
    out = bytearray(width * height * 4)
    p = 0
    for bx, by in _iter_blocks(width, height):
        rvals = _alpha_block(data[p:p + 8])
        gvals = _alpha_block(data[p + 8:p + 16])
        texels = []
        for i in range(16):
            r = rvals[i]; g = gvals[i]
            if reconstruct_z:
                nx = r / 127.5 - 1.0
                ny = g / 127.5 - 1.0
                nz = max(0.0, 1.0 - nx * nx - ny * ny) ** 0.5
                b = int(round((nz + 1.0) * 127.5))
                b = 0 if b < 0 else (255 if b > 255 else b)
            else:
                b = 255
            texels.append((r, g, b, 255))
        _blit(out, width, height, bx, by, texels)
        p += 16
    return bytes(out)


# --------------------------------------------------------------------------- #
# BC6H / BC7 — optional accelerator
# --------------------------------------------------------------------------- #

def _try_texture2ddecoder(data, width, height, which):
    try:
        import texture2ddecoder  # type: ignore
    except Exception:
        return None
    fn = {
        "bc6h": texture2ddecoder.decode_bc6,
        "bc7": texture2ddecoder.decode_bc7,
    }[which]
    bgra = fn(data, width, height)
    # texture2ddecoder returns BGRA; swap to RGBA.
    out = bytearray(bgra)
    for i in range(0, len(out), 4):
        out[i], out[i + 2] = out[i + 2], out[i]
    return bytes(out)


def decode_bc7(data: bytes, width: int, height: int) -> bytes:
    res = _try_texture2ddecoder(data, width, height, "bc7")
    if res is not None:
        return res
    raise UnsupportedTextureFormat(
        "BC7 needs the optional 'texture2ddecoder' package "
        "(pip install texture2ddecoder)")


def decode_bc6h(data: bytes, width: int, height: int) -> bytes:
    res = _try_texture2ddecoder(data, width, height, "bc6h")
    if res is not None:
        return res
    raise UnsupportedTextureFormat(
        "BC6H needs the optional 'texture2ddecoder' package "
        "(pip install texture2ddecoder)")
