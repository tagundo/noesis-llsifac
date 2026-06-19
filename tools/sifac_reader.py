#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_reader — a little-endian binary reader mirroring Noesis' NoeBitStream
===========================================================================

The SIFAC parsers were originally written against Noesis' ``NoeBitStream``.
This reader exposes the same method names (``readUInt``, ``readUInt64``,
``readFloat``, ``seek``/``tell`` with ABS/REL semantics, …) so the ported
parsing code reads almost identically to the Noesis plugin, just without the
Noesis dependency.
"""

from __future__ import annotations

import struct

SEEK_ABS = 0
SEEK_REL = 1


class BinaryReader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    # -- position ---------------------------------------------------------- #

    def tell(self) -> int:
        return self.pos

    def seek(self, off: int, whence: int = SEEK_ABS) -> None:
        self.pos = off if whence == SEEK_ABS else self.pos + off

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def align(self, pad: int) -> None:
        rem = self.pos % pad
        if rem:
            self.pos += pad - rem

    # -- primitives -------------------------------------------------------- #

    def read(self, n: int) -> bytes:
        b = self.data[self.pos:self.pos + n]
        self.pos += n
        return b

    readBytes = read

    def _unpack(self, fmt: str, size: int):
        v = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += size
        return v

    def readUByte(self) -> int:  return self._unpack("<B", 1)
    def readByte(self) -> int:   return self._unpack("<b", 1)
    def readUShort(self) -> int: return self._unpack("<H", 2)
    def readShort(self) -> int:  return self._unpack("<h", 2)
    def readUInt(self) -> int:   return self._unpack("<I", 4)
    def readInt(self) -> int:    return self._unpack("<i", 4)
    def readUInt64(self) -> int: return self._unpack("<Q", 8)
    def readInt64(self) -> int:  return self._unpack("<q", 8)
    def readFloat(self) -> float: return self._unpack("<f", 4)
    def readHalf(self) -> float: return self._unpack("<e", 2)

    def readMagic(self, n: int = 4) -> str:
        return self.read(n).split(b"\x00", 1)[0].decode("ascii", "replace")

    def readFixedString(self, n: int) -> str:
        return self.read(n).split(b"\x00", 1)[0].decode("ascii", "replace")

    def readCString(self) -> str:
        """Read a NUL-terminated string at the current position."""
        end = self.data.find(b"\x00", self.pos)
        if end < 0:
            end = len(self.data)
        s = self.data[self.pos:end].decode("utf-8", "replace")
        self.pos = end + 1
        return s

    def stringAt(self, off: int) -> str:
        """Read a NUL-terminated string at ``off`` without moving the cursor.

        Equivalent to the plugin's ``getOffString``."""
        end = self.data.find(b"\x00", off)
        if end < 0:
            end = len(self.data)
        return self.data[off:end].decode("utf-8", "replace")
