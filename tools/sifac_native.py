#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIFAC native extractor — no QuickBMS, no compilation
====================================================

Pure-Python reimplementation of the two SIFAC formats handled by the bundled
QuickBMS scripts, so the tool works on a Mac (or anywhere) without compiling
QuickBMS:

  * ``.cmp``  (magic ``cmp\\0``)  -> LZMA, exactly like ``LoveLive_CMP.bms``
                                      (``comtype lzma`` == COMP_LZMA /
                                      LZMA_FLAGS_NONE: 5-byte LZMA properties
                                      followed by the raw LZMA1 stream).
  * ``ARC``   (magic ``ARC\\0``)  -> archive of models/textures/motions, exactly
                                      like ``LoveLive_PAC.bms``.

The format details were derived directly from the QuickBMS source
(``unz.c`` / ``perform.c``) and from the .bms scripts, then verified with a
round-trip self-test (see ``tools/tests``). They are byte-compatible with what
QuickBMS produces.

Note: this reads each file fully into memory. That is fine for typical SIFAC
assets; for unusually huge archives, QuickBMS (streaming) is still available.
"""

from __future__ import annotations

import lzma
import os
import struct
from pathlib import Path
from typing import NamedTuple

ARC_MAGIC = b"ARC\x00"
CMP_MAGIC = b"cmp\x00"


# --------------------------------------------------------------------------- #
# .cmp  (LZMA)
# --------------------------------------------------------------------------- #

def decompress_cmp(data: bytes) -> bytes:
    """Decompress a SIFAC ``cmp\\0`` blob to its original bytes.

    Header layout (little-endian), matching LoveLive_CMP.bms::

        0x00  char[4]  "cmp\\0"
        0x04  char[4]  comtype tag (unused; the script hardcodes lzma)
        0x08  u32      ZSIZE  (compressed size, incl. the 5 prop bytes)
        0x0C  u32      SIZE   (uncompressed size)
        0x10  ...      5 LZMA property bytes + raw LZMA1 stream
    """
    if data[:4] != CMP_MAGIC:
        raise ValueError("not a cmp\\0 file")
    if len(data) < 0x10:
        raise ValueError("cmp header truncated")
    zsize, size = struct.unpack_from("<II", data, 0x08)
    blob = data[0x10:0x10 + zsize]
    if len(blob) < 5:
        raise ValueError("cmp payload too small")
    props, stream = blob[:5], blob[5:]

    # Primary path: raw LZMA1 with the properties parsed from the 5-byte header
    # (this is exactly what QuickBMS' LzmaDec does for COMP_LZMA).
    try:
        d = props[0]
        lc = d % 9
        d //= 9
        lp = d % 5
        pb = d // 5
        dict_size = struct.unpack_from("<I", props, 1)[0] or 1
        filt = [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                 "lc": lc, "lp": lp, "pb": pb}]
        dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filt)
        out = dec.decompress(stream, size)
        if len(out) == size:
            return out
    except (lzma.LZMAError, ValueError, IndexError):
        pass

    # Fallback: rebuild a legacy "LZMA alone" header (props + 8-byte size).
    alone = props + struct.pack("<Q", size) + stream
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    out = dec.decompress(alone, size)
    if len(out) < size:
        out += dec.decompress(b"", size - len(out))
    return out


# --------------------------------------------------------------------------- #
# ARC archive
# --------------------------------------------------------------------------- #

class ArcEntry(NamedTuple):
    name: str
    offset: int   # absolute offset of the file data
    size: int


def _align(pos: int, n: int = 0x20) -> int:
    """quickbms `padding n`: advance to the next multiple of n."""
    rem = pos % n
    return pos if rem == 0 else pos + (n - rem)


# TYPE tag -> (extension, set of already-acceptable extensions) from LoveLive_PAC.bms
_TYPE_EXT = {
    "acc":  (".pac",  (".pac",)),
    "bin":  (".bin",  (".bin", ".pac")),
    "dat":  (".dat",  (".dat",)),
    "dyns": (".pac",  (".pac",)),
    "efx":  (".efx",  (".efxa", ".efx")),
    "mdl":  (".bmarc", (".bmarc",)),
    "mot":  (".bmarc", (".bmarc",)),
    "SARC": (".sarc", (".sarc",)),
    "SHG":  (".shg",  (".shg",)),
    "SHP":  (".shp",  (".shp",)),
    "tex":  (".btx",  (".btx",)),
    "texs": (".pac",  (".pac",)),
}


def _apply_ext(name: str, type_tag: str) -> str:
    spec = _TYPE_EXT.get(type_tag)
    if not spec:
        return name
    ext, ok = spec
    low = name.lower()
    if any(low.endswith(e) for e in ok):
        return name
    return name + ext


def parse_arc(data: bytes) -> list[ArcEntry]:
    """Parse a SIFAC ``ARC\\0`` archive into a list of entries.

    Mirrors LoveLive_PAC.bms exactly (little-endian)."""
    if data[:4] != ARC_MAGIC:
        raise ValueError("not an ARC\\0 archive")
    # Header: magic(4) UNK1(2) UNK2(2) FILES(4) UNK3(4) FNAME(0x20) -> ends 0x30
    files = struct.unpack_from("<I", data, 0x08)[0]
    pos = 0x30
    if files < 0 or files > 10_000_000:
        raise ValueError(f"implausible file count: {files}")

    offsets = list(struct.unpack_from("<%dI" % files, data, pos))
    pos += 4 * files
    pos = _align(pos, 0x20)

    names: list[str] = []
    for i in range(files):
        type_tag = data[pos:pos + 4].split(b"\x00", 1)[0].decode("ascii", "replace")
        pos += 4
        # UNK1 u16, IDX u16, HASH u32, UNK2 u16, NSIZE u16, UNK3 u32  (16 bytes)
        # NSIZE sits at +10 (2+2+4+2), name data starts after the full 16.
        nsize = struct.unpack_from("<H", data, pos + 10)[0]
        pos += 16
        raw = data[pos:pos + nsize]
        pos += nsize
        name = raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
        if name == "":
            name = str(i)
        name = _apply_ext(name, type_tag)
        names.append(name)
        pos = _align(pos, 0x20)

    entries: list[ArcEntry] = []
    for i in range(files):
        base = offsets[i]
        fsize, foffset = struct.unpack_from("<II", data, base)
        entries.append(ArcEntry(names[i], base + foffset, fsize))
    return entries


def extract_arc(data: bytes, out_dir: Path, subfolder: str | None = None,
                name_filter=None) -> list[Path]:
    """Extract every entry of an ARC archive under ``out_dir``.

    Files are written to ``out_dir/<subfolder>/<name>`` to mirror QuickBMS,
    which nests extracted files in a folder named after the archive.
    ``name_filter`` is an optional ``name -> bool`` predicate; entries for
    which it returns False are skipped."""
    entries = parse_arc(data)
    base = Path(out_dir)
    if subfolder:
        base = base / _safe(subfolder)
    written: list[Path] = []
    for e in entries:
        if name_filter is not None and not name_filter(e.name):
            continue
        chunk = data[e.offset:e.offset + e.size]
        dest = base / _safe(e.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(chunk)
        written.append(dest)
    return written


def preset_predicate(preset: str | None):
    """Return a ``name -> bool`` filter for a content preset (models/live/...).

    Native equivalent of the QuickBMS ``-f`` presets in sifac_extract.py."""
    if not preset or preset == "all":
        return None
    if preset == "models":
        exts = (".bmarc", ".btx", ".pac", ".shp", ".shg")
        return lambda n: n.lower().endswith(exts) and "mot_" not in n.lower()
    if preset == "live":
        return lambda n: ("mot_" in n.lower()
                          or n.lower().endswith((".bscam", ".efx", ".efxa")))
    if preset == "textures":
        return lambda n: n.lower().endswith((".btx", ".pac"))
    return None


def _safe(name: str) -> str:
    """Keep a relative, traversal-free path."""
    name = name.replace("\\", "/")
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    return os.path.join(*parts) if parts else "_unnamed"


# --------------------------------------------------------------------------- #
# Sniffers (shared with the engine)
# --------------------------------------------------------------------------- #

def is_arc_bytes(data: bytes) -> bool:
    return data[:4] == ARC_MAGIC


def is_cmp_bytes(data: bytes) -> bool:
    return data[:4] == CMP_MAGIC


if __name__ == "__main__":
    # Tiny CLI: native_extract IN OUT  (decompress .cmp and/or extract ARC)
    import sys
    if len(sys.argv) != 3:
        print("usage: sifac_native.py <input_file> <output_dir>")
        raise SystemExit(2)
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    raw = src.read_bytes()
    out.mkdir(parents=True, exist_ok=True)
    if is_cmp_bytes(raw):
        raw = decompress_cmp(raw)
        if not is_arc_bytes(raw):
            (out / src.name).write_bytes(raw)
            print("decompressed ->", out / src.name)
            raise SystemExit(0)
    if is_arc_bytes(raw):
        files = extract_arc(raw, out, subfolder=src.stem)
        print(f"extracted {len(files)} file(s) -> {out / src.stem}")
    else:
        print("not a SIFAC cmp/ARC file")
        raise SystemExit(1)
