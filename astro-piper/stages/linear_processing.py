"""
stages/linear_processing.py -- Sprint 4: Phase 2 linear processing implementations

Concrete PipelineStage subclasses for Phase 2 (linear processing):
  DynamicCropStage        -- Crop all channels to remove stacking edges [BP1]
  GraXpertBgExtStage      -- GraXpert AI background extraction per NB channel
  SHOLinearCombineStage   -- Equal-weight SHO PixelMath for BXT input
  BXTCorrectOnlyStage     -- BlurXTerminator pass 1: aberration correction
  BXTSharpenStage         -- BlurXTerminator pass 2: sharpen + deconvolve [BP2]
  ChannelSplitStage       -- ChannelExtraction: SHO -> S, H, O channels
  GraXpertDenoiseStage    -- GraXpert AI denoising per NB channel (NXT replacement)
  SXTStage                -- StarXTerminator star removal per NB channel

SHO channel encoding through Phase 2:
  Combined SHO image: R=SII, G=Ha, B=OIII  (BXT is trained on color images)
  After ChannelExtraction: R->SII_processed, G->Ha_processed, B->OIII_processed
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator import PipelineStage, PipelineError
from pi_runner import run_pjsr_inline
from graxpert_runner import run_graxpert, run_graxpert_denoise, GraXpertError
from pjsr_generator import (
    generate_crop,
    generate_sho_linear_combine,
    generate_blur_xterminator,
    generate_channel_extraction,
    generate_star_xterminator,
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
    print(f"[linear] Running: {step_name}")
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
# Stage 1: Dynamic Crop
# =============================================================================


@dataclass
class DynamicCropStage(PipelineStage):
    """
    Crop all NB drizzle and RGB registered channels to identical margins.

    Removes stacking-edge artifacts introduced by DrizzleIntegration (NB) and
    StarAlignment (RGB). All six channels are cropped to the same pixel margin
    so subsequent processing operates on pixel-aligned data.

    NB inputs:  NGC1499_{ch}_drizzle.xisf
    RGB inputs: NGC1499_{ch}_master_registered.xisf
    Outputs:    NGC1499_{ch}_cropped.xisf  (for all NB + RGB channels)

    crop_pixels: config["preprocessing"].get("crop_pixels", 200)
      Applied equally to all four edges.

    Per-channel idempotency: if the output _cropped.xisf already exists,
    that channel is skipped.

    [BREAKPOINT 1] fires after this stage in the orchestrator when enabled.
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        nb_filters = config["acquisition"]["nb"]["filters"]
        rgb_filters = config["acquisition"]["rgb"]["filters"]
        crop_px    = config["preprocessing"].get("crop_pixels", 200)

        # Build list of (input_path, output_path) tuples for all channels
        channels: list[tuple[Path, Path]] = []

        for ch in nb_filters:
            inp = working / f"NGC1499_{ch}_drizzle.xisf"
            out = working / f"NGC1499_{ch}_cropped.xisf"
            channels.append((inp, out))

        for ch in rgb_filters:
            inp = working / f"NGC1499_{ch}_master_registered.xisf"
            out = working / f"NGC1499_{ch}_cropped.xisf"
            channels.append((inp, out))

        # Determine how many channels actually need cropping
        pending = [(inp, out) for inp, out in channels if not out.exists()]
        total   = len(channels)
        n_skip  = total - len(pending)

        if n_skip:
            print(f"[linear] Crop: skipping {n_skip}/{total} channels (outputs exist)")

        if not pending:
            print("[linear] All channels already cropped -- nothing to do")
            return 0

        print(f"[linear] Cropping {len(pending)} channels: {crop_px}px margins")

        for inp, out in pending:
            if not inp.exists():
                raise PipelineError(
                    f"DynamicCropStage: input file not found: {inp}\n"
                    "Ensure the previous preprocessing stages have completed."
                )

            script = generate_crop(
                input_path=str(inp),
                output_path=str(out),
                crop_pixels=crop_px,
            )
            _run_pjsr(script, f"Crop {inp.name}", pi_exe, CROP_TIMEOUT)

        return 0


# =============================================================================
# Stage 2: GraXpert Background Extraction
# =============================================================================


