#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sifac_motion_path — 동작 모션(bmarc)에서 캐릭터 위치 트랙을 뽑아낸다.

따라가기 카메라(front/rear view) 생성의 입력으로 쓰는 위치 JSON 을 만든다:

    {"fps": 60, "bone": "Hips", "frames": [{"t": 0.0, "pos": [x, y, z]}, ...]}

SIFAS-MODDING `sifas_timeline_inject.py cam-follow --positions <이 JSON>` 이 먹는다.

루트 본은 곡에 따라 정적(제자리 춤)일 수 있다. 기본은 **이동량이 가장 큰 루트 후보 본**을
자동 선택하고, `--bone` 으로 직접 지정할 수 있다. 제자리 춤이면 트랙이 거의 고정값이라
카메라가 안정적인 front/rear 샷이 된다.

사용:
    python3 sifac_motion_path.py mot_03_kotori_0560_BBT.bmarc -o bbt_path.json
    python3 sifac_motion_path.py mot.bmarc --bone Footsteps --scale 1.0 -o path.json
    python3 sifac_motion_path.py mot.bmarc --list-bones      # 본/이동량만 보기
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sifac_bmarc as B  # noqa: E402

# 루트(=무대 위 위치) 후보 본 — 이름에 이게 들어가면 후보로 본다
ROOT_HINTS = ("trans", "root", "footsteps", "shoe_sole", "hips", "center", "Reference")


def _span(track):
    tr = track.translation
    if len(tr) < 2:
        return 0.0
    xs = [p[1].x for p in tr]; ys = [p[1].y for p in tr]; zs = [p[1].z for p in tr]
    return max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def pick_root_bone(anim, name=None):
    """위치를 대표할 본 선택: --bone 지정 우선, 없으면 이동량 큰 루트 후보."""
    tracks = anim.tracks
    names = [t.bone_name for t in tracks]
    if name:
        if name not in names:
            raise SystemExit("bone %r not found. bones: %s" % (name, names[:40]))
        return tracks[names.index(name)]
    cands = [t for t in tracks
             if any(h.lower() in t.bone_name.lower() for h in ROOT_HINTS)]
    cands = sorted(cands, key=_span, reverse=True)
    if cands and _span(cands[0]) > 1e-4:
        return cands[0]
    # 전부 정적이면 Hips(혹은 첫 후보, 혹은 첫 본) — 정적 트랙(제자리 춤)
    for pref in ("Hips", "Kotori_trans", "Kotori_root"):
        if pref in names:
            return tracks[names.index(pref)]
    return cands[0] if cands else tracks[0]


def extract_path(anim, bone_track, fps=60.0, scale=1.0, z_flip=False):
    """본의 translation → fps 그리드 위치 트랙(프레임 리스트)."""
    keys = bone_track.translation
    if not keys:
        return [{"t": 0.0, "pos": [0.0, 0.0, 0.0]}]
    end_frame = anim.end_frame or keys[-1][0]
    src_fps = anim.fps or 60.0
    dur = end_frame / src_fps
    # 키를 (시간, x,y,z) 로
    kf = [(k[0] / src_fps, k[1].x, k[1].y, k[1].z) for k in keys]
    out, j = [], 0
    n = int(round(dur * fps)) + 1
    for i in range(max(1, n)):
        t = i / fps
        while j + 1 < len(kf) and kf[j + 1][0] < t:
            j += 1
        a = kf[j]; b = kf[min(j + 1, len(kf) - 1)]
        span = (b[0] - a[0]) or 1.0
        u = max(0.0, min(1.0, (t - a[0]) / span))
        x = a[1] + (b[1] - a[1]) * u
        y = a[2] + (b[2] - a[2]) * u
        z = a[3] + (b[3] - a[3]) * u
        x *= scale; y *= scale; z *= scale
        if z_flip:
            z = -z
        out.append({"t": round(t, 5), "pos": [round(x, 5), round(y, 5), round(z, 5)]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="동작 모션 → 캐릭터 위치 트랙 JSON")
    ap.add_argument("motion", help="모션 bmarc")
    ap.add_argument("-o", "--out", help="출력 JSON (기본: <motion>.path.json)")
    ap.add_argument("--bone", help="위치를 따올 본 이름(기본: 자동)")
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--z-flip", action="store_true")
    ap.add_argument("--list-bones", action="store_true", help="본/이동량만 출력")
    args = ap.parse_args(argv)

    data = Path(args.motion).read_bytes()
    m = B.parse_bmarc(data, Path(args.motion).stem)
    if not m.animations:
        raise SystemExit("no animation in %s" % args.motion)
    anim = m.animations[0]

    if args.list_bones:
        rows = sorted(((round(_span(t), 3), t.bone_name) for t in anim.tracks),
                      reverse=True)
        for sp, nm in rows[:40]:
            print(f"  span={sp:7.3f}  {nm}")
        return 0

    bone = pick_root_bone(anim, args.bone)
    frames = extract_path(anim, bone, fps=args.fps, scale=args.scale, z_flip=args.z_flip)
    xs = [f["pos"][0] for f in frames]; zs = [f["pos"][2] for f in frames]
    out = {"fps": args.fps, "bone": bone.bone_name, "frames": frames}
    outp = args.out or (str(Path(args.motion).with_suffix("")) + ".path.json")
    Path(outp).write_text(json.dumps(out), encoding="utf-8")
    print("[path] bone=%s frames=%d  x[%.2f,%.2f] z[%.2f,%.2f]  -> %s"
          % (bone.bone_name, len(frames), min(xs), max(xs), min(zs), max(zs), outp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
