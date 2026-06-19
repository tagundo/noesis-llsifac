# Love Live AC Noesis Script
Noesis and bms script for Love Live! School idol festival after school ACTIVITY PS4 and Arcade.
Supports models, textures, animations and cameras.

This script requires the vmd module here: https://github.com/h-kidd/noesis-vmd

## 🆕 Batch extractor + GUI (macOS friendly)

추출이 너무 오래 걸리나요? `tools/` 폴더에 **QuickBMS를 여러 개 동시에
실행**해 모델링/라이브를 한꺼번에 뽑는 배치 도구와 GUI가 있습니다.
맥북에서는 `tools/run_mac.command` 를 더블클릭하세요.

> A parallel **batch extractor + Tkinter GUI** that drives QuickBMS over a
> whole folder at once (decompress `.cmp` → extract `ARC` archives), with a
> double‑click macOS launcher. See **[`tools/README.md`](tools/README.md)**.

**맥에서 QuickBMS 컴파일이 번거롭다면** `--native` (순수 파이썬, 컴파일 불필요)
모드를 쓰세요. `.cmp`(LZMA)와 `ARC` 아카이브를 QuickBMS와 동일하게 처리합니다.

```bash
# CLI: 8개 병렬로 전부 추출 (QuickBMS 사용)
python3 tools/sifac_extract.py /game/data /output -j 8
# CLI: QuickBMS 없이 (네이티브) — 맥 권장
python3 tools/sifac_extract.py /game/data /output -j 8 --native
# GUI: macOS → double-click tools/run_mac.command (or: python3 tools/sifac_gui.py)
```

## 🆕 Batch converter → FBX (Noesis 불필요, MMD 아님)

추출한 `.bmarc`(모델/모션)·`.bscam`(카메라)·`.btx`(텍스처)를 **Noesis 없이**
곧바로 **FBX + PNG** 로 변환합니다. Noesis로 하나씩 여는 것보다 훨씬 빠르고,
`noesis-vmd`(MMD용 PMX/VMD)와 달리 **Blender·Unity·Unreal·Maya** 에서 바로
쓰는 표준 FBX를 만듭니다 — 모델/무대(메시·뼈대·스키닝·머티리얼·모프),
애니메이션(take), 카메라, 텍스처를 모두 지원합니다.

> A parallel, pure-Python **batch converter** (CLI + GUI tab) that turns the
> extracted `.bmarc`/`.bscam`/`.btx` straight into **binary FBX + PNG** — no
> Noesis, no MMD. Skeletons, skinning, materials, blendshape morphs,
> per-motion animation takes, and animated cameras. See
> **[`tools/README.md`](tools/README.md)**.

```bash
# 추출 결과 폴더 전체를 FBX/PNG로 (8개 병렬)
python3 tools/sifac_convert.py /output/02_extracted /fbx_output -j 8
# 모델/무대만 · 모션만 · 카메라만 · 텍스처만
python3 tools/sifac_convert.py IN OUT --preset models
python3 tools/sifac_convert.py IN OUT --preset animations
# GUI: run_mac.command → "② 변환 → FBX" 탭
```

> **두 가지 FBX 엔진** 모두 Blender·Unity·Maya에서 메시·스키닝·애니가 정상입니다.
> 기본·권장은 **무설치·고속의 순수 파이썬 엔진**(`--engine python`) — 표준 바인드
> 행렬·`BindPose`·미터 단위로 캐릭터가 약 1.5 m로 똑바로 들어옵니다. Blender의 본
> 방향(head/tail/roll)까지 네이티브로 맞추고 싶으면 `pip install bpy` 후
> `--engine blender`(또는 GUI에서 엔진 'blender'). 자세히는
> **[`tools/README.md`](tools/README.md)**.

Options:
printMatInfo - Print extra material Info from the models shader file.
exportVmd - Export a vmd file for the loaded animation.
pmxScale - The scale used for vmd export (must be the same as the scale used to import your pmx model int pmxEditor).