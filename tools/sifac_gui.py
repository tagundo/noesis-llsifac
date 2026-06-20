#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIFAC Toolbox — graphical interface (Extract + Convert + Retarget)
==================================================================

A small Tkinter front-end with three tabs:

  * **Extract** — drives ``sifac_extract.py``: game ``.cmp``/``ARC`` ->
    ``.bmarc`` / ``.btx`` (parallel QuickBMS or the pure-Python native engine).
  * **Convert** — drives ``sifac_convert.py``: extracted
    ``.bmarc`` / ``.btx`` / ``.bscam`` -> **FBX + PNG** (models, motions,
    cameras), fast and parallel, *no Noesis and no MMD*.
  * **Retarget** — drives ``sifac_anim_retarget.py``: a SIFAC motion FBX ->
    a SIFAS clip, **without Unity's lossy Humanoid path**, with adjustable
    ArmRoll strength / smoothing and multi-format output (.anim / FBX / glTF /
    BVH).  Needs ``bpy`` (the same one-click install the Convert tab offers).

The UI is in **English by default**; use the language selector (top-right) to
switch to **Korean (한국어)** — the whole window re-translates live.

Tkinter ships with the python.org / macOS system Python, so no ``pip install``
is required.  Run ``python3 sifac_gui.py`` or double-click ``run_mac.command``.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sifac_extract as engine  # noqa: E402
import sifac_convert as convert  # noqa: E402

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception as exc:  # pragma: no cover - environment dependent
    sys.stderr.write(
        "Tkinter is not available in this Python.\n"
        f"  ({exc})\n\n"
        "On macOS, install a Python that bundles Tk:\n"
        "  * python.org installer (recommended), or\n"
        "  * Homebrew:  brew install python-tk\n"
        "Then run:  python3 sifac_gui.py\n"
        "You can also use the CLIs: python3 sifac_extract.py --help / "
        "python3 sifac_convert.py --help\n")
    raise SystemExit(1)


# --------------------------------------------------------------------------- #
# Internationalisation (English default, Korean optional)
# --------------------------------------------------------------------------- #

LANGS = [("en", "English"), ("ko", "한국어")]

# Preset / engine option *ids* (stable); display labels live in STRINGS.
EXTRACT_PRESETS = ["all", "models", "live", "textures"]
CONVERT_PRESETS = ["all", "models", "animations", "cameras", "textures"]

