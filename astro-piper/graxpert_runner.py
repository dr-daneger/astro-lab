"""
graxpert_runner.py — GraXpert CLI integration

Provides run_graxpert(): invokes GraXpert from the command line for background
extraction or denoising operations on XISF/FITS images.

GraXpert CLI reference:
    GraXpert-win64.exe <input_path> -cli -cmd <operation>
        -output <output_path>
        -correction <Subtraction|Division>
        -smoothing <0.0–1.0>
        -gpu <true|false>

Supported operations:
    background-extraction   AI-based background gradient removal
    denoising               (optional, not used in primary pipeline)

Usage:
    from graxpert_runner import run_graxpert, GraXpertError

    run_graxpert(
        graxpert_exe="C:/Program Files/GraXpert/GraXpert-win64.exe",
        input_path="E:/data/Ha_master.xisf",
        output_path="E:/data/Ha_bgext.xisf",
        smoothing=0.1,
        gpu=True,
    )
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional


class GraXpertError(RuntimeError):
    """Raised when a GraXpert CLI invocation fails."""


DEFAULT_GRAXPERT_EXE = "C:/Program Files/GraXpert/GraXpert-win64.exe"
DEFAULT_TIMEOUT = 600  # seconds — background extraction is typically 1–3 min

# ── GraXpert console capture ──────────────────────────────────────────────────
_last_stdout: str = ""
_last_stderr: str = ""

_ERROR_PATTERNS: list[str] = [
    "error", "exception", "traceback", "failed", "critical", "abort",
]


def get_last_graxpert_output() -> dict:
    """Return stdout/stderr from the most recent GraXpert invocation."""
    flagged = [
        line for line in (_last_stdout + "\n" + _last_stderr).splitlines()
        if line.strip() and any(p in line.lower() for p in _ERROR_PATTERNS)
    ]
    return {
        "stdout": _last_stdout,
        "stderr": _last_stderr,
        "flagged_lines": flagged,
    }


def run_graxpert(
    input_path: "str | Path",
    output_path: "str | Path",
    operation: str = "background-extraction",
    correction: str = "Subtraction",
    smoothing: float = 0.1,
    gpu: bool = True,
    graxpert_exe: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> int:
    """
    Execute GraXpert via CLI for background extraction or denoising.

    Args:
        input_path:     Absolute path to the input XISF/FITS file.
        output_path:    Absolute path for the output file. GraXpert writes
                        the result to this path.
        operation:      GraXpert operation string. One of:
                            "background-extraction"  (default)
                            "denoising"
        correction:     Background correction method: "Subtraction" (default)
                        or "Division". For most narrowband data, Subtraction
                        is correct. Use Division only for multiplicative
                        gradients (rare).
        smoothing:      Controls the smoothness of the AI background model.
                        Range 0.0 (fine) to 1.0 (very smooth). Recommended
                        0.05–0.15 for narrowband with large-scale emission.
        gpu:            Enable CUDA GPU acceleration. Falls back to CPU if
                        no compatible GPU is found.
        graxpert_exe:   Path to GraXpert executable. Defaults to
                        DEFAULT_GRAXPERT_EXE.
        timeout:        Subprocess timeout in seconds.

    Returns:
        Integer exit code. 0 = success.

    Raises:
        GraXpertError:              If the executable is not found or GraXpert
                                    returns a non-zero exit code.
        subprocess.TimeoutExpired:  If execution exceeds `timeout` seconds.
        OSError:                    On process creation failure.
    """
    exe = Path(graxpert_exe or DEFAULT_GRAXPERT_EXE)
    if not exe.exists():
        raise GraXpertError(
            f"GraXpert executable not found: {exe}\n"
            "Update tools.graxpert_exe in pipeline_config.json."
        )

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise GraXpertError(f"GraXpert input file not found: {input_path}")

    # Ensure the output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # GraXpert v3.x appends the .xisf extension itself. Pass the stem only
    # to avoid double-extension (e.g. "result.xisf.xisf"). Confirmed in v3.1.0rc2.
    output_stem = str(output_path.with_suffix(""))

    cmd = [
        str(exe),
        str(input_path),
        "-cli",
        "-cmd", operation,
        "-output", output_stem,
        "-correction", correction,
        "-smoothing", str(smoothing),
        "-gpu", str(gpu).lower(),
    ]

    global _last_stdout, _last_stderr
    _last_stdout = ""
    _last_stderr = ""

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[graxpert_runner] GraXpert timed out after {timeout}s "
            f"processing: {input_path}",
            file=sys.stderr,
        )
        raise

    _last_stdout = result.stdout or ""
    _last_stderr = result.stderr or ""

    if _last_stdout:
        print(_last_stdout, end="", flush=True)
    if _last_stderr:
        print(f"[graxpert_runner] stderr:\n{_last_stderr}", file=sys.stderr, flush=True)

    if result.returncode != 0:
        raise GraXpertError(
            f"GraXpert failed (code={result.returncode}) on {input_path}.\n"
            f"stderr: {_last_stderr[:2000] or '(no stderr)'}"
        )

    return result.returncode


# GraXpert denoising timeout is much longer than bgext: 257s on 875MB Drizzle 2x
# with GPU (observed in spike test). Allow up to 20 min per channel.
DENOISE_TIMEOUT = 1200


def run_graxpert_denoise(
    input_path: "str | Path",
    output_path: "str | Path",
    strength: float = 0.5,
    batch_size: int = 4,
    gpu: bool = True,
    ai_version: Optional[str] = None,
    graxpert_exe: Optional[str] = None,
    timeout: int = DENOISE_TIMEOUT,
) -> int:
    """
    Run GraXpert AI denoising via CLI.

    Replaces NoiseXTerminator in the pipeline (not owned). GPU-accelerated,
    CLI-automatable, and free. Observed performance on 875MB Drizzle 2x: ~257s
    with GPU (spike test 2026-02-20).

    Design doc note (Section 10 NR comparison):
        "Can soften stars; reports of mottled residuals"
    Apply after BXT, before StarXTerminator. Tune strength conservatively
    (0.5 default; 0.3-0.6 range) since GraXpert denoising is more aggressive
    than NXT at equivalent settings.

    Args:
        input_path:   Input XISF path.
        output_path:  Output XISF path. Extension appended by GraXpert v3.x.
        strength:     Denoise strength (0.0-1.0). Default 0.5 (GraXpert stored value).
        batch_size:   Tile batch size for GPU processing. Reduce to 2 if OOM.
        gpu:          Enable GPU acceleration.
        ai_version:   Override AI model version (e.g. "3.0.2"). None = use stored.
        graxpert_exe: Path to GraXpert executable.
        timeout:      Subprocess timeout in seconds.

    Returns:
        Integer exit code. 0 = success.
    """
    exe = Path(graxpert_exe or DEFAULT_GRAXPERT_EXE)
    if not exe.exists():
        raise GraXpertError(
            f"GraXpert executable not found: {exe}\n"
            "Update tools.graxpert_exe in pipeline_config.json."
        )

    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise GraXpertError(f"GraXpert input file not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_stem = str(output_path.with_suffix(""))

    cmd = [
        str(exe),
        str(input_path),
        "-cli",
        "-cmd", "denoising",
        "-output", output_stem,
        "-gpu", str(gpu).lower(),
        "-strength", str(strength),
        "-batch_size", str(batch_size),
    ]
    if ai_version:
        cmd += ["-ai_version", ai_version]

    global _last_stdout, _last_stderr
    _last_stdout = ""
    _last_stderr = ""

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[graxpert_runner] GraXpert denoising timed out after {timeout}s: {input_path}",
            file=sys.stderr,
        )
        raise

    _last_stdout = result.stdout or ""
    _last_stderr = result.stderr or ""

    if _last_stdout:
        print(_last_stdout, end="", flush=True)
    if _last_stderr:
        print(f"[graxpert_runner] stderr:\n{_last_stderr}", file=sys.stderr, flush=True)

    if result.returncode != 0:
        raise GraXpertError(
            f"GraXpert denoising failed (code={result.returncode}) on {input_path}.\n"
            f"stderr: {_last_stderr[:2000] or '(no stderr)'}"
        )

    return result.returncode


def run_graxpert_batch(
    channel_paths: dict[str, "str | Path"],
    output_dir: "str | Path",
    smoothing: float = 0.1,
    gpu: bool = True,
    graxpert_exe: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Path]:
    """
    Run background extraction on multiple NB channels in sequence.

    Args:
        channel_paths:  Dict mapping channel name to input path.
                        e.g. {"Ha": "/data/Ha_master.xisf", "OIII": ..., "SII": ...}
        output_dir:     Directory to write output files.
        smoothing:      GraXpert smoothing parameter (applied to all channels).
        gpu:            Enable CUDA GPU acceleration.
        graxpert_exe:   GraXpert executable path.
        timeout:        Per-channel timeout in seconds.

    Returns:
        Dict mapping channel name to output Path.

    Raises:
        GraXpertError: On any channel failure. Already-processed channels
                       are not re-run if their output files exist (idempotent
                       behavior matches the orchestrator pattern).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}

    for channel, in_path in channel_paths.items():
        in_path = Path(in_path)
        stem = in_path.stem
        out_path = out_dir / f"{stem}_bgext{in_path.suffix}"

        # Idempotency: skip if output already exists
        if out_path.exists():
            print(f"[graxpert_runner] Skip {channel} — output exists: {out_path.name}")
            outputs[channel] = out_path
            continue

        print(f"[graxpert_runner] Processing {channel}: {in_path.name} → {out_path.name}")
        run_graxpert(
            input_path=in_path,
            output_path=out_path,
            smoothing=smoothing,
            gpu=gpu,
            graxpert_exe=graxpert_exe,
            timeout=timeout,
        )
        outputs[channel] = out_path

    return outputs


def verify_graxpert_installation(graxpert_exe: Optional[str] = None) -> bool:
    """
    Return True if the GraXpert executable exists and is reachable.
    Used by Sprint 0 spike tests to confirm environment readiness.
    """
    exe = Path(graxpert_exe or DEFAULT_GRAXPERT_EXE)
    return exe.exists() and exe.is_file()
