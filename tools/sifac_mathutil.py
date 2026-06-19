#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_mathutil — tiny pure-Python linear algebra for the SIFAC converter
========================================================================

The Noesis plugin (``plugins/python/fmt_Blade_bmarc.py``) leans on Noesis'
``NoeVec3``/``NoeQuat``/``NoeMat43`` helpers.  This module reimplements just the
slice we need so the converter can run *without Noesis* (and therefore fast, in
batch, on any machine with stock Python 3).

Conventions
-----------
We deliberately mirror Noesis so the ported parsing math stays line-for-line
faithful:

* Matrices are 4x4, **row-major**, and transform **row vectors** on the left::

      p' = p · M           (p is a 1x4 row vector [x, y, z, 1])

  Row 3 (``m[3]``) is the translation, rows 0..2 are the basis (rotation*scale).
  This is exactly Noesis' ``NoeMat43`` memory order.

* Composing a bone hierarchy therefore reads ``world = local · parent_world``
  (this is what Noesis' ``rapi.multiplyBones`` does).

FBX, on the other hand, stores column-major matrices that transform column
vectors (``v' = M · v``).  The single conversion point is :func:`Mat4.fbx_array`,
which transposes on the way out — every other module stays in the Noesis
convention.
"""

from __future__ import annotations

import math
import struct
from typing import Iterable, List, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Half float
# --------------------------------------------------------------------------- #

def half_to_float(h: int) -> float:
    """Decode an IEEE-754 binary16 (stored in a uint16) to a Python float.

    Equivalent to Noesis' ``noesis.getFloat16``."""
    return struct.unpack("<e", struct.pack("<H", h & 0xFFFF))[0]


def float_to_half(f: float) -> int:
    """Encode a float as binary16, returned as a uint16."""
    return struct.unpack("<H", struct.pack("<e", f))[0]


# --------------------------------------------------------------------------- #
# Vectors
# --------------------------------------------------------------------------- #

class Vec3:
    __slots__ = ("x", "y", "z")

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = float(x); self.y = float(y); self.z = float(z)

    def __iter__(self):
        yield self.x; yield self.y; yield self.z

    def __getitem__(self, i):
        return (self.x, self.y, self.z)[i]

    def __add__(self, o): return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)
    def __sub__(self, o): return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)
    def __mul__(self, s): return Vec3(self.x * s, self.y * s, self.z * s)

    def __eq__(self, o):
        return (isinstance(o, Vec3) and self.x == o.x
                and self.y == o.y and self.z == o.z)

    def dot(self, o) -> float:
        return self.x * o.x + self.y * o.y + self.z * o.z

    def length(self) -> float:
        return math.sqrt(self.dot(self))

    def normalized(self) -> "Vec3":
        l = self.length()
        if l < 1e-12:
            return Vec3(0.0, 0.0, 0.0)
        return Vec3(self.x / l, self.y / l, self.z / l)

    def __repr__(self):
        return f"Vec3({self.x:.5f}, {self.y:.5f}, {self.z:.5f})"


# --------------------------------------------------------------------------- #
# Quaternion (x, y, z, w)
# --------------------------------------------------------------------------- #

class Quat:
    __slots__ = ("x", "y", "z", "w")

    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
        self.x = float(x); self.y = float(y); self.z = float(z); self.w = float(w)

    def __iter__(self):
        yield self.x; yield self.y; yield self.z; yield self.w

    def __getitem__(self, i):
        return (self.x, self.y, self.z, self.w)[i]

    def normalized(self) -> "Quat":
        n = math.sqrt(self.x * self.x + self.y * self.y
                      + self.z * self.z + self.w * self.w)
        if n < 1e-12:
            return Quat(0.0, 0.0, 0.0, 1.0)
        return Quat(self.x / n, self.y / n, self.z / n, self.w / n)

    def to_mat3(self) -> List[List[float]]:
        """Return the 3x3 rotation in **row-vector** convention (p' = p·R)."""
        q = self.normalized()
        x, y, z, w = q.x, q.y, q.z, q.w
        xx, yy, zz = x * x, y * y, z * z
        xy, xz, yz = x * y, x * z, y * z
        wx, wy, wz = w * x, w * y, w * z
        # Column-vector rotation Rc (v' = Rc·v):
        rc = [
            [1 - 2 * (yy + zz), 2 * (xy - wz),     2 * (xz + wy)],
            [2 * (xy + wz),     1 - 2 * (xx + zz), 2 * (yz - wx)],
            [2 * (xz - wy),     2 * (yz + wx),     1 - 2 * (xx + yy)],
        ]
        # Row-vector convention is the transpose.
        return [[rc[0][0], rc[1][0], rc[2][0]],
                [rc[0][1], rc[1][1], rc[2][1]],
                [rc[0][2], rc[1][2], rc[2][2]]]

    def __repr__(self):
        return f"Quat({self.x:.5f}, {self.y:.5f}, {self.z:.5f}, {self.w:.5f})"


