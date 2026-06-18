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

Options:
printMatInfo - Print extra material Info from the models shader file.
exportVmd - Export a vmd file for the loaded animation.
pmxScale - The scale used for vmd export (must be the same as the scale used to import your pmx model int pmxEditor).