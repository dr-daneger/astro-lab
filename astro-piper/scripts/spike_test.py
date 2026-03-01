#!/usr/bin/env python3
"""
scripts/spike_test.py — Sprint 0: Headless feasibility spike tests

Validates that the local environment can run the pipeline's external tools in
headless/automation mode before any real data is processed.

Checklist (matches design_doc.md Section 13, Sprint 0):
  [1] PixInsight exits cleanly in automation mode (--force-exit return code)
  [2] BXT executes on a single XISF frame via PJSR automation
  [3] NXT executes on a single XISF frame via PJSR automation
  [4] SXT executes on a single XISF frame via PJSR automation
  [5] GraXpert CLI runs on a single frame
  [6] Confirm return codes and output file creation

Usage:
    python scripts/spike_test.py --config pipeline_config.json [--test-xisf PATH]

    --test-xisf     Path to a single XISF file used for PI spike tests.
                    If omitted, PI tests generate a minimal synthetic frame.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

# Allow imports from parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from pi_runner import run_pjsr, run_pjsr_inline, verify_pi_installation
from graxpert_runner import run_graxpert, verify_graxpert_installation


# ── Result tracking ────────────────────────────────────────────────────────────


class SpikeResult:
    def __init__(self):
        self.results: list[dict] = []

    def record(self, test_name: str, passed: bool, detail: str = "") -> None:
        status = "PASS" if passed else "FAIL"
        self.results.append({"test": test_name, "status": status, "detail": detail})
        symbol = "✓" if passed else "✗"
        print(f"  [{symbol}] {test_name}: {status}")
        if detail:
            for line in detail.splitlines():
                print(f"       {line}")

    def summary(self) -> int:
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        total = len(self.results)
        print(f"\n{'=' * 60}")
        print(f"  Sprint 0 Spike Tests: {passed}/{total} passed")
        print(f"{'=' * 60}\n")
        return 0 if passed == total else 1


# ── Individual tests ───────────────────────────────────────────────────────────


def test_pi_found(results: SpikeResult, pi_exe: str) -> bool:
    found = verify_pi_installation(pi_exe)
    results.record(
        "PixInsight executable found",
        found,
        detail=f"Path: {pi_exe}",
    )
    return found


def test_graxpert_found(results: SpikeResult, graxpert_exe: str) -> bool:
    found = verify_graxpert_installation(graxpert_exe)
    results.record(
        "GraXpert executable found",
        found,
        detail=f"Path: {graxpert_exe}",
    )
    return found


def test_pi_clean_exit(results: SpikeResult, pi_exe: str) -> bool:
    """
    Run the minimal possible PJSR script (no-op) and confirm PI exits
    with code 0 when --force-exit is present.
    """
    noop_script = textwrap.dedent("""\
        // Spike test: minimal no-op script to verify --force-exit behavior
        Console.writeln("astro-pipeline spike test: clean exit check");
    """)
    try:
        code = run_pjsr_inline(noop_script, pi_exe=pi_exe, timeout=60)
        passed = (code == 0)
        results.record(
            "PixInsight clean exit (--force-exit)",
            passed,
            detail=f"Exit code: {code}",
        )
        return passed
    except Exception as exc:
        results.record(
            "PixInsight clean exit (--force-exit)",
            False,
            detail=str(exc),
        )
        return False


def test_bxt_headless(results: SpikeResult, pi_exe: str, test_xisf: str) -> bool:
    """Run BlurXTerminator in correct-only mode on the test XISF."""
    out = Path(test_xisf).with_stem(Path(test_xisf).stem + "_bxt_spike")
    script = textwrap.dedent(f"""\
        #include <pjsr/DataType.jsh>
        var w = ImageWindow.open("{Path(test_xisf).as_posix()}")[0];
        var view = w.currentView;
        var BXT = new BlurXTerminator;
        BXT.correct_only = true;
        BXT.automatic_psf = true;
        BXT.executeOn(view);
        w.saveAs("{out.as_posix()}", false, false, false, false);
        w.forceClose();
        Console.writeln("BXT spike test complete");
    """)
    try:
        code = run_pjsr_inline(script, pi_exe=pi_exe, timeout=2700)
        output_exists = Path(out).exists()
        passed = (code == 0) and output_exists
        results.record(
            "BlurXTerminator headless execution",
            passed,
            detail=f"Exit code: {code} | Output exists: {output_exists} | {out.name}",
        )
        return passed
    except Exception as exc:
        results.record("BlurXTerminator headless execution", False, detail=str(exc))
        return False


def test_nxt_headless(results: SpikeResult, pi_exe: str, test_xisf: str) -> bool:
    """Run NoiseXTerminator on the test XISF."""
    out = Path(test_xisf).with_stem(Path(test_xisf).stem + "_nxt_spike")
    script = textwrap.dedent(f"""\
        #include <pjsr/DataType.jsh>
        var w = ImageWindow.open("{Path(test_xisf).as_posix()}")[0];
        var view = w.currentView;
        var NXT = new NoiseXTerminator;
        NXT.denoise = 0.80;
        NXT.detail = 0.15;
        NXT.executeOn(view);
        w.saveAs("{out.as_posix()}", false, false, false, false);
        w.forceClose();
        Console.writeln("NXT spike test complete");
    """)
    try:
        code = run_pjsr_inline(script, pi_exe=pi_exe, timeout=2700)
        output_exists = Path(out).exists()
        passed = (code == 0) and output_exists
        results.record(
            "NoiseXTerminator headless execution",
            passed,
            detail=f"Exit code: {code} | Output exists: {output_exists}",
        )
        return passed
    except Exception as exc:
        results.record("NoiseXTerminator headless execution", False, detail=str(exc))
        return False


def test_sxt_headless(results: SpikeResult, pi_exe: str, test_xisf: str) -> bool:
    """Run StarXTerminator on the test XISF."""
    out = Path(test_xisf).with_stem(Path(test_xisf).stem + "_sxt_spike")
    script = textwrap.dedent(f"""\
        #include <pjsr/DataType.jsh>
        var w = ImageWindow.open("{Path(test_xisf).as_posix()}")[0];
        var view = w.currentView;
        var SXT = new StarXTerminator;
        SXT.stars_image = false;
        SXT.unscreen = false;
        SXT.executeOn(view);
        w.saveAs("{out.as_posix()}", false, false, false, false);
        w.forceClose();
        Console.writeln("SXT spike test complete");
    """)
    try:
        code = run_pjsr_inline(script, pi_exe=pi_exe, timeout=2700)
        output_exists = Path(out).exists()
        passed = (code == 0) and output_exists
        results.record(
            "StarXTerminator headless execution",
            passed,
            detail=f"Exit code: {code} | Output exists: {output_exists}",
        )
        return passed
    except Exception as exc:
        results.record("StarXTerminator headless execution", False, detail=str(exc))
        return False


def test_graxpert_headless(
    results: SpikeResult, graxpert_exe: str, test_xisf: str
) -> bool:
    """Run GraXpert background extraction on the test XISF."""
    out = Path(test_xisf).with_stem(Path(test_xisf).stem + "_bgext_spike")
    try:
        from graxpert_runner import run_graxpert
        code = run_graxpert(
            input_path=test_xisf,
            output_path=str(out),
            operation="background-extraction",
            smoothing=0.1,
            gpu=True,
            graxpert_exe=graxpert_exe,
            timeout=300,
        )
        output_exists = out.exists()
        passed = (code == 0) and output_exists
        results.record(
            "GraXpert CLI background extraction",
            passed,
            detail=f"Exit code: {code} | Output exists: {output_exists}",
        )
        return passed
    except Exception as exc:
        results.record("GraXpert CLI background extraction", False, detail=str(exc))
        return False


# ── Main ───────────────────────────────────────────────────────────────────────


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Sprint 0: Headless feasibility spike tests for astro-pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", "-c",
        default="pipeline_config.json",
        metavar="PATH",
        help="Path to pipeline_config.json",
    )
    parser.add_argument(
        "--test-xisf",
        default=None,
        metavar="PATH",
        dest="test_xisf",
        help="Path to a single XISF frame for PI spike tests (BXT/NXT/SXT). "
             "Required for tool-execution tests.",
    )
    parser.add_argument(
        "--pi-only",
        action="store_true",
        help="Run only PixInsight tests",
    )
    parser.add_argument(
        "--graxpert-only",
        action="store_true",
        help="Run only GraXpert tests",
    )
    args = parser.parse_args(argv)

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    with config_path.open() as f:
        config = json.load(f)

    pi_exe = config.get("tools", {}).get("pixinsight_exe", "")
    graxpert_exe = config.get("tools", {}).get("graxpert_exe", "")

    results = SpikeResult()

    print("\nAstro-Pipeline — Sprint 0 Spike Tests")
    print("=" * 60)

    run_pi = not args.graxpert_only
    run_gx = not args.pi_only

    # ── Existence checks ─────────────────────────────────────────────────────
    pi_ok = test_pi_found(results, pi_exe) if run_pi else False
    gx_ok = test_graxpert_found(results, graxpert_exe) if run_gx else False

    # ── PixInsight tests ─────────────────────────────────────────────────────
    if run_pi and pi_ok:
        test_pi_clean_exit(results, pi_exe)

        if args.test_xisf:
            xisf = args.test_xisf
            if not Path(xisf).exists():
                print(f"\nWarning: --test-xisf file not found: {xisf}")
                print("Skipping BXT/NXT/SXT execution tests.\n")
            else:
                test_bxt_headless(results, pi_exe, xisf)
                test_nxt_headless(results, pi_exe, xisf)
                test_sxt_headless(results, pi_exe, xisf)
        else:
            print("\n  (Skipping BXT/NXT/SXT tests — no --test-xisf provided)")
            print("  Run with --test-xisf /path/to/frame.xisf to test tool execution.\n")

    # ── GraXpert tests ───────────────────────────────────────────────────────
    if run_gx and gx_ok and args.test_xisf:
        test_graxpert_headless(results, graxpert_exe, args.test_xisf)

    return results.summary()


if __name__ == "__main__":
    sys.exit(main())
