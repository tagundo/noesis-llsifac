#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIFAC Batch Extractor — graphical interface
===========================================

A small Tkinter front-end for ``sifac_extract.py``.  Tkinter ships with the
python.org and macOS system Python, so no ``pip install`` is required.

Usage
-----
    python3 sifac_gui.py

or on a Mac just double-click ``run_mac.command``.

The window lets you:
  * pick the input folder (the game's data) and an output folder,
  * pick / auto-detect the quickbms executable,
  * choose how many files to extract in parallel (the speed knob),
  * choose what to pull out (all / models / live / textures),
  * watch a progress bar and a live log, and Stop at any time.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

# Make sure we can import the sibling engine no matter the CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sifac_extract as engine  # noqa: E402

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
        "You can always use the command line tool instead: python3 sifac_extract.py --help\n")
    raise SystemExit(1)


# Bilingual (Korean / English) preset labels shown in the UI.
PRESET_LABELS = [
    ("all", "전체 (All)"),
    ("models", "모델링만 (Models)"),
    ("live", "라이브/모션만 (Live / Motions)"),
    ("textures", "텍스처만 (Textures)"),
]


class SifacGUI:
    def __init__(self, root: "tk.Tk"):
        self.root = root
        root.title("SIFAC Batch Extractor")
        root.minsize(760, 560)

        self._extractor: engine.Extractor | None = None
        self._worker: threading.Thread | None = None
        self._msgq: "queue.Queue[tuple]" = queue.Queue()

        self._build_widgets()
        self._autodetect()
        self.root.after(100, self._drain_queue)

    # ------------------------------------------------------------------ UI

    def _build_widgets(self) -> None:
        pad = {"padx": 6, "pady": 4}
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        row = 0
        # Input folder
        ttk.Label(main, text="입력 폴더 (Input)").grid(row=row, column=0, sticky="w", **pad)
        self.var_input = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_input).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(main, text="찾기…", command=self._pick_input).grid(row=row, column=2, **pad)

        row += 1
        # Output folder
        ttk.Label(main, text="출력 폴더 (Output)").grid(row=row, column=0, sticky="w", **pad)
        self.var_output = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_output).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(main, text="찾기…", command=self._pick_output).grid(row=row, column=2, **pad)

        row += 1
        # quickbms
        ttk.Label(main, text="quickbms 경로").grid(row=row, column=0, sticky="w", **pad)
        self.var_quickbms = tk.StringVar()
        ttk.Entry(main, textvariable=self.var_quickbms).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(main, text="찾기…", command=self._pick_quickbms).grid(row=row, column=2, **pad)

        row += 1
        # QuickBMS setup (build with a button — no Terminal needed)
        setup = ttk.Frame(main)
        setup.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        ttk.Button(setup, text="QuickBMS 빌드 (폴더 선택)…",
                   command=self._setup_quickbms).pack(side="left", padx=2)
        ttk.Button(setup, text="자동 다운로드+빌드",
                   command=self._download_quickbms).pack(side="left", padx=2)
        self.var_qbstatus = tk.StringVar(value="")
        ttk.Label(setup, textvariable=self.var_qbstatus).pack(side="left", padx=8)

        row += 1
        # Options row
        opts = ttk.LabelFrame(main, text="옵션 (Options)", padding=8)
        opts.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        opts.columnconfigure(5, weight=1)

        ttk.Label(opts, text="동시 작업 수 (Parallel jobs)").grid(row=0, column=0, sticky="w", padx=4)
        self.var_jobs = tk.IntVar(value=max(1, os.cpu_count() or 4))
        ttk.Spinbox(opts, from_=1, to=64, width=5, textvariable=self.var_jobs).grid(row=0, column=1, sticky="w", padx=4)

        ttk.Label(opts, text="추출 대상 (Extract)").grid(row=0, column=2, sticky="w", padx=4)
        self.preset_combo = ttk.Combobox(
            opts, width=22, state="readonly",
            values=[label for _, label in PRESET_LABELS])
        self.preset_combo.current(0)
        self.preset_combo.grid(row=0, column=3, sticky="w", padx=4)

        self.var_decompress = tk.BooleanVar(value=True)
        self.var_extract = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text=".cmp 압축해제", variable=self.var_decompress).grid(row=1, column=0, columnspan=2, sticky="w", padx=4)
        ttk.Checkbutton(opts, text=".arc 추출", variable=self.var_extract).grid(row=1, column=2, columnspan=2, sticky="w", padx=4)
        self.var_skip = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="기존 파일 건너뛰기 (skip existing)", variable=self.var_skip).grid(row=1, column=4, columnspan=2, sticky="w", padx=4)

        self.var_native = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts,
            text="QuickBMS 없이 추출 (네이티브 · 파이썬, 맥 권장)",
            variable=self.var_native).grid(row=2, column=0, columnspan=6, sticky="w", padx=4)

        self.var_collapse = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts,
            text="중복 폴더 이름 합치기 (예: live_0001/live_0001 → live_0001)",
            variable=self.var_collapse).grid(row=3, column=0, columnspan=6, sticky="w", padx=4)

        row += 1
        # Buttons
        btns = ttk.Frame(main)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        self.btn_start = ttk.Button(btns, text="▶ 추출 시작 (Start)", command=self._start)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(btns, text="■ 중지 (Stop)", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        ttk.Button(btns, text="출력 폴더 열기", command=self._open_output).pack(side="left", padx=4)
        ttk.Button(btns, text="자동 감지 (Re-detect)", command=self._autodetect).pack(side="left", padx=4)

        row += 1
        # Progress
        self.var_status = tk.StringVar(value="대기 중 (idle)")
        ttk.Label(main, textvariable=self.var_status).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1
        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)

        row += 1
        # Log
        main.rowconfigure(row, weight=1)
        logframe = ttk.Frame(main)
        logframe.grid(row=row, column=0, columnspan=3, sticky="nsew", **pad)
        logframe.rowconfigure(0, weight=1)
        logframe.columnconfigure(0, weight=1)
        self.log = tk.Text(logframe, height=12, wrap="none", state="disabled",
                           font=("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10))
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logframe, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        for tag, color in (("error", "#c0392b"), ("warn", "#b9770e"),
                           ("ok", "#1e8449"), ("info", "#2471a3"),
                           ("build", "#6c3483")):
            self.log.tag_configure(tag, foreground=color)

    # -------------------------------------------------------------- helpers

    def _preset_key(self) -> str:
        idx = self.preset_combo.current()
        return PRESET_LABELS[idx][0] if 0 <= idx < len(PRESET_LABELS) else "all"

    def _pick_input(self) -> None:
        d = filedialog.askdirectory(title="입력 폴더 선택")
        if d:
            self.var_input.set(d)
            if not self.var_output.get():
                self.var_output.set(str(Path(d).parent / "sifac_extracted"))

    def _pick_output(self) -> None:
        d = filedialog.askdirectory(title="출력 폴더 선택")
        if d:
            self.var_output.set(d)

    def _pick_quickbms(self) -> None:
        f = filedialog.askopenfilename(title="quickbms 실행 파일 선택")
        if f:
            self.var_quickbms.set(f)

    def _open_output(self) -> None:
        out = self.var_output.get()
        if not out or not Path(out).is_dir():
            messagebox.showinfo("출력 폴더", "출력 폴더가 아직 없습니다.")
            return
        try:
            if sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", out])
            elif os.name == "nt":
                os.startfile(out)  # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.Popen(["xdg-open", out])
        except Exception as exc:
            messagebox.showerror("열기 실패", str(exc))

    def _autodetect(self) -> None:
        qb = engine.find_quickbms(self.var_quickbms.get() or None)
        if qb:
            self.var_quickbms.set(str(qb))
        cmp_s, pac_s = engine.find_scripts()
        self._log("info", f"quickbms: {qb or 'NOT FOUND — build it or browse to it'}")
        self._log("info", f"scripts : CMP={'OK' if cmp_s else 'MISSING'} "
                          f"PAC={'OK' if pac_s else 'MISSING'}")
        if not qb:
            if getattr(engine, "native", None) is not None:
                self.var_native.set(True)
                self._log("info", "QuickBMS가 없어 '네이티브(파이썬)' 모드를 켰습니다 "
                                  "— 컴파일 없이 바로 추출할 수 있습니다.")
            self._log("warn", "QuickBMS로 추출하려면 빌드 버튼을 쓰거나 '찾기…'로 "
                              "지정하세요 (선택).")

    def _log(self, level: str, msg: str) -> None:
        self.log.configure(state="normal")
        tag = level if level in ("error", "warn", "ok", "info", "build") else None
        self.log.insert("end", f"[{level}] {msg}\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    # ----------------------------------------------------- QuickBMS setup

    def _is_busy(self) -> bool:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("작업 중", "현재 작업이 끝난 뒤 다시 시도하세요.")
            return True
        return False

    def _setup_quickbms(self) -> None:
        """Pick a QuickBMS checkout folder and build it (no Terminal needed)."""
        if self._is_busy():
            return
        d = filedialog.askdirectory(
            title="QuickBMS 저장소(또는 src) 폴더 선택 — src/quickbms.c 가 있는 곳")
        if d:
            self._run_build([d], "QuickBMS 빌드")

    def _download_quickbms(self) -> None:
        if self._is_busy():
            return
        if not messagebox.askyesno(
                "자동 다운로드",
                "인터넷에서 QuickBMS 소스를 내려받아 빌드합니다.\n진행할까요?"):
            return
        self._run_build(["--download"], "QuickBMS 다운로드+빌드")

    def _run_build(self, args: list, label: str) -> None:
        script = Path(__file__).resolve().parent / "build_quickbms_macos.sh"
        if not script.exists():
            messagebox.showerror("스크립트 없음", f"{script} 가 없습니다.")
            return
        cmd = ["bash", str(script), *args]
        self.var_qbstatus.set("빌드 중… (building)")
        self.btn_start.configure(state="disabled")
        self._log("info", f"{label} 시작…")

        def work():
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, errors="replace")
                for line in proc.stdout:
                    self._msgq.put(("log", ("build", line.rstrip())))
                proc.wait()
                self._msgq.put(("build_done", (proc.returncode,)))
            except Exception as exc:
                self._msgq.put(("build_done", (-1, str(exc))))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    # ----------------------------------------------------------- run / stop

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        inp = self.var_input.get().strip()
        out = self.var_output.get().strip()
        if not inp or not Path(inp).is_dir():
            messagebox.showerror("입력 오류", "유효한 입력 폴더를 선택하세요.")
            return
        if not out:
            messagebox.showerror("출력 오류", "출력 폴더를 선택하세요.")
            return

        preset = self._preset_key()
        cfg = engine.ExtractConfig(
            input_dir=Path(inp),
            output_dir=Path(out),
            quickbms=engine.find_quickbms(self.var_quickbms.get() or None),
            jobs=int(self.var_jobs.get()),
            do_decompress=bool(self.var_decompress.get()),
            do_extract=bool(self.var_extract.get()),
            content_filter=engine.PRESET_CONTENT_FILTERS.get(preset),
            preset=preset,
            native=bool(self.var_native.get()),
            collapse_dupes=bool(self.var_collapse.get()),
            skip_existing=bool(self.var_skip.get()),
        )
        cmp_s, pac_s = engine.find_scripts()
        cfg.cmp_script, cfg.pac_script = cmp_s, pac_s

        self._extractor = engine.Extractor(
            cfg,
            progress_cb=lambda *a: self._msgq.put(("progress", a)),
            log_cb=lambda level, m: self._msgq.put(("log", (level, m))))

        self.progress.configure(value=0, maximum=100)
        self.var_status.set("실행 중… (running)")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()

    def _run_worker(self) -> None:
        try:
            summary = self._extractor.run()
            self._msgq.put(("done", (summary,)))
        except Exception as exc:  # surfaced to the user, not swallowed
            self._msgq.put(("error", (str(exc),)))

    def _stop(self) -> None:
        if self._extractor:
            self._log("warn", "중지 요청… 실행 중인 quickbms 프로세스를 종료합니다.")
            self._extractor.request_stop()
        self.btn_stop.configure(state="disabled")

    # ------------------------------------------------------- queue pumping

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._msgq.get_nowait()
                if kind == "log":
                    self._log(*payload)
                elif kind == "progress":
                    stage, done, total, current = payload
                    self.progress.configure(
                        maximum=max(1, total), value=done)
                    self.var_status.set(
                        f"{stage}: {done}/{total}  {current}")
                elif kind == "done":
                    self._on_done(payload[0])
                elif kind == "error":
                    self._on_done(None, error=payload[0])
                elif kind == "build_done":
                    rc = payload[0]
                    self.btn_start.configure(state="normal")
                    self._autodetect()
                    if rc == 0:
                        self.var_qbstatus.set("빌드 완료 ✓ (ready)")
                    else:
                        self.var_qbstatus.set("빌드 실패 ✗ — 로그 확인")
                        if len(payload) > 1:
                            self._log("error", payload[1])
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _on_done(self, summary, error: str | None = None) -> None:
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        if error:
            self.var_status.set("오류 (error)")
            self._log("error", error)
            messagebox.showerror("추출 실패", error)
            return
        self.var_status.set(
            f"완료: 압축해제 {summary.decompressed}, 추출 {summary.extracted}, "
            f"실패 {len(summary.failures)} — {summary.elapsed:.1f}s"
            + ("  (중지됨)" if summary.cancelled else ""))
        if summary.failures and not summary.cancelled:
            messagebox.showwarning(
                "일부 실패",
                f"{len(summary.failures)}개 파일에서 오류가 발생했습니다.\n"
                "로그 창에서 상세 내용을 확인하세요.")


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("aqua")  # native look on macOS
    except Exception:
        pass
    SifacGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
