"""
stages/__init__.py — Pipeline stage registry

Central registry of all 28 pipeline stages in execution order, matching the
processing table in design_doc.md Section 4.

Sprint 1: All stages are registered as StubStage instances.
Sprint 2: pjsr_generator.py covers all script generation.
Sprint 3: Phase 1 stages use concrete implementations from stages.preprocessing.
Sprint 4+: All stages are concrete implementations.

Sub-module map:
    stages.preprocessing     — Phase 1 (calibration, registration, integration)
    stages.linear_processing — Phase 2 (crop, bgext, BXT, GraXpert denoise, SXT)
    stages.stretching        — Phase 3 (stretch, Foraxx palette)
    stages.nonlinear         — Phase 4 (SCNR, curves, HDR, LHE)
    stages.star_processing   — Phase 5 (RGB stars, SPCC, screen blend)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from orchestrator import PipelineStage
from stages.preprocessing import (
    SubframeInspectionStage,
    NBCalibrationStage,
    NBDrizzleStage,
    RGBCalibrationStage,
    RGBToNBRegistrationStage,
)
from stages.linear_processing import (
    DynamicCropStage,
    GraXpertBgExtStage,
    SHOLinearCombineStage,
    BXTCorrectOnlyStage,
    BXTSharpenStage,
    ChannelSplitStage,
    GraXpertDenoiseStage,
    SXTStage,
)
from stages.stretching import (
    MeasureHistogramStage,
    StretchNBStage,
    LinearFitStage,
    ForaxxPaletteStage,
)
from stages.nonlinear import (
    SCNRStage,
    CurvesHueStage,
    CurvesContrastSatStage,
    HDRMultiscaleStage,
    LHEStage,
    GraXpertDenoiseNonlinearStage,
    QualityCheckStage,
)
from stages.star_processing import (
    RGBChannelCombineStage,
    SPCCStage,
    RGBStretchStage,
    SXTRGBStage,
    StarHaloReductionStage,
    ScreenBlendStage,
    FinalCropStage,
)


# ─────────────────────────────────────────────────────────────────────────────
# Stub stage — used for all not-yet-implemented stages
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class StubStage(PipelineStage):
    """
    Placeholder for a pipeline stage not yet implemented.

    Calling execute() raises NotImplementedError with a message indicating
    which sprint will implement the stage. This allows the full stage list
    to be enumerated (--list-stages, --dry-run) before any PJSR scripts
    are written.
    """

    sprint: int = 2  # Sprint in which this stage will be implemented

    def execute(self, config: dict) -> int:
        raise NotImplementedError(
            f"Stage '{self.name}' is not yet implemented. "
            f"Scheduled for Sprint {self.sprint}."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Stage registry
# ─────────────────────────────────────────────────────────────────────────────


def get_all_stages(config: dict) -> list[PipelineStage]:
    """
    Return the ordered list of all pipeline stages.

    The config dict is available for stages that need to resolve file paths
    from config['directories'] or read processing parameters. Stub stages
    ignore it.
    """
    w = config.get("directories", {}).get("working", "")   # working dir
    o = config.get("directories", {}).get("output", "")    # output dir
    nb = config.get("acquisition", {}).get("nb", {}).get("filters", ["Ha", "OIII", "SII"])
    rgb = config.get("acquisition", {}).get("rgb", {}).get("filters", ["R", "G", "B"])

    def nb_master(ch: str) -> str:
        return str(Path(w) / f"NGC1499_{ch}_master.xisf")

    def rgb_master(ch: str) -> str:
        return str(Path(w) / f"NGC1499_{ch}_master.xisf")

    stages: list[PipelineStage] = [

        # ── Phase 1 — Preprocessing (Narrowband track) ────────────────────────

        SubframeInspectionStage(
            name="Subframe Inspection and Rejection",
            phase=1, track="nb",
            output_spec=[str(Path(w) / "subframe_weights_nb.csv")],
            breakpoint=False,
        ),
        NBCalibrationStage(
            name="NB Calibration Registration Integration",
            phase=1, track="nb",
            output_spec=[nb_master(ch) for ch in nb],
            breakpoint=False,
        ),
        NBDrizzleStage(
            name="NB DrizzleIntegration",
            phase=1, track="nb",
            input_spec=[nb_master(ch) for ch in nb],
            output_spec=[str(Path(w) / f"NGC1499_{ch}_drizzle.xisf") for ch in nb],
            breakpoint=False,
        ),

        # ── Phase 1b — Preprocessing (RGB star track) ─────────────────────────

        RGBCalibrationStage(
            name="RGB Calibration Registration Integration",
            phase=1, track="rgb",
            output_spec=[rgb_master(ch) for ch in rgb],
            breakpoint=False,
        ),
        RGBToNBRegistrationStage(
            name="RGB to NB Frame Registration",
            phase=1, track="rgb",
            input_spec=[rgb_master(ch) for ch in rgb],
            output_spec=[
                str(Path(w) / f"NGC1499_{ch}_master_registered.xisf") for ch in rgb
            ],
            breakpoint=False,
        ),

        # ── Phase 2 — Linear Processing ───────────────────────────────────────

        DynamicCropStage(
            name="DynamicCrop All Channels",
            phase=2, track="nb",
            input_spec=[
                str(Path(w) / f"NGC1499_{ch}_drizzle.xisf") for ch in nb
            ] + [
                str(Path(w) / f"NGC1499_{ch}_master_registered.xisf") for ch in rgb
            ],
            output_spec=[
                str(Path(w) / f"NGC1499_{ch}_cropped.xisf") for ch in nb
            ] + [
                str(Path(w) / f"NGC1499_{ch}_cropped.xisf") for ch in rgb
            ],
            breakpoint=True,
        ),
        GraXpertBgExtStage(
            name="GraXpert Background Extraction",
            phase=2, track="nb",
            input_spec=[str(Path(w) / f"NGC1499_{ch}_cropped.xisf") for ch in nb],
            output_spec=[str(Path(w) / f"NGC1499_{ch}_bgext.xisf") for ch in nb],
            breakpoint=False,
        ),
        SHOLinearCombineStage(
            name="SHO Channel Combination for BXT",
            phase=2, track="nb",
            input_spec=[str(Path(w) / f"NGC1499_{ch}_bgext.xisf") for ch in nb],
            output_spec=[str(Path(w) / "NGC1499_SHO_linear.xisf")],
            breakpoint=False,
        ),
        BXTCorrectOnlyStage(
            name="BlurXTerminator Correct Only",
            phase=2, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_linear.xisf")],
            output_spec=[str(Path(w) / "NGC1499_SHO_bxt_corrected.xisf")],
            breakpoint=False,
        ),
        BXTSharpenStage(
            name="BlurXTerminator Sharpen",
            phase=2, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_bxt_corrected.xisf")],
            output_spec=[str(Path(w) / "NGC1499_SHO_bxt.xisf")],
            breakpoint=True,
        ),
        ChannelSplitStage(
            name="Channel Split SHO to S H O",
            phase=2, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_bxt.xisf")],
            output_spec=[str(Path(w) / f"NGC1499_{ch}_processed.xisf") for ch in nb],
            breakpoint=False,
        ),
        GraXpertDenoiseStage(
            name="GraXpert Denoise Per NB Channel",
            phase=2, track="nb",
            input_spec=[str(Path(w) / f"NGC1499_{ch}_processed.xisf") for ch in nb],
            output_spec=[str(Path(w) / f"NGC1499_{ch}_denoised.xisf") for ch in nb],
            breakpoint=False,
        ),
        SXTStage(
            name="StarXTerminator Per NB Channel",
            phase=2, track="nb",
            input_spec=[str(Path(w) / f"NGC1499_{ch}_denoised.xisf") for ch in nb],
            output_spec=[str(Path(w) / f"NGC1499_{ch}_starless.xisf") for ch in nb],
            breakpoint=False,
        ),

        # ── Phase 3 — Stretching and Palette Combination ──────────────────────

        MeasureHistogramStage(
            name="Measure Histogram Stats for GHS SP",
            phase=3, track="nb",
            input_spec=[str(Path(w) / f"NGC1499_{ch}_bgext.xisf") for ch in nb],
            output_spec=[str(Path(w) / "histogram_stats.json")],
            breakpoint=False,
        ),
        StretchNBStage(
            name="Stretch Starless NB Channels",
            phase=3, track="nb",
            input_spec=[str(Path(w) / f"NGC1499_{ch}_starless.xisf") for ch in nb],
            output_spec=[
                str(Path(w) / f"NGC1499_{ch}_starless_stretched.xisf") for ch in nb
            ],
            breakpoint=True,
        ),
        LinearFitStage(
            name="LinearFit Ha+SII to OIII Reference",
            phase=3, track="nb",
            input_spec=[
                str(Path(w) / "NGC1499_Ha_starless_stretched.xisf"),
                str(Path(w) / "NGC1499_SII_starless_stretched.xisf"),
                str(Path(w) / "NGC1499_OIII_starless_stretched.xisf"),
            ],
            output_spec=[
                str(Path(w) / "NGC1499_Ha_starless_linearfit.xisf"),
                str(Path(w) / "NGC1499_SII_starless_linearfit.xisf"),
            ],
            breakpoint=False,
        ),
        ForaxxPaletteStage(
            name="Foraxx Dynamic Palette Combination",
            phase=3, track="nb",
            input_spec=[
                str(Path(w) / "NGC1499_Ha_starless_linearfit.xisf"),
                str(Path(w) / "NGC1499_SII_starless_linearfit.xisf"),
                str(Path(w) / "NGC1499_OIII_starless_stretched.xisf"),
            ],
            output_spec=[str(Path(w) / "NGC1499_SHO_foraxx.xisf")],
            breakpoint=False,
        ),
        QualityCheckStage(
            name="Quality Check After Foraxx",
            phase=3, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_foraxx.xisf")],
            output_spec=[str(Path(w) / "quality_after_foraxx.json")],
            breakpoint=False,
            stage_label="after_foraxx",
        ),

        # ── Phase 4 — Nonlinear Processing ────────────────────────────────────

        SCNRStage(
            name="SCNR Green Removal",
            phase=4, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_foraxx.xisf")],
            output_spec=[str(Path(w) / "NGC1499_SHO_scnr.xisf")],
            breakpoint=False,
        ),
        CurvesHueStage(
            name="CurvesTransformation Hue Shift",
            phase=4, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_scnr.xisf")],
            output_spec=[str(Path(w) / "NGC1499_SHO_hue.xisf")],
            breakpoint=True,
        ),
        CurvesContrastSatStage(
            name="CurvesTransformation Contrast Saturation",
            phase=4, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_hue.xisf")],
            output_spec=[str(Path(w) / "NGC1499_SHO_curves.xisf")],
            breakpoint=False,
        ),
        HDRMultiscaleStage(
            name="HDRMultiscaleTransform",
            phase=4, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_curves.xisf")],
            output_spec=[str(Path(w) / "NGC1499_SHO_hdr.xisf")],
            breakpoint=False,
        ),
        LHEStage(
            name="LocalHistogramEqualization",
            phase=4, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_hdr.xisf")],
            output_spec=[str(Path(w) / "NGC1499_SHO_lhe.xisf")],
            breakpoint=False,
        ),
        GraXpertDenoiseNonlinearStage(
            name="GraXpert Denoise Nonlinear",
            phase=4, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_lhe.xisf")],
            output_spec=[str(Path(w) / "NGC1499_SHO_final_starless.xisf")],
            breakpoint=False,
        ),
        QualityCheckStage(
            name="Quality Check After Final Starless",
            phase=4, track="nb",
            input_spec=[str(Path(w) / "NGC1499_SHO_final_starless.xisf")],
            output_spec=[str(Path(w) / "quality_after_final_starless.json")],
            breakpoint=False,
            stage_label="after_final_starless",
        ),

        # ── Phase 5 — RGB Star Processing and Final Combination ───────────────

        RGBChannelCombineStage(
            name="RGB ChannelCombination",
            phase=5, track="rgb",
            input_spec=[
                str(Path(w) / f"NGC1499_{ch}_cropped.xisf") for ch in rgb
            ],
            output_spec=[str(Path(w) / "NGC1499_RGB_composite.xisf")],
            breakpoint=False,
        ),
        SPCCStage(
            name="SpectrophotometricColorCalibration",
            phase=5, track="rgb",
            input_spec=[str(Path(w) / "NGC1499_RGB_composite.xisf")],
            output_spec=[str(Path(w) / "NGC1499_RGB_spcc.xisf")],
            breakpoint=False,
        ),
        RGBStretchStage(
            name="RGB Star Stretch",
            phase=5, track="rgb",
            input_spec=[str(Path(w) / "NGC1499_RGB_spcc.xisf")],
            output_spec=[str(Path(w) / "NGC1499_RGB_stretched.xisf")],
            breakpoint=False,
        ),
        SXTRGBStage(
            name="StarXTerminator RGB Composite",
            phase=5, track="rgb",
            input_spec=[str(Path(w) / "NGC1499_RGB_stretched.xisf")],
            output_spec=[
                str(Path(w) / "NGC1499_RGB_starless.xisf"),
                str(Path(w) / "NGC1499_RGB_stars_only.xisf"),
            ],
            breakpoint=False,
        ),
        StarHaloReductionStage(
            name="Star Halo Reduction",
            phase=5, track="rgb",
            input_spec=[str(Path(w) / "NGC1499_RGB_stars_only.xisf")],
            output_spec=[str(Path(w) / "NGC1499_RGB_stars_haloreduced.xisf")],
            breakpoint=False,
        ),
        ScreenBlendStage(
            name="Screen Blend Star Recombination",
            phase=5, track="merge",
            input_spec=[
                str(Path(w) / "NGC1499_SHO_final_starless.xisf"),
                str(Path(w) / "NGC1499_RGB_stars_haloreduced.xisf"),
            ],
            output_spec=[str(Path(w) / "NGC1499_combined.xisf")],
            breakpoint=True,
        ),
        FinalCropStage(
            name="Final Crop and Cleanup",
            phase=5, track="final",
            input_spec=[str(Path(w) / "NGC1499_combined.xisf")],
            output_spec=[str(Path(o) / "NGC1499_final.xisf")],
            breakpoint=False,
        ),
    ]

    return stages
