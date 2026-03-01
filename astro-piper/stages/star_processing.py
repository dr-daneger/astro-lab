"""
stages/star_processing.py -- Sprint 5: Phase 5 RGB star processing and final combination

Concrete PipelineStage subclasses for Phase 5:
  RGBChannelCombineStage    -- ChannelCombination: R+G+B cropped masters -> RGB composite
  SPCCStage                 -- SpectrophotometricColorCalibration on RGB image
  RGBStretchStage           -- GHS stretch on RGB composite (lighter than NB channels)
  SXTRGBStage               -- StarXTerminator: extract RGB stars-only layer
  StarHaloReductionStage    -- Pass-through (copy stars_only -> haloreduced for operator)
  ScreenBlendStage          -- Screen blend SHO starless + RGB stars [BP5]
  FinalCropStage            -- Final crop of combined image to output directory

Data flow:
  R/G/B_cropped -> RGB_composite -> RGB_spcc -> RGB_stretched
                                                     -> SXT -> RGB_stars_only
                                                                  -> (halo reduce)
                                                                  -> RGB_stars_haloreduced
  SHO_final_starless + RGB_stars_haloreduced -> screen blend -> NGC1499_combined
                                                                    -> Final crop
                                                                    -> NGC1499_final.xisf
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator import PipelineStage, PipelineError
from pi_runner import run_pjsr_inline
from pjsr_generator import (
    generate_channel_combination,
    generate_spcc,
    generate_ghs_stretch,
    generate_star_xterminator,
    generate_screen_blend,
    generate_crop,
)


# =============================================================================
# Per-stage subprocess timeouts (seconds)
# =============================================================================

PI_TIMEOUT   = 3600   # 1 h -- generous for all PI operations in Phase 5
CROP_TIMEOUT = 600    # 10 min -- final crop is a simple operation


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
    print(f"[star_processing] Running: {step_name}")
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
# Stage 1: RGB Channel Combination
# =============================================================================


@dataclass
class RGBChannelCombineStage(PipelineStage):
    """
    Combine the three registered+cropped RGB channel masters into a single
    RGB composite image for SPCC and star processing.

    Inputs:
        NGC1499_R_cropped.xisf
        NGC1499_G_cropped.xisf
        NGC1499_B_cropped.xisf
    Output:
        NGC1499_RGB_composite.xisf

    The _cropped.xisf files are produced by DynamicCropStage (Phase 2), which
    crops all NB drizzle AND RGB registered masters to identical margins.
    This ensures the RGB composite is pixel-aligned with the NB channels.

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)
        output  = working / "NGC1499_RGB_composite.xisf"

        if output.exists():
            print("[star_processing] RGB composite exists -- skipping")
            return 0

        r_path = working / "NGC1499_R_cropped.xisf"
        g_path = working / "NGC1499_G_cropped.xisf"
        b_path = working / "NGC1499_B_cropped.xisf"

        print(
            f"[star_processing] ChannelCombination R+G+B -> {output.name}"
        )

        script = generate_channel_combination(
            r_path=str(r_path),
            g_path=str(g_path),
            b_path=str(b_path),
            output_path=str(output),
        )
        _run_pjsr(script, "RGB ChannelCombination", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 2: SpectrophotometricColorCalibration
# =============================================================================


@dataclass
class SPCCStage(PipelineStage):
    """
    SpectrophotometricColorCalibration (SPCC) on the RGB composite.

    Calibrates star colors against the Gaia DR3 BP/RP spectrophotometric
    catalog for photometrically accurate stellar chromaticity.

    Prerequisites:
      - The RGB composite must have a valid WCS solution. Either:
        a) Inherit WCS from StarAlignment registration to the NB reference
        b) Run ImageSolver on the composite before this stage

    SPCC requires the Gaia DR3 catalog to be available in PI's online catalog
    system. If the catalog server is unreachable or the WCS is invalid, SPCC
    will fail and the orchestrator will surface the PixInsight log output.

    Input:  NGC1499_RGB_composite.xisf
    Output: NGC1499_RGB_spcc.xisf

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)

        inp = working / "NGC1499_RGB_composite.xisf"
        out = working / "NGC1499_RGB_spcc.xisf"

        if out.exists():
            print("[star_processing] SPCC output exists -- skipping")
            return 0

        print(f"[star_processing] SPCC: {inp.name} -> {out.name}")

        script = generate_spcc(
            input_path=str(inp),
            output_path=str(out),
        )
        _run_pjsr(script, "SpectrophotometricColorCalibration", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 3: RGB Star Stretch
# =============================================================================


@dataclass
class RGBStretchStage(PipelineStage):
    """
    GeneralizedHyperbolicStretch (GHS) stretch of the linear RGB composite.

    Uses a lighter stretch than the NB channels (D=3.0 vs D=5.0) because
    RGB stars are already bright relative to the NB nebulosity. An overly
    aggressive stretch blows out star cores and introduces color clipping.

    Input:  NGC1499_RGB_spcc.xisf
    Output: NGC1499_RGB_stretched.xisf

    GHS parameters:
        D  = config["processing"].get("ghs_rgb_stretch_factor", 3.0)
        b  = config["processing"].get("ghs_shape_param", 2.0)
        SP = 0.0001 (fixed -- same as NB channels)

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)

        D  = config["processing"].get("ghs_rgb_stretch_factor", 3.0)
        b  = config["processing"].get("ghs_shape_param", 2.0)
        SP = 0.0001  # fixed -- histogram peak for dark linear data

        inp = working / "NGC1499_RGB_spcc.xisf"
        out = working / "NGC1499_RGB_stretched.xisf"

        if out.exists():
            print("[star_processing] RGB stretched output exists -- skipping")
            return 0

        print(
            f"[star_processing] GHS stretch RGB: {inp.name} -> {out.name} "
            f"(D={D}, b={b}, SP={SP})"
        )

        script = generate_ghs_stretch(
            input_path=str(inp),
            output_path=str(out),
            D=D,
            b=b,
            SP=SP,
        )
        _run_pjsr(script, "GHS stretch RGB", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 4: StarXTerminator RGB Composite
# =============================================================================


@dataclass
class SXTRGBStage(PipelineStage):
    """
    StarXTerminator star extraction on the stretched RGB composite.

    Extracts the stars-only layer from the RGB composite for screen blend
    recombination in Phase 5. Both outputs are kept:
        - Stars-only:  NGC1499_RGB_stars_only.xisf  (fed to StarHaloReduction)
        - Starless:    NGC1499_RGB_starless.xisf     (saved for reference)

    unscreen=False: the stretched RGB was not produced by screen blend, so
    clean subtraction mode is used (not inverse-screen).

    Note on stars window ID: SXT creates the stars-only image with an ID
    derived from the input image's view ID. The generate_star_xterminator()
    script looks up the window by "{stem}_stars". If PI assigns a different
    ID, the stars window save will log a warning but not raise an error
    (SXT creates the stars layer -- the window lookup is the only potential
    failure point). Verify the stars output exists after this stage.

    Input:  NGC1499_RGB_stretched.xisf
    Outputs:
        NGC1499_RGB_starless.xisf     (reference only -- not used downstream)
        NGC1499_RGB_stars_only.xisf   (fed to StarHaloReductionStage)

    Idempotency: if BOTH outputs exist, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)

        inp         = working / "NGC1499_RGB_stretched.xisf"
        starless    = working / "NGC1499_RGB_starless.xisf"
        stars_only  = working / "NGC1499_RGB_stars_only.xisf"

        if starless.exists() and stars_only.exists():
            print("[star_processing] SXT RGB outputs exist -- skipping")
            return 0

        print(
            f"[star_processing] StarXTerminator RGB: {inp.name} -> "
            f"{starless.name} + {stars_only.name}"
        )

        script = generate_star_xterminator(
            input_path=str(inp),
            starless_output_path=str(starless),
            stars_output_path=str(stars_only),
            unscreen=False,
        )
        _run_pjsr(script, "StarXTerminator RGB", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 5: Star Halo Reduction (pass-through)
# =============================================================================


@dataclass
class StarHaloReductionStage(PipelineStage):
    """
    Star halo reduction pass-through stage.

    SETI Astro Halo Reducer is not CLI-automatable. This stage performs a
    file copy from the stars-only image to the haloreduced destination,
    allowing the pipeline to continue uninterrupted.

    To apply manual halo reduction: before running this stage, manually
    process NGC1499_RGB_stars_only.xisf in PixInsight and save the result
    to NGC1499_RGB_stars_haloreduced.xisf. If that file already exists,
    this stage is skipped (idempotency honors manual work).

    Design doc note (Section 9): "Usually unnecessary with the 75Q's
    well-corrected optics and short RGB exposures."

    Input:  NGC1499_RGB_stars_only.xisf
    Output: NGC1499_RGB_stars_haloreduced.xisf

    Idempotency: if the output file exists (manually or auto), stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        stars_only = working / "NGC1499_RGB_stars_only.xisf"
        haloreduced = working / "NGC1499_RGB_stars_haloreduced.xisf"

        if haloreduced.exists():
            print("[star_processing] Halo-reduced stars file exists -- skipping")
            return 0

        if not stars_only.exists():
            raise PipelineError(
                f"StarHaloReductionStage: stars-only file not found: {stars_only}\n"
                "Run SXTRGBStage first."
            )

        print(
            f"[star_processing] Halo reduction: copying {stars_only.name} "
            f"-> {haloreduced.name} (no automated halo reduction applied)"
        )
        shutil.copy2(str(stars_only), str(haloreduced))
        return 0


# =============================================================================
# Stage 6: Screen Blend Star Recombination (BREAKPOINT 5)
# =============================================================================


@dataclass
class ScreenBlendStage(PipelineStage):
    """
    Screen blend recombination: starless SHO + RGB stars -> final combined image.

    Merges the fully processed starless SHO nebula with the RGB stars-only
    layer using the screen blend formula: ~(~starless * ~stars).

    Screen blend prevents pixel clipping where stars overlap bright nebulosity.
    Additive blending would clip to white at star positions atop bright Ha
    regions; screen blend compresses this naturally.

    The star_brightness_factor (default 0.70) scales the star layer before
    blending. Reduce toward 0.5 if stars dominate; increase toward 1.0 if
    stars look too faint in the final image. This is BREAKPOINT 5.

    Inputs:
        NGC1499_SHO_final_starless.xisf
        NGC1499_RGB_stars_haloreduced.xisf
    Output:
        NGC1499_combined.xisf

    Parameters from config["processing"]:
        star_brightness_factor = 0.70 (default)

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working         = Path(config["directories"]["working"])
        pi_exe          = _get_pi_exe(config)
        star_brightness = config["processing"].get("star_brightness_factor", 0.70)

        starless   = working / "NGC1499_SHO_final_starless.xisf"
        stars      = working / "NGC1499_RGB_stars_haloreduced.xisf"
        output     = working / "NGC1499_combined.xisf"

        if output.exists():
            print("[star_processing] Screen blend output exists -- skipping")
            return 0

        print(
            f"[star_processing] Screen blend: {starless.name} + {stars.name} "
            f"-> {output.name} (star_brightness={star_brightness})"
        )

        script = generate_screen_blend(
            starless_path=str(starless),
            stars_path=str(stars),
            output_path=str(output),
            star_brightness=star_brightness,
        )
        _run_pjsr(script, "Screen blend recombination", pi_exe, PI_TIMEOUT)
        return 0


# =============================================================================
# Stage 7: Final Crop and Output
# =============================================================================


@dataclass
class FinalCropStage(PipelineStage):
    """
    Final crop of the combined image to the pipeline output directory.

    Applies a final crop to remove any remaining edge artifacts or to refine
    the composition after screen blend. The output is written to the configured
    output directory (not working/) as the pipeline's final deliverable.

    If final_crop_pixels is 0 (default), no pixels are removed -- the image
    is re-saved to the output directory without cropping. This is useful when
    the first crop (DynamicCropStage) already achieved the desired composition.

    Input:  working/NGC1499_combined.xisf
    Output: output/NGC1499_final.xisf

    Parameters from config["preprocessing"]:
        final_crop_pixels = 0 (default; increase to trim composition further)

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        output_dir = Path(config["directories"]["output"])
        pi_exe     = _get_pi_exe(config)
        crop_px    = config["preprocessing"].get("final_crop_pixels", 0)

        inp = working    / "NGC1499_combined.xisf"
        out = output_dir / "NGC1499_final.xisf"

        if out.exists():
            print("[star_processing] Final output exists -- skipping")
            return 0

        output_dir.mkdir(parents=True, exist_ok=True)

        print(
            f"[star_processing] Final crop: {inp.name} -> {out} "
            f"(crop_pixels={crop_px})"
        )

        script = generate_crop(
            input_path=str(inp),
            output_path=str(out),
            crop_pixels=crop_px,
        )
        _run_pjsr(script, "Final crop", pi_exe, CROP_TIMEOUT)
        return 0
