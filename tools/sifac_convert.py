#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SIFAC Converter — extracted ``.bmarc``/``.btx`` -> FBX + PNG, in batch
======================================================================

The companion to ``sifac_extract.py``.  Where the extractor turns the game's
``.cmp``/``ARC`` archives into individual ``.bmarc`` (models/motions), ``.bscam``
(cameras) and ``.btx`` (textures) files, **this** tool converts those into
ready-to-use assets *without Noesis* and *without MMD*:

    * models / stages   ->  ``.fbx`` (skeleton, skinning, materials, blendshapes)
    * motions (mot_*)    ->  ``.fbx`` (the dir's model rigged + the motion as a take)
    * cameras (.bscam)   ->  ``.fbx`` (animated camera)
    * textures (.btx)    ->  ``.png``

It is fast because it is pure Python (no Noesis round-trip) and runs many files
in parallel.  Importable by the GUI (``sifac_gui.py``) or usable on its own.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import (ProcessPoolExecutor, ThreadPoolExecutor,
                                 as_completed)
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sifac_bmarc
import sifac_btx
import sifac_fbx
import sifac_native

# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #

ProgressCB = Callable[[str, int, int, str], None]
LogCB = Callable[[str, str], None]


_BPY_SCRIPT = str(Path(__file__).resolve().parent / "sifac_fbx_bpy.py")


def bpy_available() -> bool:
    """True if Blender's ``bpy`` module can be imported (without loading it)."""
    try:
        return importlib.util.find_spec("bpy") is not None
    except Exception:
        return False


def resolve_engine(engine: str) -> str:
    if engine == "auto":
        return "blender" if bpy_available() else "python"
    return engine


def _run_bpy(model_path: str, motion_paths, out_path: str, scale: float,
             include_morphs: bool, anim_only: bool, texture_dir: str,
             up_axis: str = "y"):
    """Build + export one FBX in an isolated Blender (bpy) subprocess."""
    cmd = [sys.executable, _BPY_SCRIPT, model_path, out_path,
           "--scale", str(scale), "--texture-dir", texture_dir,
           "--up-axis", up_axis]
    for mp in motion_paths:
        cmd += ["--motion", mp]
    if not include_morphs:
        cmd.append("--no-morphs")
    if anim_only:
        cmd.append("--anim-only")
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace")
    ok = proc.returncode == 0 and Path(out_path).is_file()
    msg = "" if ok else (proc.stdout.strip().splitlines() or [""])[-1]
    return ok, msg


def _noop_progress(stage: str, done: int, total: int, current: str) -> None:
    pass


def _print_log(level: str, msg: str) -> None:
    stream = sys.stderr if level in ("error", "warn") else sys.stdout
    print(f"[{level}] {msg}", file=stream, flush=True)


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

def is_motion_name(name: str) -> bool:
    low = os.path.basename(name).lower()
    return low.startswith("mot_")


@lru_cache(maxsize=None)
def _tokens(name: str) -> frozenset:
    base = os.path.splitext(os.path.basename(name))[0].lower()
    out, cur = set(), ""
    for ch in base:
        if ch.isalnum():
            cur += ch
        elif cur:
            out.add(cur); cur = ""
    if cur:
        out.add(cur)
    return frozenset(out)


def best_model_for(motion: Path, models: List[Path],
                   require_match: bool = False) -> Optional[Path]:
    """Pick the model that best matches a motion by name.

    Motion / model names share a ``<NN>_<charname>`` key (e.g.
    ``mot_03_kotori_...`` <-> ``mod_03_kotori_...``).  With ``require_match`` a
    model is only returned when it shares at least one such token, so motions in
    a model-less folder pair with the right character's model elsewhere rather
    than grabbing an arbitrary one."""
    if not models:
        return None
    mt = _tokens(motion.name) - {"mot", "mod"}
    best, score = None, -1
    for m in models:
        s = len(mt & (_tokens(m.name) - {"mot", "mod"}))
        if s > score:
            best, score = m, s
    if require_match and score < 1:
        return None
    if len(models) == 1 and not require_match:
        return models[0]
    return best


# --------------------------------------------------------------------------- #
# Texture resolution
# --------------------------------------------------------------------------- #

def _texture_blobs(texture_dir: Path) -> Dict[str, bytes]:
    """Map base texture name -> raw .btx bytes, from texture.pac and *.btx."""
    blobs: Dict[str, bytes] = {}
    search = [texture_dir, texture_dir.parent]
    for d in search:
        pac = d / "texture.pac"
        if pac.is_file():
            try:
                data = pac.read_bytes()
                if sifac_native.is_arc_bytes(data):
                    for e in sifac_native.parse_arc(data):
                        base = os.path.splitext(os.path.basename(e.name))[0]
                        blob = data[e.offset:e.offset + e.size]
                        if blob[:3] == b"btx":
                            blobs.setdefault(base, blob)
            except Exception:
                pass
    if texture_dir.is_dir():
        for f in texture_dir.glob("*.btx"):
            blobs.setdefault(f.stem, f.read_bytes())
    return blobs


def _write_textures(model, texture_dir: Path, out_dir: Path,
                    log: LogCB) -> Tuple[int, int]:
    """Decode every texture the model references into PNGs in ``out_dir``."""
    wanted = list(dict.fromkeys(model.referenced_textures))
    blobs = _texture_blobs(texture_dir) if wanted else {}
    ok = fail = 0
    for name in wanted:
        dest = out_dir / f"{name}.png"
        if dest.exists():
            ok += 1
            continue
        tex = model.embedded_textures.get(name)
        try:
            if tex is None:
                blob = blobs.get(name)
                if blob is None:
                    fail += 1
                    continue
                tex = sifac_btx.decode_btx(blob)
            sifac_btx.save_texture(tex, dest)
            ok += 1
        except Exception as exc:
            fail += 1
            log("warn", f"texture {name}: {exc}")
    return ok, fail


# --------------------------------------------------------------------------- #
# Worker jobs (kept top-level so they are picklable for ProcessPoolExecutor)
# --------------------------------------------------------------------------- #

@dataclass
class JobResult:
    src: str
    out: str
    ok: bool
    produced: List[str] = field(default_factory=list)
    message: str = ""


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def convert_model_job(model_path: str, motion_paths: List[str], out_path: str,
                      scale: float, include_morphs: bool,
                      with_textures: bool, engine: str = "python",
                      up_axis: str = "y") -> JobResult:
    logs: List[str] = []

    def log(level, msg):
        logs.append(f"[{level}] {msg}")
    if engine == "blender":
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        ok, msg = _run_bpy(model_path, motion_paths, out_path, scale,
                           include_morphs, False, str(Path(model_path).parent),
                           up_axis)
        return JobResult(model_path, out_path, ok,
                         [Path(out_path).name] if ok else [], msg)
    try:
        parsed = sifac_bmarc.parse_bmarc(_read(model_path),
                                         Path(model_path).stem)
        if parsed.model is None or not parsed.model.has_geometry():
            return JobResult(model_path, out_path, False,
                             message="no geometry in model")
        model = parsed.model
        anims = list(parsed.animations)
        for mp in motion_paths:
            try:
                a = sifac_bmarc.parse_bmarc(_read(mp), Path(mp).stem)
                anims.extend(a.animations)
            except Exception as exc:
                log("warn", f"motion {Path(mp).name}: {exc}")
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        produced = []
        if with_textures:
            ok, fail = _write_textures(model, Path(model_path).parent,
                                       out.parent, log)
            if ok:
                produced.append(f"{ok} texture(s)")
        sifac_fbx.write_model_fbx(out, model, anims, texture_dir="",
                                  scale=scale, include_morphs=include_morphs,
                                  up_axis=up_axis)
        produced.append(out.name)
        return JobResult(model_path, out_path, True, produced,
                         " | ".join(logs))
    except Exception as exc:
        return JobResult(model_path, out_path, False,
                         message=f"{exc} {' '.join(logs)}")


def convert_motion_job(motion_path: str, model_path: Optional[str], out_path: str,
                       scale: float, anim_only: bool,
                       with_textures: bool, engine: str = "python",
                       up_axis: str = "y") -> JobResult:
    logs: List[str] = []

    def log(level, msg):
        logs.append(f"[{level}] {msg}")
    if model_path is None:
        return JobResult(motion_path, out_path, False,
                         message="no model found to rig this motion "
                                 "(use --models-dir / --model)")
    if engine == "blender":
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        ok, msg = _run_bpy(model_path, [motion_path], out_path, scale,
                           False, anim_only, str(Path(model_path).parent),
                           up_axis)
        return JobResult(motion_path, out_path, ok,
                         [Path(out_path).name] if ok else [], msg)
    try:
        parsed_anim = sifac_bmarc.parse_bmarc(_read(motion_path),
                                              Path(motion_path).stem)
        if not parsed_anim.animations:
            return JobResult(motion_path, out_path, False,
                             message="no animation in motion")
        parsed_model = sifac_bmarc.parse_bmarc(_read(model_path),
                                               Path(model_path).stem)
        model = parsed_model.model
        if model is None or not model.bones:
            return JobResult(motion_path, out_path, False,
                             message="paired model has no skeleton")
        if anim_only:
            model.submeshes = []          # skeleton + animation only
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        produced = []
        if with_textures and not anim_only:
            ok, _ = _write_textures(model, Path(model_path).parent, out.parent, log)
            if ok:
                produced.append(f"{ok} texture(s)")
        sifac_fbx.write_model_fbx(out, model, parsed_anim.animations,
                                  texture_dir="", scale=scale,
                                  include_morphs=False, up_axis=up_axis)
        produced.append(out.name)
        return JobResult(motion_path, out_path, True, produced, " | ".join(logs))
    except Exception as exc:
        return JobResult(motion_path, out_path, False, message=str(exc))


def convert_camera_job(cam_path: str, out_path: str, scale: float,
                       up_axis: str = "y") -> JobResult:
    try:
        parsed = sifac_bmarc.parse_bscam(_read(cam_path), Path(cam_path).stem)
        if parsed.camera is None:
            return JobResult(cam_path, out_path, False, message="no camera data")
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        sifac_fbx.write_camera_fbx(out, parsed.camera, scale=scale,
                                   up_axis=up_axis)
        return JobResult(cam_path, out_path, True, [out.name])
    except Exception as exc:
        return JobResult(cam_path, out_path, False, message=str(exc))


def convert_texture_job(tex_path: str, out_path: str) -> JobResult:
    try:
        data = _read(tex_path)
        produced = []
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if data[:3] == b"btx":
            tex = sifac_btx.decode_btx(data)
            sifac_btx.save_texture(tex, out)
            produced.append(out.name)
        elif sifac_native.is_arc_bytes(data):
            for e in sifac_native.parse_arc(data):
                blob = data[e.offset:e.offset + e.size]
                if blob[:3] != b"btx":
                    continue
                base = os.path.splitext(os.path.basename(e.name))[0]
                dest = out.parent / f"{base}.png"
                try:
                    sifac_btx.save_texture(sifac_btx.decode_btx(blob), dest)
                    produced.append(dest.name)
                except Exception:
                    pass
        else:
            return JobResult(tex_path, out_path, False, message="not a texture")
        return JobResult(tex_path, out_path, True, produced)
    except Exception as exc:
        return JobResult(tex_path, out_path, False, message=str(exc))


# --------------------------------------------------------------------------- #
# Config / summary
# --------------------------------------------------------------------------- #

PRESETS = ("all", "models", "stages", "animations", "cameras", "textures")


@dataclass
class ConvertConfig:
    input_dir: Path
    output_dir: Path
    preset: str = "all"
    jobs: int = max(1, (os.cpu_count() or 4))
    scale: float = 1.0
    include_morphs: bool = True
    with_textures: bool = True
    anim_only: bool = False
    bundle_motions: bool = False
    force_model: Optional[Path] = None
    models_dir: Optional[Path] = None
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    skip_existing: bool = False
    use_processes: bool = True
    engine: str = "auto"            # auto | python | blender
    up_axis: str = "y"              # y (SIFAS / Y-up) | z (Z-up)
    verbose: bool = False
    dry_run: bool = False


@dataclass
class Plan:
    model_jobs: List[tuple] = field(default_factory=list)
    motion_jobs: List[tuple] = field(default_factory=list)
    camera_jobs: List[tuple] = field(default_factory=list)
    texture_jobs: List[tuple] = field(default_factory=list)

    def total(self) -> int:
        return (len(self.model_jobs) + len(self.motion_jobs)
                + len(self.camera_jobs) + len(self.texture_jobs))


@dataclass
class RunSummary:
    converted: int = 0
    failures: List[JobResult] = field(default_factory=list)
    cancelled: bool = False
    elapsed: float = 0.0


# --------------------------------------------------------------------------- #
# Converter
# --------------------------------------------------------------------------- #

class Converter:
    def __init__(self, cfg: ConvertConfig,
                 progress_cb: Optional[ProgressCB] = None,
                 log_cb: Optional[LogCB] = None):
        self.cfg = cfg
        self.progress = progress_cb or _noop_progress
        self.log = log_cb or _print_log
        self.engine = resolve_engine(cfg.engine)
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # -- scanning / planning ---------------------------------------------- #

    def _match(self, name: str) -> bool:
        inc, exc = self.cfg.include, self.cfg.exclude
        if inc and not any(fnmatch.fnmatch(name, p) for p in inc):
            return False
        if exc and any(fnmatch.fnmatch(name, p) for p in exc):
            return False
        return True

    def _out_for(self, src: Path, suffix: str = ".fbx") -> Path:
        try:
            rel = src.resolve().relative_to(Path(self.cfg.input_dir).resolve())
        except ValueError:
            rel = Path(src.name)
        return Path(self.cfg.output_dir) / rel.parent / (src.stem + suffix)

    @staticmethod
    def _scan_models_dir(models_dir: Optional[Path]) -> List[Path]:
        """Recursively collect model (non-motion) .bmarc files under a folder."""
        if not models_dir or not Path(models_dir).is_dir():
            return []
        out: List[Path] = []
        for dirpath, _dn, files in os.walk(models_dir):
            for fn in files:
                if fn.lower().endswith(".bmarc") and not is_motion_name(fn):
                    out.append(Path(dirpath) / fn)
        return out

    def plan(self) -> Plan:
        cfg = self.cfg
        plan = Plan()
        root = Path(cfg.input_dir)
        per_dir_models: Dict[Path, List[Path]] = {}
        per_dir_motions: Dict[Path, List[Path]] = {}
        cameras: List[Path] = []
        textures: List[Path] = []
        for dirpath, _dn, files in os.walk(root):
            d = Path(dirpath)
            for fn in files:
                if not self._match(fn):
                    continue
                p = d / fn
                low = fn.lower()
                if low.endswith(".bmarc"):
                    if is_motion_name(fn):
                        per_dir_motions.setdefault(d, []).append(p)
                    else:
                        per_dir_models.setdefault(d, []).append(p)
                elif low.endswith(".bscam"):
                    cameras.append(p)
                elif low.endswith(".btx") or low == "texture.pac":
                    textures.append(p)

        want_models = cfg.preset in ("all", "models", "stages")
        want_motions = cfg.preset in ("all", "animations")
        want_cameras = cfg.preset in ("all", "cameras")
        want_textures = cfg.preset in ("all", "textures")

        if want_models:
            for d, models in per_dir_models.items():
                motions = per_dir_motions.get(d, []) if cfg.bundle_motions else []
                for m in models:
                    out = self._out_for(m)
                    if cfg.skip_existing and out.exists():
                        continue
                    bundled = [str(x) for x in motions] if cfg.bundle_motions else []
                    plan.model_jobs.append(
                        (str(m), bundled, str(out), cfg.scale,
                         cfg.include_morphs, cfg.with_textures, self.engine,
                         cfg.up_axis))

        if want_motions:
            # Models found anywhere in the input tree, plus an optional separate
            # models folder, form a global pool so motions in a model-less
            # "live" folder still pair with their character's model by name.
            all_models = [m for ms in per_dir_models.values() for m in ms]
            all_models += self._scan_models_dir(cfg.models_dir)
            unpaired = 0
            for d, motions in per_dir_motions.items():
                same = per_dir_models.get(d, [])
                for mo in motions:
                    out = self._out_for(mo)
                    if cfg.skip_existing and out.exists():
                        continue
                    if cfg.force_model:
                        model = cfg.force_model
                    elif same:
                        model = best_model_for(mo, same)
                    else:
                        model = best_model_for(mo, all_models, require_match=True)
                    if model is None:
                        unpaired += 1
                    plan.motion_jobs.append(
                        (str(mo), str(model) if model else None, str(out),
                         cfg.scale, cfg.anim_only, cfg.with_textures, self.engine,
                         cfg.up_axis))
            if unpaired:
                self.log("warn",
                         f"{unpaired} motion(s) have no matching model. Point "
                         f"--models-dir at your extracted models folder, or pass "
                         f"--model <a model .bmarc>, so they can be rigged.")

        if want_cameras:
            for c in cameras:
                out = self._out_for(c)
                if cfg.skip_existing and out.exists():
                    continue
                plan.camera_jobs.append((str(c), str(out), cfg.scale, cfg.up_axis))

        if want_textures:
            for t in textures:
                out = self._out_for(t, ".png")
                plan.texture_jobs.append((str(t), str(out)))
        return plan

    # -- execution --------------------------------------------------------- #

    def run(self) -> RunSummary:
        cfg = self.cfg
        if not Path(cfg.input_dir).is_dir():
            raise FileNotFoundError(f"input folder does not exist: {cfg.input_dir}")
        started = time.time()
        summary = RunSummary()
        self.log("info", f"input  : {cfg.input_dir}")
        self.log("info", f"output : {cfg.output_dir}")
        self.log("info", f"preset : {cfg.preset}   jobs: {cfg.jobs}   "
                         f"engine: {self.engine}"
                         + ("  (Blender — slower but Blender-perfect)"
                            if self.engine == "blender" else
                            "  (pure-Python — fast; FBX-SDK/Unity/Maya)"))

        plan = self.plan()
        total = plan.total()
        self.log("info",
                 f"plan: {len(plan.model_jobs)} model(s), "
                 f"{len(plan.motion_jobs)} motion(s), "
                 f"{len(plan.camera_jobs)} camera(s), "
                 f"{len(plan.texture_jobs)} texture file(s)")
        if total == 0:
            self.log("warn", "nothing to convert (check the preset / input folder)")
            summary.elapsed = time.time() - started
            return summary

        tasks = (
            [(convert_model_job, a, Path(a[0]).name) for a in plan.model_jobs]
            + [(convert_motion_job, a, Path(a[0]).name) for a in plan.motion_jobs]
            + [(convert_camera_job, a, Path(a[0]).name) for a in plan.camera_jobs]
            + [(convert_texture_job, a, Path(a[0]).name) for a in plan.texture_jobs]
        )

        if cfg.dry_run:
            for fn, args, label in tasks:
                out = args[1] if fn in (convert_texture_job, convert_camera_job) \
                    else args[2]
                self.log("info", f"DRY-RUN {fn.__name__}: {label} -> {out}")
            summary.elapsed = time.time() - started
            return summary

        done = 0
        # The Blender engine runs each job as its own subprocess, so manage
        # them with threads (no pickling, no nested process pools).
        if self.engine == "blender" or not cfg.use_processes:
            executor_cls = ThreadPoolExecutor
        else:
            executor_cls = ProcessPoolExecutor
        try:
            self._execute(executor_cls, tasks, summary, total)
        except Exception as exc:
            # Process pools can fail to start in odd environments; fall back.
            self.log("warn", f"parallel pool failed ({exc}); running with threads")
            self._execute(ThreadPoolExecutor, tasks, summary, total)

        summary.cancelled = self._stop.is_set()
        summary.elapsed = time.time() - started
        self.log("info",
                 f"done in {summary.elapsed:.1f}s — converted {summary.converted}, "
                 f"failed {len(summary.failures)}"
                 + (" (CANCELLED)" if summary.cancelled else ""))
        return summary

    def _execute(self, executor_cls, tasks, summary: RunSummary, total: int) -> None:
        done = 0
        max_workers = max(1, int(self.cfg.jobs))
        with executor_cls(max_workers=max_workers) as pool:
            futures = {}
            for fn, args, label in tasks:
                if self._stop.is_set():
                    break
                futures[pool.submit(fn, *args)] = label
            for fut in as_completed(futures):
                label = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:
                    res = JobResult(label, "", False, message=str(exc))
                done += 1
                if res.ok:
                    summary.converted += 1
                    self.log("ok", f"{label} -> {', '.join(res.produced)}"
                                    + (f"  ({res.message})" if res.message.strip() else ""))
                else:
                    summary.failures.append(res)
                    self.log("error", f"FAILED {label}: {res.message}")
                self.progress("convert", done, total, label)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sifac_convert",
        description="Convert extracted SIFAC .bmarc/.btx/.bscam to FBX + PNG "
                    "(fast, parallel, no Noesis, no MMD).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input", nargs="?", help="folder with extracted SIFAC files")
    p.add_argument("output", nargs="?", help="folder to write .fbx / .png")
    p.add_argument("-j", "--jobs", type=int, default=max(1, os.cpu_count() or 4),
                   help="parallel worker count")
    p.add_argument("--preset", choices=PRESETS, default="all",
                   help="what to convert")
    p.add_argument("--scale", type=float, default=1.0,
                   help="uniform scale applied to all geometry/animation")
    p.add_argument("--no-morphs", action="store_true",
                   help="skip blendshape (morph) export")
    p.add_argument("--no-textures", action="store_true",
                   help="do not decode textures to PNG")
    p.add_argument("--anim-only", action="store_true",
                   help="motions export skeleton+animation only (no mesh)")
    p.add_argument("--bundle-motions", action="store_true",
                   help="bundle a folder's motions into its model FBX as takes")
    p.add_argument("--model", help="force this model file to rig all motions")
    p.add_argument("--models-dir",
                   help="folder of extracted models; motions are matched to a "
                        "model here by character name (e.g. 03_kotori) when no "
                        "model sits beside them")
    p.add_argument("--include", action="append", default=[],
                   help="only process filenames matching this glob (repeatable)")
    p.add_argument("--exclude", action="append", default=[],
                   help="skip filenames matching this glob (repeatable)")
    p.add_argument("--skip-existing", action="store_true")
    p.add_argument("--engine", choices=["auto", "python", "blender"],
                   default="auto",
                   help="FBX engine. 'blender' builds via bpy (Blender-perfect "
                        "skinning+animation; needs 'pip install bpy', slower); "
                        "'python' is the fast dependency-free writer; 'auto' "
                        "uses blender when bpy is installed")
    p.add_argument("--up-axis", choices=["y", "z"], default="y",
                   help="up axis of the output FBX. 'y' (default) matches SIFAS "
                        "and most DCC tools (Y-up); 'z' writes a Z-up file. Both "
                        "stand upright in Blender — use 'y' to match the existing "
                        "SIFAS modding-tool FBX.")
    p.add_argument("--threads", action="store_true",
                   help="use threads instead of processes")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check", action="store_true",
                   help="report what would be converted, then exit")
    return p


