#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIFAC Batch Extractor — QuickBMS automation engine
==================================================

Batch-extract Love Live! School idol festival After school aCtivity (SIFAC)
models, textures, motions and live data using QuickBMS and the bundled
LoveLive_*.bms scripts.

This module is the *engine*: it discovers the quickbms binary and the .bms
scripts, scans an input folder, and drives many quickbms processes in
parallel.  It can be used as a command line tool or imported by the GUI
(``sifac_gui.py``).

Why this is faster than the QuickBMS GUI
----------------------------------------
QuickBMS itself is single-threaded and extracts one archive at a time.  The
slow part of "extracting SIFAC" is that there are *thousands* of files and the
classic workflow runs them one by one by hand.  This engine:

  * walks the whole folder once,
  * runs ``--jobs`` quickbms processes at the same time, and
  * automatically chains the two stages (decompress ``.cmp`` -> extract the
    ``ARC`` archive that comes out of it).

Pipeline
--------
Stage 1 (decompress):  every ``cmp\\0`` file -> LoveLive_CMP.bms
Stage 2 (extract):     every ``ARC\\0`` archive (original *or* produced by
                       stage 1) -> LoveLive_PAC.bms

Because ``get NAME filename`` in LoveLive_CMP.bms keeps the original name
(e.g. ``live_0001.cmp``), a decompressed archive does NOT get an ``.arc``
extension.  We therefore detect archives by their 4-byte magic, not by their
extension.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

# The pure-Python engine (no QuickBMS needed). Optional import so the file
# still works if sifac_native.py is missing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import sifac_native as native
except Exception:  # pragma: no cover
    native = None

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

ARC_MAGIC = b"ARC\x00"
CMP_MAGIC = b"cmp\x00"

CMP_SCRIPT_NAME = "LoveLive_CMP.bms"
PAC_SCRIPT_NAME = "LoveLive_PAC.bms"

# Candidate quickbms executable names, most preferred first.  The "_4gb_files"
# build is the 64-bit one able to handle very large live archives.
QUICKBMS_NAMES = (
    "quickbms_4gb_files",
    "quickbms_4gb_files.exe",
    "quickbms",
    "quickbms.exe",
)

# Output sub-folders created under the chosen output directory.
DECOMPRESS_DIR = "01_decompressed"
EXTRACT_DIR = "02_extracted"

# Content (inside-archive) filters passed straight to quickbms via -f.  These
# are best-effort presets you can freely edit; SIFAC names motions "mot_*" and
# turns mdl/tex/mot into .bmarc/.btx/.bmarc respectively.
PRESET_CONTENT_FILTERS = {
    "all": None,
    "models": "{}.bmarc;{}.btx;{}.pac;{}.shp;{}.shg;!{}mot_{}",
    "live": "{}mot_{};{}.bscam;{}.efx;{}.efxa",
    "textures": "{}.btx;{}.pac",
}

# Progress / log callback type aliases (kept loose on purpose).
ProgressCB = Callable[[str, int, int, str], None]
LogCB = Callable[[str, str], None]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def _is_windows() -> bool:
    return os.name == "nt"