STRINGS = {
    # chrome ----------------------------------------------------------------
    "title": {"en": "SIFAC Toolbox — Extract & Convert (FBX)",
              "ko": "SIFAC 도구상자 — 추출 & 변환 (FBX)"},
    "lang_label": {"en": "Language", "ko": "언어"},
    "tab_extract": {"en": "  ① Extract  ", "ko": "  ① 추출  "},
    "tab_convert": {"en": "  ② Convert → FBX  ",
                    "ko": "  ② 변환 → FBX  "},
    "idle": {"en": "Idle", "ko": "대기 중"},
    "browse": {"en": "Browse…", "ko": "찾기…"},
    "stop": {"en": "■ Stop", "ko": "■ 중지"},
    "open_output": {"en": "Open output folder", "ko": "출력 폴더 열기"},
    "options": {"en": "Options", "ko": "옵션"},
    "jobs": {"en": "Parallel jobs", "ko": "동시 작업 수"},
    # extract tab -----------------------------------------------------------
    "ex_input": {"en": "Input folder", "ko": "입력 폴더"},
    "ex_output": {"en": "Output folder", "ko": "출력 폴더"},
    "ex_quickbms": {"en": "quickbms path", "ko": "quickbms 경로"},
    "ex_build_pick": {"en": "Build QuickBMS (pick folder)…",
                      "ko": "QuickBMS 빌드 (폴더 선택)…"},
    "ex_build_auto": {"en": "Auto download + build", "ko": "자동 다운로드+빌드"},
    "ex_target": {"en": "Extract target", "ko": "추출 대상"},
    "ex_cb_cmp": {"en": "Decompress .cmp", "ko": ".cmp 압축해제"},
    "ex_cb_arc": {"en": "Extract .arc", "ko": ".arc 추출"},
    "ex_cb_skip": {"en": "Skip existing files", "ko": "기존 파일 건너뛰기"},
    "ex_cb_native": {"en": "Extract without QuickBMS (native · Python, "
                           "recommended on Mac)",
                     "ko": "QuickBMS 없이 추출 (네이티브 · 파이썬, 맥 권장)"},
    "ex_cb_collapse": {"en": "Collapse duplicate folder names "
                             "(e.g. live_0001/live_0001 → live_0001)",
                       "ko": "중복 폴더 이름 합치기 "
                             "(예: live_0001/live_0001 → live_0001)"},
    "ex_start": {"en": "▶ Start extract", "ko": "▶ 추출 시작"},
    "ex_autodetect": {"en": "Auto-detect", "ko": "자동 감지"},
    "ex_send": {"en": "➡ Send to Convert tab", "ko": "➡ 변환 탭으로 보내기"},
    "preset_ex_all": {"en": "All", "ko": "전체 (All)"},
    "preset_ex_models": {"en": "Models only", "ko": "모델링만 (Models)"},
    "preset_ex_live": {"en": "Live / Motions only",
                       "ko": "라이브/모션만 (Live / Motions)"},
    "preset_ex_textures": {"en": "Textures only", "ko": "텍스처만 (Textures)"},
    # convert tab -----------------------------------------------------------
    "cv_input": {"en": "Input folder (extracted .bmarc/.btx)",
                 "ko": "입력 폴더 (추출된 .bmarc/.btx)"},
    "cv_output": {"en": "Output folder (FBX/PNG)", "ko": "출력 폴더 (FBX/PNG)"},
    "cv_models": {"en": "Model folder (optional)", "ko": "모델 폴더 (선택)"},
    "cv_target": {"en": "Convert target", "ko": "변환 대상"},
    "cv_scale": {"en": "Scale", "ko": "스케일"},
    "cv_engine": {"en": "FBX engine", "ko": "FBX 엔진"},
    "cv_upaxis": {"en": "Up axis", "ko": "업(Up) 축"},
    "upaxis_y": {"en": "Y-up (SIFAS / Maya / Unity — recommended)",
                 "ko": "Y-up (SIFAS / Maya / Unity — 권장)"},
    "upaxis_z": {"en": "Z-up (Blender native)", "ko": "Z-up (Blender 기본)"},
    "cv_cb_morphs": {"en": "Include morphs (expressions)", "ko": "모프(표정) 포함"},
    "cv_cb_tex": {"en": "Decode textures to PNG", "ko": "텍스처 PNG로 디코드"},
    "cv_cb_animonly": {"en": "Motions: bones + anim only (no mesh)",
                       "ko": "모션은 뼈+애니만 (메시 제외)"},
    "cv_cb_bundle": {"en": "Bundle motions into the model FBX (takes)",
                     "ko": "모션을 모델 FBX에 묶기 (takes)"},
    "cv_cb_skip": {"en": "Skip existing files", "ko": "기존 파일 건너뛰기"},
    "cv_start": {"en": "▶ Start convert (→ FBX)",
                 "ko": "▶ 변환 시작 (→ FBX)"},
    "cv_info": {
        "en": "Output: models/stages → .fbx (skeleton, skinning, "
              "materials, morphs), motions → .fbx, cameras → .fbx, "
              "textures → .png. FBX targets Blender/Unity/Maya (not MMD).\n"
              "★ The default 'python' engine (no install, fast) works "
              "correctly in Blender too. To match Blender's native bone axes, "
              "'pip install bpy' then pick 'blender'. For BC7/BC6H textures: "
              "'pip install texture2ddecoder'.",
        "ko": "결과: 모델/무대 → .fbx (뼈대·스키닝·머티리얼·"
              "모프), 모션 → .fbx, 카메라 → .fbx, 텍스처 → .png. "
              "FBX는 Blender·Unity·Maya용 (MMD 아님).\n"
              "★ 기본 'python' 엔진(무설치·고속)으로 Blender에서도 정상 "
              "동작합니다. Blender 본 방향까지 맞추려면 'pip install bpy' 후 "
              "'blender'. BC7/BC6H 텍스처는 'pip install texture2ddecoder'."},
    "preset_cv_all": {"en": "All", "ko": "전체 (All)"},
    "preset_cv_models": {"en": "Models / Stages", "ko": "모델/무대 (Models / Stages)"},
    "preset_cv_animations": {"en": "Animations / Motions",
                             "ko": "애니메이션/모션 (Animations)"},
    "preset_cv_cameras": {"en": "Cameras", "ko": "카메라 (Cameras)"},
    "preset_cv_textures": {"en": "Textures", "ko": "텍스처 (Textures)"},
    # retarget tab ----------------------------------------------------------
    "tab_retarget": {"en": "  ③ Retarget → SIFAS  ",
                     "ko": "  ③ 리타깃 → SIFAS  "},
    "rt_input": {"en": "SIFAC motion FBX (or a folder in batch mode)",
                 "ko": "SIFAC 모션 FBX (일괄 모드는 폴더)"},
    "rt_output": {"en": "Output folder", "ko": "출력 폴더"},
    "rt_member": {"en": "Member name (optional — usually blank)",
                  "ko": "멤버 이름 (선택 — 보통 비움)"},
    "rt_prefab": {"en": "SIFAS member .prefab (optional)",
                  "ko": "SIFAS 멤버 .prefab (선택)"},
    "rt_cb_batch": {"en": "Batch: input is a folder of .fbx",
                    "ko": "일괄: 입력이 .fbx 폴더"},
    "rt_cb_root": {"en": "Root / stage motion", "ko": "루트/무대 이동"},
    "rt_twist": {"en": "ArmRoll strength (0 = off, 1 = game value, >1 stronger)",
                 "ko": "ArmRoll 강도 (0 = 끔, 1 = 게임값, >1 강하게)"},
    "rt_smooth": {"en": "Smoothing (frames, 0 = off / faithful)",
                  "ko": "부드러움 (프레임, 0 = 끔 / 충실)"},
    "rt_step": {"en": "Frame step (decimate)", "ko": "프레임 간격 (솎기)"},
    "rt_song": {"en": "Song length s (0 = no trim)",
                "ko": "노래 길이 초 (0 = 안 자름)"},
    "rt_formats": {"en": "Export formats", "ko": "내보내기 형식"},
    "rt_target_model": {"en": "Bake onto my model FBX (fbx/glb, optional)",
                        "ko": "내 모델 FBX에 굽기 (fbx/glb, 선택)"},
    "rt_cb_bundle": {"en": "Also inject into a Unity bundle (template needed)",
                     "ko": "Unity 번들에도 주입 (템플릿 필요)"},
    "rt_template": {"en": "Template bundle (.unity / .bundle)",
                    "ko": "템플릿 번들 (.unity / .bundle)"},
    "rt_err_template": {"en": "Bundle output needs a template bundle and single "
                              "(non-batch) mode with the .anim format on.",
                        "ko": "번들 출력은 템플릿 번들 + 단일(비일괄) 모드 + .anim "
                              "형식이 필요합니다."},
    "rt_bundle_running": {"en": "Injecting into bundle…", "ko": "번들 주입 중…"},
    "rt_bundle_done": {"en": "Bundle written ✓", "ko": "번들 작성 완료 ✓"},
    "rt_bundle_fail": {"en": "Bundle injection failed ✗ (code {rc}) — see log "
                             "(needs: pip install UnityPy)",
                       "ko": "번들 주입 실패 ✗ (코드 {rc}) — 로그 확인 "
                             "(필요: pip install UnityPy)"},
    "rt_start": {"en": "▶ Start retarget", "ko": "▶ 리타깃 시작"},
    "rt_info": {
        "en": "Retargets a SIFAC motion onto the SIFAS rig WITHOUT Unity's "
              "lossy Humanoid path, and drives the SIFAS twist/roll bones so "
              "the shoulder/elbow skin doesn't pinch. Needs bpy (one-click "
              "install on the ② Convert tab). '.anim' drops straight into "
              "Unity; FBX / glTF (.glb) / BVH open in any DCC.",
        "ko": "SIFAC 모션을 Unity의 손실 Humanoid 경로 없이 SIFAS 리그로 "
              "리타깃하고, SIFAS 트위스트/롤 본을 구동해 어깨·팔꿈치 살 접힘을 "
              "막습니다. bpy 필요(② 변환 탭에서 원클릭 설치). '.anim'은 Unity에 "
              "바로, FBX / glTF (.glb) / BVH는 어떤 DCC에서나 열립니다."},
    "rt_err_input": {"en": "Pick a valid SIFAC .fbx file (or a folder in "
                           "batch mode).",
                     "ko": "유효한 SIFAC .fbx 파일(또는 일괄 모드에서는 폴더)을 "
                           "선택하세요."},
    "rt_err_fmt": {"en": "Pick at least one export format.",
                   "ko": "내보내기 형식을 하나 이상 선택하세요."},
    "run_retarget": {"en": "Retargeting…", "ko": "리타깃 실행 중…"},
    "rt_done": {"en": "Retarget finished ✓", "ko": "리타깃 완료 ✓"},
    "rt_done_fail": {"en": "Retarget failed ✗ (code {rc}) — see log",
                     "ko": "리타깃 실패 ✗ (코드 {rc}) — 로그 확인"},
    "engine_auto": {"en": "auto (Blender if bpy present, else python)",
                    "ko": "auto (bpy 있으면 Blender, 없으면 파이썬)"},
    "engine_blender": {"en": "blender (native bone axes — needs bpy, slow)",
                       "ko": "blender (Blender 본 방향까지 네이티브 — bpy 필요, 느림)"},
    "engine_python": {"en": "python (recommended — no install, fast, "
                            "Blender/Unity/Maya OK)",
                      "ko": "python (권장·무설치·고속 — Blender/Unity/Maya 정상)"},
    # bpy status / installer ------------------------------------------------
    "bpy_checking": {"en": "bpy: checking…", "ko": "bpy: 확인 중…"},
    "bpy_ok": {"en": "bpy: installed ✓ (Blender engine available)",
               "ko": "bpy: 설치됨 ✓ (Blender 엔진 사용 가능)"},
    "bpy_missing": {"en": "bpy: not found ✗ — install it to use the "
                          "'blender' engine",
                    "ko": "bpy: 없음 ✗ — 'blender' 엔진을 쓰려면 설치하세요"},
    "bpy_installing": {"en": "Installing bpy… (hundreds of MB, please wait)",
                       "ko": "bpy 설치 중… (수백 MB, 잠시 기다려 주세요)"},
    "bpy_btn_install": {"en": "Install bpy (for Blender engine)",
                        "ko": "bpy 설치 (Blender 엔진용)"},
    "bpy_btn_reinstall": {"en": "Reinstall / upgrade bpy",
                          "ko": "bpy 재설치/업그레이드"},
    "bpy_install_start": {"en": "pip install bpy started — {py}",
                          "ko": "pip install bpy 시작 — {py}"},
    "bpy_done_ok": {"en": "bpy install complete ✓",
                    "ko": "bpy 설치 완료 ✓"},
    "bpy_resume": {"en": "Resuming conversion…",
                   "ko": "변환을 이어서 시작합니다…"},
    "bpy_fail_log": {"en": "bpy install failed.", "ko": "bpy 설치 실패."},
    "bpy_fail_title": {"en": "bpy install failed", "ko": "bpy 설치 실패"},
    "bpy_fail_msg": {
        "en": "Installing bpy failed.{detail}\n\n"
              "• There may be no bpy wheel for this Python version "
              "(bpy supports specific versions only — e.g. Blender 4.x"
              "↔3.11, 5.x↔3.13).\n"
              "• Retry on a compatible Python (e.g. the python.org build), "
              "or\n"
              "• leave the engine on 'python' and convert (no install, "
              "fast).",
        "ko": "bpy 설치에 실패했습니다.{detail}\n\n"
              "• 이 파이썬 버전에 맞는 bpy 휠이 없을 수 있어요 (bpy는 특정 "
              "파이썬 버전만 지원 — 예: Blender 4.x↔3.11, 5.x↔3.13).\n"
              "• python.org 설치본 같은 호환 파이썬에서 다시 시도하거나,\n"
              "• 엔진을 'python'으로 두고 변환하세요 (무설치·고속)."},
    "bpy_need_title": {"en": "bpy required", "ko": "bpy 필요"},
    "bpy_need_msg": {
        "en": "The 'blender' engine needs the bpy module, which is not "
              "installed.\n\nInstall it now with 'pip install bpy'? "
              "(hundreds of MB; the conversion resumes when it finishes.)\n\n"
              "Click 'No' to convert with the fast 'python' engine instead.",
        "ko": "'blender' 엔진은 bpy 모듈이 필요한데 설치돼 있지 않습니다.\n\n"
              "지금 'pip install bpy'로 설치할까요? (수백 MB, 끝나면 변환을 이어서 "
              "시작합니다.)\n\n'아니오'를 누르면 빠른 'python' 엔진으로 진행합니다."},
    "bpy_fell_back": {"en": "bpy not installed → converting with the "
                            "'python' engine this time.",
                      "ko": "bpy 미설치 → 이번 변환은 'python' 엔진으로 진행합니다."},
    # quickbms --------------------------------------------------------------
    "qb_building": {"en": "Building…", "ko": "빌드 중…"},
    "qb_done": {"en": "Build complete ✓", "ko": "빌드 완료 ✓"},
    "qb_failed": {"en": "Build failed ✗", "ko": "빌드 실패 ✗"},
    "qb_pick_title": {"en": "Pick the QuickBMS repo (or src) folder — the "
                            "one containing src/quickbms.c",
                      "ko": "QuickBMS 저장소(또는 src) 폴더 선택 — src/quickbms.c 가 있는 곳"},
    "qb_dl_title": {"en": "Auto download", "ko": "자동 다운로드"},
    "qb_dl_msg": {"en": "Download the QuickBMS source from the internet and "
                        "build it.\nProceed?",
                  "ko": "인터넷에서 QuickBMS 소스를 내려받아 빌드합니다.\n진행할까요?"},
    "qb_label_build": {"en": "QuickBMS build", "ko": "QuickBMS 빌드"},
    "qb_label_dl": {"en": "QuickBMS download + build", "ko": "QuickBMS 다운로드+빌드"},
    "qb_noscript_title": {"en": "Script missing", "ko": "스크립트 없음"},
    "qb_noscript_msg": {"en": "{path} is missing.", "ko": "{path} 가 없습니다."},
    "pick_quickbms_title": {"en": "Select the quickbms executable",
                            "ko": "quickbms 실행 파일 선택"},
    # generic dialogs / logs ------------------------------------------------
    "open_folder_title": {"en": "Open folder", "ko": "폴더 열기"},
    "open_folder_msg": {"en": "The folder doesn't exist yet.",
                        "ko": "폴더가 아직 없습니다."},
    "open_fail_title": {"en": "Open failed", "ko": "열기 실패"},
    "sent_to_convert": {"en": "Set the Convert tab input to the extract output.",
                        "ko": "변환 탭 입력 폴더를 추출 결과로 설정했습니다."},
    "log_quickbms": {"en": "quickbms: {v}", "ko": "quickbms: {v}"},
    "log_scripts": {"en": "scripts : CMP={c} PAC={p}",
                    "ko": "scripts : CMP={c} PAC={p}"},
    "log_native_on": {"en": "QuickBMS not found — enabled native (Python) "
                            "mode.",
                      "ko": "QuickBMS가 없어 '네이티브(파이썬)' 모드를 켰습니다."},
    "qb_not_found": {"en": "NOT FOUND — build it or browse to it",
                     "ko": "없음 — 빌드하거나 경로를 지정하세요"},
    "busy_title": {"en": "Busy", "ko": "작업 중"},
    "busy_msg": {"en": "Wait until the current task finishes, then try again.",
                 "ko": "현재 작업이 끝난 뒤 다시 시도하세요."},
    "err_input_title": {"en": "Input error", "ko": "입력 오류"},
    "err_input_ex": {"en": "Pick a valid input folder.",
                     "ko": "유효한 입력 폴더를 선택하세요."},
    "err_input_cv": {"en": "Pick a valid input folder (the extract output).",
                     "ko": "유효한 입력 폴더(추출 결과)를 선택하세요."},
    "err_output_title": {"en": "Output error", "ko": "출력 오류"},
    "err_output_msg": {"en": "Pick an output folder.", "ko": "출력 폴더를 선택하세요."},
    "run_extract": {"en": "Extracting…", "ko": "추출 실행 중…"},
    "run_convert": {"en": "Converting…", "ko": "변환 실행 중…"},
    "stop_requested": {"en": "Stop requested…", "ko": "중지 요청…"},
    "status_error": {"en": "Error", "ko": "오류"},
    "job_fail_title": {"en": "Task failed", "ko": "작업 실패"},
    "cancelled_suffix": {"en": "  (cancelled)", "ko": "  (중지됨)"},
    "extract_done": {"en": "Extract done: decompressed {d}, extracted {e}, "
                           "failed {f} — {t:.1f}s",
                     "ko": "추출 완료: 압축해제 {d}, 추출 {e}, 실패 {f} — {t:.1f}s"},
    "convert_done": {"en": "Convert done: {c} converted, {f} failed — "
                           "{t:.1f}s",
                     "ko": "변환 완료: {c}개 변환, 실패 {f} — {t:.1f}s"},
    "partial_fail_title": {"en": "Some failures", "ko": "일부 실패"},
    "partial_fail_ex": {"en": "{n} file(s) failed.", "ko": "{n}개 파일에서 오류가 발생했습니다."},
    "partial_fail_cv": {"en": "{n} file(s) failed.\nSee the log for details.",
                        "ko": "{n}개 파일에서 오류가 발생했습니다.\n로그 창에서 상세 내용을 확인하세요."},
}


