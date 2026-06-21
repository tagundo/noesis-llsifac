#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-tests for sifac_camera_inject (no UnityPy, no real game assets required).

Run:  python3 tools/tests/test_camera_inject.py

Checks the pure conversion logic that the bundle injector relies on:
quaternion -> Unity euler (ZXY) is the exact inverse of Unity's euler->quat,
the 60fps resampler preserves endpoints and frame math, and the dense
sample-array layout / z-flip behave as documented.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sifac_camera_inject as ci


def _euler_to_quat_unity(ex, ey, ez):
    rx, ry, rz = math.radians(ex) / 2, math.radians(ey) / 2, math.radians(ez) / 2
    qx = (math.sin(rx), 0, 0, math.cos(rx))
    qy = (0, math.sin(ry), 0, math.cos(ry))
    qz = (0, 0, math.sin(rz), math.cos(rz))

    def qm(a, b):
        ax, ay, az, aw = a
        bx, by, bz, bw = b
        return (aw * bx + ax * bw + ay * bz - az * by,
                aw * by - ax * bz + ay * bw + az * bx,
                aw * bz + ax * by - ay * bx + az * bw,
                aw * bw - ax * bx - ay * by - az * bz)

    return qm(qm(qy, qx), qz)  # Unity: Y * X * Z


def test_euler_roundtrip_matches_unity():
    # quat -> our euler -> quat must reproduce the rotation (sign-agnostic)
    import random
    rng = random.Random(0)
    worst = 0.0
    for _ in range(20000):
        e = (rng.uniform(-179, 179), rng.uniform(-179, 179), rng.uniform(-179, 179))
        q = _euler_to_quat_unity(*e)
        ex, ey, ez = ci.quat_to_unity_euler(q)
        q2 = _euler_to_quat_unity(ex, ey, ez)
        d = min(sum((a - b) ** 2 for a, b in zip(q, q2)),
                sum((a + b) ** 2 for a, b in zip(q, q2)))
        worst = max(worst, d)
    assert worst < 1e-5, worst


def test_identity_quat_is_zero_euler():
    ex, ey, ez = ci.quat_to_unity_euler((0.0, 0.0, 0.0, 1.0))
    assert abs(ex) < 1e-6 and abs(ey) < 1e-6 and abs(ez) < 1e-6


def _frames(n, fps=60.0):
    return [{"t": i / fps, "pos": [float(i), 0.0, 2.0 * i],
             "rot": [0.0, 0.0, 0.0, 1.0], "fov": 30.0 + i} for i in range(n)]


def test_resample_preserves_endpoints():
    fr = _frames(5)
    s = ci.resample_60(fr)
    assert len(s) == 5                       # already 60fps, 5 frames -> 5 samples
    assert abs(s[0][1][0] - 0.0) < 1e-6       # first pos.x
    assert abs(s[-1][1][2] - 8.0) < 1e-6      # last pos.z = 2*4
    assert abs(s[0][3] - 30.0) < 1e-6         # first fov


def test_sample_array_layout_and_zflip():
    fr = _frames(3)
    s = ci.resample_60(fr)
    # no flip: layout [px,py,pz, ex,ey,ez, fov]
    arr = ci.build_sample_array(s, z_flip=False)
    assert len(arr) == 3 * 7
    assert abs(arr[2] - 0.0) < 1e-6           # frame0 pz = 0
    assert abs(arr[7 * 2 + 2] - 4.0) < 1e-6   # frame2 pz = pos[2]=2*i, i=2 -> 4
    assert abs(arr[6] - 30.0) < 1e-6          # frame0 fov
    # z-flip: pz negated
    arrf = ci.build_sample_array(s, z_flip=True)
    assert abs(arrf[7 * 2 + 2] + 4.0) < 1e-6  # frame2 pz flipped -> -4


def test_scale_applies_to_position():
    fr = _frames(2)
    s = ci.resample_60(fr)
    arr = ci.build_sample_array(s, scale=2.0)
    # frame1 pos.x = 1 * scale 2 = 2
    assert abs(arr[7 + 0] - 2.0) < 1e-6


ALL_TESTS = [
    test_euler_roundtrip_matches_unity,
    test_identity_quat_is_zero_euler,
    test_resample_preserves_endpoints,
    test_sample_array_layout_and_zflip,
    test_scale_applies_to_position,
]


def main():
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            import traceback
            print("[FAIL] %s: %s" % (t.__name__, exc))
            traceback.print_exc()
    print("\n%d/%d tests passed" % (len(ALL_TESTS) - failures, len(ALL_TESTS)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