def read_magic(path: Path, size: int = 4) -> bytes:
    """Return the first ``size`` bytes of ``path`` (empty bytes on error)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(size)
    except OSError:
        return b""


def is_arc(path: Path) -> bool:
    return read_magic(path) == ARC_MAGIC


def is_cmp(path: Path) -> bool:
    # Match by magic first (most reliable), fall back to the .cmp extension.
    magic = read_magic(path)
    if magic == CMP_MAGIC:
        return True
    return path.suffix.lower() == ".cmp"


def human_count(n: int) -> str:
    return f"{n:,}"


def _noop_progress(stage: str, done: int, total: int, current: str) -> None:
    pass


def _print_log(level: str, msg: str) -> None:
    stream = sys.stderr if level in ("error", "warn") else sys.stdout
    print(f"[{level}] {msg}", file=stream, flush=True)


# --------------------------------------------------------------------------- #
# Discovery: quickbms binary and .bms scripts
# --------------------------------------------------------------------------- #

def find_quickbms(explicit: Optional[str] = None,
                  extra_search_dirs: Iterable[Path] = ()) -> Optional[Path]:
    """Locate a usable quickbms executable.

    Search order:
      1. ``explicit`` path (if given and runnable),
      2. the ``QUICKBMS`` environment variable,
      3. the system PATH,
      4. a sibling QuickBMS source checkout (``../QuickBMS`` etc.),
      5. any ``extra_search_dirs`` provided by the caller.
    """
    candidates: list[Path] = []

    if explicit:
        candidates.append(Path(explicit).expanduser())

    env = os.environ.get("QUICKBMS")
    if env:
        candidates.append(Path(env).expanduser())

    # PATH lookup.
    for name in QUICKBMS_NAMES:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    # A binary dropped next to this tool (e.g. by build_quickbms_macos.sh).
    here = Path(__file__).resolve()
    for local in (here.parent, here.parent / "bin"):
        for name in QUICKBMS_NAMES:
            candidates.append(local / name)

    # Sibling QuickBMS checkouts relative to this repo and the CWD.
    roots = [here.parent.parent, here.parent.parent.parent, Path.cwd()]
    for root in roots:
        for sib in ("QuickBMS", "quickbms"):
            base = root / sib
            for sub in ("", "src"):
                for name in QUICKBMS_NAMES:
                    candidates.append(base / sub / name)

    for extra in extra_search_dirs:
        extra = Path(extra)
        for name in QUICKBMS_NAMES:
            candidates.append(extra / name)
            candidates.append(extra / "src" / name)

    seen: set[Path] = set()
    for cand in candidates:
        try:
            cand = cand.resolve()
        except OSError:
            continue
        if cand in seen:
            continue
        seen.add(cand)
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def find_scripts(scripts_dir: Optional[str] = None) -> tuple[Optional[Path], Optional[Path]]:
    """Return ``(cmp_script, pac_script)`` paths, or ``None`` for missing ones.

    Defaults to the repository root (the parent of this ``tools/`` directory),
    which is where the bundled LoveLive_*.bms scripts live.
    """
    search: list[Path] = []
    if scripts_dir:
        search.append(Path(scripts_dir).expanduser())
    repo_root = Path(__file__).resolve().parent.parent
    search.extend([repo_root, Path.cwd()])

    cmp_path = pac_path = None
    for base in search:
        if cmp_path is None and (base / CMP_SCRIPT_NAME).is_file():
            cmp_path = (base / CMP_SCRIPT_NAME).resolve()
        if pac_path is None and (base / PAC_SCRIPT_NAME).is_file():
            pac_path = (base / PAC_SCRIPT_NAME).resolve()
    return cmp_path, pac_path


# --------------------------------------------------------------------------- #
# Configuration and result types
# --------------------------------------------------------------------------- #

@dataclass
class ExtractConfig:
    input_dir: Path
    output_dir: Path
    quickbms: Optional[Path] = None
    cmp_script: Optional[Path] = None
    pac_script: Optional[Path] = None
    jobs: int = max(1, (os.cpu_count() or 4))
    do_decompress: bool = True
    do_extract: bool = True
    include: list[str] = field(default_factory=list)   # filename globs (keep)
    exclude: list[str] = field(default_factory=list)   # filename globs (drop)
    content_filter: Optional[str] = None               # passed to quickbms -f
    preset: Optional[str] = None                        # used by the native engine
    native: bool = False                                # use pure-Python engine
    collapse_dupes: bool = True                          # merge repeated folder names
    skip_existing: bool = False                         # -k instead of -o
    verbose: bool = False
    dry_run: bool = False


@dataclass
class JobResult:
    src: Path
    output_dir: Path
    ok: bool
    returncode: int
    produced: list[Path] = field(default_factory=list)
    message: str = ""


@dataclass
class RunSummary:
    decompressed: int = 0
    extracted: int = 0
    failures: list[JobResult] = field(default_factory=list)
    cancelled: bool = False
    elapsed: float = 0.0


# --------------------------------------------------------------------------- #
# The extractor
# --------------------------------------------------------------------------- #

class Extractor:
    """Drives QuickBMS over a folder of SIFAC files, in parallel."""

    def __init__(self, cfg: ExtractConfig,
                 progress_cb: Optional[ProgressCB] = None,
                 log_cb: Optional[LogCB] = None):
        self.cfg = cfg
        self.progress = progress_cb or _noop_progress
        self.log = log_cb or _print_log
        self._stop = threading.Event()
        self._active: set[subprocess.Popen] = set()
        self._active_lock = threading.Lock()

    # -- public control ---------------------------------------------------- #

    def request_stop(self) -> None:
        """Signal cancellation and terminate any running quickbms processes."""
        self._stop.set()
        with self._active_lock:
            for proc in list(self._active):
                try:
                    proc.terminate()
                except Exception:
                    pass

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # -- validation -------------------------------------------------------- #

    def resolve(self) -> None:
        """Fill in any unset quickbms / script paths and validate everything."""
        cfg = self.cfg
        problems: list[str] = []

        if cfg.native:
            if native is None:
                problems.append("native engine (sifac_native.py) is not available.")
        else:
            if cfg.quickbms is None:
                cfg.quickbms = find_quickbms()
            if cfg.cmp_script is None or cfg.pac_script is None:
                cmp_s, pac_s = find_scripts()
                cfg.cmp_script = cfg.cmp_script or cmp_s
                cfg.pac_script = cfg.pac_script or pac_s
            if not cfg.quickbms or not Path(cfg.quickbms).is_file():
                problems.append(
                    "quickbms executable not found. Build it "
                    "(tools/build_quickbms_macos.sh), use --native (no QuickBMS), "
                    "or set the QUICKBMS env var / --quickbms.")
            if cfg.do_decompress and (not cfg.cmp_script or not Path(cfg.cmp_script).is_file()):
                problems.append(f"{CMP_SCRIPT_NAME} not found.")
            if cfg.do_extract and (not cfg.pac_script or not Path(cfg.pac_script).is_file()):
                problems.append(f"{PAC_SCRIPT_NAME} not found.")

        if not cfg.input_dir or not Path(cfg.input_dir).is_dir():
            problems.append(f"Input folder does not exist: {cfg.input_dir}")
        if problems:
            raise FileNotFoundError("\n".join(problems))

    # -- scanning ---------------------------------------------------------- #

    def _matches_filters(self, name: str) -> bool:
        inc, exc = self.cfg.include, self.cfg.exclude
        if inc and not any(fnmatch.fnmatch(name, p) for p in inc):
            return False
        if exc and any(fnmatch.fnmatch(name, p) for p in exc):
            return False
        return True

    def scan(self) -> tuple[list[Path], list[Path]]:
        """Return ``(cmp_files, arc_files)`` found directly under the input."""
        cmp_files: list[Path] = []
        arc_files: list[Path] = []
        root = Path(self.cfg.input_dir)
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not self._matches_filters(fn):
                    continue
                p = Path(dirpath) / fn
                if is_cmp(p):
                    cmp_files.append(p)
                elif is_arc(p):
                    arc_files.append(p)
        cmp_files.sort()
        arc_files.sort()
        return cmp_files, arc_files

    # -- quickbms invocation ----------------------------------------------- #

    def _flags(self) -> list[str]:
        cfg = self.cfg
        flags = ["-Y"]                       # auto-answer prompts (never hang)
        flags.append("-k" if cfg.skip_existing else "-o")
        if not cfg.verbose:
            flags.append("-q")
        if cfg.content_filter:
            flags += ["-f", cfg.content_filter]
        return flags

    def _run_quickbms(self, kind: str, script: Path, src: Path, out_dir: Path) -> JobResult:
        """Run quickbms for a single file.

        For ``arc`` the LoveLive_PAC.bms script itself writes into a
        ``<basename>/`` subfolder of ``out_dir`` (basename == ``src.stem``), so
        we look there for the produced files. For ``cmp`` the output keeps the
        input's name. Output is detected deterministically (no directory
        snapshot), which is correct even when many jobs share ``out_dir``."""
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [str(self.cfg.quickbms), *self._flags(),
               str(script), str(src), str(out_dir)]

        if self.cfg.dry_run:
            self.log("info", "DRY-RUN " + " ".join(cmd))
            return JobResult(src=src, output_dir=out_dir, ok=True, returncode=0)

        if self._stop.is_set():
            return JobResult(src=src, output_dir=out_dir, ok=False,
                             returncode=-1, message="cancelled")

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace")
        except OSError as exc:
            return JobResult(src=src, output_dir=out_dir, ok=False,
                             returncode=-1, message=str(exc))

        with self._active_lock:
            self._active.add(proc)
        try:
            out, _ = proc.communicate()
        finally:
            with self._active_lock:
                self._active.discard(proc)

        ok = proc.returncode == 0
        if kind == "cmp":
            cand = out_dir / src.name
            produced = [cand] if cand.is_file() else []
        else:  # arc -> files land under out_dir/<basename>/
            sub = out_dir / src.stem
            produced = (sorted(p for p in sub.rglob("*") if p.is_file())
                        if sub.is_dir() else [])
        if self.cfg.verbose and out:
            self.log("debug", f"{src.name}: {out.strip()[:2000]}")
        msg = ""
        if not ok and out:
            lines = out.strip().splitlines()
            msg = lines[-1] if lines else ""
        return JobResult(src=src, output_dir=out_dir, ok=ok,
                         returncode=proc.returncode,
                         produced=produced, message=msg)

    # -- per-file dispatch (quickbms or native) ---------------------------- #

    def _process_one(self, kind: str, script: Path, src: Path, out_dir: Path) -> JobResult:
        if self.cfg.native:
            return self._process_native(kind, src, out_dir)
        return self._run_quickbms(kind, script, src, out_dir)

    def _process_native(self, kind: str, src: Path, out_dir: Path) -> JobResult:
        """Decompress (.cmp) or extract (ARC) a single file with pure Python.

        ``out_dir`` is the final destination: for ``arc`` it already includes
        the per-archive folder, so nothing extra is nested here."""
        out_dir.mkdir(parents=True, exist_ok=True)
        if self.cfg.dry_run:
            self.log("info", f"DRY-RUN native {kind}: {src}")
            return JobResult(src=src, output_dir=out_dir, ok=True, returncode=0)
        if self._stop.is_set():
            return JobResult(src=src, output_dir=out_dir, ok=False,
                             returncode=-1, message="cancelled")
        try:
            raw = Path(src).read_bytes()
            if kind == "cmp":
                data = native.decompress_cmp(raw)
                # Keep the original name (like quickbms `get NAME filename`); the
                # orchestrator re-detects ARC output by magic for stage 2.
                dest = out_dir / src.name
                dest.write_bytes(data)
                return JobResult(src=src, output_dir=out_dir, ok=True,
                                 returncode=0, produced=[dest])
            # kind == "arc"
            if not native.is_arc_bytes(raw):
                return JobResult(src=src, output_dir=out_dir, ok=False,
                                 returncode=-1, message="not an ARC archive")
            keep = native.preset_predicate(self.cfg.preset)
            written = native.extract_arc(raw, out_dir, subfolder=None,
                                         name_filter=keep)
            return JobResult(src=src, output_dir=out_dir, ok=True,
                             returncode=0, produced=written)
        except Exception as exc:
            return JobResult(src=src, output_dir=out_dir, ok=False,
                             returncode=-1, message=str(exc))

    # -- output path helpers ----------------------------------------------- #

    def _rel_dir_parts(self, src: Path, root: Path) -> list[str]:
        """The parts of src's parent directory relative to ``root``."""
        try:
            rel = Path(src).resolve().relative_to(Path(root).resolve())
            return list(rel.parent.parts)
        except ValueError:
            return []

    def _clean_join(self, base: Path, parts: list[str]) -> Path:
        """Join ``parts`` under ``base``, collapsing consecutive duplicate
        folder names (e.g. live_0001/live_0001 -> live_0001)."""
        parts = [p for p in parts if p not in ("", ".")]
        if self.cfg.collapse_dupes:
            collapsed: list[str] = []
            for p in parts:
                if collapsed and collapsed[-1] == p:
                    continue
                collapsed.append(p)
            parts = collapsed
        return Path(base).joinpath(*parts) if parts else Path(base)

    def _decompress_dir(self, src: Path, decomp_base: Path) -> Path:
        return self._clean_join(decomp_base, self._rel_dir_parts(src, self.cfg.input_dir))

    def _extract_dir(self, src: Path, extract_base: Path, mirror_root: Path) -> Path:
        """Final output dir for extracting archive ``src``.

        Native writes the members directly here, so the per-archive folder
        (``src.stem``) is part of the path. For quickbms the .bms script adds
        that folder itself, so we stop one level higher (and avoid a duplicate
        if the parent folder is already named after the archive)."""
        parts = self._rel_dir_parts(src, mirror_root)
        if self.cfg.native:
            return self._clean_join(extract_base, parts + [src.stem])
        # quickbms: script appends src.stem; drop a parent that would duplicate it
        if self.cfg.collapse_dupes and parts and parts[-1] == src.stem:
            parts = parts[:-1]
        return self._clean_join(extract_base, parts)

    # -- stages ------------------------------------------------------------ #

    def _run_stage(self, label: str, kind: str, script: Path,
                   jobs_in: list[tuple[Path, Path]],
                   on_result: Callable[[JobResult], None]) -> None:
        total = len(jobs_in)
        if total == 0:
            return
        self.progress(label, 0, total, "")
        done = 0
        max_workers = max(1, int(self.cfg.jobs))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for src, out_dir in jobs_in:
                if self._stop.is_set():
                    break
                futures[pool.submit(self._process_one, kind, script, src, out_dir)] = src
            for fut in as_completed(futures):
                res = fut.result()
                done += 1
                on_result(res)
                if res.ok:
                    self.log("ok" if res.produced else "warn",
                             f"{label}: {res.src.name} "
                             f"({len(res.produced)} file(s))")
                else:
                    self.log("error",
                             f"{label} FAILED: {res.src.name} "
                             f"(code {res.returncode}) {res.message}")
                self.progress(label, done, total, res.src.name)

    # -- orchestration ----------------------------------------------------- #

    def run(self) -> RunSummary:
        self.resolve()
        cfg = self.cfg
        started = time.time()
        summary = RunSummary()

        self.log("info", f"engine   : {'native (Python, no QuickBMS)' if cfg.native else cfg.quickbms}")
        self.log("info", f"input    : {cfg.input_dir}")
        self.log("info", f"output   : {cfg.output_dir}")
        self.log("info", f"jobs     : {cfg.jobs}")

        cmp_files, arc_files = self.scan()
        self.log("info",
                 f"scan: {human_count(len(cmp_files))} compressed (.cmp), "
                 f"{human_count(len(arc_files))} archive(s)")

        out_root = Path(cfg.output_dir)
        decomp_base = out_root / DECOMPRESS_DIR
        extract_base = out_root / EXTRACT_DIR

        # Archives produced by decompression are queued here for stage 2.
        produced_archives: list[Path] = []

        # ---- Stage 1: decompress .cmp ----
        if cfg.do_decompress and cmp_files and not self._stop.is_set():
            jobs_in = [(f, self._decompress_dir(f, decomp_base)) for f in cmp_files]

            def _after_cmp(res: JobResult) -> None:
                if res.ok:
                    summary.decompressed += 1
                    for p in res.produced:
                        if p.is_file() and is_arc(p):
                            produced_archives.append(p)
                else:
                    summary.failures.append(res)

            self._run_stage("decompress", "cmp", cfg.cmp_script, jobs_in, _after_cmp)

        # ---- Stage 2: extract ARC archives ----
        if cfg.do_extract and not self._stop.is_set():
            all_archives = list(arc_files) + produced_archives
            if produced_archives:
                self.log("info",
                         f"stage 2 also includes {human_count(len(produced_archives))} "
                         f"archive(s) produced by decompression")
            jobs_in = []
            for f in all_archives:
                # Decompressed archives live under decomp_base; mirror their
                # path under the extract folder so output stays organised.
                mirror_root = (decomp_base if str(f).startswith(str(decomp_base))
                               else cfg.input_dir)
                jobs_in.append((f, self._extract_dir(f, extract_base, mirror_root)))

            def _after_arc(res: JobResult) -> None:
                if res.ok:
                    summary.extracted += 1
                else:
                    summary.failures.append(res)

            self._run_stage("extract", "arc", cfg.pac_script, jobs_in, _after_arc)

        summary.cancelled = self._stop.is_set()
        summary.elapsed = time.time() - started

        self.log("info",
                 f"done in {summary.elapsed:.1f}s — "
                 f"decompressed {summary.decompressed}, "
                 f"extracted {summary.extracted}, "
                 f"failed {len(summary.failures)}"
                 + (" (CANCELLED)" if summary.cancelled else ""))
        return summary