@dataclass
class GraXpertBgExtStage(PipelineStage):
    """
    GraXpert AI background extraction on each NB channel.

    Input:  NGC1499_{ch}_cropped.xisf
    Output: NGC1499_{ch}_bgext.xisf

    Per-channel idempotency: output is skipped if it already exists on disk.

    GraXpert parameters:
        smoothing   = config["processing"].get("graxpert_smoothing", 0.1)
        correction  = config["processing"].get("graxpert_correction", "Subtraction")
        gpu         = True  (always GPU-accelerated; confirmed 16s on 875MB drizzle)
        graxpert_exe = config["tools"].get("graxpert_exe")

    GraXpertError is caught and re-raised as PipelineError so the orchestrator
    can handle it uniformly.
    """

    def execute(self, config: dict) -> int:
        working      = Path(config["directories"]["working"])
        nb_filters   = config["acquisition"]["nb"]["filters"]
        smoothing    = config["processing"].get("graxpert_smoothing", 0.1)
        correction   = config["processing"].get("graxpert_correction", "Subtraction")
        graxpert_exe = config["tools"].get("graxpert_exe")

        for ch in nb_filters:
            inp = working / f"NGC1499_{ch}_cropped.xisf"
            out = working / f"NGC1499_{ch}_bgext.xisf"

            if out.exists():
                print(f"[linear] {ch}: bgext output exists -- skipping")
                continue

            print(f"[linear] {ch}: GraXpert bgext {inp.name} -> {out.name}")

            try:
                run_graxpert(
                    input_path=inp,
                    output_path=out,
                    smoothing=smoothing,
                    correction=correction,
                    gpu=True,
                    graxpert_exe=graxpert_exe,
                    timeout=BGEXT_TIMEOUT,
                )
            except GraXpertError as exc:
                raise PipelineError(
                    f"GraXpert background extraction failed for channel {ch}: {exc}"
                ) from exc

        return 0


# =============================================================================
# Stage 3: SHO Linear Combine
# =============================================================================


@dataclass
class SHOLinearCombineStage(PipelineStage):
    """
    Combine the three NB bgext channels into an equal-weight SHO image for BXT.

    BXT's AI deconvolution model was trained on RGB color images. Running it on
    the combined SHO image produces better aberration correction and star
    sharpening than processing each channel individually.

    Channel mapping:  R = SII,  G = Ha,  B = OIII  (Hubble palette order)

    Inputs:
        NGC1499_Ha_bgext.xisf
        NGC1499_SII_bgext.xisf
        NGC1499_OIII_bgext.xisf
    Output:
        NGC1499_SHO_linear.xisf

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        nb_filters = config["acquisition"]["nb"]["filters"]

        output = working / "NGC1499_SHO_linear.xisf"

        if output.exists():
            print("[linear] SHO linear combine output exists -- skipping")
            return 0

        # Locate the bgext path for each filter name
        filter_set = set(nb_filters)
        if "Ha" not in filter_set or "SII" not in filter_set or "OIII" not in filter_set:
            raise PipelineError(
                f"SHOLinearCombineStage: expected nb_filters to contain Ha, SII, OIII. "
                f"Got: {nb_filters}"
            )

        ha_path   = working / "NGC1499_Ha_bgext.xisf"
        sii_path  = working / "NGC1499_SII_bgext.xisf"
        oiii_path = working / "NGC1499_OIII_bgext.xisf"

        script = generate_sho_linear_combine(
            ha_path=str(ha_path),
            sii_path=str(sii_path),
            oiii_path=str(oiii_path),
            output_path=str(output),
        )
        _run_pjsr(script, "SHO Linear Combine", pi_exe, PI_TIMEOUT)

        return 0


# =============================================================================
# Stage 4: BXT Correct Only (Pass 1)
# =============================================================================


@dataclass
class BXTCorrectOnlyStage(PipelineStage):
    """
    BlurXTerminator pass 1: aberration correction only, no sharpening.

    Corrects optical PSF aberrations (coma, astigmatism, field curvature)
    without introducing sharpening artifacts. The corrected image feeds
    BXTSharpenStage for the full deconvolution pass.

    Input:  NGC1499_SHO_linear.xisf
    Output: NGC1499_SHO_bxt_corrected.xisf

    correct_only=True disables star sharpening and nonstellar enhancement.
    The sharpen_stars, sharpen_nonstellar, adjust_halos parameters are
    passed as zeros (ignored by BXT when correct_only=True).

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)

        inp = working / "NGC1499_SHO_linear.xisf"
        out = working / "NGC1499_SHO_bxt_corrected.xisf"

        if out.exists():
            print("[linear] BXT correct-only output exists -- skipping")
            return 0

        script = generate_blur_xterminator(
            input_path=str(inp),
            output_path=str(out),
            correct_only=True,
            automatic_psf=True,
            sharpen_stars=0.0,
            sharpen_nonstellar=0.0,
            adjust_halos=0.0,
        )
        _run_pjsr(script, "BlurXTerminator pass 1 (correct only)", pi_exe, PI_TIMEOUT)

        return 0


