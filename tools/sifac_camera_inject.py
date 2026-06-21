#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sifac_camera_inject.py — SIFAC 카메라(JSON) → SIFAS 라이브 타임라인 주입
=======================================================================

``sifac_camera.py`` 가 뽑은 카메라 트랙(JSON: pos / rot(quat) / fov, Y-up)을
SIFAS 라이브 번들의 **카메라 AnimationClip** 에 기입합니다.

SIFAS 카메라 구조(실제 번들에서 확인):
  * ``GroupTrack "CameraMotion"`` ▸ ``AnimationTrack "Camera1"`` 위에 컷별 클립
  * 각 클립은 **AnimationClip**(60fps, generic). 커브 7개를 바인딩:
        genericBindings = [Transform.position(attr1),
                           Transform.euler(attr4),
                           LiveCoreCameraWork.FOV(attr hash, classID 114)]
    → 커브 순서 = pos.x,pos.y,pos.z, eul.x,eul.y,eul.z, fov

주입 방식(견고): 새 오브젝트를 만들지 않고, **기존 카메라 클립 하나의 데이터를
DenseClip 으로 덮어써서** 우리 카메라 한 컷으로 전체 곡을 커버하고, 나머지 카메라
클립은 길이 0으로 비활성화합니다. 바인딩(m_ClipBindingConstant)은 기존 클립 것을
그대로 재사용하므로 FOV 해시/스크립트 PPtr가 정확히 일치합니다.

회전: JSON 쿼터니언 → **Unity 오일러(ZXY, deg)** 로 변환(라운드트립 검증됨).

Z 반전 옵션: SIFAS 게임 ↔ Unity 에디터 좌표 차이가 있을 때 ``--z-flip`` 으로
pos.z 부호를, ``--yaw180`` 으로 Y축 180° 를 보정(나중에 불필요하면 빼면 됨).

사용:
    python3 sifac_camera_inject.py --camera cam.json \\
        --bundle 1ili3e_0.unity --out 1ili3e_0.cam.unity [--z-flip] [--scale 1.0]
