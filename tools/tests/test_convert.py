#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-tests for the SIFAC converter (no pytest, no real game assets required).

Run:  python3 tools/tests/test_convert.py

Validates the pieces we can verify in isolation:
  * math round-trips (euler<->matrix, quaternion, inverse),
  * PNG encode/decode,
  * raw + BC1 texture decode,
  * the from-scratch binary-FBX encoder, round-tripped through its own reader,
  * and the full scene->FBX builder on a tiny hand-built model + animation,
    re-parsing the FBX to confirm the object graph and the geometry survive.
"""

import math
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sifac_bcn as bcn
import sifac_fbx
import sifac_fbx_encode as fbxenc
import sifac_mathutil as mu
import sifac_png as png
from sifac_scene import (AnimTrack, Animation, Bone, Material, MorphTarget,
                         Model, SubMesh)

_FAILS = []


def check(cond, msg):
    if cond:
        print(f"  ok  {msg}")
    else:
        print(f"FAIL  {msg}")
        _FAILS.append(msg)


def approx(a, b, eps=1e-3):
    return abs(a - b) <= eps


# --------------------------------------------------------------------------- #

def test_math():
    print("math:")
    # Euler -> matrix -> Euler round-trip across a spread of angles.
    worst = 0.0
    for ax in (-120, -30, 0, 25, 80):
        for ay in (-75, -10, 15, 60):
            for az in (-150, -45, 5, 95):
                e = mu.Vec3(math.radians(ax), math.radians(ay), math.radians(az))
                m = mu.euler_xyz_to_mat3(e)
                back = mu.mat3_to_euler_xyz(m)
                m2 = mu.euler_xyz_to_mat3(mu.Vec3(math.radians(back.x),
                                                  math.radians(back.y),
                                                  math.radians(back.z)))
                for i in range(3):
                    for j in range(3):
                        worst = max(worst, abs(m[i][j] - m2[i][j]))
    check(worst < 1e-5, f"euler<->matrix round-trip (worst {worst:.2e})")

    # Quaternion -> matrix -> quaternion (via euler) stays a rotation.
    q = mu.Quat(0.1, -0.2, 0.3, 0.9).normalized()
    e = mu.quat_to_euler_xyz(q)
    m = mu.euler_xyz_to_mat3(mu.Vec3(math.radians(e.x), math.radians(e.y),
                                     math.radians(e.z)))
    q2 = mu.mat3_row_to_quat(m)
    # q and q2 may differ by sign; compare |dot| ~ 1.
    dot = abs(q.x * q2.x + q.y * q2.y + q.z * q2.z + q.w * q2.w)
    check(approx(dot, 1.0, 1e-4), f"quat->euler->quat preserves rotation (dot {dot:.5f})")

    # 4x4 inverse.
    t = mu.Mat4.from_trs_noesis(mu.Vec3(3, -2, 5),
                                mu.Vec3(0.3, -0.5, 1.1),
                                mu.Vec3(1, 1, 1))
    inv = t.inverse()
    prod = t * inv
    err = max(abs(prod.m[i][j] - (1.0 if i == j else 0.0))
              for i in range(4) for j in range(4))
    check(err < 1e-5, f"Mat4 inverse (identity error {err:.2e})")

    # Hierarchy compose then transform a point.
    parent = mu.Mat4.translation(mu.Vec3(10, 0, 0))
    child = mu.Mat4.translation(mu.Vec3(0, 5, 0))
    world = child * parent
    p = world.transform_point(mu.Vec3(0, 0, 0))
    check(approx(p.x, 10) and approx(p.y, 5),
          f"hierarchy compose places child at (10,5,0) -> ({p.x:.1f},{p.y:.1f})")


def test_png():
    print("png:")
    w, h = 7, 5
    buf = bytearray()
    for i in range(w * h):
        buf += bytes((i * 3 % 256, i * 5 % 256, i * 7 % 256, (i * 11) % 256))
    data = png.encode_png(bytes(buf), w, h, 4)
    info = png.read_png_info(data)
    check(info == (w, h, 8, 6), f"PNG header {info}")
    ww, hh, ch, out = png.decode_png(data)
    check((ww, hh, ch) == (w, h, 4) and out == bytes(buf),
          "PNG encode/decode round-trip is lossless")


def test_bcn():
    print("bcn:")
    # Raw BGRA -> RGBA channel swap.
    src = bytes((10, 20, 30, 40))            # b,g,r,a
    out = bcn.decode_raw(src, 1, 1, "b8g8r8a8")
    check(out == bytes((30, 20, 10, 40)), "raw b8g8r8a8 swizzles to RGBA")

    # BC1 block, single colour c0==c1 (so palette[0] is that colour).
    # 565 for pure red = 0xF800.
    block = struct.pack("<HH", 0xF800, 0xF800) + struct.pack("<I", 0)
    rgba = bcn.decode_bc1(block, 4, 4)
    r, g, b, a = rgba[0], rgba[1], rgba[2], rgba[3]
    check(r > 240 and g < 16 and b < 16 and a == 255,
          f"BC1 decodes pure red ({r},{g},{b})")


def test_fbx_encoder():
    print("fbx encoder:")
    root = fbxenc.FBXNode("Objects")
    geo = root.child("Geometry")
    geo.prop_i64(123)
    geo.prop("Cube\x00\x01Geometry")
    geo.prop("Mesh")
    verts = [float(i) * 0.5 for i in range(300)]   # > threshold -> zlib path
    geo.child("Vertices").prop_f64_array(verts)
    geo.child("PolygonVertexIndex").prop_i32_array([0, 1, ~2])
    small = root.child("Small")
    small.prop_i32(7); small.prop("hello"); small.prop_f64(3.5)

    data = fbxenc.encode_fbx([root])
    check(data[:21] == b"Kaydara FBX Binary  \x00", "FBX header magic present")
    check(data[-16:] == fbxenc._FOOT_MAGIC, "FBX footer magic present")

    parsed = fbxenc.parse_fbx(data)
    check(len(parsed) == 1 and parsed[0].name == "Objects", "root parses back")
    pgeo = parsed[0].find("Geometry")
    check(pgeo is not None, "Geometry node found")
    check(pgeo.props[0] == 123, "i64 property round-trips")
    check(pgeo.props[1] == "Cube\x00\x01Geometry", "object name string round-trips")
    pv = pgeo.find("Vertices")
    check(pv is not None and len(pv.props[0]) == 300
          and approx(pv.props[0][299], verts[299]),
          "compressed f64 array round-trips")
    ppi = pgeo.find("PolygonVertexIndex")
    check(ppi.props[0] == [0, 1, -3], "i32 array (with ~index) round-trips")
    psmall = parsed[0].find("Small")
    check(psmall.props == [7, "hello", 3.5], "mixed small props round-trip")


def _toy_model() -> Model:
    """Two-bone skeleton, one skinned quad (two triangles), a material, a morph."""
    root = Bone(0, "Root", None, mu.Mat4.identity())
    child = Bone(1, "Bone1", "Root", mu.Mat4.translation(mu.Vec3(0, 1, 0)))
    bones = [root, child]
    # Resolve world matrices the way the parser does.
    root.parent_index = -1; root.world_matrix = root.local_matrix
    child.parent_index = 0
    child.world_matrix = child.local_matrix * root.world_matrix

    mat = Material("Skin", diffuse_tex="face")
    sub = SubMesh("quad", "Skin")
    sub.positions = [mu.Vec3(0, 0, 0), mu.Vec3(1, 0, 0),
                     mu.Vec3(1, 1, 0), mu.Vec3(0, 1, 0)]
    sub.normals = [mu.Vec3(0, 0, 1)] * 4
    sub.uvs = [(0, 0), (1, 0), (1, 1), (0, 1)]
    sub.colors = [(255, 255, 255, 255)] * 4
    sub.triangles = [(0, 1, 2), (0, 2, 3)]
    sub.skin = [[(0, 1.0)], [(0, 0.5), (1, 0.5)],
                [(1, 1.0)], [(0, 0.5), (1, 0.5)]]
    sub.morphs = [MorphTarget("smile",
                              position_deltas=[mu.Vec3(0, 0, 0), mu.Vec3(0, 0, 0),
                                               mu.Vec3(0.1, 0, 0), mu.Vec3(0, 0.1, 0)],
                              normal_deltas=[mu.Vec3(0, 0, 0)] * 4)]
    m = Model("toy", bones=bones, materials=[mat], submeshes=[sub])
    m.referenced_textures = ["face"]
    return m


def _toy_anim() -> Animation:
    anim = Animation("wave", end_frame=2, fps=30.0)
    tr = AnimTrack("Bone1")
    tr.rotation = [(0, mu.Quat(0, 0, 0, 1)),
                   (1, mu.Quat(0, 0, 0.2588, 0.9659)),
                   (2, mu.Quat(0, 0, 0.5, 0.866))]
    tr.translation = [(0, mu.Vec3(0, 1, 0)), (2, mu.Vec3(0, 1.5, 0))]
    anim.tracks.append(tr)
    return anim


def test_builder():
    print("fbx builder:")
    model = _toy_model()
    anim = _toy_anim()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "toy.fbx"
        sifac_fbx.write_model_fbx(out, model, [anim], scale=1.0,
                                  include_morphs=True)
        check(out.exists() and out.stat().st_size > 0, "model FBX written")
        data = out.read_bytes()
        roots = fbxenc.parse_fbx(data)
        names = {r.name for r in roots}
        check({"FBXHeaderExtension", "GlobalSettings", "Definitions",
               "Objects", "Connections"} <= names,
              "all required top-level sections present")
        objs = next(r for r in roots if r.name == "Objects")
        models = objs.find_all("Model")
        limb = [m for m in models if m.props[2] == "LimbNode"]
        mesh = [m for m in models if m.props[2] == "Mesh"]
        check(len(limb) == 2, f"two LimbNode bones ({len(limb)})")
        check(len(mesh) == 1, f"one Mesh model ({len(mesh)})")
        geos = objs.find_all("Geometry")
        gmesh = [g for g in geos if g.props[2] == "Mesh"]
        check(len(gmesh) == 1, "one mesh Geometry")
        verts = gmesh[0].find("Vertices").props[0]
        check(len(verts) == 12, f"4 control points -> 12 doubles ({len(verts)})")
        deformers = objs.find_all("Deformer")
        kinds = [d.props[2] for d in deformers]
        check("Skin" in kinds, "Skin deformer present")
        check("Cluster" in kinds, "Cluster sub-deformer present")
        check("BlendShape" in kinds, "BlendShape deformer present")
        check(len(objs.find_all("AnimationStack")) == 1, "one AnimationStack")
        check(len(objs.find_all("AnimationLayer")) == 1, "one AnimationLayer")
        check(len(objs.find_all("AnimationCurveNode")) >= 2,
              "animation curve nodes present")
        check(len(objs.find_all("AnimationCurve")) >= 6,
              "animation curves present (>=6 for T+R channels)")
        conns = next(r for r in roots if r.name == "Connections")
        check(len(conns.find_all("C")) > 0, "connections present")

        # A cluster's TransformLink should equal Bone1's world bind matrix.
        clusters = [d for d in deformers if d.props[2] == "Cluster"]
        link_ok = False
        b1_world = model.bones[1].world_matrix.fbx_array()
        for c in clusters:
            tl = c.find("TransformLink")
            if tl and all(approx(tl.props[0][i], b1_world[i], 1e-4)
                          for i in range(16)):
                link_ok = True
        check(link_ok, "a cluster TransformLink matches Bone1 bind world matrix")

        # Blender reconstructs each mesh bind as TransformLink · Transform and
        # assumes it's the same for every cluster — it must be identity, or the
        # mesh scatters into a blob.  (Regression guard for the skin fix.)
        def _m4(arr):  # column-major flat -> [row][col]
            return [[arr[c * 4 + r] for c in range(4)] for r in range(4)]
        def _mul(a, b):
            return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
                    for r in range(4)]
        bind_identity = True
        for c in clusters:
            tl = c.find("TransformLink").props[0]
            tx = c.find("Transform").props[0]
            prod = _mul(_m4(tl), _m4(tx))   # column-vector: TL · Transform
            for r in range(4):
                for cc in range(4):
                    if not approx(prod[r][cc], 1.0 if r == cc else 0.0, 1e-4):
                        bind_identity = False
        check(bind_identity,
              "every cluster: TransformLink · Transform == identity")

        # BindPose is required for Blender to place bones at their rest world.
        poses = objs.find_all("Pose")
        bindposes = [p for p in poses if p.props[2] == "BindPose"]
        check(len(bindposes) == 1, "one BindPose present")
        if bindposes:
            check(len(bindposes[0].find_all("PoseNode")) >= len(model.bones),
                  "BindPose lists every bone")

        # Units: metres declared as 100 cm/unit so importers don't shrink ×100.
        gs = next(r for r in roots if r.name == "GlobalSettings")
        usf = None
        for p in gs.find("Properties70").children:
            if p.props and p.props[0] == "UnitScaleFactor":
                usf = p.props[-1]
        check(usf == 100.0, f"UnitScaleFactor is 100 ({usf})")


def test_fbx_matrix_layout():
    """fbx_array must put translation at flat indices 12,13,14 (column-major,
    column-vector) — the layout Blender/Maya/Unity expect.  A regression here
    transposes every bind matrix and collapses skinned meshes onto the origin."""
    print("fbx matrix layout:")
    m = mu.Mat4.identity()
    m.m[3][0], m.m[3][1], m.m[3][2] = 3.0, 5.0, 7.0   # row-vector translation
    arr = m.fbx_array()
    check((arr[12], arr[13], arr[14]) == (3.0, 5.0, 7.0),
          f"translation at indices 12,13,14 ({arr[12:15]})")
    check(arr[3] == 0.0 and arr[7] == 0.0 and arr[11] == 0.0,
          "no translation leaking into indices 3,7,11 (transpose guard)")


def _geo_bbox(roots):
    objs = next(r for r in roots if r.name == "Objects")
    mn = [1e9] * 3
    mx = [-1e9] * 3
    for g in objs.find_all("Geometry"):
        if str(g.props[2]).find("Mesh") < 0:
            continue
        v = g.find("Vertices")
        if not v:
            continue
        arr = list(v.props[0])
        for k in range(0, len(arr), 3):
            for i in range(3):
                mn[i] = min(mn[i], arr[k + i])
                mx[i] = max(mx[i], arr[k + i])
    return [mx[i] - mn[i] for i in range(3)]


def _up_axis_value(roots):
    gs = next(r for r in roots if r.name == "GlobalSettings")
    for p in gs.find("Properties70").children:
        if p.props and p.props[0] == "UpAxis":
            return p.props[-1]
    return None


def test_up_axis():
    """--up-axis y keeps the SIFAS Y-up convention; z rotates the whole scene to
    Z-up (UpAxis=2 and the geometry's tall axis swaps Y<->Z)."""
    print("up axis:")
    with tempfile.TemporaryDirectory() as td:
        ya = Path(td) / "y.fbx"
        za = Path(td) / "z.fbx"
        sifac_fbx.write_model_fbx(ya, _toy_model(), [_toy_anim()], up_axis="y")
        sifac_fbx.write_model_fbx(za, _toy_model(), [_toy_anim()], up_axis="z")
        ry = fbxenc.parse_fbx(ya.read_bytes())
        rz = fbxenc.parse_fbx(za.read_bytes())
        check(_up_axis_value(ry) == 1, "y mode declares UpAxis=Y (1) like SIFAS")
        check(_up_axis_value(rz) == 2, "z mode declares UpAxis=Z (2)")
        by, bz = _geo_bbox(ry), _geo_bbox(rz)
        # +90° about X swaps the Y and Z extents; X is unchanged.
        check(approx(by[0], bz[0], 1e-5), "X extent unchanged by rotation")
        check(approx(by[1], bz[2], 1e-5) and approx(by[2], bz[1], 1e-5),
              "z mode swaps the Y and Z extents (geometry rotated +90X)")


def _make_btx(width, height, pixels_bgra):
    """Build a minimal raw-BGRA (texFormat 0x00) .btx for the decoder test."""
    buf = bytearray(0x40)
    buf[0:4] = b"btx\x00"
    struct.pack_into("<H", buf, 0x08, width)
    struct.pack_into("<H", buf, 0x0A, height)
    buf[0x10] = 0x00            # texFormat raw BGRA
    buf[0x12] = 1               # mipCount
    struct.pack_into("<I", buf, 0x18, 0x40)   # dataOff
    struct.pack_into("<I", buf, 0x24, 0x30)   # name offset
    buf[0x30:0x35] = b"test\x00"
    for b, g, r, a in pixels_bgra:
        buf += bytes((b, g, r, a))
    return bytes(buf)


def test_texture_pipeline():
    print("texture pipeline:")
    import sifac_convert
    pix = [(10, 20, 30, 255), (40, 50, 60, 200),
           (70, 80, 90, 128), (100, 110, 120, 64)]
    data = _make_btx(2, 2, pix)
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "test.btx"
        src.write_bytes(data)
        out = Path(td) / "test.png"
        res = sifac_convert.convert_texture_job(str(src), str(out))
        check(res.ok and out.exists(), "btx -> png job succeeds")
        w, h, ch, raw = png.decode_png(out.read_bytes())
        check((w, h, ch) == (2, 2, 4), f"png is 2x2 RGBA ({w}x{h}x{ch})")
        # Pixel 0: BGRA (10,20,30,255) -> RGBA (30,20,10,255)
        check(tuple(raw[0:4]) == (30, 20, 10, 255),
              f"pixel 0 swizzled correctly ({tuple(raw[0:4])})")


def test_camera():
    print("camera:")
    from sifac_scene import CameraAnim
    cam = CameraAnim("cam", end_frame=3, fps=30.0,
                     base_pos=mu.Vec3(0, 1, -5), base_fov=50.0)
    cam.translation = [(0, mu.Vec3(0, 1, -5)), (3, mu.Vec3(1, 1, -4))]
    cam.rotation = [(0, mu.Quat(0, 0, 0, 1)), (3, mu.Quat(0, 0.1, 0, 0.99))]
    cam.fov = [(0, 50.0), (3, 35.0)]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "cam.fbx"
        sifac_fbx.write_camera_fbx(out, cam)
        roots = fbxenc.parse_fbx(out.read_bytes())
        objs = next(r for r in roots if r.name == "Objects")
        attrs = objs.find_all("NodeAttribute")
        check(any(a.props[2] == "Camera" for a in attrs), "Camera node attribute")
        models = objs.find_all("Model")
        check(any(m.props[2] == "Camera" for m in models), "Camera model")
        check(len(objs.find_all("AnimationStack")) == 1, "camera has an AnimationStack")


def test_bscam_parse_truncated():
    """A real .bscam ends right after the last fov key's value, omitting the two
    trailing tangent floats.  The parser must not read off the end of the buffer
    (regression for the 'unpack_from requires ... 4 bytes at offset EOF' crash)."""
    print("bscam parse (truncated tail):")
    import struct
    import sifac_bmarc
    hdr = b"".join((
        b"BSCM", struct.pack("<II", 0, 0), b"\x00\x00\x00\x00",
        struct.pack("<f", 30.0), struct.pack("<f", 10.0),   # fps, end_frame
        struct.pack("<iiii", 1, 1, 0, 1),                   # tran/rot/scl/fov
        struct.pack("<fff", 0, 0, 0), struct.pack("<fff", 0, 0, 0),
        struct.pack("<fff", 1, 1, 1), struct.pack("<f", 45.0),
        struct.pack("<I", 0),
        struct.pack("<QQQQ", 116, 136, 0, 160)))            # offsets
    check(len(hdr) == 116, f"bscam header is 116 bytes ({len(hdr)})")
    tran = struct.pack("<IIfff", 0, 0, 1.0, 2.0, 3.0)       # 20 bytes
    rot = struct.pack("<IIffff", 0, 0, 0.0, 0.0, 0.0, 1.0)  # 24 bytes
    fov = struct.pack("<IIff", 0, 0, 45.0, 0.0)             # 16 bytes: 1 tangent
    data = hdr + tran + rot + fov                            # final key truncated
    try:
        parsed = sifac_bmarc.parse_bscam(data, "cam")
        ok = True
    except Exception as exc:  # pragma: no cover
        ok = False
        print("   parse raised:", exc)
    check(ok, "truncated .bscam parses without overrunning the buffer")
    if ok:
        cam = parsed.camera
        check(len(cam.translation) == 1 and len(cam.rotation) == 1
              and len(cam.fov) == 1, "all keyframes recovered")
        check(approx(cam.fov[0][1], 45.0, 1e-4), "fov value read correctly")


def test_planner():
    print("planner:")
    import sifac_convert
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "live_0001"
        d.mkdir(parents=True)
        for fn in ("mod_chara01.bmarc", "mot_dance_chara01.bmarc",
                   "mot_dance_chara02.bmarc", "mod_chara02.bmarc",
                   "camera.bscam", "face.btx"):
            (d / fn).write_bytes(b"\x00")
        cfg = sifac_convert.ConvertConfig(input_dir=Path(td), output_dir=Path(td) / "out",
                                          preset="all")
        plan = sifac_convert.Converter(cfg).plan()
        check(len(plan.model_jobs) == 2, f"two model jobs ({len(plan.model_jobs)})")
        check(len(plan.motion_jobs) == 2, f"two motion jobs ({len(plan.motion_jobs)})")
        check(len(plan.camera_jobs) == 1, f"one camera job ({len(plan.camera_jobs)})")
        check(len(plan.texture_jobs) == 1, f"one texture job ({len(plan.texture_jobs)})")
        # mot_dance_chara01 should pair with mod_chara01 (shared 'chara01').
        pairing = {Path(mo).name: (Path(md).name if md else None)
                   for mo, md, *_ in plan.motion_jobs}
        check(pairing.get("mot_dance_chara01.bmarc") == "mod_chara01.bmarc",
              f"motion paired to matching model by name ({pairing})")


def main():
    test_math()
    test_png()
    test_bcn()
    test_fbx_encoder()
    test_builder()
    test_fbx_matrix_layout()
    test_up_axis()
    test_texture_pipeline()
    test_camera()
    test_bscam_parse_truncated()
    test_planner()
    print()
    if _FAILS:
        print(f"{len(_FAILS)} FAILURE(S):")
        for f in _FAILS:
            print("  -", f)
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
