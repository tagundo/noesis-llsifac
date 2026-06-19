#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_fbx_encode — a from-scratch binary FBX 7.4 writer (and a reader for tests)
================================================================================

There is no dependency-free pure-Python FBX library, and Blender's importer
*rejects ASCII FBX* — so to give the converter a real, Blender/Unity/Maya
friendly target we encode **binary FBX, version 7400** ourselves.

The on-disk record format (the part importers actually parse) is:

    record := EndOffset:u32, NumProps:u32, PropListLen:u32,
              NameLen:u8, Name, Property*, [NestedRecord*, NullRecord(13B)]

A node that has children terminates its child list with a 13-byte null record;
the top-level record list is likewise null-terminated.  After that comes the
footer (importers don't validate it, but we write the standard layout so strict
SDK importers are happy too).

:class:`FBXNode` builds the tree; :func:`write_fbx` serialises it.  The little
:func:`parse_fbx` reader is here purely so the self-tests can round-trip what we
wrote and prove the structure is correct without needing real FBX tooling.
"""

from __future__ import annotations

import array
import struct
import zlib
from pathlib import Path
from typing import List

_HEADER_MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
_FILE_VERSION = 7400
_SENTINEL = b"\x00" * 13
_ARRAY_COMPRESS_THRESHOLD = 128

_FOOT_ID = bytes((0xfa, 0xbc, 0xab, 0x09, 0xd0, 0xc8, 0xd4, 0x66,
                  0xb1, 0x76, 0xfb, 0x83, 0x1c, 0xf7, 0x26, 0x7e))
_FOOT_MAGIC = bytes((0xf8, 0x5a, 0x8c, 0x6a, 0xde, 0xf5, 0xd9, 0x7e,
                     0xec, 0xe9, 0x0c, 0xe3, 0x75, 0xe2, 0x49, 0x5c))


class FBXNode:
    """One FBX record: a name, a list of typed properties, and child nodes."""

    __slots__ = ("name", "props", "children", "_end_offset", "_props_len")

    def __init__(self, name: str):
        self.name = name.encode("ascii") if isinstance(name, str) else name
        self.props: List[bytes] = []
        self.children: List["FBXNode"] = []
        self._end_offset = 0
        self._props_len = 0

    # -- child / property builders ---------------------------------------- #

    def add(self, child: "FBXNode") -> "FBXNode":
        self.children.append(child)
        return child

    def child(self, name: str, *props) -> "FBXNode":
        c = FBXNode(name)
        for p in props:
            c.prop(p)
        self.children.append(c)
        return c

    def prop(self, value) -> "FBXNode":
        """Append a property, picking the FBX type code from ``value``'s type."""
        if isinstance(value, bool):
            self.props.append(b"C" + struct.pack("<?", value))
        elif isinstance(value, int):
            # Default plain ints to 32-bit; use prop_i64 for explicit 64-bit.
            self.props.append(b"I" + struct.pack("<i", value))
        elif isinstance(value, float):
            self.props.append(b"D" + struct.pack("<d", value))
        elif isinstance(value, str):
            data = value.encode("utf-8")
            self.props.append(b"S" + struct.pack("<I", len(data)) + data)
        elif isinstance(value, bytes):
            self.props.append(b"R" + struct.pack("<I", len(value)) + value)
        else:
            raise TypeError(f"unsupported property type: {type(value)}")
        return self

    def prop_i16(self, v: int) -> "FBXNode":
        self.props.append(b"Y" + struct.pack("<h", v)); return self

    def prop_i32(self, v: int) -> "FBXNode":
        self.props.append(b"I" + struct.pack("<i", v)); return self

    def prop_i64(self, v: int) -> "FBXNode":
        self.props.append(b"L" + struct.pack("<q", v)); return self

    def prop_f32(self, v: float) -> "FBXNode":
        self.props.append(b"F" + struct.pack("<f", v)); return self

    def prop_f64(self, v: float) -> "FBXNode":
        self.props.append(b"D" + struct.pack("<d", v)); return self

    def prop_raw(self, data: bytes) -> "FBXNode":
        self.props.append(b"R" + struct.pack("<I", len(data)) + data); return self

    def _array(self, type_code: bytes, arr: "array.array") -> bytes:
        raw = arr.tobytes()
        if len(raw) > _ARRAY_COMPRESS_THRESHOLD:
            encoding = 1
            payload = zlib.compress(raw)
        else:
            encoding = 0
            payload = raw
        return (type_code + struct.pack("<III", len(arr), encoding, len(payload))
                + payload)

    def prop_f32_array(self, values) -> "FBXNode":
        self.props.append(self._array(b"f", array.array("f", values))); return self

    def prop_f64_array(self, values) -> "FBXNode":
        self.props.append(self._array(b"d", array.array("d", values))); return self

    def prop_i32_array(self, values) -> "FBXNode":
        self.props.append(self._array(b"i", array.array("i", values))); return self

    def prop_i64_array(self, values) -> "FBXNode":
        self.props.append(self._array(b"l", array.array("q", values))); return self

    # -- serialisation ----------------------------------------------------- #

    def _calc(self, offset: int) -> int:
        offset += 12 + 1 + len(self.name)
        self._props_len = sum(len(p) for p in self.props)
        offset += self._props_len
        if self.children:
            for c in self.children:
                offset = c._calc(offset)
            offset += len(_SENTINEL)
        self._end_offset = offset
        return offset

    def _write(self, out: bytearray) -> None:
        out += struct.pack("<III", self._end_offset, len(self.props), self._props_len)
        out += bytes((len(self.name),))
        out += self.name
        for p in self.props:
            out += p
        if self.children:
            for c in self.children:
                c._write(out)
            out += _SENTINEL


