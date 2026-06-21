#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-tests for sifac_camera (no pytest, no real game assets required).

Run:  python3 tools/tests/test_camera.py

Builds CameraAnim objects directly and checks the dense-track sampling: base
fallback, linear position/FOV interpolation, quaternion nlerp, frame timing,
and the JSON round-trip through convert-shaped output.
"""

import json
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sifac_camera as cam
from sifac_scene import CameraAnim
from sifac_mathutil import Vec3, Quat


def _approx(a, b, eps=1e-4):
    return abs(a - b) <= eps


def test_base_fallback():
    """Empty tracks -> every frame is the base pose/fov."""
    c = CameraAnim(name="c", end_frame=3, fps=60.0,
                   base_pos=Vec3(1.0, 2.0, 3.0), base_rot=Quat(0, 0, 0, 1),
                   base_fov=37.5)
    track = cam.camera_to_track(c)
    assert track["frame_count"] == 4
    for fr in track["frames"]:
        assert fr["pos"] == [1.0, 2.0, 3.0]
        assert fr["fov"] == 37.5
        assert fr["rot"] == [0.0, 0.0, 0.0, 1.0]
    print("[ok] empty tracks fall back to the base pose/fov")


def test_linear_position_and_fov():
    """A key at frame 0 and frame 10 -> midpoint at frame 5."""
    c = CameraAnim(name="c", end_frame=10, fps=10.0)
    c.translation = [(0, Vec3(0.0, 0.0, 0.0)), (10, Vec3(10.0, 20.0, -30.0))]
    c.fov = [(0, 40.0), (10, 60.0)]
    track = cam.camera_to_track(c)
    mid = track["frames"][5]
    assert _approx(mid["pos"][0], 5.0) and _approx(mid["pos"][1], 10.0) and _approx(mid["pos"][2], -15.0)
    assert _approx(mid["fov"], 50.0)
    assert _approx(mid["t"], 0.5)            # frame 5 @ 10fps
    # ends hold exactly
    assert track["frames"][0]["pos"] == [0.0, 0.0, 0.0]
    assert track["frames"][10]["pos"] == [10.0, 20.0, -30.0]
    print("[ok] linear position + fov interpolation and frame timing")


def test_quaternion_nlerp_is_unit_and_midpoint():
    """Halfway between identity and a 90deg-Z rotation is a unit 45deg-Z quat."""
    half = math.sqrt(0.5)
    c = CameraAnim(name="c", end_frame=2, fps=2.0)
    c.rotation = [(0, Quat(0, 0, 0, 1)), (2, Quat(0, 0, half, half))]  # 0 -> 90deg about Z
    track = cam.camera_to_track(c)
    q = track["frames"][1]["rot"]
    # unit length
    assert _approx(math.sqrt(sum(v * v for v in q)), 1.0)
    # 45deg about Z: (0,0,sin22.5,cos22.5)
    assert _approx(q[0], 0.0) and _approx(q[1], 0.0)
    assert _approx(q[2], math.sin(math.radians(22.5)), 1e-3)
    assert _approx(q[3], math.cos(math.radians(22.5)), 1e-3)
    print("[ok] rotation nlerp stays unit-length and hits the 45deg midpoint")


def test_hold_past_ends():
    """Frames before the first / after the last key hold those key values."""
    c = CameraAnim(name="c", end_frame=10, fps=10.0, base_fov=99.0)
    c.translation = [(3, Vec3(5.0, 5.0, 5.0)), (7, Vec3(9.0, 9.0, 9.0))]
    track = cam.camera_to_track(c)
    assert track["frames"][0]["pos"] == [5.0, 5.0, 5.0]   # before first key
    assert track["frames"][10]["pos"] == [9.0, 9.0, 9.0]  # after last key
    print("[ok] values hold past the first/last key")


def test_json_roundtrip_and_schema():
    with tempfile.TemporaryDirectory() as d:
        c = CameraAnim(name="cam_test", end_frame=5, fps=30.0,
                       base_pos=Vec3(0, 1, 0), base_rot=Quat(0, 0, 0, 1), base_fov=45.0)
        track = cam.camera_to_track(c)
        out = os.path.join(d, "cam.json")
        Path(out).write_text(json.dumps(track), encoding="utf-8")
        got = json.loads(Path(out).read_text(encoding="utf-8"))
        assert got["format"] == "sifac-camera/1"
        assert got["up_axis"] == "y"
        assert got["fps"] == 30.0
        assert len(got["frames"]) == 6
        assert set(got["frames"][0].keys()) == {"t", "pos", "rot", "fov"}
        print("[ok] JSON schema + round-trip")


def test_fps_resample_changes_timing_not_count():
    c = CameraAnim(name="c", end_frame=4, fps=60.0)
    t60 = cam.camera_to_track(c)
    t30 = cam.camera_to_track(c, fps=30.0)
    assert t60["frame_count"] == t30["frame_count"] == 5
    assert _approx(t30["frames"][4]["t"], 4 / 30.0)
    assert _approx(t60["frames"][4]["t"], 4 / 60.0)
    print("[ok] --fps changes frame timing, not frame count")


def _build_bscam_bytes():
    """A minimal valid BSCM: 3 frames, 2 translation keys, 2 fov keys, no rot."""
    import struct
    header = b"".join([
        b"BSCM",
        struct.pack("<II", 0, 0),
        b"v1\x00\x00",                       # version magic
        struct.pack("<f", 60.0),             # fps
        struct.pack("<f", 2.0),              # end_frame
        struct.pack("<iiii", 2, 0, 0, 2),    # tran, rot, scl, fov counts
        struct.pack("<fff", 0.0, 0.0, 0.0),  # base_pos
        struct.pack("<fff", 0.0, 0.0, 0.0),  # base rot (euler) -> identity quat
        struct.pack("<fff", 1.0, 1.0, 1.0),  # base scale
        struct.pack("<f", 45.0),             # base_fov
        struct.pack("<I", 0),                # padding uint
        struct.pack("<QQQQ", 116, 156, 156, 156),  # tran/rot/scl/fov offsets
    ])
    assert len(header) == 116, len(header)
    tran = (struct.pack("<IIfff", 0, 0, 0.0, 0.0, 0.0)
            + struct.pack("<IIfff", 2, 0, 2.0, 4.0, 6.0))
    fov = (struct.pack("<IIfff", 0, 0, 40.0, 0.0, 0.0)
           + struct.pack("<IIfff", 2, 0, 60.0, 0.0, 0.0))
    return header + tran + fov


def test_end_to_end_bscam_parse_and_convert():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "cam.bscam")
        Path(src).write_bytes(_build_bscam_bytes())
        out = os.path.join(d, "cam.json")
        cam.convert_file(src, out, verbose=False)
        track = json.loads(Path(out).read_text(encoding="utf-8"))
        assert track["frame_count"] == 3
        frames = track["frames"]
        assert frames[0]["pos"] == [0.0, 0.0, 0.0]
        assert frames[1]["pos"] == [1.0, 2.0, 3.0]     # midpoint of the 2 keys
        assert frames[2]["pos"] == [2.0, 4.0, 6.0]
        assert _approx(frames[1]["fov"], 50.0)         # 40 -> 60 midpoint
        # no rotation keys -> identity from the (0,0,0) euler base
        assert _approx(frames[0]["rot"][3], 1.0)
        print("[ok] end-to-end: synthetic .bscam -> parse -> dense track JSON")


ALL_TESTS = [
    test_base_fallback,
    test_linear_position_and_fov,
    test_quaternion_nlerp_is_unit_and_midpoint,
    test_hold_past_ends,
    test_json_roundtrip_and_schema,
    test_fps_resample_changes_timing_not_count,
    test_end_to_end_bscam_parse_and_convert,
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
