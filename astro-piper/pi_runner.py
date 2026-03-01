"""
pi_runner.py — PixInsight subprocess management

Provides run_pjsr(): executes a PJSR .js script in PixInsight's headless
automation mode and returns the process exit code.

PixInsight CLI flags used:
    -n                  No splash screen
    --automation-mode   Non-interactive / headless execution
    --force-exit        Exit when the script finishes (mandatory — PI hangs otherwise)
    -r="<script>"       Run the named script file

Windows quoting: the full command is passed as a string with shell=True because
Windows CreateProcess cannot handle complex quoting in argv lists when the
executable path or arguments contain spaces.

CRITICAL: All paths passed to PJSR scripts must use forward slashes, even on
Windows. Use Path.as_posix() or str.replace("\\\\", "/") before embedding paths
in generated .js content. pi_runner itself handles its own invocation path.

Usage:
    from pi_runner import run_pjsr, PIRunnerError

    exit_code = run_pjsr(
        pi_exe="C:/Program Files/PixInsight/bin/PixInsight.exe",
        script_path="E:/pipeline/scripts/bxt_phase2.js",
        args=["E:/data/Ha_bgext.xisf", "E:/data/Ha_bxt.xisf"],
        timeout=3600,
    )
    if exit_code != 0:
        raise RuntimeError(f"BXT script failed with code {exit_code}")
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Optional


class PIRunnerError(RuntimeError):
    """Raised when PixInsight fails to start or returns a non-zero exit code."""


# Default PixInsight executable path (override via config or function argument)
DEFAULT_PI_EXE = "C:/Program Files/PixInsight/bin/PixInsight.exe"

# Maximum timeout for a single PJSR invocation (seconds). Long integrations
# (DrizzleIntegration on 100+ subs) can take 30–60 min.
DEFAULT_TIMEOUT = 7200

# ── PI console capture ────────────────────────────────────────────────────────
# Module-level storage for the console output of the most recent PI invocation.
# Stages don't need to change — the orchestrator reads these after each stage.

_last_stdout: str = ""
_last_stderr: str = ""

# Known PI error / warning patterns worth flagging explicitly.
_ERROR_PATTERNS: list[str] = [
    "*** Error",
    "Error:",
    "Unable to compute",
    "failed",
    "exception",
    "ReferenceError",
    "TypeError",
    "SyntaxError",
]


def get_last_pi_output() -> dict:
    """
    Return stdout/stderr from the most recent PI invocation and any
    error lines detected by pattern matching.

    Note: PI routes Console.writeln() to stderr (not stdout) in automation
    mode. The "console" key combines both streams for log writing. Flagged
    lines are scanned from both streams.

    Call this after run_pjsr_inline() to retrieve what PI printed.
    """
    all_lines = (_last_stdout + "\n" + _last_stderr).splitlines()
    flagged = [
        line for line in all_lines
        if any(pat.lower() in line.lower() for pat in _ERROR_PATTERNS)
    ]
    return {
        "stdout": _last_stdout,
        "stderr": _last_stderr,
        "console": _last_stdout + ("\n" if _last_stdout and _last_stderr else "") + _last_stderr,
        "flagged_lines": flagged,
    }


def run_pjsr(
    script_path: "str | Path",
    args: Optional[list[str]] = None,
    pi_exe: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    capture_output: bool = True,
) -> int:
    """
    Execute a PJSR script in PixInsight automation mode.

    Args:
        script_path:    Absolute path to the .js script file.
        args:           Optional list of string arguments forwarded to the
                        script via the -r= argument list. Joined with commas.
        pi_exe:         Path to PixInsight.exe. Defaults to DEFAULT_PI_EXE.
        timeout:        Subprocess timeout in seconds. Raises subprocess.TimeoutExpired
                        if exceeded.
        capture_output: If True, stdout/stderr are captured and returned in the
                        CompletedProcess (useful for logging). If False, output
                        flows to the console.

    Returns:
        Integer exit code. 0 = success.

    Raises:
        PIRunnerError:              If PixInsight executable is not found.
        subprocess.TimeoutExpired:  If the script exceeds `timeout` seconds.
        OSError:                    On process creation failure.
    """
    exe = pi_exe or DEFAULT_PI_EXE
    exe_path = Path(exe)
    if not exe_path.exists():
        raise PIRunnerError(
            f"PixInsight executable not found: {exe_path}\n"
            "Update tools.pixinsight_exe in pipeline_config.json."
        )

    # Normalize script path — Windows needs forward slashes in the -r= argument
    script_fwd = Path(script_path).as_posix()

    # Build -r= argument: script,arg1,arg2,...
    if args:
        r_value = script_fwd + "," + ",".join(args)
    else:
        r_value = script_fwd

    # Build the full command string. We use shell=True on Windows because
    # subprocess list-form doesn't reliably handle spaces in paths inside the
    # -r=... argument when the OS command interpreter is involved.
    #
    # Double-quote the -r value to handle paths with spaces.
    cmd_str = (
        f'"{exe_path}" -n --automation-mode --force-exit -r="{r_value}"'
    )

    global _last_stdout, _last_stderr
    _last_stdout = ""
    _last_stderr = ""

    try:
        result = subprocess.run(
            cmd_str,
            shell=True,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[pi_runner] PixInsight script timed out after {timeout}s: {script_path}",
            file=sys.stderr,
        )
        raise

    if capture_output:
        _last_stdout = result.stdout or ""
        _last_stderr = result.stderr or ""

        # Always print PI console output so errors are visible regardless of exit code.
        # PI exits 0 even when a process fails internally, so we can't rely on returncode.
        if _last_stdout:
            print(_last_stdout, end="", flush=True)
        if _last_stderr:
            print(f"[pi_runner] stderr:\n{_last_stderr}", file=sys.stderr, flush=True)

        # Extra flag: scan for known error patterns even when exit code is 0
        flagged = [
            line for line in _last_stdout.splitlines()
            if any(pat.lower() in line.lower() for pat in _ERROR_PATTERNS)
        ]
        if flagged:
            print(
                f"[pi_runner] WARNING -- {len(flagged)} flagged line(s) in PI output:",
                file=sys.stderr,
            )
            for line in flagged:
                print(f"  >> {line}", file=sys.stderr)

    return result.returncode


def run_pjsr_inline(
    script_source: str,
    args: Optional[list[str]] = None,
    pi_exe: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> int:
    """
    Write a PJSR script string to a temporary .js file and execute it.

    Use this when the script is generated programmatically (Sprint 2+) rather
    than stored as a static template file.

    Args:
        script_source:  The full PJSR JavaScript source as a string.
        args:           Optional argument list (same as run_pjsr).
        pi_exe:         PixInsight executable path.
        timeout:        Subprocess timeout in seconds.

    Returns:
        Integer exit code from PixInsight.

    Notes:
        The temporary file is deleted after execution regardless of outcome.
        Use Path.as_posix() for all file paths embedded in script_source.
    """
    # Dedent in case the caller uses triple-quoted indented strings
    source = textwrap.dedent(script_source)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".js",
        prefix="astropipe_",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(source)
        tmp_path = Path(tmp.name)

    try:
        return run_pjsr(tmp_path, args=args, pi_exe=pi_exe, timeout=timeout)
    finally:
        tmp_path.unlink(missing_ok=True)


def launch_pi_review(
    script_source: str,
    pi_exe: Optional[str] = None,
    script_dir: Optional["str | Path"] = None,
) -> subprocess.Popen:
    """
    Write a review script to a persistent temp file and launch PI in normal
    GUI mode (no --automation-mode, no --force-exit) so it stays open.

    Unlike run_pjsr_inline(), this is non-blocking: it returns immediately
    after spawning PI, allowing the pipeline breakpoint prompt to appear
    while PI opens in the background.

    The script file is written to script_dir (or the system temp dir) and
    NOT deleted — PI reads it asynchronously after launch.

    Args:
        script_source: PJSR JavaScript source string.
        pi_exe:        Path to PixInsight.exe.
        script_dir:    Directory to write the review script file.
                       Defaults to the system temp directory.

    Returns:
        The Popen handle for the launched PI process.

    Raises:
        PIRunnerError: If the PixInsight executable is not found.
    """
    import textwrap
    exe = Path(pi_exe or DEFAULT_PI_EXE)
    if not exe.exists():
        raise PIRunnerError(
            f"PixInsight executable not found: {exe}\n"
            "Update tools.pixinsight_exe in pipeline_config.json."
        )

    out_dir = Path(script_dir) if script_dir else Path(tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    script_path = out_dir / "astropipe_review.js"
    script_path.write_text(textwrap.dedent(script_source), encoding="utf-8")

    script_fwd = script_path.as_posix()
    # -n = no splash; -r = run script; no --automation-mode so PI stays open
    cmd_str = f'"{exe}" -n -r="{script_fwd}"'
    proc = subprocess.Popen(cmd_str, shell=True)
    return proc


def verify_pi_installation(pi_exe: Optional[str] = None) -> bool:
    """
    Return True if the PixInsight executable exists and is reachable.
    Used by Sprint 0 spike tests to confirm environment readiness.
    """
    exe = Path(pi_exe or DEFAULT_PI_EXE)
    return exe.exists() and exe.is_file()
