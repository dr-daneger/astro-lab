#!/usr/bin/env python3
"""
Astro-Pipeline Orchestrator

Main pipeline controller for narrowband SHO astrophotography processing.
Implements:
  - PipelineStage ABC with input/output validation and artifact-based idempotency
  - PipelineOrchestrator state machine with breakpoint system
  - StructuredLogger: JSON log per stage (inputs, params, timing, exit code, SHA256 hashes)
  - load_config() with structural validation
  - CLI: --config, --start-stage, --force, --dry-run, --list-stages
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────


class PipelineError(RuntimeError):
    """Raised when a pipeline stage fails validation or execution."""


class ConfigError(ValueError):
    """Raised when the pipeline configuration is invalid or missing required keys."""


# ─────────────────────────────────────────────────────────────────────────────
# Structured Logger
# ─────────────────────────────────────────────────────────────────────────────


class StructuredLogger:
    """
    Writes one JSON log file per stage execution to the log directory.

    Log schema per file:
        stage_name          str   — Human-readable stage name
        phase               int   — Pipeline phase number
        track               str   — "nb" | "rgb" | "merge" | "final"
        input_files         list  — Declared input file paths
        parameters          dict  — Stage-specific parameters passed at runtime
        start_time          str   — ISO 8601 UTC timestamp
        end_time            str   — ISO 8601 UTC timestamp
        wall_clock_seconds  float — Elapsed time in seconds
        exit_code           int   — Subprocess/stage exit code (0 = success)
        output_file_hashes  dict  — {path: sha256_hex | null if missing}
    """

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = Path(log_dir)
        # Directory is created lazily on first write so --dry-run / --list-stages
        # work even when the configured working drive doesn't exist yet.
        self._current: dict[str, Any] = {}
        self._start_time: float = 0.0

    # ── Console helpers ───────────────────────────────────────────────────────

    def info(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] INFO  {message}")

    def warning(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] WARN  {message}", file=sys.stderr)

    def error(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] ERROR {message}", file=sys.stderr)

    # ── Stage lifecycle ───────────────────────────────────────────────────────

    def begin_stage(
        self,
        stage: "PipelineStage",
        parameters: Optional[dict[str, Any]] = None,
    ) -> None:
        """Record stage start. Call before stage.execute()."""
        self._start_time = time.monotonic()
        self._current = {
            "stage_name": stage.name,
            "phase": stage.phase,
            "track": stage.track,
            "input_files": list(stage.input_spec),
            "parameters": parameters or {},
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": None,
            "wall_clock_seconds": None,
            "exit_code": None,
            "output_file_hashes": {},
        }
        self.info(f"Starting  [{stage.phase}/{stage.track}] {stage.name}")

    def end_stage(self, stage: "PipelineStage", exit_code: int) -> None:
        """Record stage end, hash outputs, write JSON + tool console log files."""
        elapsed = time.monotonic() - self._start_time
        self._current["end_time"] = datetime.now(timezone.utc).isoformat()
        self._current["wall_clock_seconds"] = round(elapsed, 3)
        self._current["exit_code"] = exit_code
        self._current["output_file_hashes"] = self._hash_paths(stage.output_spec)

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_name = stage.name.replace(" ", "_").replace("/", "-").replace("\\", "-")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # ── Tool console log (.pi.log / .graxpert.log) ────────────────────────
        from pi_runner import get_last_pi_output
        from graxpert_runner import get_last_graxpert_output

        pi_out = get_last_pi_output()
        gx_out = get_last_graxpert_output()

        tool_log_path: Optional[Path] = None
        flagged_lines: list[str] = []

        # PI routes Console.writeln() to stderr in automation mode.
        # Use the combined "console" key (stdout + stderr) for the log file.
        if pi_out["console"]:
            tool_log_path = self.log_dir / f"{safe_name}_{ts}.pi.log"
            tool_log_path.write_text(pi_out["console"], encoding="utf-8")
            flagged_lines = pi_out["flagged_lines"]
        elif gx_out["stdout"] or gx_out["stderr"]:
            tool_log_path = self.log_dir / f"{safe_name}_{ts}.graxpert.log"
            combined = ""
            if gx_out["stdout"]:
                combined += "=== stdout ===\n" + gx_out["stdout"]
            if gx_out["stderr"]:
                combined += "\n=== stderr ===\n" + gx_out["stderr"]
            tool_log_path.write_text(combined, encoding="utf-8")
            flagged_lines = gx_out["flagged_lines"]

        self._current["tool_log_file"] = str(tool_log_path) if tool_log_path else None
        self._current["pi_flagged_lines"] = flagged_lines or None

        if flagged_lines:
            self.warning(
                f"{len(flagged_lines)} flagged line(s) in tool output "
                f"for [{stage.name}]:"
            )
            for line in flagged_lines:
                self.warning(f"  >> {line.strip()}")

        # ── JSON stage log ────────────────────────────────────────────────────
        log_path = self.log_dir / f"{safe_name}_{ts}.json"
        with log_path.open("w", encoding="utf-8") as f:
            json.dump(self._current, f, indent=2)

        status = "OK" if exit_code == 0 else f"FAILED (code={exit_code})"
        self.info(f"Completed [{stage.phase}/{stage.track}] {stage.name} — {status} — {elapsed:.1f}s")
        self.info(f"  Log: {log_path.name}")
        if tool_log_path:
            self.info(f"  Log: {tool_log_path.name}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _hash_paths(self, paths: list[str]) -> dict[str, Optional[str]]:
        """Compute SHA-256 for each path. Returns None for missing files."""
        result: dict[str, Optional[str]] = {}
        for p in paths:
            fp = Path(p)
            if fp.exists() and fp.is_file():
                sha = hashlib.sha256()
                with fp.open("rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        sha.update(chunk)
                result[p] = sha.hexdigest()
            else:
                result[p] = None
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Stage base class
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PipelineStage(ABC):
    """
    Abstract base class for all pipeline stages.

    Subclasses implement execute() which performs the actual work and returns
    a shell-style exit code (0 = success).

    Idempotency is artifact-based: is_complete() returns True when all
    declared output files already exist on disk. Stages are skipped unless
    --force is passed.
    """

    name: str
    phase: int
    track: str                            # "nb" | "rgb" | "merge" | "final"
    input_spec: list[str] = field(default_factory=list)
    output_spec: list[str] = field(default_factory=list)
    breakpoint: bool = False
    pjsr_template: Optional[str] = None  # Path to .js template (Sprint 2+)
    external_cmd: Optional[str] = None   # CLI command for external tools

    def validate_inputs(self) -> bool:
        """Return True if all declared input files exist."""
        return all(Path(f).exists() for f in self.input_spec)

    def validate_outputs(self) -> bool:
        """Return True if all declared output files exist."""
        return all(Path(f).exists() for f in self.output_spec)

    def is_complete(self) -> bool:
        """
        Artifact-based idempotency check.
        A stage is complete if it has declared outputs and all of them exist.
        Stages with no declared outputs are never considered complete.
        """
        if not self.output_spec:
            return False
        return all(Path(f).exists() for f in self.output_spec)

    def missing_inputs(self) -> list[str]:
        """Return list of input paths that do not exist."""
        return [f for f in self.input_spec if not Path(f).exists()]

    def missing_outputs(self) -> list[str]:
        """Return list of output paths that do not exist."""
        return [f for f in self.output_spec if not Path(f).exists()]

    @abstractmethod
    def execute(self, config: dict) -> int:
        """Execute the stage. Returns 0 on success, non-zero on failure."""
        ...

    def __repr__(self) -> str:
        return (
            f"<PipelineStage name={self.name!r} phase={self.phase} "
            f"track={self.track!r} breakpoint={self.breakpoint}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Orchestrator
# ─────────────────────────────────────────────────────────────────────────────


class PipelineOrchestrator:
    """
    State machine that sequences pipeline stages in order, manages breakpoints,
    enforces idempotency, and writes structured JSON logs.

    Pipeline state = which output files exist on disk. Re-running the pipeline
    after a partial completion automatically resumes from the first incomplete
    stage. --force re-runs all stages regardless.
    """

    def __init__(
        self,
        config: dict,
        stages: list[PipelineStage],
        log_dir: Path,
    ) -> None:
        self.config = config
        self.stages = stages
        self.log = StructuredLogger(Path(log_dir))

    # ── Main run loop ─────────────────────────────────────────────────────────

    def run(
        self,
        start_stage: Optional[str] = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> None:
        """
        Execute the pipeline.

        Args:
            start_stage: Stage name to begin from. All preceding stages are
                         logged as skipped (no validation or execution).
            force:       Ignore artifact-based completion check; re-run every
                         stage even if output files already exist.
            dry_run:     Print what would run without executing anything.
        """
        active = (start_stage is None)
        total = len(self.stages)
        ran = 0
        skipped = 0

        print(f"\nAstro-Pipeline -- {total} stages registered")
        if dry_run:
            print("(DRY RUN - no stages will execute)\n")

        for stage in self.stages:
            # ── Stage gating ─────────────────────────────────────────────────
            if not active:
                if stage.name == start_stage:
                    active = True
                    self.log.info(f"Resuming pipeline from: {stage.name!r}")
                else:
                    continue

            # ── Idempotency check ─────────────────────────────────────────────
            if stage.is_complete() and not force:
                self.log.info(f"Skip  [{stage.phase}/{stage.track}] {stage.name} — outputs exist")
                skipped += 1
                continue

            # ── Dry run ───────────────────────────────────────────────────────
            if dry_run:
                bp_flag = "  [BREAKPOINT]" if stage.breakpoint else ""
                print(f"  Phase {stage.phase:>2} | {stage.track:<6} | {stage.name}{bp_flag}")
                ran += 1
                continue

            # ── Input validation ──────────────────────────────────────────────
            if stage.input_spec and not stage.validate_inputs():
                missing = stage.missing_inputs()
                raise PipelineError(
                    f"Stage '{stage.name}': missing input files: {missing}"
                )

            # ── Execute ───────────────────────────────────────────────────────
            self.log.begin_stage(stage)
            try:
                exit_code = stage.execute(self.config)
            except KeyboardInterrupt:
                self.log.warning(f"Stage {stage.name!r} interrupted by user.")
                sys.exit(130)
            except Exception as exc:
                self.log.error(f"Stage {stage.name!r} raised an unhandled exception: {exc}")
                raise

            self.log.end_stage(stage, exit_code)
            ran += 1

            if exit_code != 0:
                raise PipelineError(
                    f"Stage '{stage.name}' exited with code {exit_code}. "
                    "Inspect the stage log for details."
                )

            # ── Output validation ─────────────────────────────────────────────
            if stage.output_spec and not stage.validate_outputs():
                missing = stage.missing_outputs()
                raise PipelineError(
                    f"Stage '{stage.name}': outputs missing after successful execution: {missing}"
                )

            # ── Breakpoint ────────────────────────────────────────────────────
            if stage.breakpoint and self._breakpoint_enabled(stage):
                self._prompt_breakpoint(stage)

        print(f"\nPipeline complete -- {ran} executed, {skipped} skipped.")

    # ── Breakpoint helpers ────────────────────────────────────────────────────

    def _breakpoint_enabled(self, stage: PipelineStage) -> bool:
        """
        Look up whether this stage's breakpoint is toggled on in config.
        Breakpoints default to True if the config key is absent.
        """
        bps = self.config.get("breakpoints", {})
        if not bps:
            return True
        n = stage.name.lower()
        if "crop" in n:
            return bps.get("crop", True)
        if any(k in n for k in ("deconv", "blur", "bxt", "sharpen")):
            return bps.get("deconvolution_review", True)
        if "stretch" in n:
            return bps.get("stretch_review", True)
        if any(k in n for k in ("hue", "color", "curves")):
            return bps.get("color_grading", True)
        if "screen blend" in n or "recombination" in n:
            return bps.get("star_recombination", True)
        return True

    def _prompt_breakpoint(self, stage: PipelineStage) -> None:
        """
        Pause the pipeline, auto-open existing outputs in PI with auto-STF
        applied and all windows tiled, then prompt the operator for direction.
        """
        from pjsr_generator import generate_review_script
        from pi_runner import launch_pi_review

        pi_exe   = self.config.get("tools", {}).get("pixinsight_exe", "")
        working  = self.config.get("directories", {}).get("working", "")
        existing = [f for f in stage.output_spec if Path(f).exists()]

        print(f"\n{'=' * 64}")
        print(f"  BREAKPOINT -- Phase {stage.phase} | {stage.name}")
        print(f"{'-' * 64}")
        for f in stage.output_spec:
            status = "EXISTS " if Path(f).exists() else "MISSING"
            print(f"    [{status}]  {f}")
        print(f"{'=' * 64}")

        # Auto-open PI with auto-STF + tiled layout if outputs exist
        if existing and pi_exe and Path(pi_exe).exists():
            try:
                script = generate_review_script(existing, label=stage.name)
                launch_pi_review(script, pi_exe=pi_exe, script_dir=working)
                print(f"  PixInsight opened -- {len(existing)} file(s), auto-STF applied, tiled.")
            except Exception as exc:
                print(f"  WARNING: could not launch PI review: {exc}")
        elif not existing:
            print("  (No output files exist yet -- nothing to open in PI.)")

        # Non-interactive mode (e.g. background process, no TTY):
        # PI has already opened for review; auto-continue so the pipeline
        # keeps running while the operator inspects the images.
        if not sys.stdin.isatty():
            print("  (Non-interactive: pipeline auto-continues. Review PI at your leisure.)")
            self.log.info(f"Breakpoint auto-continued (non-interactive): {stage.name!r}")
            return

        while True:
            choice = input(
                "  [Enter] continue pipeline  |  [Q] quit: "
            ).strip().lower()

            if choice == "":
                self.log.info(f"Breakpoint cleared: {stage.name!r}")
                return

            if choice == "q":
                print("  Pipeline halted at operator request.")
                sys.exit(0)

            print("  Invalid input. Press Enter to continue, M to open PI, Q to quit.")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration loading and validation
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_TOP_KEYS = {"target", "directories", "acquisition", "preprocessing", "processing"}
_REQUIRED_DIR_KEYS = {"raw_nb", "raw_rgb", "working", "output"}
_REQUIRED_ACQ_TRACK_KEYS = {"gain", "exposure", "temperature", "filters"}
_REQUIRED_PREPROCESSING_KEYS = {"pedestal", "drizzle_scale", "rejection_algorithm"}
_REQUIRED_PROCESSING_KEYS = {
    "bxt_sharpen_stars",
    "bxt_sharpen_nonstellar",
    "graxpert_denoise_strength",
    "stretch_target_median",
}


def load_config(config_path: "str | Path") -> dict:
    """
    Load pipeline_config.json from disk, validate its structure, and return
    the parsed dict.

    Raises:
        ConfigError: if the file is missing, contains invalid JSON, or fails
                     structural validation.
    """
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path.resolve()}")

    with path.open("r", encoding="utf-8") as f:
        try:
            cfg = json.load(f)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc

    _validate_config(cfg, path)
    return cfg


def _validate_config(cfg: dict, path: Path) -> None:
    """Raise ConfigError for any structural violation."""
    # Top-level keys
    missing = _REQUIRED_TOP_KEYS - set(cfg)
    if missing:
        raise ConfigError(f"{path}: missing top-level keys: {sorted(missing)}")

    # directories
    dirs = cfg.get("directories", {})
    missing = _REQUIRED_DIR_KEYS - set(dirs)
    if missing:
        raise ConfigError(f"{path}: directories missing keys: {sorted(missing)}")

    # acquisition tracks
    acq = cfg.get("acquisition", {})
    for track in ("nb", "rgb"):
        if track not in acq:
            raise ConfigError(f"{path}: acquisition.{track} section is required")
        t_missing = _REQUIRED_ACQ_TRACK_KEYS - set(acq[track])
        if t_missing:
            raise ConfigError(
                f"{path}: acquisition.{track} missing keys: {sorted(t_missing)}"
            )

    # preprocessing
    pre = cfg.get("preprocessing", {})
    missing = _REQUIRED_PREPROCESSING_KEYS - set(pre)
    if missing:
        raise ConfigError(f"{path}: preprocessing missing keys: {sorted(missing)}")

    # processing
    proc = cfg.get("processing", {})
    missing = _REQUIRED_PROCESSING_KEYS - set(proc)
    if missing:
        raise ConfigError(f"{path}: processing missing keys: {sorted(missing)}")


def normalize_paths(cfg: dict) -> dict:
    """
    Build cfg['_paths'] with two path representations for all directory entries:
        native  — OS-native separators (for Python / subprocess)
        pjsr    — forward slashes (mandatory for PJSR / JavaScript on Windows)

    The original cfg['directories'] values are left unchanged.
    """
    native: dict[str, str] = {}
    pjsr: dict[str, str] = {}
    for key, val in cfg.get("directories", {}).items():
        if isinstance(val, str):
            native[key] = str(Path(val))
            pjsr[key] = Path(val).as_posix()
    cfg["_paths"] = {"native": native, "pjsr": pjsr}
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="astro-pipeline",
        description=(
            "Automated narrowband SHO astrophotography processing pipeline.\n"
            "Target: NGC 1499 (California Nebula) — Ha / OIII / SII"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python orchestrator.py --config pipeline_config.json
  python orchestrator.py --config pipeline_config.json --dry-run
  python orchestrator.py --config pipeline_config.json --list-stages
  python orchestrator.py --config pipeline_config.json --start-stage "GraXpert Background Extraction"
  python orchestrator.py --config pipeline_config.json --force
""",
    )
    p.add_argument(
        "--config", "-c",
        default="pipeline_config.json",
        metavar="PATH",
        help="Path to pipeline_config.json  (default: pipeline_config.json)",
    )
    p.add_argument(
        "--start-stage", "-s",
        default=None,
        metavar="STAGE_NAME",
        dest="start_stage",
        help="Resume pipeline from this stage name (all preceding stages are skipped)",
    )
    p.add_argument(
        "--force", "-f",
        action="store_true",
        default=False,
        help="Re-run all stages even if output files already exist",
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        default=False,
        dest="dry_run",
        help="Print stages that would execute without running them",
    )
    p.add_argument(
        "--list-stages",
        action="store_true",
        default=False,
        dest="list_stages",
        help="Print all registered pipeline stages and exit",
    )
    return p