# --------------------------------------------------------------------------- #
# Command line interface
# --------------------------------------------------------------------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sifac_extract",
        description="Batch-extract SIFAC models/live with QuickBMS (parallel).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input", nargs="?", help="folder containing SIFAC files")
    p.add_argument("output", nargs="?", help="folder to write extracted files")
    p.add_argument("--quickbms", help="path to the quickbms executable")
    p.add_argument("--scripts", help="folder containing the LoveLive_*.bms scripts")
    p.add_argument("-j", "--jobs", type=int, default=max(1, os.cpu_count() or 4),
                   help="number of quickbms processes to run in parallel")
    p.add_argument("--stage", choices=["all", "cmp", "arc"], default="all",
                   help="all = decompress then extract; cmp = decompress only; "
                        "arc = extract archives only")
    p.add_argument("--preset", choices=sorted(PRESET_CONTENT_FILTERS),
                   help="content filter preset applied inside archives")
    p.add_argument("-f", "--content-filter",
                   help="raw quickbms -f filter (overrides --preset)")
    p.add_argument("--include", action="append", default=[],
                   help="only process input filenames matching this glob "
                        "(repeatable)")
    p.add_argument("--exclude", action="append", default=[],
                   help="skip input filenames matching this glob (repeatable)")
    p.add_argument("--native", action="store_true",
                   help="use the pure-Python engine (no QuickBMS / no compiling)")
    p.add_argument("--no-collapse", action="store_true",
                   help="keep repeated folder names instead of merging "
                        "duplicates (e.g. live_0001/live_0001)")
    p.add_argument("--skip-existing", action="store_true",
                   help="keep existing output files instead of overwriting")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="print the quickbms commands without running them")
    p.add_argument("--check", action="store_true",
                   help="only report what was auto-detected, then exit")
    return p