def write_fbx(path, roots: List[FBXNode]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(encode_fbx(roots))


def encode_fbx(roots: List[FBXNode]) -> bytes:
    out = bytearray()
    out += _HEADER_MAGIC
    out += struct.pack("<I", _FILE_VERSION)
    offset = len(out)
    for r in roots:
        offset = r._calc(offset)
    offset += len(_SENTINEL)            # top-level null record
    for r in roots:
        r._write(out)
    out += _SENTINEL
    # Footer.
    out += _FOOT_ID
    pad = (16 - (len(out) % 16)) % 16
    out += b"\x00" * pad
    out += struct.pack("<I", 0)
    out += struct.pack("<I", _FILE_VERSION)
    out += b"\x00" * 120
    out += _FOOT_MAGIC
    return bytes(out)


# --------------------------------------------------------------------------- #
# Minimal reader — test-only, mirrors the structure we emit above.
# --------------------------------------------------------------------------- #

class ParsedNode:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props, children):
        self.name = name
        self.props = props
        self.children = children

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def find_all(self, name):
        return [c for c in self.children if c.name == name]


def _read_property(data: bytes, pos: int):
    code = data[pos:pos + 1]; pos += 1
    if code == b"Y":
        v = struct.unpack_from("<h", data, pos)[0]; return v, pos + 2
    if code == b"C":
        v = struct.unpack_from("<?", data, pos)[0]; return v, pos + 1
    if code == b"I":
        v = struct.unpack_from("<i", data, pos)[0]; return v, pos + 4
    if code == b"F":
        v = struct.unpack_from("<f", data, pos)[0]; return v, pos + 4
    if code == b"D":
        v = struct.unpack_from("<d", data, pos)[0]; return v, pos + 8
    if code == b"L":
        v = struct.unpack_from("<q", data, pos)[0]; return v, pos + 8
    if code in (b"S", b"R"):
        ln = struct.unpack_from("<I", data, pos)[0]; pos += 4
        v = data[pos:pos + ln]; pos += ln
        return (v.decode("utf-8", "replace") if code == b"S" else v), pos
    if code in (b"f", b"d", b"i", b"l", b"b"):
        length, encoding, comp_len = struct.unpack_from("<III", data, pos); pos += 12
        raw = data[pos:pos + comp_len]; pos += comp_len
        if encoding == 1:
            raw = zlib.decompress(raw)
        tc = {b"f": "f", b"d": "d", b"i": "i", b"l": "q", b"b": "b"}[code]
        arr = array.array(tc); arr.frombytes(raw)
        return list(arr), pos
    raise ValueError(f"unknown property code {code!r} at {pos}")


def _read_node(data: bytes, pos: int):
    end_offset, num_props, prop_len = struct.unpack_from("<III", data, pos)
    if end_offset == 0:
        return None, pos + 13
    p = pos + 12
    name_len = data[p]; p += 1
    name = data[p:p + name_len].decode("ascii", "replace"); p += name_len
    props = []
    for _ in range(num_props):
        v, p = _read_property(data, p)
        props.append(v)
    children = []
    # Nested records (if any) run until end_offset; a null record terminates.
    while p < end_offset:
        child, p = _read_node(data, p)
        if child is None:
            break
        children.append(child)
    return ParsedNode(name, props, children), end_offset


def parse_fbx(data: bytes) -> List[ParsedNode]:
    if data[:len(_HEADER_MAGIC)] != _HEADER_MAGIC:
        raise ValueError("not a binary FBX")
    pos = len(_HEADER_MAGIC) + 4
    roots = []
    while True:
        node, pos = _read_node(data, pos)
        if node is None:
            break
        roots.append(node)
    return roots