def _print_stage_list(stages: list[PipelineStage]) -> None:
    header = f"{'Ph':>3}  {'Track':<6}  {'BP':>2}  Stage"
    print(f"\n{header}")
    print("-" * 64)
    for s in stages:
        bp_marker = "*" if s.breakpoint else " "
        print(f"  {s.phase:>2}  {s.track:<6}  {bp_marker:>2}  {s.name}")
    print()


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Load and validate configuration
    try:
        config = load_config(args.config)
        config = normalize_paths(config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    # Import stage registry (populated by stages/ subpackage; stubs in Sprint 1)
    try:
        from stages import get_all_stages
    except ImportError as exc:
        print(f"Failed to import stage registry: {exc}", file=sys.stderr)
        return 1

    stages = get_all_stages(config)

    if args.list_stages:
        _print_stage_list(stages)
        return 0

    # Validate --start-stage
    if args.start_stage:
        known = {s.name for s in stages}
        if args.start_stage not in known:
            print(
                f"Error: unknown stage name '{args.start_stage}'.\n"
                "Use --list-stages to see available stage names.",
                file=sys.stderr,
            )
            return 1

    # Log directory sits inside the working directory
    log_dir = Path(config["directories"]["working"]) / "pipeline_logs"
    orchestrator = PipelineOrchestrator(config, stages, log_dir)

    try:
        orchestrator.run(
            start_stage=args.start_stage,
            force=args.force,
            dry_run=args.dry_run,
        )
    except PipelineError as exc:
        print(f"\nPipeline halted: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