class SifacGUI:
    def __init__(self, root: "tk.Tk"):
        self.root = root
        self.lang = "en"
        self._txt: list = []            # (widget, key) for live re-translation
        self._status_render = lambda: self.t("idle")

        self._worker: threading.Thread | None = None
        self._extractor: engine.Extractor | None = None
        self._converter: convert.Converter | None = None
        self._msgq: "queue.Queue[tuple]" = queue.Queue()
        self._pending_convert = False
        self._pending_retarget = False
        self._pending_bundle = None
        self._proc = None

        root.title(self.t("title"))
        root.minsize(820, 660)

        self._build_widgets()
        self._autodetect()
        self._refresh_bpy_status()
        self.root.after(100, self._drain_queue)

    # --------------------------------------------------------- i18n helpers

    def t(self, key: str, **fmt) -> str:
        entry = STRINGS.get(key)
        s = (entry.get(self.lang) or entry.get("en")) if entry else key
        return s.format(**fmt) if fmt else s

    def _tr(self, widget, key: str):
        """Set a widget's text now and register it for live re-translation."""
        self._txt.append((widget, key))
        widget.configure(text=self.t(key))
        return widget

    def _on_lang_change(self, *_):
        self.lang = LANGS[self.lang_combo.current()][0]
        self._retranslate()

    def _retranslate(self) -> None:
        self.root.title(self.t("title"))
        for widget, key in self._txt:
            try:
                widget.configure(text=self.t(key))
            except tk.TclError:
                pass
        self.nb.tab(self.extract_tab, text=self.t("tab_extract"))
        self.nb.tab(self.convert_tab, text=self.t("tab_convert"))
        self.nb.tab(self.retarget_tab, text=self.t("tab_retarget"))
        self._refresh_combo(self.preset_combo,
                            ["preset_ex_" + p for p in EXTRACT_PRESETS])
        self._refresh_combo(self.cpreset_combo,
                            ["preset_cv_" + p for p in CONVERT_PRESETS])
        self._refresh_combo(self.engine_combo,
                            ["engine_auto", "engine_blender", "engine_python"])
        self._refresh_combo(self.upaxis_combo, ["upaxis_y", "upaxis_z"])
        self._refresh_bpy_status()
        if not (self._worker and self._worker.is_alive()):
            self.var_status.set(self._status_render())

    def _refresh_combo(self, combo, keys) -> None:
        idx = combo.current()
        combo.configure(values=[self.t(k) for k in keys])
        if idx >= 0:
            combo.current(idx)

    # ------------------------------------------------------------------ UI

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(2, weight=1)
        outer.columnconfigure(0, weight=1)

        # Top bar: language selector (right-aligned).
        top = ttk.Frame(outer)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        self._tr(ttk.Label(top), "lang_label").grid(row=0, column=1,
                                                     sticky="e", padx=(0, 4))
        self.lang_combo = ttk.Combobox(top, width=10, state="readonly",
                                       values=[name for _, name in LANGS])
        self.lang_combo.current(0)
        self.lang_combo.grid(row=0, column=2, sticky="e")
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_lang_change)

        self.nb = ttk.Notebook(outer)
        self.nb.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.extract_tab = ttk.Frame(self.nb, padding=8)
        self.convert_tab = ttk.Frame(self.nb, padding=8)
        self.retarget_tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.extract_tab, text=self.t("tab_extract"))
        self.nb.add(self.convert_tab, text=self.t("tab_convert"))
        self.nb.add(self.retarget_tab, text=self.t("tab_retarget"))
        self._build_extract_tab(self.extract_tab)
        self._build_convert_tab(self.convert_tab)
        self._build_retarget_tab(self.retarget_tab)

        # Shared progress + log at the bottom.
        bottom = ttk.Frame(outer)
        bottom.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        bottom.rowconfigure(2, weight=1)
        bottom.columnconfigure(0, weight=1)
        self.var_status = tk.StringVar(value=self.t("idle"))
        ttk.Label(bottom, textvariable=self.var_status).grid(
            row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.grid(row=1, column=0, sticky="ew", pady=4)
        logframe = ttk.Frame(bottom)
        logframe.grid(row=2, column=0, sticky="nsew")
        logframe.rowconfigure(0, weight=1)
        logframe.columnconfigure(0, weight=1)
        self.log = tk.Text(logframe, height=12, wrap="none", state="disabled",
                           font=("Menlo", 11) if sys.platform == "darwin"
                           else ("Consolas", 10))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logframe, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        for tag, color in (("error", "#c0392b"), ("warn", "#b9770e"),
                           ("ok", "#1e8449"), ("info", "#2471a3"),
                           ("build", "#6c3483")):
            self.log.tag_configure(tag, foreground=color)

    # ------------------------------------------------------- Extract tab

    def _build_extract_tab(self, main: "ttk.Frame") -> None:
        pad = {"padx": 6, "pady": 4}
        main.columnconfigure(1, weight=1)
        row = 0
        self._tr(ttk.Label(main), "ex_input").grid(row=row, column=0, sticky="w", **pad)
        self.var_input = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_input).grid(row=row, column=1, sticky="ew", **pad)
        self._tr(ttk.Button(main, command=self._pick(self.var_input, True)), "browse").grid(row=row, column=2, **pad)

        row += 1
        self._tr(ttk.Label(main), "ex_output").grid(row=row, column=0, sticky="w", **pad)
        self.var_output = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_output).grid(row=row, column=1, sticky="ew", **pad)
        self._tr(ttk.Button(main, command=self._pick(self.var_output, True)), "browse").grid(row=row, column=2, **pad)

        row += 1
        self._tr(ttk.Label(main), "ex_quickbms").grid(row=row, column=0, sticky="w", **pad)
        self.var_quickbms = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_quickbms).grid(row=row, column=1, sticky="ew", **pad)
        self._tr(ttk.Button(main, command=self._pick_quickbms), "browse").grid(row=row, column=2, **pad)

        row += 1
        setup = ttk.Frame(main)
        setup.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        self._tr(ttk.Button(setup, command=self._setup_quickbms), "ex_build_pick").pack(side="left", padx=2)
        self._tr(ttk.Button(setup, command=self._download_quickbms), "ex_build_auto").pack(side="left", padx=2)
        self.var_qbstatus = tk.StringVar(value="")
        ttk.Label(setup, textvariable=self.var_qbstatus).pack(side="left", padx=8)

        row += 1
        opts = ttk.LabelFrame(main, padding=8)
        self._tr(opts, "options")
        opts.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        self._tr(ttk.Label(opts), "jobs").grid(row=0, column=0, sticky="w", padx=4)
        self.var_jobs = tk.IntVar(value=max(1, os.cpu_count() or 4))
        ttk.Spinbox(opts, from_=1, to=64, width=5, textvariable=self.var_jobs).grid(row=0, column=1, sticky="w", padx=4)
        self._tr(ttk.Label(opts), "ex_target").grid(row=0, column=2, sticky="w", padx=4)
        self.preset_combo = ttk.Combobox(opts, width=22, state="readonly",
                                          values=[self.t("preset_ex_" + p) for p in EXTRACT_PRESETS])
        self.preset_combo.current(0)
        self.preset_combo.grid(row=0, column=3, sticky="w", padx=4)
        self.var_decompress = tk.BooleanVar(value=True)
        self.var_extract = tk.BooleanVar(value=True)
        self._tr(ttk.Checkbutton(opts, variable=self.var_decompress), "ex_cb_cmp").grid(row=1, column=0, columnspan=2, sticky="w", padx=4)
        self._tr(ttk.Checkbutton(opts, variable=self.var_extract), "ex_cb_arc").grid(row=1, column=2, columnspan=2, sticky="w", padx=4)
        self.var_skip = tk.BooleanVar(value=False)
        self._tr(ttk.Checkbutton(opts, variable=self.var_skip), "ex_cb_skip").grid(row=1, column=4, columnspan=2, sticky="w", padx=4)
        self.var_native = tk.BooleanVar(value=False)
        self._tr(ttk.Checkbutton(opts, variable=self.var_native), "ex_cb_native").grid(row=2, column=0, columnspan=6, sticky="w", padx=4)
        self.var_collapse = tk.BooleanVar(value=True)
        self._tr(ttk.Checkbutton(opts, variable=self.var_collapse), "ex_cb_collapse").grid(row=3, column=0, columnspan=6, sticky="w", padx=4)

        row += 1
        btns = ttk.Frame(main)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        self.btn_start = ttk.Button(btns, command=self._start_extract)
        self._tr(self.btn_start, "ex_start")
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(btns, command=self._stop, state="disabled")
        self._tr(self.btn_stop, "stop")
        self.btn_stop.pack(side="left", padx=4)
        self._tr(ttk.Button(btns, command=lambda: self._open(self.var_output)), "open_output").pack(side="left", padx=4)
        self._tr(ttk.Button(btns, command=self._autodetect), "ex_autodetect").pack(side="left", padx=4)
        self._tr(ttk.Button(btns, command=self._send_to_convert), "ex_send").pack(side="left", padx=4)

    # ------------------------------------------------------- Convert tab

    def _build_convert_tab(self, main: "ttk.Frame") -> None:
        pad = {"padx": 6, "pady": 4}
        main.columnconfigure(1, weight=1)
        row = 0
        self._tr(ttk.Label(main), "cv_input").grid(row=row, column=0, sticky="w", **pad)
        self.var_cin = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_cin).grid(row=row, column=1, sticky="ew", **pad)
        self._tr(ttk.Button(main, command=self._pick(self.var_cin, True)), "browse").grid(row=row, column=2, **pad)

        row += 1
        self._tr(ttk.Label(main), "cv_output").grid(row=row, column=0, sticky="w", **pad)
        self.var_cout = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_cout).grid(row=row, column=1, sticky="ew", **pad)
        self._tr(ttk.Button(main, command=self._pick(self.var_cout, True)), "browse").grid(row=row, column=2, **pad)

        row += 1
        self._tr(ttk.Label(main), "cv_models").grid(row=row, column=0, sticky="w", **pad)
        self.var_cmodels = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_cmodels).grid(row=row, column=1, sticky="ew", **pad)
        self._tr(ttk.Button(main, command=self._pick(self.var_cmodels, True)), "browse").grid(row=row, column=2, **pad)

        row += 1
        opts = ttk.LabelFrame(main, padding=8)
        self._tr(opts, "options")
        opts.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        opts.columnconfigure(5, weight=1)
        self._tr(ttk.Label(opts), "jobs").grid(row=0, column=0, sticky="w", padx=4)
        self.var_cjobs = tk.IntVar(value=max(1, os.cpu_count() or 4))
        ttk.Spinbox(opts, from_=1, to=64, width=5, textvariable=self.var_cjobs).grid(row=0, column=1, sticky="w", padx=4)
        self._tr(ttk.Label(opts), "cv_target").grid(row=0, column=2, sticky="w", padx=4)
        self.cpreset_combo = ttk.Combobox(opts, width=26, state="readonly",
                                          values=[self.t("preset_cv_" + p) for p in CONVERT_PRESETS])
        self.cpreset_combo.current(0)
        self.cpreset_combo.grid(row=0, column=3, sticky="w", padx=4)
        self._tr(ttk.Label(opts), "cv_scale").grid(row=0, column=4, sticky="e", padx=4)
        self.var_scale = tk.StringVar(value="1.0")
        ttk.Entry(opts, width=7, textvariable=self.var_scale).grid(row=0, column=5, sticky="w", padx=4)

        self._tr(ttk.Label(opts), "cv_engine").grid(row=4, column=0, sticky="w", padx=4)
        self.engine_combo = ttk.Combobox(
            opts, width=52, state="readonly",
            values=[self.t("engine_auto"), self.t("engine_blender"),
                    self.t("engine_python")])
        self.engine_combo.current(0)
        self.engine_combo.grid(row=4, column=1, columnspan=5, sticky="w", padx=4)
        self.engine_combo.bind("<<ComboboxSelected>>",
                               lambda *_: self._refresh_bpy_status())

        # bpy status + one-click installer (for the Blender engine).
        bpyrow = ttk.Frame(opts)
        bpyrow.grid(row=5, column=0, columnspan=6, sticky="w", padx=4, pady=(2, 0))
        self.var_bpystatus = tk.StringVar(value=self.t("bpy_checking"))
        self.lbl_bpystatus = ttk.Label(bpyrow, textvariable=self.var_bpystatus)
        self.lbl_bpystatus.pack(side="left")
        self.btn_bpy = ttk.Button(bpyrow, command=self._install_bpy)
        self.btn_bpy.pack(side="left", padx=8)

        self._tr(ttk.Label(opts), "cv_upaxis").grid(row=6, column=0, sticky="w", padx=4, pady=(2, 0))
        self.upaxis_combo = ttk.Combobox(
            opts, width=40, state="readonly",
            values=[self.t("upaxis_y"), self.t("upaxis_z")])
        self.upaxis_combo.current(0)
        self.upaxis_combo.grid(row=6, column=1, columnspan=5, sticky="w", padx=4, pady=(2, 0))

        self.var_morphs = tk.BooleanVar(value=True)
        self.var_ctex = tk.BooleanVar(value=True)
        self.var_animonly = tk.BooleanVar(value=False)
        self.var_bundle = tk.BooleanVar(value=False)
        self.var_cskip = tk.BooleanVar(value=False)
        self._tr(ttk.Checkbutton(opts, variable=self.var_morphs), "cv_cb_morphs").grid(row=1, column=0, columnspan=2, sticky="w", padx=4)
        self._tr(ttk.Checkbutton(opts, variable=self.var_ctex), "cv_cb_tex").grid(row=1, column=2, columnspan=2, sticky="w", padx=4)
        self._tr(ttk.Checkbutton(opts, variable=self.var_animonly), "cv_cb_animonly").grid(row=1, column=4, columnspan=2, sticky="w", padx=4)
        self._tr(ttk.Checkbutton(opts, variable=self.var_bundle), "cv_cb_bundle").grid(row=2, column=0, columnspan=3, sticky="w", padx=4)
        self._tr(ttk.Checkbutton(opts, variable=self.var_cskip), "cv_cb_skip").grid(row=2, column=3, columnspan=2, sticky="w", padx=4)

        row += 1
        info = ttk.Label(main, foreground="#555", wraplength=760, justify="left")
        self._tr(info, "cv_info")
        info.grid(row=row, column=0, columnspan=3, sticky="w", **pad)

        row += 1
        btns = ttk.Frame(main)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        self.btn_cstart = ttk.Button(btns, command=self._start_convert)
        self._tr(self.btn_cstart, "cv_start")
        self.btn_cstart.pack(side="left", padx=4)
        self.btn_cstop = ttk.Button(btns, command=self._stop, state="disabled")
        self._tr(self.btn_cstop, "stop")
        self.btn_cstop.pack(side="left", padx=4)
        self._tr(ttk.Button(btns, command=lambda: self._open(self.var_cout)), "open_output").pack(side="left", padx=4)

    # ------------------------------------------------------- Retarget tab

    def _build_retarget_tab(self, main: "ttk.Frame") -> None:
        pad = {"padx": 6, "pady": 4}
        main.columnconfigure(1, weight=1)
        row = 0
        self._tr(ttk.Label(main), "rt_input").grid(row=row, column=0, sticky="w", **pad)
        self.var_rin = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_rin).grid(row=row, column=1, sticky="ew", **pad)
        self._tr(ttk.Button(main, command=self._pick_rin), "browse").grid(row=row, column=2, **pad)

        row += 1
        self._tr(ttk.Label(main), "rt_output").grid(row=row, column=0, sticky="w", **pad)
        self.var_rout = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_rout).grid(row=row, column=1, sticky="ew", **pad)
        self._tr(ttk.Button(main, command=self._pick(self.var_rout, True)), "browse").grid(row=row, column=2, **pad)

        row += 1
        self._tr(ttk.Label(main), "rt_member").grid(row=row, column=0, sticky="w", **pad)
        self.var_rmember = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_rmember).grid(row=row, column=1, sticky="ew", **pad)

        row += 1
        self._tr(ttk.Label(main), "rt_prefab").grid(row=row, column=0, sticky="w", **pad)
        self.var_rprefab = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_rprefab).grid(row=row, column=1, sticky="ew", **pad)
        self._tr(ttk.Button(main, command=self._pick(self.var_rprefab, False)), "browse").grid(row=row, column=2, **pad)

        row += 1
        opts = ttk.LabelFrame(main, padding=8)
        self._tr(opts, "options")
        opts.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        opts.columnconfigure(5, weight=1)
        self.var_rbatch = tk.BooleanVar(value=False)
        self._tr(ttk.Checkbutton(opts, variable=self.var_rbatch), "rt_cb_batch").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=4)
        self.var_rroot = tk.BooleanVar(value=True)
        self._tr(ttk.Checkbutton(opts, variable=self.var_rroot), "rt_cb_root").grid(
            row=0, column=3, columnspan=2, sticky="w", padx=4)

        self._tr(ttk.Label(opts), "rt_twist").grid(row=1, column=0, columnspan=2, sticky="w", padx=4)
        self.var_rtwist = tk.DoubleVar(value=1.0)
        ttk.Spinbox(opts, from_=0.0, to=3.0, increment=0.1, width=6,
                    textvariable=self.var_rtwist).grid(row=1, column=2, sticky="w", padx=4)
        self._tr(ttk.Label(opts), "rt_smooth").grid(row=1, column=3, sticky="e", padx=4)
        self.var_rsmooth = tk.IntVar(value=0)
        ttk.Spinbox(opts, from_=0, to=21, increment=1, width=6,
                    textvariable=self.var_rsmooth).grid(row=1, column=4, sticky="w", padx=4)

        self._tr(ttk.Label(opts), "rt_step").grid(row=2, column=0, columnspan=2, sticky="w", padx=4)
        self.var_rstep = tk.IntVar(value=1)
        ttk.Spinbox(opts, from_=1, to=10, increment=1, width=6,
                    textvariable=self.var_rstep).grid(row=2, column=2, sticky="w", padx=4)
        self._tr(ttk.Label(opts), "rt_song").grid(row=2, column=3, sticky="e", padx=4)
        self.var_rsong = tk.DoubleVar(value=0.0)   # 0 = off (no trim)
        ttk.Spinbox(opts, from_=0.0, to=600.0, increment=1.0, width=8,
                    textvariable=self.var_rsong).grid(row=2, column=4, sticky="w", padx=4)

        self._tr(ttk.Label(opts), "rt_formats").grid(row=3, column=0, sticky="w", padx=4)
        fmtrow = ttk.Frame(opts)
        fmtrow.grid(row=3, column=1, columnspan=5, sticky="w", padx=4)
        self.var_fmt_anim = tk.BooleanVar(value=True)
        self.var_fmt_fbx = tk.BooleanVar(value=False)
        self.var_fmt_glb = tk.BooleanVar(value=False)
        self.var_fmt_bvh = tk.BooleanVar(value=False)
        for txt, var in (("Unity .anim", self.var_fmt_anim), ("FBX", self.var_fmt_fbx),
                         ("glTF (.glb)", self.var_fmt_glb), ("BVH", self.var_fmt_bvh)):
            ttk.Checkbutton(fmtrow, text=txt, variable=var).pack(side="left", padx=4)

        # Target model: bake fbx/glb/bvh straight onto the user's own SIFAS rig
        # (absolute world orientation -> no limb twisting from bone-roll diffs).
        self._tr(ttk.Label(opts), "rt_target_model").grid(row=4, column=0, sticky="w", padx=4)
        self.var_rmodel = tk.StringVar()
        ttk.Entry(opts, textvariable=self.var_rmodel).grid(
            row=4, column=1, columnspan=4, sticky="ew", padx=4)
        self._tr(ttk.Button(opts, command=self._pick(self.var_rmodel, False)),
                 "browse").grid(row=4, column=5, sticky="w", padx=4)

        self.var_rbundle = tk.BooleanVar(value=False)
        self._tr(ttk.Checkbutton(opts, variable=self.var_rbundle), "rt_cb_bundle").grid(
            row=5, column=0, columnspan=4, sticky="w", padx=4, pady=(2, 0))
        self._tr(ttk.Label(opts), "rt_template").grid(row=6, column=0, sticky="w", padx=4)
        self.var_rtemplate = tk.StringVar()
        ttk.Entry(opts, textvariable=self.var_rtemplate).grid(
            row=6, column=1, columnspan=4, sticky="ew", padx=4)
        self._tr(ttk.Button(opts, command=self._pick(self.var_rtemplate, False)),
                 "browse").grid(row=6, column=5, sticky="w", padx=4)

        row += 1
        info = ttk.Label(main, foreground="#555", wraplength=760, justify="left")
        self._tr(info, "rt_info")
        info.grid(row=row, column=0, columnspan=3, sticky="w", **pad)

        row += 1
        btns = ttk.Frame(main)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        self.btn_rstart = ttk.Button(btns, command=self._start_retarget)
        self._tr(self.btn_rstart, "rt_start")
        self.btn_rstart.pack(side="left", padx=4)
        self.btn_rstop = ttk.Button(btns, command=self._stop, state="disabled")
        self._tr(self.btn_rstop, "stop")
        self.btn_rstop.pack(side="left", padx=4)
        self._tr(ttk.Button(btns, command=lambda: self._open(self.var_rout)), "open_output").pack(side="left", padx=4)

    def _pick_rin(self) -> None:
        if bool(self.var_rbatch.get()):
            d = filedialog.askdirectory()
        else:
            d = filedialog.askopenfilename(
                filetypes=[("FBX", "*.fbx"), ("All files", "*.*")])
        if d:
            self.var_rin.set(d)

    # -------------------------------------------------------------- helpers

    def _pick(self, var, is_dir):
        def cb():
            d = (filedialog.askdirectory() if is_dir
                 else filedialog.askopenfilename())
            if d:
                var.set(d)
        return cb

    def _pick_quickbms(self) -> None:
        f = filedialog.askopenfilename(title=self.t("pick_quickbms_title"))
        if f:
            self.var_quickbms.set(f)

    def _open(self, var) -> None:
        out = var.get()
        if not out or not Path(out).is_dir():
            messagebox.showinfo(self.t("open_folder_title"),
                                self.t("open_folder_msg"))
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", out])
            elif os.name == "nt":
                os.startfile(out)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", out])
        except Exception as exc:
            messagebox.showerror(self.t("open_fail_title"), str(exc))

    def _send_to_convert(self) -> None:
        """Use the extractor's output folder as the converter's input."""
        out = self.var_output.get().strip()
        if out:
            extracted = str(Path(out) / "02_extracted")
            self.var_cin.set(extracted if Path(extracted).is_dir() else out)
            if not self.var_cout.get():
                self.var_cout.set(str(Path(out).parent / "sifac_fbx"))
            self._log("info", self.t("sent_to_convert"))

    def _autodetect(self) -> None:
        qb = engine.find_quickbms(self.var_quickbms.get() or None)
        if qb:
            self.var_quickbms.set(str(qb))
        cmp_s, pac_s = engine.find_scripts()
        self._log("info", self.t("log_quickbms",
                                 v=qb or self.t("qb_not_found")))
        self._log("info", self.t("log_scripts",
                                 c="OK" if cmp_s else "MISSING",
                                 p="OK" if pac_s else "MISSING"))
        if not qb and getattr(engine, "native", None) is not None:
            self.var_native.set(True)
            self._log("info", self.t("log_native_on"))

    def _log(self, level: str, msg: str) -> None:
        self.log.configure(state="normal")
        tag = level if level in ("error", "warn", "ok", "info", "build") else None
        self.log.insert("end", f"[{level}] {msg}\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _busy(self) -> bool:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo(self.t("busy_title"), self.t("busy_msg"))
            return True
        return False

    def _set_running(self, running: bool) -> None:
        state_run = "disabled" if running else "normal"
        state_stop = "normal" if running else "disabled"
        for b in (self.btn_start, self.btn_cstart, self.btn_rstart):
            b.configure(state=state_run)
        for b in (self.btn_stop, self.btn_cstop, self.btn_rstop):
            b.configure(state=state_stop)
        if hasattr(self, "btn_bpy"):
            self.btn_bpy.configure(state=state_run)

    # ---------------------------------------------------------- bpy setup

    def _bpy_installed(self) -> bool:
        """Ask the *target* interpreter whether ``bpy`` is importable.  Doing it
        in a subprocess (rather than this process) keeps the answer correct even
        right after an install, with no import-cache staleness."""
        try:
            r = subprocess.run(
                [sys.executable, "-c",
                 "import importlib.util,sys;"
                 "sys.exit(0 if importlib.util.find_spec('bpy') else 1)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return r.returncode == 0
        except Exception:
            return False

    def _refresh_bpy_status(self) -> bool:
        ok = self._bpy_installed()
        self.var_bpystatus.set(self.t("bpy_ok") if ok else self.t("bpy_missing"))
        try:
            self.lbl_bpystatus.configure(
                foreground="#1e8449" if ok else "#b9770e")
            self.btn_bpy.configure(
                text=self.t("bpy_btn_reinstall") if ok
                else self.t("bpy_btn_install"))
        except Exception:
            pass
        return ok

    def _install_bpy(self) -> None:
        if self._busy():
            return
        self.var_bpystatus.set(self.t("bpy_installing"))
        self._set_running(True)
        self._log("info", self.t("bpy_install_start", py=sys.executable))
        py = sys.executable

        def work():
            try:
                # pip may be missing on bare interpreters; bootstrap it quietly.
                subprocess.run([py, "-m", "ensurepip", "--upgrade"],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
                proc = subprocess.Popen(
                    [py, "-m", "pip", "install", "--upgrade", "bpy"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, errors="replace")
                for line in proc.stdout:
                    self._msgq.put(("log", ("build", line.rstrip())))
                proc.wait()
                self._msgq.put(("bpy_done", (proc.returncode,)))
            except Exception as exc:
                self._msgq.put(("bpy_done", (-1, str(exc))))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    # ----------------------------------------------------- QuickBMS setup

    def _setup_quickbms(self) -> None:
        if self._busy():
            return
        d = filedialog.askdirectory(title=self.t("qb_pick_title"))
        if d:
            self._run_build([d], self.t("qb_label_build"))

    def _download_quickbms(self) -> None:
        if self._busy():
            return
        if not messagebox.askyesno(self.t("qb_dl_title"), self.t("qb_dl_msg")):
            return
        self._run_build(["--download"], self.t("qb_label_dl"))

    def _run_build(self, args: list, label: str) -> None:
        script = Path(__file__).resolve().parent / "build_quickbms_macos.sh"
        if not script.exists():
            messagebox.showerror(self.t("qb_noscript_title"),
                                 self.t("qb_noscript_msg", path=script))
            return
        cmd = ["bash", str(script), *args]
        self.var_qbstatus.set(self.t("qb_building"))
        self._set_running(True)
        self._log("info", f"{label}…")

        def work():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        errors="replace")
                for line in proc.stdout:
                    self._msgq.put(("log", ("build", line.rstrip())))
                proc.wait()
                self._msgq.put(("build_done", (proc.returncode,)))
            except Exception as exc:
                self._msgq.put(("build_done", (-1, str(exc))))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    # ----------------------------------------------------------- Extract run

    def _start_extract(self) -> None:
        if self._busy():
            return
        inp, out = self.var_input.get().strip(), self.var_output.get().strip()
        if not inp or not Path(inp).is_dir():
            messagebox.showerror(self.t("err_input_title"), self.t("err_input_ex"))
            return
        if not out:
            messagebox.showerror(self.t("err_output_title"), self.t("err_output_msg"))
            return
        preset = EXTRACT_PRESETS[self.preset_combo.current()]
        cfg = engine.ExtractConfig(
            input_dir=Path(inp), output_dir=Path(out),
            quickbms=engine.find_quickbms(self.var_quickbms.get() or None),
            jobs=int(self.var_jobs.get()),
            do_decompress=bool(self.var_decompress.get()),
            do_extract=bool(self.var_extract.get()),
            content_filter=engine.PRESET_CONTENT_FILTERS.get(preset),
            preset=preset, native=bool(self.var_native.get()),
            collapse_dupes=bool(self.var_collapse.get()),
            skip_existing=bool(self.var_skip.get()))
        cfg.cmp_script, cfg.pac_script = engine.find_scripts()
        self._extractor = engine.Extractor(
            cfg, progress_cb=lambda *a: self._msgq.put(("progress", a)),
            log_cb=lambda lv, m: self._msgq.put(("log", (lv, m))))
        self._begin("run_extract")
        self._worker = threading.Thread(target=self._extract_worker, daemon=True)
        self._worker.start()

    def _extract_worker(self) -> None:
        try:
            self._msgq.put(("edone", (self._extractor.run(),)))
        except Exception as exc:
            self._msgq.put(("error", (str(exc),)))

    # ----------------------------------------------------------- Convert run

    def _start_convert(self) -> None:
        if self._busy():
            return
        inp, out = self.var_cin.get().strip(), self.var_cout.get().strip()
        if not inp or not Path(inp).is_dir():
            messagebox.showerror(self.t("err_input_title"), self.t("err_input_cv"))
            return
        if not out:
            messagebox.showerror(self.t("err_output_title"), self.t("err_output_msg"))
            return
        try:
            scale = float(self.var_scale.get())
        except ValueError:
            scale = 1.0
        preset = CONVERT_PRESETS[self.cpreset_combo.current()]
        engine_choice = ["auto", "blender", "python"][self.engine_combo.current()]
        # The Blender engine needs bpy. If it isn't installed, offer to install
        # it now (one click) and resume the conversion automatically afterwards.
        if engine_choice == "blender" and not self._bpy_installed():
            if messagebox.askyesno(self.t("bpy_need_title"), self.t("bpy_need_msg")):
                self._pending_convert = True
                self._install_bpy()
                return
            engine_choice = "python"
            self._log("warn", self.t("bpy_fell_back"))
        self._pending_convert = False
        cfg = convert.ConvertConfig(
            input_dir=Path(inp), output_dir=Path(out), preset=preset,
            jobs=int(self.var_cjobs.get()), scale=scale,
            include_morphs=bool(self.var_morphs.get()),
            with_textures=bool(self.var_ctex.get()),
            anim_only=bool(self.var_animonly.get()),
            bundle_motions=bool(self.var_bundle.get()),
            models_dir=Path(self.var_cmodels.get()) if self.var_cmodels.get().strip() else None,
            engine=engine_choice,
            up_axis=["y", "z"][self.upaxis_combo.current()],
            skip_existing=bool(self.var_cskip.get()))
        self._converter = convert.Converter(
            cfg, progress_cb=lambda *a: self._msgq.put(("progress", a)),
            log_cb=lambda lv, m: self._msgq.put(("log", (lv, m))))
        self._begin("run_convert")
        self._worker = threading.Thread(target=self._convert_worker, daemon=True)
        self._worker.start()

    def _convert_worker(self) -> None:
        try:
            self._msgq.put(("cdone", (self._converter.run(),)))
        except Exception as exc:
            self._msgq.put(("error", (str(exc),)))

    # ---------------------------------------------------------- Retarget run

    def _start_retarget(self) -> None:
        if self._busy():
            return
        inp = self.var_rin.get().strip()
        out = self.var_rout.get().strip()
        batch = bool(self.var_rbatch.get())
        valid_in = (Path(inp).is_dir() if batch
                    else (Path(inp).is_file() and inp.lower().endswith(".fbx")))
        if not inp or not valid_in:
            messagebox.showerror(self.t("err_input_title"), self.t("rt_err_input"))
            return
        if not out:
            messagebox.showerror(self.t("err_output_title"), self.t("err_output_msg"))
            return
        formats = [name for name, var in (
            ("anim", self.var_fmt_anim), ("fbx", self.var_fmt_fbx),
            ("glb", self.var_fmt_glb), ("bvh", self.var_fmt_bvh)) if var.get()]
        if not formats:
            messagebox.showerror(self.t("err_input_title"), self.t("rt_err_fmt"))
            return
        # Optional: chain a Unity-bundle injection after the retarget.  Needs a
        # template bundle, single (non-batch) mode and the .anim on (we inject
        # the .anim).  Stored and kicked off when the retarget finishes.
        self._pending_bundle = None
        if bool(self.var_rbundle.get()):
            tmpl = self.var_rtemplate.get().strip()
            if batch or "anim" not in formats or not (tmpl and Path(tmpl).is_file()):
                messagebox.showerror(self.t("err_input_title"), self.t("rt_err_template"))
                return
            anim_path = str(Path(out) / (Path(inp).stem + ".anim"))
            bundle_path = str(Path(out) / (Path(inp).stem + ".unity"))
            self._pending_bundle = (tmpl, anim_path, bundle_path)
        # The retarget engine needs bpy. Offer the same one-click install as the
        # Convert tab, then resume automatically when it finishes.
        if not self._bpy_installed():
            if messagebox.askyesno(self.t("bpy_need_title"), self.t("bpy_need_msg")):
                self._pending_retarget = True
                self._install_bpy()
            return
        self._pending_retarget = False

        script = str(Path(__file__).resolve().parent / "sifac_anim_retarget.py")
        cmd = [sys.executable, script]
        if batch:
            cmd += ["--batch", inp, "--outdir", out]
        else:
            cmd += ["--sifac", inp,
                    "--out", str(Path(out) / (Path(inp).stem + ".anim"))]
        cmd += ["--twist-strength", "%g" % float(self.var_rtwist.get()),
                "--smooth", str(int(self.var_rsmooth.get())),
                "--step", str(max(1, int(self.var_rstep.get()))),
                "--format", ",".join(formats)]
        if not self.var_rroot.get():
            cmd.append("--no-root-motion")
        if self.var_rmember.get().strip():
            cmd += ["--member", self.var_rmember.get().strip()]
        if self.var_rprefab.get().strip():
            cmd += ["--prefab", self.var_rprefab.get().strip()]
        if self.var_rmodel.get().strip():
            cmd += ["--target-model", self.var_rmodel.get().strip()]
        try:
            song = float(self.var_rsong.get())
        except Exception:
            song = 0.0
        if song > 0:
            cmd += ["--song-length", "%g" % song]
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._begin("run_retarget")
        self._run_stream(cmd, "retarget_done")

    def _start_bundle_inject(self) -> None:
        """Kick off the Unity-bundle injection queued by _start_retarget."""
        tmpl, anim_path, bundle_path = self._pending_bundle
        self._pending_bundle = None
        # the retarget exited 0; make sure the .anim it should have written is
        # actually there before handing it to the injector (clearer than a
        # downstream "file not found").
        if not Path(anim_path).is_file():
            self._set_running(False)
            self._status_render = lambda: self.t("rt_bundle_fail", rc=2)
            self.var_status.set(self.t("rt_bundle_fail", rc=2))
            self._log("error", self.t("rt_bundle_fail", rc=2)
                      + ("\nmissing: %s" % anim_path))
            return
        script = str(Path(__file__).resolve().parent / "sifac_anim_to_bundle.py")
        cmd = [sys.executable, script, "--template", tmpl,
               "--anim", anim_path, "--out", bundle_path]
        self.var_status.set(self.t("rt_bundle_running"))
        self._set_running(True)
        self._run_stream(cmd, "bundle_done")

    @staticmethod
    def _parse_progress(line: str):
        """`[progress] <stage> <done>/<total>` -> (stage, done, total) or None."""
        s = line.strip()
        if not s.startswith("[progress] "):
            return None
        parts = s[len("[progress] "):].split()
        if len(parts) == 2 and "/" in parts[1]:
            d, _, t = parts[1].partition("/")
            try:
                return (parts[0], int(d), int(t))
            except ValueError:
                return None
        return None

    def _run_stream(self, cmd: list, done_kind: str) -> None:
        """Run a subprocess, stream its stdout to the log, post ``done_kind``
        with the return code when it exits.  `[progress] stage d/t` lines drive
        the progress bar instead of the log.  The Popen is tracked so Stop can
        terminate it."""
        # run the child unbuffered so progress/log lines arrive live, not in a
        # block at the end.
        if cmd and cmd[0] == sys.executable and "-u" not in cmd:
            cmd = [cmd[0], "-u"] + cmd[1:]

        def work():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        errors="replace")
                self._proc = proc
                for line in proc.stdout:
                    pr = self._parse_progress(line)
                    if pr:
                        self._msgq.put(("progress", (pr[0], pr[1], pr[2], "")))
                    else:
                        self._msgq.put(("log", ("build", line.rstrip())))
                proc.wait()
                self._msgq.put((done_kind, (proc.returncode,)))
            except Exception as exc:
                self._msgq.put((done_kind, (-1, str(exc))))
            finally:
                self._proc = None

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _begin(self, status_key: str) -> None:
        self.progress.configure(value=0, maximum=100)
        self._status_render = lambda: self.t(status_key)
        self.var_status.set(self.t(status_key))
        self._set_running(True)

    def _stop(self) -> None:
        if self._extractor and self._worker and self._worker.is_alive():
            self._extractor.request_stop()
        if self._converter and self._worker and self._worker.is_alive():
            self._converter.request_stop()
        # retarget / bundle run as subprocesses (no request_stop); kill the Popen
        proc = getattr(self, "_proc", None)
        if proc is not None and proc.poll() is None:
            self._pending_bundle = None   # don't chain after a manual stop
            try:
                proc.terminate()
            except Exception:
                pass
        self._log("warn", self.t("stop_requested"))

    # ------------------------------------------------------- queue pumping

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._msgq.get_nowait()
                if kind == "log":
                    self._log(*payload)
                elif kind == "progress":
                    stage, done, total, current = payload
                    self.progress.configure(maximum=max(1, total), value=done)
                    pct = int(100 * done / total) if total else 0
                    tail = f"  {current}" if current else ""
                    self.var_status.set(f"{stage}: {done}/{total} ({pct}%){tail}")
                elif kind == "edone":
                    self._on_extract_done(payload[0])
                elif kind == "cdone":
                    self._on_convert_done(payload[0])
                elif kind == "retarget_done":
                    rc = payload[0]
                    if rc == 0:
                        self._log("ok", self.t("rt_done"))
                        if self._pending_bundle:
                            self._start_bundle_inject()   # chain the injection
                        else:
                            self._set_running(False)
                            self._status_render = lambda: self.t("rt_done")
                            self.var_status.set(self.t("rt_done"))
                    else:
                        self._pending_bundle = None
                        self._set_running(False)
                        self._status_render = lambda: self.t("rt_done_fail", rc=rc)
                        self.var_status.set(self.t("rt_done_fail", rc=rc))
                        detail = f"\n{payload[1]}" if len(payload) > 1 else ""
                        self._log("error", self.t("rt_done_fail", rc=rc) + detail)
                elif kind == "bundle_done":
                    rc = payload[0]
                    self._set_running(False)
                    if rc == 0:
                        self._status_render = lambda: self.t("rt_bundle_done")
                        self.var_status.set(self.t("rt_bundle_done"))
                        self._log("ok", self.t("rt_bundle_done"))
                    else:
                        self._status_render = lambda: self.t("rt_bundle_fail", rc=rc)
                        self.var_status.set(self.t("rt_bundle_fail", rc=rc))
                        detail = f"\n{payload[1]}" if len(payload) > 1 else ""
                        self._log("error", self.t("rt_bundle_fail", rc=rc) + detail)
                elif kind == "error":
                    self._set_running(False)
                    self._status_render = lambda: self.t("status_error")
                    self.var_status.set(self.t("status_error"))
                    self._log("error", payload[0])
                    messagebox.showerror(self.t("job_fail_title"), payload[0])
                elif kind == "build_done":
                    rc = payload[0]
                    self._set_running(False)
                    self._autodetect()
                    self.var_qbstatus.set(self.t("qb_done") if rc == 0
                                          else self.t("qb_failed"))
                    if rc != 0 and len(payload) > 1:
                        self._log("error", payload[1])
                elif kind == "bpy_done":
                    rc = payload[0]
                    self._set_running(False)
                    ok = self._refresh_bpy_status()
                    if rc == 0 and ok:
                        self._log("ok", self.t("bpy_done_ok"))
                        if self._pending_convert:
                            self._log("info", self.t("bpy_resume"))
                            self._start_convert()
                        elif self._pending_retarget:
                            self._log("info", self.t("bpy_resume"))
                            self._start_retarget()
                    else:
                        self._pending_convert = False
                        self._pending_retarget = False
                        detail = f"\n{payload[1]}" if len(payload) > 1 else ""
                        self._log("error", self.t("bpy_fail_log") + detail)
                        messagebox.showerror(
                            self.t("bpy_fail_title"),
                            self.t("bpy_fail_msg", detail=detail))
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _on_extract_done(self, summary) -> None:
        self._set_running(False)

        def render():
            s = self.t("extract_done", d=summary.decompressed,
                       e=summary.extracted, f=len(summary.failures),
                       t=summary.elapsed)
            return s + (self.t("cancelled_suffix") if summary.cancelled else "")
        self._status_render = render
        self.var_status.set(render())
        if summary.failures and not summary.cancelled:
            messagebox.showwarning(self.t("partial_fail_title"),
                                   self.t("partial_fail_ex",
                                          n=len(summary.failures)))

    def _on_convert_done(self, summary) -> None:
        self._set_running(False)

        def render():
            s = self.t("convert_done", c=summary.converted,
                       f=len(summary.failures), t=summary.elapsed)
            return s + (self.t("cancelled_suffix") if summary.cancelled else "")
        self._status_render = render
        self.var_status.set(render())
        if summary.failures and not summary.cancelled:
            messagebox.showwarning(self.t("partial_fail_title"),
                                   self.t("partial_fail_cv",
                                          n=len(summary.failures)))


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("aqua")
    except Exception:
        pass
    SifacGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
