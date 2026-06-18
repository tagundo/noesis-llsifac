#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Round-trip self-tests for the native SIFAC extractor (no QuickBMS, no pytest).

Run:  python3 tools/tests/test_native.py

Builds synthetic .cmp and ARC files exactly per the format spec (derived from
LoveLive_*.bms and the QuickBMS source) and verifies the native parser decodes
them byte-for-byte, including the TYPE->extension rules and cmp->ARC chaining.
"""
import lzma
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sifac_native as N


def _align(p, a=0x20):
    r = p % a
    return p if r == 0 else p + (a - r)


def build_arc(files):
    """files: list of (type_tag, name, content) -> bytes of an ARC archive."""
    n = len(files)
    header = (b"ARC\x00" + b"\x01\x00\x02\x00" + struct.pack("<I", n)
              + struct.pack("<I", 0) + b"arc".ljust(0x20, b"\x00"))
    name_tbl = b""
    for t, nm, _c in files:
        tag = t.encode().ljust(4, b"\x00")[:4]
        nb = nm.encode("utf-8")
        e = tag + struct.pack("<HHIHHI", 1, 2, 3, 4, len(nb), 6) + nb
        e += b"\x00" * (_align(len(e)) - len(e))
        name_tbl += e
    nts = _align(0x30 + 4 * n)
    ds = _align(nts + len(name_tbl))
    offs, blocks, cur = [], b"", ds
    for _t, _nm, c in files:
        offs.append(cur)
        blk = struct.pack("<II", len(c), 8) + c
        blk += b"\x00" * (_align(len(blk)) - len(blk))
        blocks += blk
        cur += len(blk)
    buf = bytearray(header)
    buf += struct.pack("<%dI" % n, *offs)
    buf += b"\x00" * (nts - len(buf))
    buf += name_tbl
    buf += b"\x00" * (ds - len(buf))
    buf += blocks
    return bytes(buf)


def make_cmp(payload):
    """Wrap payload as a SIFAC cmp\\0 blob (LZMA, like LoveLive_CMP.bms)."""
    a = lzma.compress(payload, format=lzma.FORMAT_ALONE,
                      filters=[{"id": lzma.FILTER_LZMA1, "preset": 6}])
    blob = a[:5] + a[13:]   # 5 prop bytes + raw stream (drop the 8-byte size)
    return b"cmp\x00LZMA" + struct.pack("<II", len(blob), len(payload)) + blob


def test_cmp_roundtrip():
    payload = b"hello SIFAC " * 5000 + os.urandom(123)
    assert N.decompress_cmp(make_cmp(payload)) == payload
    print("[ok] .cmp LZMA round-trip")


def test_arc_extract():
    files = [
        ("mdl", "chara01", b"BMAR104\x00model" * 100),
        ("tex", "chara01_face", b"btx\x00tex" * 50),
        ("mot", "mot_live01", b"BMAR104\x00motion" * 80),
        ("dat", "", b"unnamed-data"),          # empty name -> "3.dat"
        ("tex", "already.btx", b"keepext"),     # extension already present
    ]
    arc = build_arc(files)
    names = [e.name for e in N.parse_arc(arc)]
    assert names == ["chara01.bmarc", "chara01_face.btx", "mot_live01.bmarc",
                     "3.dat", "already.btx"], names
    td = Path(tempfile.mkdtemp())
    written = N.extract_arc(arc, td, subfolder="myarc")
    got = {p.name: p.read_bytes() for p in written}
    for (_t, _nm, c), e in zip(files, N.parse_arc(arc)):
        assert got[e.name] == c, e.name
    print("[ok] ARC names + extensions + bytes")


def test_preset_filter():
    files = [("mdl", "m", b"a"), ("tex", "t", b"b"), ("mot", "mot_x", b"c")]
    arc = build_arc(files)
    td = Path(tempfile.mkdtemp())
    written = N.extract_arc(arc, td, name_filter=N.preset_predicate("models"))
    names = sorted(p.name for p in written)
    assert names == ["m.bmarc", "t.btx"], names   # motion excluded
    print("[ok] models preset excludes motions")


def test_cmp_arc_chain():
    arc = build_arc([("mdl", "c", b"BMAR104\x00" + b"x" * 999)])
    dec = N.decompress_cmp(make_cmp(arc))
    assert dec == arc and N.is_arc_bytes(dec)
    print("[ok] cmp->ARC chain")


if __name__ == "__main__":
    test_cmp_roundtrip()
    test_arc_extract()
    test_preset_filter()
    test_cmp_arc_chain()
    print("\nALL NATIVE TESTS PASSED ✓")