# --------------------------------------------------------------------------- #
# 4x4 matrix (row-major, row-vector transforms)
# --------------------------------------------------------------------------- #

class Mat4:
    __slots__ = ("m",)

    def __init__(self, rows: Sequence[Sequence[float]] | None = None):
        if rows is None:
            self.m = [[1.0, 0.0, 0.0, 0.0],
                      [0.0, 1.0, 0.0, 0.0],
                      [0.0, 0.0, 1.0, 0.0],
                      [0.0, 0.0, 0.0, 1.0]]
        else:
            self.m = [[float(v) for v in row] for row in rows]

    # -- factories ----------------------------------------------------------- #

    @staticmethod
    def identity() -> "Mat4":
        return Mat4()

    @staticmethod
    def translation(t: Vec3) -> "Mat4":
        r = Mat4()
        r.m[3][0] = t.x; r.m[3][1] = t.y; r.m[3][2] = t.z
        return r

    @staticmethod
    def from_trs_noesis(pos: Vec3, euler_rad: Vec3, scl: Vec3) -> "Mat4":
        """Build a bone-local matrix exactly like the Noesis NODE reader.

        The plugin does::

            boneMtx = NoeAngles((rx, ry, rz)).toDegrees().toMat43_XYZ()
            boneMtx[0] *= sx ; boneMtx[1] *= sy ; boneMtx[2] *= sz
            boneMtx[3]  = pos

        ``NoeAngles`` are degrees, the file stores radians, and ``.toMat43_XYZ``
        composes the rotation as Rz·Ry·Rx on row vectors.  We reproduce that and
        then scale the basis rows and drop the translation into row 3.
        """
        rot = euler_xyz_to_mat3(euler_rad)          # row-vector 3x3
        r = Mat4()
        r.m[0] = [rot[0][0] * scl.x, rot[0][1] * scl.x, rot[0][2] * scl.x, 0.0]
        r.m[1] = [rot[1][0] * scl.y, rot[1][1] * scl.y, rot[1][2] * scl.y, 0.0]
        r.m[2] = [rot[2][0] * scl.z, rot[2][1] * scl.z, rot[2][2] * scl.z, 0.0]
        r.m[3] = [pos.x, pos.y, pos.z, 1.0]
        return r

    @staticmethod
    def from_mat3_row(rot: Sequence[Sequence[float]]) -> "Mat4":
        r = Mat4()
        for i in range(3):
            r.m[i][0], r.m[i][1], r.m[i][2] = rot[i][0], rot[i][1], rot[i][2]
        return r

    # -- arithmetic ---------------------------------------------------------- #

    def __mul__(self, o: "Mat4") -> "Mat4":
        a, b = self.m, o.m
        out = [[0.0] * 4 for _ in range(4)]
        for i in range(4):
            ai = a[i]
            for j in range(4):
                out[i][j] = (ai[0] * b[0][j] + ai[1] * b[1][j]
                             + ai[2] * b[2][j] + ai[3] * b[3][j])
        return Mat4(out)

    def transform_point(self, p: Vec3) -> Vec3:
        m = self.m
        return Vec3(
            p.x * m[0][0] + p.y * m[1][0] + p.z * m[2][0] + m[3][0],
            p.x * m[0][1] + p.y * m[1][1] + p.z * m[2][1] + m[3][1],
            p.x * m[0][2] + p.y * m[1][2] + p.z * m[2][2] + m[3][2],
        )

    def transform_vector(self, v: Vec3) -> Vec3:
        """Transform a direction (ignores translation)."""
        m = self.m
        return Vec3(
            v.x * m[0][0] + v.y * m[1][0] + v.z * m[2][0],
            v.x * m[0][1] + v.y * m[1][1] + v.z * m[2][1],
            v.x * m[0][2] + v.y * m[1][2] + v.z * m[2][2],
        )

    def translation_part(self) -> Vec3:
        return Vec3(self.m[3][0], self.m[3][1], self.m[3][2])

    def transposed(self) -> "Mat4":
        m = self.m
        return Mat4([[m[j][i] for j in range(4)] for i in range(4)])

    def inverse(self) -> "Mat4":
        """General 4x4 inverse (Gauss-Jordan)."""
        m = [row[:] for row in self.m]
        inv = [[float(i == j) for j in range(4)] for i in range(4)]
        for col in range(4):
            pivot = col
            best = abs(m[col][col])
            for r in range(col + 1, 4):
                if abs(m[r][col]) > best:
                    best = abs(m[r][col]); pivot = r
            if best < 1e-18:
                # Singular — fall back to identity rather than blowing up.
                return Mat4()
            if pivot != col:
                m[col], m[pivot] = m[pivot], m[col]
                inv[col], inv[pivot] = inv[pivot], inv[col]
            piv = m[col][col]
            for j in range(4):
                m[col][j] /= piv
                inv[col][j] /= piv
            for r in range(4):
                if r == col:
                    continue
                factor = m[r][col]
                if factor == 0.0:
                    continue
                for j in range(4):
                    m[r][j] -= factor * m[col][j]
                    inv[r][j] -= factor * inv[col][j]
        return Mat4(inv)

    # -- decomposition ------------------------------------------------------- #

    def decompose(self) -> Tuple[Vec3, "Vec3", Vec3]:
        """Return (translation, euler_degrees_XYZ, scale).

        Euler is extracted in the FBX default order (eEulerXYZ) so it can be
        written straight into ``Lcl Rotation``."""
        m = self.m
        trans = Vec3(m[3][0], m[3][1], m[3][2])
        row0 = Vec3(m[0][0], m[0][1], m[0][2])
        row1 = Vec3(m[1][0], m[1][1], m[1][2])
        row2 = Vec3(m[2][0], m[2][1], m[2][2])
        sx = row0.length(); sy = row1.length(); sz = row2.length()
        # Preserve handedness: a negative determinant means one axis is mirrored.
        det = (row0.x * (row1.y * row2.z - row1.z * row2.y)
               - row0.y * (row1.x * row2.z - row1.z * row2.x)
               + row0.z * (row1.x * row2.y - row1.y * row2.x))
        if det < 0:
            sx = -sx
        if sx == 0.0: sx = 1e-12
        if sy == 0.0: sy = 1e-12
        if sz == 0.0: sz = 1e-12
        rot = [[m[0][0] / sx, m[0][1] / sx, m[0][2] / sx],
               [m[1][0] / sy, m[1][1] / sy, m[1][2] / sy],
               [m[2][0] / sz, m[2][1] / sz, m[2][2] / sz]]
        euler = mat3_to_euler_xyz(rot)
        return trans, euler, Vec3(sx, sy, sz)

    def to_quat(self) -> Quat:
        """Extract a (normalized) rotation quaternion from the basis rows."""
        m = self.m
        row0 = Vec3(m[0][0], m[0][1], m[0][2]).normalized()
        row1 = Vec3(m[1][0], m[1][1], m[1][2]).normalized()
        row2 = Vec3(m[2][0], m[2][1], m[2][2]).normalized()
        return mat3_row_to_quat([list(row0), list(row1), list(row2)])

    def fbx_array(self) -> List[float]:
        """Flatten to the 16 doubles FBX expects.

        Our matrices are row-vector / row-major (``p' = p · M``, translation in
        row 3).  FBX stores column-vector / column-major matrices (``v' = M·v``,
        translation in the last *column* → flat indices 12,13,14).  The
        column-vector form of our ``M`` is ``Mᵀ``; flattening ``Mᵀ``
        column-major is the same as flattening ``M`` **row-major**, which puts
        ``m[3][0..2]`` at indices 12,13,14 exactly as FBX (and Blender) want."""
        m = self.m
        return [m[0][0], m[0][1], m[0][2], m[0][3],
                m[1][0], m[1][1], m[1][2], m[1][3],
                m[2][0], m[2][1], m[2][2], m[2][3],
                m[3][0], m[3][1], m[3][2], m[3][3]]

    def __repr__(self):
        return "Mat4(" + ", ".join(f"{v:.4f}" for row in self.m for v in row) + ")"