# =============================================================================
# Stage 5: BXT Sharpen (Pass 2)
# =============================================================================


@dataclass
class BXTSharpenStage(PipelineStage):
    """
    BlurXTerminator pass 2: full deconvolution and sharpening.

    Applies star sharpening, nonstellar detail enhancement, and halo reduction
    to the aberration-corrected SHO image. This is BREAKPOINT 2 -- the operator
    reviews the deconvolved result before proceeding to channel split.

    Input:  NGC1499_SHO_bxt_corrected.xisf
    Output: NGC1499_SHO_bxt.xisf

    Parameters from config["processing"] (with defaults):
        bxt_sharpen_stars      = 0.25
        bxt_sharpen_nonstellar = 0.40
        bxt_adjust_halos       = 0.05

    Idempotency: if the output file exists, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working            = Path(config["directories"]["working"])
        pi_exe             = _get_pi_exe(config)
        sharpen_stars      = config["processing"].get("bxt_sharpen_stars", 0.25)
        sharpen_nonstellar = config["processing"].get("bxt_sharpen_nonstellar", 0.40)
        adjust_halos       = config["processing"].get("bxt_adjust_halos", 0.05)

        inp = working / "NGC1499_SHO_bxt_corrected.xisf"
        out = working / "NGC1499_SHO_bxt.xisf"

        if out.exists():
            print("[linear] BXT sharpen output exists -- skipping")
            return 0

        script = generate_blur_xterminator(
            input_path=str(inp),
            output_path=str(out),
            correct_only=False,
            automatic_psf=True,
            sharpen_stars=sharpen_stars,
            sharpen_nonstellar=sharpen_nonstellar,
            adjust_halos=adjust_halos,
        )
        _run_pjsr(script, "BlurXTerminator pass 2 (sharpen)", pi_exe, PI_TIMEOUT)

        return 0


# =============================================================================
# Stage 6: Channel Split
# =============================================================================


@dataclass
class ChannelSplitStage(PipelineStage):
    """
    Split the SHO_bxt.xisf combined image back into individual NB channels.

    BXT processes the combined SHO image as a color image. After BXT, the
    channels must be split back out for per-channel denoising, star removal,
    and stretching.

    SHO encoding:
        R = SII  ->  NGC1499_SII_processed.xisf
        G = Ha   ->  NGC1499_Ha_processed.xisf
        B = OIII ->  NGC1499_OIII_processed.xisf

    Input:  NGC1499_SHO_bxt.xisf
    Outputs:
        NGC1499_SII_processed.xisf
        NGC1499_Ha_processed.xisf
        NGC1499_OIII_processed.xisf

    Idempotency: if all three output files exist, stage is skipped.
    """

    def execute(self, config: dict) -> int:
        working = Path(config["directories"]["working"])
        pi_exe  = _get_pi_exe(config)

        inp = working / "NGC1499_SHO_bxt.xisf"

        sii_out  = working / "NGC1499_SII_processed.xisf"
        ha_out   = working / "NGC1499_Ha_processed.xisf"
        oiii_out = working / "NGC1499_OIII_processed.xisf"

        if sii_out.exists() and ha_out.exists() and oiii_out.exists():
            print("[linear] Channel split outputs exist -- skipping")
            return 0

        output_paths = {
            "R": str(sii_out),   # R channel = SII
            "G": str(ha_out),    # G channel = Ha
            "B": str(oiii_out),  # B channel = OIII
        }

        script = generate_channel_extraction(
            input_path=str(inp),
            output_paths=output_paths,
        )
        _run_pjsr(script, "ChannelExtraction SHO -> S/H/O", pi_exe, PI_TIMEOUT)

        return 0


# =============================================================================
# Stage 7: GraXpert Denoise
# =============================================================================


@dataclass
class GraXpertDenoiseStage(PipelineStage):
    """
    GraXpert AI denoising per NB channel (NXT replacement).

    Applies after ChannelSplit, before StarXTerminator. GraXpert denoising is
    GPU-accelerated, CLI-automatable, and free. Observed performance: ~257s per
    875MB drizzle 2x file with GPU (spike test 2026-02-20).

    Input:  NGC1499_{ch}_processed.xisf
    Output: NGC1499_{ch}_denoised.xisf

    Per-channel denoise strength from config["processing"] (with defaults):
        Ha:   graxpert_denoise_strength_ha   = 0.40  (highest SNR, least NR)
        OIII: graxpert_denoise_strength_oiii = 0.60  (faintest channel)
        SII:  graxpert_denoise_strength_sii  = 0.50
        other channels: graxpert_denoise_strength = 0.5

    batch_size = config["processing"].get("graxpert_denoise_batch_size", 4)
    Reduce batch_size to 2 if GPU runs out of memory.

    Per-channel idempotency: output is skipped if it already exists.
    GraXpertError is caught and re-raised as PipelineError.
    """

    def execute(self, config: dict) -> int:
        working      = Path(config["directories"]["working"])
        nb_filters   = config["acquisition"]["nb"]["filters"]
        graxpert_exe = config["tools"].get("graxpert_exe")
        batch_size   = config["processing"].get("graxpert_denoise_batch_size", 4)

        # Per-channel strength lookup
        strength_map = {
            "Ha":   config["processing"].get("graxpert_denoise_strength_ha",   0.40),
            "OIII": config["processing"].get("graxpert_denoise_strength_oiii", 0.60),
            "SII":  config["processing"].get("graxpert_denoise_strength_sii",  0.50),
        }
        default_strength = config["processing"].get("graxpert_denoise_strength", 0.5)

        for ch in nb_filters:
            inp = working / f"NGC1499_{ch}_processed.xisf"
            out = working / f"NGC1499_{ch}_denoised.xisf"

            if out.exists():
                print(f"[linear] {ch}: denoise output exists -- skipping")
                continue

            strength = strength_map.get(ch, default_strength)
            print(f"[linear] {ch}: GraXpert denoise {inp.name} -> {out.name} (strength={strength})")

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
                    f"GraXpert denoising failed for channel {ch}: {exc}"
                ) from exc

        return 0


# =============================================================================
# Stage 8: StarXTerminator
# =============================================================================


@dataclass
class SXTStage(PipelineStage):
    """
    StarXTerminator star removal per NB channel (linear domain).

    Removes stars from each denoised NB channel to produce starless images for
    the stretching and Foraxx palette stages. The NB stars-only images are
    DISCARDED (stars_output_path=None) -- the final star layer comes from the
    RGB track in Phase 5.

    unscreen=False because the input is linear data (stars were not added via
    screen blend; the image contains native linear photon counts).

    Passing stars_output_path=None avoids a known PI window-ID lookup issue
    when the stars image is not required.

    Input:  NGC1499_{ch}_denoised.xisf
    Output: NGC1499_{ch}_starless.xisf

    Per-channel idempotency: output is skipped if it already exists.
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        nb_filters = config["acquisition"]["nb"]["filters"]

        for ch in nb_filters:
            inp = working / f"NGC1499_{ch}_denoised.xisf"
            out = working / f"NGC1499_{ch}_starless.xisf"

            if out.exists():
                print(f"[linear] {ch}: SXT starless output exists -- skipping")
                continue

            print(f"[linear] {ch}: StarXTerminator {inp.name} -> {out.name}")

            script = generate_star_xterminator(
                input_path=str(inp),
                starless_output_path=str(out),
                stars_output_path=None,   # NB stars discarded; RGB track provides stars
                unscreen=False,           # linear data
            )
            _run_pjsr(script, f"StarXTerminator {ch}", pi_exe, PI_TIMEOUT)

        return 0