def cmd_main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.check:
        qb = find_quickbms(args.quickbms)
        cmp_s, pac_s = find_scripts(args.scripts)
        print("quickbms       :", qb or "NOT FOUND")
        print("LoveLive_CMP   :", cmp_s or "NOT FOUND")
        print("LoveLive_PAC   :", pac_s or "NOT FOUND")
        print("native engine  :", "available" if native else "NOT FOUND")
        print("default jobs   :", max(1, os.cpu_count() or 4))
        # Native mode needs no quickbms, so report success if either path works.
        return 0 if (qb or native) else 1

    if not args.input or not args.output:
        build_arg_parser().error("input and output folders are required")

    content_filter = args.content_filter
    if content_filter is None and args.preset:
        content_filter = PRESET_CONTENT_FILTERS.get(args.preset)

    cfg = ExtractConfig(
        input_dir=Path(args.input),
        output_dir=Path(args.output),
        quickbms=find_quickbms(args.quickbms),
        jobs=args.jobs,
        do_decompress=args.stage in ("all", "cmp"),
        do_extract=args.stage in ("all", "arc"),
        include=args.include,
        exclude=args.exclude,
        content_filter=content_filter,
        preset=args.preset,
        native=args.native,
        collapse_dupes=not args.no_collapse,
        skip_existing=args.skip_existing,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )
    cmp_s, pac_s = find_scripts(args.scripts)
    cfg.cmp_script, cfg.pac_script = cmp_s, pac_s

    extractor = Extractor(cfg, log_cb=_print_log)
    try:
        summary = extractor.run()
    except FileNotFoundError as exc:
        print("error:\n" + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        extractor.request_stop()
        print("\ninterrupted", file=sys.stderr)
        return 130

    if summary.failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(cmd_main())
