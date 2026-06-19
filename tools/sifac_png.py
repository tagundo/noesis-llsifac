#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_png — minimal, dependency-free PNG writer
===============================================

The converter decodes SIFAC ``.btx`` textures to raw RGBA and needs to save
them next to the FBX so Blender/Unity can pick them up.  Rather than pull in
Pillow (an install the existing tools deliberately avoid), we write PNG with
nothing but the standard-library ``zlib`` and ``struct``.

Only what we need: 8-bit RGBA / RGB / grayscale, no interlacing, a single
IDAT.  That is plenty for game textures and is read by every image tool.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _filter_none(raw: bytes, width: int, height: int, channels: int) -> bytes:
    """Prepend a 0 (filter type None) byte to each scanline."""
    stride = width * channels
    out = bytearray()
    mv = memoryview(raw)
    for y in range(height):
        out.append(0)
        out += mv[y * stride:(y + 1) * stride]
    return bytes(out)


def encode_png(rgba: bytes, width: int, height: int, channels: int = 4,
               level: int = 6) -> bytes:
    """Encode raw pixel bytes to an in-memory PNG.

    ``channels`` is 1 (gray), 3 (RGB) or 4 (RGBA); ``rgba`` must be exactly
    ``width * height * channels`` bytes, top row first."""
    if channels not in (1, 3, 4):
        raise ValueError(f"unsupported channel count: {channels}")
    expected = width * height * channels
    if len(rgba) != expected:
        raise ValueError(f"pixel buffer is {len(rgba)} bytes, expected {expected}")
    color_type = {1: 0, 3: 2, 4: 6}[channels]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    filtered = _filter_none(rgba, width, height, channels)
    idat = zlib.compress(filtered, level)
    return (_PNG_SIG
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", idat)
            + _chunk(b"IEND", b""))


def write_png(path, rgba: bytes, width: int, height: int,
              channels: int = 4, level: int = 6) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(encode_png(rgba, width, height, channels, level))


# --------------------------------------------------------------------------- #
# A tiny reader, used only by the self-tests to validate what we wrote.
# --------------------------------------------------------------------------- #

def read_png_info(data: bytes):
    """Return (width, height, bitdepth, color_type) from a PNG header, or None."""
    if data[:8] != _PNG_SIG:
        return None
    if data[12:16] != b"IHDR":
        return None
    width, height, bitdepth, color_type = struct.unpack(">IIBB", data[16:26])
    return width, height, bitdepth, color_type


def decode_png(data: bytes):
    """Decode a PNG produced by :func:`encode_png` back to (w, h, ch, bytes).

    Intentionally minimal: handles the filter types stock zlib + the standard
    Paeth/up/avg/sub predictors emit, so the round-trip test is meaningful even
    if a future change raises the compression level / changes filtering."""
    info = read_png_info(data)
    if info is None:
        raise ValueError("not a PNG")
    width, height, bitdepth, color_type = info
    if bitdepth != 8:
        raise ValueError("only 8-bit PNG supported by this reader")
    channels = {0: 1, 2: 3, 6: 4}[color_type]
    # Gather IDAT.
    pos = 8
    idat = bytearray()
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if tag == b"IDAT":
            idat += chunk
        elif tag == b"IEND":
            break
        pos += 12 + ln
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        ftype = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        if ftype == 1:    # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, channels, bytes(out)