def cmd_main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.input or not args.output:
        if args.check and args.input:
            args.output = args.input  # check only needs input
        else:
            build_arg_parser().error("input and output folders are required")

    cfg = ConvertConfig(
        input_dir=Path(args.input),
        output_dir=Path(args.output),
        preset=args.preset,
        jobs=args.jobs,
        scale=args.scale,
        include_morphs=not args.no_morphs,
        with_textures=not args.no_textures,
        anim_only=args.anim_only,
        bundle_motions=args.bundle_motions,
        force_model=Path(args.model) if args.model else None,
        models_dir=Path(args.models_dir) if args.models_dir else None,
        include=args.include,
        exclude=args.exclude,
        skip_existing=args.skip_existing,
        use_processes=not args.threads,
        engine=args.engine,
        up_axis=args.up_axis,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )
    conv = Converter(cfg, log_cb=_print_log)
    if args.check:
        plan = conv.plan()
        print("models   :", len(plan.model_jobs))
        print("motions  :", len(plan.motion_jobs))
        print("cameras  :", len(plan.camera_jobs))
        print("textures :", len(plan.texture_jobs))
        return 0
    try:
        summary = conv.run()
    except FileNotFoundError as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        conv.request_stop()
        print("\ninterrupted", file=sys.stderr)
        return 130
    return 1 if summary.failures else 0


if __name__ == "__main__":
    raise SystemExit(cmd_main())