"""

from __future__ import annotations
import argparse
import json
import math
from pathlib import Path


# --- 쿼터니언 → Unity 오일러(ZXY, degrees) -------------------------------- #
def quat_to_unity_euler(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    m12 = 2 * (y * z - w * x)
    m02 = 2 * (x * z + w * y)
    m22 = 1 - 2 * (x * x + y * y)
    m10 = 2 * (x * y + w * z)
    m11 = 1 - 2 * (x * x + z * z)
    m20 = 2 * (x * z - w * y)
    m00 = 1 - 2 * (y * y + z * z)
    sx = max(-1.0, min(1.0, -m12))
    ex = math.asin(sx)
    if abs(sx) < 0.9999999:
        ey = math.atan2(m02, m22)
        ez = math.atan2(m10, m11)
    else:
        ey = math.atan2(-m20, m00)
        ez = 0.0
    return math.degrees(ex), math.degrees(ey), math.degrees(ez)


def _nlerp(qa, qb, t):
    d = sum(a * b for a, b in zip(qa, qb))
    if d < 0:
        qb = [-b for b in qb]
    q = [a + (b - a) * t for a, b in zip(qa, qb)]
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return [c / n for c in q]


def resample_60(frames, fps_out=60.0):
    """JSON frames(t,pos,rot,fov) → 60fps 균일 그리드."""
    if not frames:
        return []
    dur = frames[-1]["t"]
    n = int(round(dur * fps_out)) + 1
    out = []
    j = 0
    for i in range(n):
        t = i / fps_out
        while j + 1 < len(frames) and frames[j + 1]["t"] < t:
            j += 1
        f0 = frames[j]
        f1 = frames[min(j + 1, len(frames) - 1)]
        span = (f1["t"] - f0["t"]) or 1.0
        a = max(0.0, min(1.0, (t - f0["t"]) / span))
        pos = [f0["pos"][k] + (f1["pos"][k] - f0["pos"][k]) * a for k in range(3)]
        rot = _nlerp(f0["rot"], f1["rot"], a)
        fov = f0["fov"] + (f1["fov"] - f0["fov"]) * a
        out.append((t, pos, rot, fov))
    return out


def build_sample_array(samples, scale=1.0, z_flip=False, yaw180=False):
    """samples → DenseClip flat float array [px,py,pz, ex,ey,ez, fov] × N."""
    arr = []
    for _t, pos, rot, fov in samples:
        px, py, pz = pos[0] * scale, pos[1] * scale, pos[2] * scale
        if z_flip:
            pz = -pz
        ex, ey, ez = quat_to_unity_euler(rot)
        if z_flip:
            # mirror across XY-plane also flips the rotation's Y(yaw)/Z-handedness
            ey = -ey
            ez = -ez
        if yaw180:
            ey += 180.0
        arr.extend([px, py, pz, ex, ey, ez, float(fov)])
    return arr


# --- 번들 주입 ------------------------------------------------------------- #
def inject(camera_json: Path, bundle_in: Path, bundle_out: Path,
           scale=1.0, z_flip=False, yaw180=False, verbose=True):
    import UnityPy
    track = json.loads(Path(camera_json).read_text(encoding="utf-8"))
    frames = track.get("frames", [])
    samples = resample_60(frames)
    n = len(samples)
    if n == 0:
        raise ValueError("camera json has no frames")
    sample_array = build_sample_array(samples, scale=scale, z_flip=z_flip, yaw180=yaw180)
    stop_time = (n - 1) / 60.0

    env = UnityPy.load(str(bundle_in))
    objs = list(env.objects)
    smap = {o.path_id: o.read().m_Name for o in objs if o.type.name == "MonoScript"}

    def scls(t):
        return smap.get(t.get("m_Script", {}).get("m_PathID"))

    # find the camera AnimationTrack ("Camera1") and its clips
    cam_track = None
    for o in objs:
        if o.type.name == "MonoBehaviour":
            t = o.read_typetree()
            if scls(t) == "AnimationTrack" and "camera" in str(t.get("m_Name", "")).lower():
                cam_track = (o, t)
                break
    if cam_track is None:
        # fallback: any AnimationTrack whose clips reference *_camera AnimationClips
        for o in objs:
            if o.type.name == "MonoBehaviour" and scls(o.read_typetree()) == "AnimationTrack":
                cam_track = (o, o.read_typetree())
                break
    if cam_track is None:
        raise RuntimeError("no AnimationTrack (Camera1) found in bundle")

    # locate AnimationClips named *camera*
    cam_clips = [o for o in objs
                 if o.type.name == "AnimationClip" and "camera" in o.read().m_Name.lower()]
    if not cam_clips:
        raise RuntimeError("no camera AnimationClip found in bundle")

    # pick the first camera clip as our target to overwrite
    target = sorted(cam_clips, key=lambda o: o.read().m_Name)[0]
    ct = target.read_typetree()

    # rewrite the dense clip data, clear streamed/constant
    clip = ct["m_MuscleClip"]["m_Clip"]["data"]
    clip["m_StreamedClip"]["data"] = []
    clip["m_StreamedClip"]["curveCount"] = 0
    dense = clip["m_DenseClip"]
    dense["m_FrameCount"] = n
    dense["m_CurveCount"] = 7
    dense["m_SampleRate"] = 60.0
    dense["m_BeginTime"] = 0.0
    dense["m_SampleArray"] = sample_array
    if isinstance(clip.get("m_ConstantClip"), dict):
        clip["m_ConstantClip"]["data"] = []
    ct["m_MuscleClip"]["m_StopTime"] = stop_time
    ct["m_MuscleClip"]["m_StartTime"] = 0.0
    ct["m_SampleRate"] = 60.0
    target.save_typetree(ct)

    # adjust the timeline: target clip spans whole song, others off
    o_tr, t_tr = cam_track
    clips = t_tr.get("m_Clips", [])
    target_name = target.read().m_Name
    # find which timeline clip points (transitively) at our target clip name
    set_main = False
    for c in clips:
        dn = str(c.get("m_DisplayName", ""))
        # heuristic: the first clip (start 0) becomes our full-song camera
        if not set_main and abs(float(c.get("m_Start", 0.0))) < 1e-6:
            c["m_Start"] = 0.0
            c["m_Duration"] = stop_time
            set_main = True
        else:
            c["m_Duration"] = 0.0
    if not set_main and clips:
        clips[0]["m_Start"] = 0.0
        clips[0]["m_Duration"] = stop_time
        for c in clips[1:]:
            c["m_Duration"] = 0.0
    o_tr.save_typetree(t_tr)

    Path(bundle_out).parent.mkdir(parents=True, exist_ok=True)
    with open(bundle_out, "wb") as f:
        f.write(env.file.save(packer="lz4"))
    if verbose:
        fovs = [s[3] for s in samples]
        print(f"[ok] wrote {bundle_out}")
        print(f"  camera: {n} samples @60fps  ({stop_time:.2f}s)  "
              f"fov {min(fovs):.1f}..{max(fovs):.1f}  z_flip={z_flip} yaw180={yaw180}")
        print(f"  overwrote AnimationClip '{target_name}', parked other camera cuts")
    return target_name, n, stop_time


def main(argv=None):
    ap = argparse.ArgumentParser(description="Inject a SIFAC camera JSON into a SIFAS live bundle")
    ap.add_argument("--camera", required=True, help="camera JSON from sifac_camera.py")
    ap.add_argument("--bundle", required=True, help="SIFAS live-timeline AssetBundle")
    ap.add_argument("--out", help="output bundle (default: <bundle>.cam.unity)")
    ap.add_argument("--scale", type=float, default=1.0, help="position scale")
    ap.add_argument("--z-flip", action="store_true", help="negate pos.z & flip yaw/roll (game<->editor handedness)")
    ap.add_argument("--yaw180", action="store_true", help="rotate camera 180° about Y")
    args = ap.parse_args(argv)
    out = args.out or (str(Path(args.bundle).with_suffix("")) + ".cam.unity")
    inject(Path(args.camera), Path(args.bundle), Path(out),
           scale=args.scale, z_flip=args.z_flip, yaw180=args.yaw180)


if __name__ == "__main__":
    main()
