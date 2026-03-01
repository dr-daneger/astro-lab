"""
stages/stretching.py -- Sprint 4: Phase 3 stretching and palette implementations

Concrete PipelineStage subclasses for Phase 3:
  StretchNBStage      -- GHS stretch each starless NB channel [BP3]
  LinearFitStage      -- Normalize Ha+SII to OIII reference before Foraxx
  ForaxxPaletteStage  -- Foraxx dynamic SHO palette PixelMath combination

LinearFit rationale (design_doc.md Section 12 -- NGC 1499 strategy):
  NGC 1499 has extreme channel imbalance: Ha >> SII > OIII. Without LinearFit,
  Ha overwhelms the Foraxx combination. LinearFit normalizes Ha and SII to the
  OIII brightness level (the weakest, faintest channel) so all three channels
  contribute proportionally to the palette.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator import PipelineStage, PipelineError
from pi_runner import run_pjsr_inline
from pjsr_generator import (
    generate_histogram_stats,
    generate_ghs_stretch,
    generate_foraxx_palette,
    generate_linear_fit,
)


# =============================================================================
# Per-stage subprocess timeouts (seconds)
# =============================================================================

CROP_TIMEOUT    = 600    # 10 min for all 6 channels
BGEXT_TIMEOUT   = 300    # 5 min per channel (GraXpert, confirmed 16s GPU)
PI_TIMEOUT      = 3600   # 1 h for BXT/SXT/stretch/combine
DENOISE_TIMEOUT = 1200   # 20 min per channel (GraXpert denoise, confirmed 257s GPU)


# =============================================================================
# Local helpers
# =============================================================================


def _run_pjsr(
    script: str,
    step_name: str,
    pi_exe: Optional[str],
    timeout: int,
) -> None:
    """Execute a PJSR script and raise PipelineError on non-zero exit code."""
    print(f"[stretching] Running: {step_name}")
    exit_code = run_pjsr_inline(script, pi_exe=pi_exe, timeout=timeout)
    if exit_code != 0:
        raise PipelineError(
            f"{step_name} failed with exit code {exit_code}.\n"
            "Check PixInsight console output / log for details."
        )


def _get_pi_exe(config: dict) -> Optional[str]:
    """Extract pi_exe from config, returning None if not set."""
    return config.get("tools", {}).get("pixinsight_exe") or None


# =============================================================================
# Stage 0: Measure Histogram Stats per NB Channel (for GHS SP calibration)
# =============================================================================

HISTOGRAM_STATS_FILE = "histogram_stats.json"


@dataclass
class MeasureHistogramStage(PipelineStage):
    """
    Measure per-channel background statistics for GHS symmetry point (SP) calibration.

    Runs a PJSR Statistics script on each background-extracted NB channel to
    measure the background median. The median of a linear NB image after GraXpert
    background extraction is a reliable proxy for the histogram peak (SP), which
    is where GHS should anchor its stretch transition.

    Setting SP to the actual per-channel median rather than a hardcoded 0.0001
    ensures GHS is anchored correctly for each channel independently. This is
    critical for NGC 1499 because:
      - Ha background is relatively brighter (strong emission, higher pedestal)
      - OIII background is darker (faint signal, lower post-bgext level)
      - SII is intermediate
    With identical SP=0.0001, OIII gets more stretch than Ha, causing the
    blue/purple color cast observed in first pipeline runs.

    Input:  NGC1499_{ch}_bgext.xisf  (from GraXpert background extraction stage)
    Output: working/histogram_stats.json

    JSON structure:
        {
            "Ha":   {"median": 0.000312, "mean": 0.000331, ...},
            "OIII": {"median": 0.000187, "mean": 0.000194, ...},
            "SII":  {"median": 0.000251, "mean": 0.000268, ...}
        }

    Idempotency: if histogram_stats.json exists and contains all required channels,
    the stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        nb_filters = config["acquisition"]["nb"]["filters"]

        stats_file = working / HISTOGRAM_STATS_FILE

        # Load existing stats (if any) to check which channels still need measuring
        existing: dict = {}
        if stats_file.exists():
            try:
                existing = json.loads(stats_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = {}

        missing = [ch for ch in nb_filters if ch not in existing]
        if not missing:
            print(f"[stretching] Histogram stats exist for all channels -- skipping")
            return 0

        for ch in missing:
            inp = working / f"NGC1499_{ch}_bgext.xisf"
            if not inp.exists():
                raise PipelineError(
                    f"MeasureHistogramStage: bgext file not found for {ch}: {inp}\n"
                    "Run GraXpert background extraction first."
                )
            # Write per-channel JSON to a temp file, then merge into combined file
            ch_json = working / f"histogram_stats_{ch}.json"
            print(f"[stretching] {ch}: measuring histogram stats from {inp.name}")
            script = generate_histogram_stats(
                input_path=str(inp),
                output_json_path=str(ch_json),
                channel_id=ch,
            )
            _run_pjsr(script, f"Histogram stats {ch}", pi_exe, PI_TIMEOUT)

            if not ch_json.exists():
                raise PipelineError(
                    f"Histogram stats script completed but {ch_json.name} not found. "
                    "Check PI console output."
                )

            try:
                ch_data = json.loads(ch_json.read_text(encoding="utf-8"))
                existing[ch] = ch_data
                ch_json.unlink(missing_ok=True)  # clean up temp file
            except (json.JSONDecodeError, KeyError) as exc:
                raise PipelineError(
                    f"Failed to parse histogram stats for {ch}: {exc}"
                ) from exc

            print(
                f"[stretching] {ch}: median={existing[ch].get('median', '?'):.6f}  "
                f"mean={existing[ch].get('mean', '?'):.6f}"
            )

        stats_file.write_text(
            json.dumps(existing, indent=2), encoding="utf-8"
        )
        print(f"[stretching] Histogram stats written -> {stats_file.name}")
        return 0


# =============================================================================
# Stage 1: GHS Stretch per NB Channel
# =============================================================================


@dataclass
class StretchNBStage(PipelineStage):
    """
    GeneralizedHyperbolicStretch (GHS) stretch each starless NB channel.

    Transforms each linear starless NB channel from linear to a nonlinear
    (display-ready) stretch. GHS is preferred over AutoSTF / HistogramTransformation
    because it allows independent control of stretch intensity and shape, making
    it possible to stretch faint OIII without blowing out the bright Ha core.

    Input:  NGC1499_{ch}_starless.xisf
    Output: NGC1499_{ch}_starless_stretched.xisf

    GHS parameters from config["processing"] (with defaults):
        D  = ghs_stretch_factor  (default 5.0) -- stretch intensity
        b  = ghs_shape_param     (default 2.0) -- curve shape
        SP = 0.0001 (fixed)                    -- histogram peak for dark linear NB

    SP is fixed at 0.0001 because linear NB data after bgext has a very dark
    background median. Setting SP to the actual histogram peak ensures GHS
    anchors the stretch transition at the correct brightness level.

    Per-channel idempotency: output is skipped if it already exists.

    NOTE: BREAKPOINT 3 fires after this stage completes. The operator should
    review the stretched channels in PixInsight to confirm the stretch is
    appropriate before the pipeline proceeds to LinearFit and Foraxx.
    If the stretch is too aggressive or too gentle, adjust ghs_stretch_factor
    and ghs_shape_param in pipeline_config.json and re-run from StretchNBStage
    with --force.
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        nb_filters = config["acquisition"]["nb"]["filters"]
        proc       = config["processing"]

        # Base stretch factor (fallback for all channels if per-channel key absent)
        D_default = proc.get("ghs_stretch_factor", 5.0)
        b         = proc.get("ghs_shape_param", 2.0)

        # Load per-channel measured background medians for SP calibration.
        # SP should be set to the histogram peak (background median) of each
        # channel's linear bgext output. Falls back to hardcoded 0.0001 if stats
        # are not available (e.g. MeasureHistogramStage was skipped).
        stats_file = working / HISTOGRAM_STATS_FILE
        hist_stats: dict = {}
        if stats_file.exists():
            try:
                hist_stats = json.loads(stats_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                print("[stretching] WARNING: could not read histogram_stats.json -- using SP=0.0001")

        for ch in nb_filters:
            inp = working / f"NGC1499_{ch}_starless.xisf"
            out = working / f"NGC1499_{ch}_starless_stretched.xisf"

            if out.exists():
                print(f"[stretching] {ch}: stretched output exists -- skipping")
                continue

            # Per-channel D: check for ghs_stretch_factor_ha, _oiii, _sii (lowercase key)
            D = proc.get(f"ghs_stretch_factor_{ch.lower()}", D_default)

            # Per-channel SP: use measured median if available, else config override,
            # else 0.0001 hardcoded fallback
            if ch in hist_stats and "median" in hist_stats[ch]:
                SP = float(hist_stats[ch]["median"])
                sp_source = "measured"
            else:
                SP = proc.get("ghs_sp", 0.0001)
                sp_source = "config/default"

            # Clamp SP to sane range: must be >0 and < 0.01 for linear NB data
            SP = max(1e-7, min(SP, 0.01))

            print(
                f"[stretching] {ch}: GHS stretch {inp.name} -> {out.name} "
                f"(D={D}, b={b}, SP={SP:.6f} [{sp_source}])"
            )

            script = generate_ghs_stretch(
                input_path=str(inp),
                output_path=str(out),
                D=D,
                b=b,
                SP=SP,
            )
            _run_pjsr(script, f"GHS Stretch {ch}", pi_exe, PI_TIMEOUT)

        return 0


# =============================================================================
# Stage 2: LinearFit
# =============================================================================


@dataclass
class LinearFitStage(PipelineStage):
    """
    Normalize Ha and SII stretched channels to the OIII reference.

    NGC 1499 has extreme channel imbalance (Ha >> SII > OIII). Without
    normalization, Ha would overwhelm the Foraxx PixelMath combination and
    suppress the OIII signal. LinearFit scales Ha and SII down to the OIII
    brightness level so all three channels contribute proportionally.

    Reference: NGC1499_OIII_starless_stretched.xisf  (weakest channel)
    Targets:   Ha and SII only -- OIII is the reference and is NOT re-saved.

    Inputs:
        NGC1499_Ha_starless_stretched.xisf    (target)
        NGC1499_SII_starless_stretched.xisf   (target)
        NGC1499_OIII_starless_stretched.xisf  (reference, read-only)
    Outputs:
        NGC1499_Ha_starless_linearfit.xisf
        NGC1499_SII_starless_linearfit.xisf

    OIII remains at its stretched path (NGC1499_OIII_starless_stretched.xisf)
    and is passed directly to ForaxxPaletteStage as the reference channel.

    Idempotency: if BOTH output files exist, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)

        ha_stretched   = working / "NGC1499_Ha_starless_stretched.xisf"
        sii_stretched  = working / "NGC1499_SII_starless_stretched.xisf"
        oiii_stretched = working / "NGC1499_OIII_starless_stretched.xisf"

        ha_out  = working / "NGC1499_Ha_starless_linearfit.xisf"
        sii_out = working / "NGC1499_SII_starless_linearfit.xisf"

        if ha_out.exists() and sii_out.exists():
            print("[stretching] LinearFit outputs exist -- skipping")
            return 0

        print(
            f"[stretching] LinearFit: Ha + SII -> OIII reference\n"
            f"  reference: {oiii_stretched.name}\n"
            f"  targets:   {ha_stretched.name}, {sii_stretched.name}"
        )

        script = generate_linear_fit(
            target_paths=[str(ha_stretched), str(sii_stretched)],
            output_paths=[str(ha_out), str(sii_out)],
            reference_path=str(oiii_stretched),
        )
        _run_pjsr(script, "LinearFit Ha+SII to OIII", pi_exe, PI_TIMEOUT)

        return 0