# --------------------------------------------------------------------------- #
# Euler <-> rotation matrix (row-vector convention, FBX eEulerXYZ order)
# --------------------------------------------------------------------------- #
#
# We use the XYZ Tait-Bryan order matching FBX's default eEulerXYZ.  Because the
# *same* order is used for the bind pose and every animation key, the rig and
# its motions stay self-consistent regardless of any global-orientation nuance.

def _rot_x_row(a: float):
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0], [0, c, s], [0, -s, c]]


def _rot_y_row(a: float):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, -s], [0, 1, 0], [s, 0, c]]


def _rot_z_row(a: float):
    c, s = math.cos(a), math.sin(a)
    return [[c, s, 0], [-s, c, 0], [0, 0, 1]]


def _mat3_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def euler_xyz_to_mat3(euler_rad: Vec3):
    """Euler (radians, XYZ) -> 3x3 row-vector rotation matrix.

    Row-vector composition ``p' = p · Rx · Ry · Rz`` corresponds to the
    column-vector matrix ``Rz·Ry·Rx`` (FBX eEulerXYZ)."""
    rx = _rot_x_row(euler_rad.x)
    ry = _rot_y_row(euler_rad.y)
    rz = _rot_z_row(euler_rad.z)
    return _mat3_mul(_mat3_mul(rx, ry), rz)


