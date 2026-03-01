"""
stages/nonlinear.py -- Sprint 5: Phase 4 nonlinear processing implementations

Concrete PipelineStage subclasses for Phase 4 (nonlinear processing):
  SCNRStage                      -- SCNR green removal from SHO image
  CurvesHueStage                 -- CurvesTransformation hue shift [BP4]
  CurvesContrastSatStage         -- CurvesTransformation contrast and saturation
  HDRMultiscaleStage             -- HDRMultiscaleTransform dynamic range compression
  LHEStage                       -- LocalHistogramEqualization local contrast boost
  GraXpertDenoiseNonlinearStage  -- GraXpert AI denoise (nonlinear, light touch)

Processing order:
  SHO_foraxx -> SCNR -> [BP4 hue] -> curves -> HDR -> LHE -> GraXpert denoise -> SHO_final_starless

All inputs are nonlinear (stretched) images from Phase 3. The GraXpertDenoiseNonlinear
stage produces the final starless SHO image that feeds the Phase 5 screen blend.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator import PipelineStage, PipelineError
from pi_runner import run_pjsr_inline
from graxpert_runner import run_graxpert_denoise, GraXpertError
import json

from pjsr_generator import (
    generate_quality_report,
    generate_hue_analysis,
    generate_scnr,
    generate_curves_hue_shift,
    generate_curves_saturation_contrast,
    generate_hdr_multiscale,
    generate_local_histogram_equalization,
)


# =============================================================================
# Per-stage subprocess timeouts (seconds)
# =============================================================================

PI_TIMEOUT      = 3600   # 1 h -- generous for all PI nonlinear operations
DENOISE_TIMEOUT = 1200   # 20 min -- GraXpert nonlinear denoise (confirmed ~257s GPU)


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
    print(f"[nonlinear] Running: {step_name}")
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
# Stage 1: SCNR Green Removal
# =============================================================================


@dataclass
class SCNRStage(PipelineStage):
    """
    SCNR green removal on the Foraxx SHO palette image.

    In the SHO palette, Ha maps to the green channel. After Foraxx combination,
    residual green cast remains. SCNR removes this using MaximumMask protection
    to avoid desaturating non-green hues (cyan OIII especially).

    Apply AFTER stretching (nonlinear domain only). SCNR on linear data produces
    incorrect results because the green channel statistics are not calibrated
    relative to the stretched dynamic range.

    Input:  NGC1499_SHO_foraxx.xisf
    Output: NGC1499_SHO_scnr.xisf

    Parameters from config["processing"]:
        scnr_amount = 0.65 (default)

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)
        amount  = config["processing"].get("scnr_amount", 0.65)

        inp = working / "NGC1499_SHO_foraxx.xisf"
        out = working / "NGC1499_SHO_scnr.xisf"

        if out.exists():
            print("[nonlinear] SCNR output exists -- skipping")
            return 0

        print(f"[nonlinear] SCNR: {inp.name} -> {out.name} (amount={amount})")

        script = generate_scnr(
            input_path=str(inp),
            output_path=str(out),
            amount=amount,
        )
        _run_pjsr(script, "SCNR green removal", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 2: CurvesTransformation Hue Shift (BREAKPOINT 4)
# =============================================================================


@dataclass
class CurvesHueStage(PipelineStage):
    """
    CurvesTransformation hue adjustment -- BREAKPOINT 4.

    Shifts residual green hues toward gold/amber and boosts cyan (OIII)
    saturation. The exact curve depends on the image -- this is the primary
    aesthetic color-grading step of the pipeline.

    The default hue curve shifts green (0.33) toward yellow-orange (0.28),
    which is a common starting point for SHO data. The operator should review
    and adjust at BREAKPOINT 4.

    Input:  NGC1499_SHO_scnr.xisf
    Output: NGC1499_SHO_hue.xisf

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)

        inp = working / "NGC1499_SHO_scnr.xisf"
        out = working / "NGC1499_SHO_hue.xisf"

        if out.exists():
            print("[nonlinear] Curves hue output exists -- skipping")
            return 0

        print(f"[nonlinear] CurvesTransformation hue shift: {inp.name} -> {out.name}")

        script = generate_curves_hue_shift(
            input_path=str(inp),
            output_path=str(out),
        )
        _run_pjsr(script, "CurvesTransformation hue shift", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 3: CurvesTransformation Contrast and Saturation
# =============================================================================


@dataclass
class CurvesContrastSatStage(PipelineStage):
    """
    CurvesTransformation contrast and saturation adjustment.

    Applies a mild S-curve contrast boost and saturation increase to the
    hue-adjusted SHO image. Default curves are conservative starting points.
    Fine-tune at BREAKPOINT 4 alongside the hue adjustment.

    Input:  NGC1499_SHO_hue.xisf
    Output: NGC1499_SHO_curves.xisf

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)

        inp = working / "NGC1499_SHO_hue.xisf"
        out = working / "NGC1499_SHO_curves.xisf"

        if out.exists():
            print("[nonlinear] Curves contrast/sat output exists -- skipping")
            return 0

        print(f"[nonlinear] CurvesTransformation contrast/saturation: {inp.name} -> {out.name}")

        script = generate_curves_saturation_contrast(
            input_path=str(inp),
            output_path=str(out),
        )
        _run_pjsr(script, "CurvesTransformation contrast/saturation", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 4: HDRMultiscaleTransform
# =============================================================================


@dataclass
class HDRMultiscaleStage(PipelineStage):
    """
    HDRMultiscaleTransform dynamic range compression.

    Compresses the bright central Ha ridge in NGC 1499 while preserving faint
    outer OIII structure. Apply with a luminance mask protecting the background
    (the script itself does not apply a mask -- for full mask support use the
    PI GUI at BREAKPOINT 4).

    Input:  NGC1499_SHO_curves.xisf
    Output: NGC1499_SHO_hdr.xisf

    Parameters from config["processing"]:
        hdrmt_layers     = 6  (wavelet layers; 6 for large-scale DR compression)
        hdrmt_iterations = 1  (passes; 1 is usually sufficient)

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        n_layers   = config["processing"].get("hdrmt_layers", 6)
        n_iters    = config["processing"].get("hdrmt_iterations", 1)

        inp = working / "NGC1499_SHO_curves.xisf"
        out = working / "NGC1499_SHO_hdr.xisf"

        if out.exists():
            print("[nonlinear] HDR output exists -- skipping")
            return 0

        print(f"[nonlinear] HDRMultiscaleTransform: {inp.name} -> {out.name} (layers={n_layers})")

        script = generate_hdr_multiscale(
            input_path=str(inp),
            output_path=str(out),
            number_of_layers=n_layers,
            number_of_iterations=n_iters,
        )
        _run_pjsr(script, "HDRMultiscaleTransform", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 5: LocalHistogramEqualization
# =============================================================================


@dataclass
class LHEStage(PipelineStage):
    """
    LocalHistogramEqualization local contrast enhancement.

    Boosts local contrast in NGC 1499's filamentary nebula structure. Apply
    with a luminance mask protecting dark background regions (prevents noise
    amplification). The script runs without a mask; for masked application
    use the PI GUI before or after this stage at BREAKPOINT 4.

    Input:  NGC1499_SHO_hdr.xisf
    Output: NGC1499_SHO_lhe.xisf

    Parameters from config["processing"]:
        lhe_kernel_radius  = 96  (pixels; covers ~3 arcmin at 1.9 arcsec/px)
        lhe_contrast_limit = 2.0 (CLAHE limit; keep <=2.5 to avoid over-processing)
        lhe_amount         = 0.35 (blend: 35% LHE mixed with original)

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working  = Path(config["directories"]["working"])
        pi_exe   = _get_pi_exe(config)
        radius   = config["processing"].get("lhe_kernel_radius", 96)
        limit    = config["processing"].get("lhe_contrast_limit", 2.0)
        amount   = config["processing"].get("lhe_amount", 0.35)

        inp = working / "NGC1499_SHO_hdr.xisf"
        out = working / "NGC1499_SHO_lhe.xisf"

        if out.exists():
            print("[nonlinear] LHE output exists -- skipping")
            return 0

        print(
            f"[nonlinear] LocalHistogramEqualization: {inp.name} -> {out.name} "
            f"(radius={radius}, limit={limit}, amount={amount})"
        )

        script = generate_local_histogram_equalization(
            input_path=str(inp),
            output_path=str(out),
            kernel_radius=radius,
            contrast_limit=limit,
            amount=amount,
        )
        _run_pjsr(script, "LocalHistogramEqualization", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 6: GraXpert Denoise Nonlinear
# =============================================================================


@dataclass
class GraXpertDenoiseNonlinearStage(PipelineStage):
    """
    GraXpert AI denoising on the final nonlinear SHO image (NXT replacement).

    Light-touch noise reduction after the full nonlinear processing chain.
    Stretching amplifies background noise, and HDR/LHE can further reveal
    low-level noise patterns. This stage smooths residual noise while
    preserving the fine structure brought out by LHE.

    Strength is intentionally lower than the linear GraXpert denoise stage
    (0.35 nonlinear vs 0.40-0.60 linear) to avoid over-smoothing the detail
    that HDR and LHE worked to enhance.

    Input:  NGC1499_SHO_lhe.xisf
    Output: NGC1499_SHO_final_starless.xisf

    Parameters from config["processing"]:
        graxpert_denoise_strength_nonlinear = 0.35
        graxpert_denoise_batch_size         = 4

    Idempotency: if the output file exists, stage is skipped.
    GraXpertError is caught and re-raised as PipelineError.
    """

    def execute(self, config: dict) -> int:
        working      = Path(config["directories"]["working"])
        graxpert_exe = config["tools"].get("graxpert_exe")
        strength     = config["processing"].get("graxpert_denoise_strength_nonlinear", 0.35)
        batch_size   = config["processing"].get("graxpert_denoise_batch_size", 4)

        inp = working / "NGC1499_SHO_lhe.xisf"
        out = working / "NGC1499_SHO_final_starless.xisf"

        if out.exists():
            print("[nonlinear] GraXpert nonlinear denoise output exists -- skipping")
            return 0

        print(
            f"[nonlinear] GraXpert nonlinear denoise: {inp.name} -> {out.name} "
            f"(strength={strength})"
        )

        try:
            run_graxpert_denoise(
                input_path=inp,
                output_path=out,
                strength=strength,
                batch_size=batch_size,
                gpu=True,
                graxpert_exe=graxpert_exe,
                timeout=DENOISE_TIMEOUT,
            )
        except GraXpertError as exc:
            raise PipelineError(
                f"GraXpert nonlinear denoising failed: {exc}"
            ) from exc

        return 0


# =============================================================================
# Quality Check Stage (can run on any RGB image at any point)
# =============================================================================

# Acceptable ranges for automated quality gating
_QUALITY_THRESHOLDS = {
    "ratio_rg": (0.75, 1.35),   # R/G — Ha contribution check
    "ratio_bg": (0.70, 1.30),   # B/G — OIII dominance check
    "color_cast_score": (0.0, 0.30),  # channel std/mean
}


@dataclass
class QualityCheckStage(PipelineStage):
    """
    Measure RGB channel balance and color cast metrics on a pipeline image.

    Runs a PJSR Statistics script that extracts per-channel mean/median, computes
    channel balance ratios (R/G, B/G), and writes a JSON quality report. The stage
    prints QUALITY_WARN to the console and marks warnings in the JSON if any metric
    falls outside the acceptable threshold range.

    This stage does NOT block the pipeline — it is purely diagnostic. It runs
    after ForaxxPalette (to catch color cast early) and after the final starless
    output (to confirm the full processing chain result).

    The color cast problem ("super blue/purple") is detectable as:
        B/G > 1.30  →  OIII over-represented (blue cast)
        R/G < 0.75  →  Ha severely suppressed

    Input:  any RGB XISF image (stage-specific)
    Output: working/quality_{stage_label}.json

    idempotency: always re-runs (quality reports are cheap and always current).
    """

    stage_label: str = "unknown"

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)

        # Input is taken from input_spec[0]
        if not self.input_spec:
            raise PipelineError("QualityCheckStage: no input_spec provided")

        inp = Path(self.input_spec[0])
        if not inp.exists():
            print(f"[nonlinear] QualityCheck: {inp.name} not found -- skipping")
            return 0

        out_json = working / f"quality_{self.stage_label}.json"

        print(f"[nonlinear] Quality check ({self.stage_label}): {inp.name}")
        script = generate_quality_report(
            input_path=str(inp),
            output_json_path=str(out_json),
            stage_label=self.stage_label,
        )
        _run_pjsr(script, f"Quality check {self.stage_label}", pi_exe, PI_TIMEOUT)

        # Also run hue distribution analysis (catches blue/purple cast directly)
        hue_json = working / f"hue_{self.stage_label}.json"
        hue_script = generate_hue_analysis(
            input_path=str(inp),
            output_json_path=str(hue_json),
            stage_label=self.stage_label,
        )
        _run_pjsr(hue_script, f"Hue analysis {self.stage_label}", pi_exe, PI_TIMEOUT)

        # Read and summarize both reports in Python console output
        all_warnings = []

        if out_json.exists():
            try:
                report = json.loads(out_json.read_text(encoding="utf-8"))
                rg = report.get("ratio_rg", 0)
                bg = report.get("ratio_bg", 0)
                cast = report.get("color_cast_score", 0)
                warns = report.get("quality_warnings", [])
                all_warnings.extend(warns)
                print(
                    f"[nonlinear] Channel balance [{self.stage_label}]: "
                    f"R/G={rg:.3f}  B/G={bg:.3f}  cast={cast:.3f}"
                )
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[nonlinear] Could not read quality report: {exc}")

        if hue_json.exists():
            try:
                hue = json.loads(hue_json.read_text(encoding="utf-8"))
                gold = hue.get("hue_gold_amber_pct", 0)
                cyan = hue.get("hue_cyan_teal_pct", 0)
                blue = hue.get("hue_blue_purple_pct", 0)
                green = hue.get("hue_green_yellow_pct", 0)
                dom = hue.get("dominant_hue_zone", "?")
                hw = hue.get("quality_warnings", [])
                all_warnings.extend(hw)
                print(
                    f"[nonlinear] Hue distribution [{self.stage_label}]: "
                    f"gold={gold:.1f}%  cyan={cyan:.1f}%  blue={blue:.1f}%  "
                    f"green={green:.1f}%  dominant={dom}"
                )
            except (json.JSONDecodeError, OSError) as exc:
                print(f"[nonlinear] Could not read hue report: {exc}")

        status = "QUALITY_OK" if not all_warnings else "QUALITY_WARN"
        if all_warnings:
            print(f"[nonlinear] {status} [{self.stage_label}]:")
            for w in all_warnings:
                print(f"[nonlinear]   !! {w}")
        else:
            print(f"[nonlinear] {status} [{self.stage_label}]: all metrics within thresholds")

        return 0