# =============================================================================
# Stage 3: Foraxx Palette
# =============================================================================


@dataclass
class ForaxxPaletteStage(PipelineStage):
    """
    Foraxx dynamic SHO palette PixelMath combination.

    Combines the three normalized, starless NB channels using the Foraxx
    Power of Inverted Pixels (PIP) weighting formula. The result is a
    gold/cyan Hubble-like color palette that avoids the overwhelming green
    cast of standard SHO.

    Inputs:
        NGC1499_Ha_starless_linearfit.xisf    (Ha, linearfit-normalized)
        NGC1499_SII_starless_linearfit.xisf   (SII, linearfit-normalized)
        NGC1499_OIII_starless_stretched.xisf  (OIII, reference -- not linearfit)
    Output:
        NGC1499_SHO_foraxx.xisf

    OIII was the LinearFit reference and was not written to a _linearfit path.
    It is sourced directly from its stretched path.

    Foraxx PixelMath expressions (from design_doc.md):
        R = (Oiii ^ ~Oiii) * Sii + ~(Oiii ^ ~Oiii) * Ha
        G = ((Oiii*Ha) ^ ~(Oiii*Ha)) * Ha + ~((Oiii*Ha) ^ ~(Oiii*Ha)) * Oiii
        B = Oiii
    Where ~ = (1-x) and ^ = power operator.

    IMPORTANT: All inputs must be stretched (nonlinear) before Foraxx.
    The formula does not produce meaningful results on linear data.

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        nb_filters = config["acquisition"]["nb"]["filters"]

        output = working / "NGC1499_SHO_foraxx.xisf"

        if output.exists():
            print("[stretching] Foraxx palette output exists -- skipping")
            return 0

        # Validate that nb_filters contains the required channels
        filter_set = set(nb_filters)
        if "Ha" not in filter_set or "SII" not in filter_set or "OIII" not in filter_set:
            raise PipelineError(
                f"ForaxxPaletteStage: expected nb_filters to contain Ha, SII, OIII. "
                f"Got: {nb_filters}"
            )

        # Ha and SII come from linearfit outputs; OIII is the reference (stretched path)
        ha_path   = working / "NGC1499_Ha_starless_linearfit.xisf"
        sii_path  = working / "NGC1499_SII_starless_linearfit.xisf"
        oiii_path = working / "NGC1499_OIII_starless_stretched.xisf"

        print(
            f"[stretching] Foraxx palette:\n"
            f"  Ha:   {ha_path.name}\n"
            f"  SII:  {sii_path.name}\n"
            f"  OIII: {oiii_path.name}\n"
            f"  ->    {output.name}"
        )

        script = generate_foraxx_palette(
            ha_path=str(ha_path),
            sii_path=str(sii_path),
            oiii_path=str(oiii_path),
            output_path=str(output),
        )
        _run_pjsr(script, "Foraxx palette PixelMath", pi_exe, PI_TIMEOUT)

        return 0