def mat3_to_euler_xyz(rot) -> Vec3:
    """Inverse of :func:`euler_xyz_to_mat3`; returns degrees.

    ``rot`` is a row-vector 3x3.  We read it through its column-vector transpose
    ``c`` (``c[i][j] = rot[j][i]``) and solve the standard XYZ decomposition.
    """
    # Column-vector form c = transpose(rot) = Rz·Ry·Rx, which expands to
    #   [ cz cy,            cz sy sx - sz cx,   cz sy cx + sz sx ]
    #   [ sz cy,            sz sy sx + cz cx,   sz sy cx - cz sx ]
    #   [ -sy,              cy sx,              cy cx            ]
    # so sin(y) = -c[2][0], and x,z come from the surrounding terms.
    c = [[rot[0][0], rot[1][0], rot[2][0]],
         [rot[0][1], rot[1][1], rot[2][1]],
         [rot[0][2], rot[1][2], rot[2][2]]]
    s20 = max(-1.0, min(1.0, c[2][0]))
    if s20 <= -0.9999999:        # y = +90°, gimbal lock
        y = math.pi / 2
        x = math.atan2(c[0][1], c[1][1])
        z = 0.0
    elif s20 >= 0.9999999:       # y = -90°, gimbal lock
        y = -math.pi / 2
        x = math.atan2(-c[0][1], c[1][1])
        z = 0.0
    else:
        y = -math.asin(s20)
        x = math.atan2(c[2][1], c[2][2])
        z = math.atan2(c[1][0], c[0][0])
    deg = 180.0 / math.pi
    return Vec3(x * deg, y * deg, z * deg)


def mat3_row_to_quat(rot) -> Quat:
    """Row-vector 3x3 -> quaternion (x, y, z, w)."""
    # Convert to column-vector form first.
    m = [[rot[0][0], rot[1][0], rot[2][0]],
         [rot[0][1], rot[1][1], rot[2][1]],
         [rot[0][2], rot[1][2], rot[2][2]]]
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    return Quat(x, y, z, w).normalized()


def quat_to_euler_xyz(q: Quat) -> Vec3:
    """Quaternion -> Euler degrees (XYZ), matching :func:`mat3_to_euler_xyz`."""
    return mat3_to_euler_xyz(q.to_mat3())


def euler_unwrap(prev_deg: Vec3, cur_deg: Vec3) -> Vec3:
    """Add multiples of 360° to ``cur`` so each channel stays near ``prev``.

    Keeps animation Euler curves continuous (no 359°→0° pops) which matters a
    lot for clean FBX rotation tracks."""
    out = []
    for p, cval in ((prev_deg.x, cur_deg.x),
                    (prev_deg.y, cur_deg.y),
                    (prev_deg.z, cur_deg.z)):
        while cval - p > 180.0:
            cval -= 360.0
        while cval - p < -180.0:
            cval += 360.0
        out.append(cval)
    return Vec3(out[0], out[1], out[2])
