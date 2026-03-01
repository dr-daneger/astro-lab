"""
stages/preprocessing.py -- Sprint 3: Phase 1 preprocessing implementations

Concrete PipelineStage subclasses for Phase 1 (preprocessing). Each stage
discovers its input files dynamically at execute() time by globbing the
configured raw/calibration directories. This makes the stages robust to
varying filename conventions.

Frame discovery conventions (tried in order):
  Subdirectory layout:  raw_nb/<Filter>/Light_*.{fit,fits,xisf}
  Flat layout:          raw_nb/Light_*<Filter>*.{fit,fits,xisf}

Calibration master conventions (in calibration_nb/):
  Master dark:         master_dark*.{xisf,fit,fits}
  Per-filter flat:     master_flat_<Filter>.{xisf,fit,fits}
  Single flat (any):   master_flat*.{xisf,fit,fits}  (fallback)

Working subdirectories created by these stages:
  working/calibrated/<ch>/   -- calibrated frames  (_c postfix)
  working/registered/<ch>/   -- registered frames  (_c_r postfix) + .xdrz
  working/normalized/<ch>/   -- normalized frames  (_c_r_n postfix)
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator import PipelineStage, PipelineError
from pi_runner import run_pjsr_inline
from pjsr_generator import (
    generate_subframe_selector,
    generate_image_calibration,
    generate_star_alignment,
    generate_star_alignment_global,
    generate_local_normalization,
    generate_image_integration,
    generate_drizzle_integration,
)


# =============================================================================
# Per-stage subprocess timeouts (seconds)
# =============================================================================

SUBFRAME_TIMEOUT    = 900    # 15 min -- star detection on all raw frames
CALIBRATION_TIMEOUT = 3600   # 1 h   -- calibration + registration + integration
DRIZZLE_TIMEOUT     = 7200   # 2 h   -- DrizzleIntegration on 100+ 800MB frames
RGB_CAL_TIMEOUT     = 1800   # 30 min -- RGB has fewer frames (10s subs)


# =============================================================================
# Frame and calibration master discovery helpers
# =============================================================================

_LIGHT_PATTERNS = ("*.fit", "*.fits", "*.xisf", "*.FIT", "*.FITS", "*.XISF")
# Raw formats only — excludes processed .xisf so rglob doesn't pick up
# calibrated/registered frames that live alongside raw lights.
_RAW_PATTERNS   = ("*.fit", "*.fits", "*.FIT", "*.FITS")

# NINA uses single-char filter codes in filenames (H, O, S) while the pipeline
# uses full names (Ha, OIII, SII).  Map full-name → abbreviation for matching.
_FILTER_ABBREV = {
    "HA":   "H",
    "OIII": "O",
    "SII":  "S",
    "LUM":  "L",
}


def _find_frames(raw_dir: Path, filter_name: str) -> list[Path]:
    """
    Discover raw light frames for a given filter name.

    Search order:
      1. raw_dir/<filter_name>/  (subdirectory layout — ASIAIR default)
      2. raw_dir/ recursively    (date-organised flat layout — NINA default)

    For the flat layout, frames are matched by filter token: the filter name
    (or its single-char NINA abbreviation) must appear as an underscore-delimited
    token in the filename stem (e.g. ``_H_`` or ``_Ha_``).  Only raw `.fit/.fits`
    files are matched; processed `.xisf` frames (calibrated/registered) are
    intentionally excluded to prevent double-counting.

    Args:
        raw_dir:     Root raw data directory.
        filter_name: Filter name as configured (e.g. "Ha", "OIII", "R").

    Returns:
        Sorted, deduplicated list of found frame paths. Empty list if none found.
    """
    frames: list[Path] = []

    # 1. Subdirectory layout (ASIAIR separates by filter into subdirs)
    sub_dir = raw_dir / filter_name
    if sub_dir.is_dir():
        for pat in _LIGHT_PATTERNS:
            frames.extend(sub_dir.glob(pat))
        if frames:
            return sorted(set(frames))

    # 2. Date-organised flat layout: search recursively for raw files only.
    # Match filter as a whole token surrounded by underscores to avoid false
    # positives (e.g. "R" matching "FRAMED").  Also try single-char NINA abbrev.
    fn_upper = filter_name.upper()
    abbrev   = _FILTER_ABBREV.get(fn_upper)
    tokens   = {f"_{fn_upper}_"}
    if abbrev:
        tokens.add(f"_{abbrev}_")

    for pat in _RAW_PATTERNS:
        for f in raw_dir.rglob(pat):
            if any(tok in f.stem.upper() for tok in tokens):
                frames.append(f)

    return sorted(set(frames))


def _find_calibration_master(cal_dir: Path, *patterns: str) -> Optional[Path]:
    """
    Find a calibration master matching any glob pattern (priority order).

    Returns the first match found across the patterns, or None.
    """
    for pattern in patterns:
        matches = sorted(cal_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def _glob_dir(directory: Path, *patterns: str) -> list[Path]:
    """Glob a directory for multiple patterns, deduplicated and sorted."""
    results: list[Path] = []
    for pat in patterns:
        results.extend(directory.glob(pat))
    return sorted(set(results))


def _expect_frames(frames: list[Path], channel: str, source: str) -> None:
    """Raise PipelineError if no frames found for a channel."""
    if not frames:
        raise PipelineError(
            f"No light frames found for channel '{channel}' in: {source}\n"
            "Check config directories.raw_nb or directories.raw_rgb.\n"
            "Expected layout:  <raw_dir>/<Filter>/Light_*.fit  "
            "or  <raw_dir>/Light_*<Filter>*.fit"
        )


def _match_drizzle_pairs(
    frames: list[Path], drizzle_files: list[Path]
) -> list[tuple[Path, Path]]:
    """
    Match registered frames to their .xdrz sidecar files by stem.

    StarAlignment writes frame_r.xdrz alongside frame_r.xisf. Matching is by
    stem (filename without extension).

    Raises:
        PipelineError: If any frame has no corresponding .xdrz file.
    """
    drizzle_map = {f.stem: f for f in drizzle_files}
    pairs: list[tuple[Path, Path]] = []
    missing: list[str] = []

    for frame in frames:
        dzl = drizzle_map.get(frame.stem)
        if dzl is None:
            missing.append(frame.name)
        else:
            pairs.append((frame, dzl))

    if missing:
        raise PipelineError(
            f"No .xdrz sidecar found for {len(missing)} registered frame(s):\n"
            + "\n".join(f"  {n}" for n in missing[:10])
            + ("\n  ..." if len(missing) > 10 else "")
            + f"\nAvailable .xdrz files: {[d.name for d in drizzle_files[:5]]}"
        )

    return pairs


def _run_pjsr(
    script: str,
    step_name: str,
    pi_exe: Optional[str],
    timeout: int,
) -> None:
    """Execute a PJSR script and raise PipelineError on non-zero exit code."""
    print(f"[preprocessing] Running: {step_name}")
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
# Shared calibration pipeline (used by both NB and RGB stages)
# =============================================================================


def _run_channel_pipeline(
    channel: str,
    raw_dir: Path,
    cal_dir: Path,
    working: Path,
    master_dark: Optional[Path],
    master_flat: Optional[Path],
    pedestal: int,
    rej_alg: str,
    esd_sig: float,
    esd_out: float,
    esd_low: float,
    local_norm: bool,
    target_prefix: str,
    registration_reference: Optional[Path],
    pi_exe: Optional[str],
    timeout: int,
    generate_drizzle_data: bool = True,
) -> Path:
    """
    Run ImageCalibration -> StarAlignment -> LocalNormalization -> ImageIntegration
    for a single channel.

    Args:
        channel:                  Filter name (e.g. "Ha", "OIII", "R").
        raw_dir:                  Root directory containing raw light frames.
        cal_dir:                  Directory containing calibration masters.
        working:                  Pipeline working directory root.
        master_dark:              Master dark path (or None to skip dark).
        master_flat:              Master flat path for this channel (or None).
        pedestal:                 Output pedestal in DN (default 150).
        rej_alg:                  Integration rejection algorithm name.
        esd_sig/out/low:          ESD rejection parameters.
        local_norm:               If True, run LocalNormalization before integration.
        target_prefix:            Prefix for output master filename (e.g. "NGC1499").
        registration_reference:   If provided, use this as the StarAlignment reference
                                  instead of the first calibrated frame. Used for
                                  cross-track registration (RGB -> Ha).
        pi_exe:                   PixInsight executable path.
        timeout:                  Per-step subprocess timeout in seconds.

    Returns:
        Path to the output master .xisf file.
    """
    cal_out  = working / "calibrated" / channel
    reg_out  = working / "registered" / channel
    norm_out = working / "normalized" / channel
    master_file = working / f"{target_prefix}_{channel}_master.xisf"

    if master_file.exists():
        print(f"[preprocessing] Skip {channel} -- master exists: {master_file.name}")
        return master_file

    cal_out.mkdir(parents=True, exist_ok=True)
    reg_out.mkdir(parents=True, exist_ok=True)
    norm_out.mkdir(parents=True, exist_ok=True)

    # ── 1. Discover raw frames ─────────────────────────────────────────────────
    raw_frames = _find_frames(raw_dir, channel)
    _expect_frames(raw_frames, channel, str(raw_dir))
    print(f"[preprocessing] {channel}: {len(raw_frames)} raw frames")

    # ── 2. ImageCalibration ────────────────────────────────────────────────────
    cal_frames = _glob_dir(cal_out, "*.xisf", "*.fit", "*.fits")
    if cal_frames:
        print(f"[preprocessing] {channel}: calibration exists ({len(cal_frames)} frames), skipping")
    else:
        cal_script = generate_image_calibration(
            light_paths=[str(f) for f in raw_frames],
            output_dir=str(cal_out),
            master_dark_path=str(master_dark) if master_dark else None,
            master_flat_path=str(master_flat) if master_flat else None,
            pedestal=pedestal,
            output_postfix="_c",
        )
        _run_pjsr(cal_script, f"{channel} ImageCalibration", pi_exe, timeout)
        cal_frames = _glob_dir(cal_out, "*.xisf", "*.fit", "*.fits")
        if not cal_frames:
            raise PipelineError(
                f"ImageCalibration produced no output in {cal_out}."
            )

    # ── 3. StarAlignment ───────────────────────────────────────────────────────
    # Use provided cross-track reference (e.g. Ha master for RGB) or the first
    # calibrated frame of this channel as an intra-channel reference.
    #
    # When generate_drizzle_data=True, use executeGlobal variant so that .xdrz
    # sidecar files are written alongside registered frames. executeOn() does NOT
    # produce .xdrz files, so DrizzleIntegration would fall back to the plain master.
    reg_frames = _glob_dir(reg_out, "*.xisf", "*.fit", "*.fits")
    if reg_frames:
        print(f"[preprocessing] {channel}: registration exists ({len(reg_frames)} frames), skipping")
    else:
        ref = str(registration_reference) if registration_reference else str(cal_frames[0])
        if generate_drizzle_data:
            # executeGlobal writes .xdrz sidecars -- required for DrizzleIntegration
            reg_script = generate_star_alignment_global(
                reference_path=ref,
                target_paths=[str(f) for f in cal_frames],
                output_dir=str(reg_out),
                distortion_correction=True,
                generate_drizzle_data=True,
                output_postfix="_r",
            )
        else:
            # executeOn loop -- no .xdrz, but reliable across all PI versions
            reg_script = generate_star_alignment(
                reference_path=ref,
                target_paths=[str(f) for f in cal_frames],
                output_dir=str(reg_out),
                distortion_correction=True,
                generate_drizzle_data=False,
                output_postfix="_r",
            )
        _run_pjsr(reg_script, f"{channel} StarAlignment", pi_exe, timeout)
        reg_frames = _glob_dir(reg_out, "*.xisf", "*.fit", "*.fits")
        if not reg_frames:
            raise PipelineError(
                f"StarAlignment produced no output in {reg_out}."
            )

    # ── 4. LocalNormalization (optional) ──────────────────────────────────────
    if local_norm:
        norm_frames = _glob_dir(norm_out, "*.xisf", "*.fit", "*.fits")
        if norm_frames:
            print(f"[preprocessing] {channel}: normalization exists ({len(norm_frames)} frames), skipping")
            integration_frames = norm_frames
        else:
            norm_script = generate_local_normalization(
                reference_path=str(reg_frames[0]),
                target_paths=[str(f) for f in reg_frames],
                output_dir=str(norm_out),
                output_postfix="_n",
            )
            _run_pjsr(norm_script, f"{channel} LocalNormalization", pi_exe, timeout)
            integration_frames = _glob_dir(norm_out, "*.xisf", "*.fit", "*.fits")
    else:
        integration_frames = reg_frames

    if not integration_frames:
        raise PipelineError(
            f"No frames available for ImageIntegration for channel {channel}."
        )

    # ── 5. ImageIntegration ────────────────────────────────────────────────────
    int_script = generate_image_integration(
        image_paths=[str(f) for f in integration_frames],
        output_path=str(master_file),
        rejection_algorithm=rej_alg,
        esd_significance=esd_sig,
        esd_outliers_fraction=esd_out,
        esd_low_relaxation=esd_low,
        generate_drizzle_output=False,  # .xdrz handled by NBDrizzleStage via SA sidecar files
    )
    _run_pjsr(int_script, f"{channel} ImageIntegration", pi_exe, timeout)

    if not master_file.exists():
        raise PipelineError(
            f"ImageIntegration completed but master not found: {master_file}"
        )

    print(f"[preprocessing] {channel}: master saved -> {master_file.name}")
    return master_file


# =============================================================================
# Stage 1: Subframe Inspection
# =============================================================================


@dataclass
class SubframeInspectionStage(PipelineStage):
    """
    Run SubframeSelector on all NB raw frames to produce quality metrics CSV.

    Output: working/subframe_weights_nb.csv

    Columns: Index, Path, Approved, FWHM, Eccentricity, SNRWeight

    No frames are removed -- this is a report only. Operator reviews CSV and
    removes poor frames from raw_nb/ before running NB calibration/integration.
    """

    def execute(self, config: dict) -> int:
        raw_nb     = Path(config["directories"]["raw_nb"])
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        nb_filters = config["acquisition"]["nb"]["filters"]

        working.mkdir(parents=True, exist_ok=True)
        output_csv = working / "subframe_weights_nb.csv"

        all_frames: list[str] = []
        for ch in nb_filters:
            frames = _find_frames(raw_nb, ch)
            _expect_frames(frames, ch, str(raw_nb))
            all_frames.extend(str(f) for f in frames)

        print(
            f"[preprocessing] SubframeSelector: {len(all_frames)} frames across "
            f"{len(nb_filters)} NB channels"
        )

        script = generate_subframe_selector(
            frame_paths=all_frames,
            output_csv=str(output_csv),
        )
        _run_pjsr(script, "SubframeSelector NB", pi_exe, SUBFRAME_TIMEOUT)
        return 0


# =============================================================================
# Stage 2: NB Calibration + Registration + Integration
# =============================================================================


@dataclass
class NBCalibrationStage(PipelineStage):
    """
    Full Phase 1 NB preprocessing: calibration -> registration ->
    local normalization -> stacking integration, for each NB channel.

    Runs in sequence for Ha, OIII, SII. Intermediate frames are stored in
    working/calibrated/, working/registered/, working/normalized/ per channel.
    Final outputs are NGC1499_{ch}_master.xisf in working/.

    Calibration masters expected in config.directories.calibration_nb:
      Master dark:  master_dark*.{xisf,fit,fits}
      Per-ch flat:  master_flat_<Ch>.{xisf,fit,fits}
                    (falls back to any master_flat*.xisf if per-filter not found)
    """

    def execute(self, config: dict) -> int:
        raw_nb     = Path(config["directories"]["raw_nb"])
        cal_nb     = Path(config["directories"]["calibration_nb"])
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        nb_filters = config["acquisition"]["nb"]["filters"]
        pre        = config["preprocessing"]
        pedestal   = pre.get("pedestal", 150)
        rej_alg    = pre.get("rejection_algorithm", "ESD")
        esd_sig    = pre.get("esd_significance", 0.05)
        esd_out    = pre.get("esd_outliers", 0.30)
        esd_low    = pre.get("esd_low_relaxation", 2.0)
        local_norm = pre.get("local_normalization", True)

        master_dark = _find_calibration_master(
            cal_nb,
            "master_dark*.xisf", "master_dark*.fit", "master_dark*.fits",
        )
        if master_dark:
            print(f"[preprocessing] Master dark: {master_dark.name}")
        else:
            print("[preprocessing] No master dark found -- dark calibration disabled")

        # NB registration always generates drizzle data so NBDrizzleStage can run
        drizzle_scale = pre.get("drizzle_scale", 2)
        generate_drizzle = drizzle_scale > 1

        for ch in nb_filters:
            master_flat = _find_calibration_master(
                cal_nb,
                f"master_flat_{ch}.xisf",    # exact name (legacy)
                f"master_flat_{ch}_*.xisf",  # builder naming: master_flat_Ha_gain100_...xisf
                f"master_flat_{ch}.fit",
                f"master_flat_{ch}.fits",
                "master_flat*.xisf", "master_flat*.fit",
            )
            _run_channel_pipeline(
                channel=ch,
                raw_dir=raw_nb,
                cal_dir=cal_nb,
                working=working,
                master_dark=master_dark,
                master_flat=master_flat,
                pedestal=pedestal,
                rej_alg=rej_alg,
                esd_sig=esd_sig,
                esd_out=esd_out,
                esd_low=esd_low,
                local_norm=local_norm,
                target_prefix="NGC1499",
                registration_reference=None,  # intra-channel; uses first cal frame
                pi_exe=pi_exe,
                timeout=CALIBRATION_TIMEOUT,
                generate_drizzle_data=generate_drizzle,
            )

        return 0


# =============================================================================
# Stage 3: NB DrizzleIntegration
# =============================================================================


@dataclass
class NBDrizzleStage(PipelineStage):
    """
    Run DrizzleIntegration 2x for each NB channel.

    Reads registered frames + .xdrz sidecar files written by StarAlignment
    in working/registered/<ch>/. Outputs NGC1499_{ch}_drizzle.xisf to working/.

    Requires:
      - working/registered/<ch>/*.xisf  -- registered calibrated frames
      - working/registered/<ch>/*.xdrz  -- drizzle sidecar files
      (Both created by NBCalibrationStage when generate_drizzle_data=True.)
    """

    def execute(self, config: dict) -> int:
        working    = Path(config["directories"]["working"])
        pi_exe     = _get_pi_exe(config)
        nb_filters = config["acquisition"]["nb"]["filters"]
        pre        = config["preprocessing"]
        scale      = float(pre.get("drizzle_scale", 2))
        drop       = float(pre.get("drizzle_drop_shrink", 0.9))
        kernel     = pre.get("drizzle_kernel", "Square")

        for ch in nb_filters:
            drizzle_file = working / f"NGC1499_{ch}_drizzle.xisf"
            if drizzle_file.exists():
                print(f"[preprocessing] Skip {ch} drizzle -- output exists")
                continue

            reg_dir = working / "registered" / ch
            if not reg_dir.is_dir():
                raise PipelineError(
                    f"Registered frame directory not found: {reg_dir}\n"
                    "Run 'NB Calibration Registration Integration' first."
                )

            reg_frames = _glob_dir(reg_dir, "*.xisf", "*.fit", "*.fits")
            drizzle_data = _glob_dir(reg_dir, "*.xdrz")

            if not reg_frames:
                raise PipelineError(
                    f"No registered frames in {reg_dir}. "
                    "Run NB Calibration Registration Integration first."
                )
            if not drizzle_data:
                # SA ran via executeOn (no .xdrz produced) — fall back to the
                # standard ImageIntegration master so downstream stages can proceed.
                master_file = working / f"NGC1499_{ch}_master.xisf"
                if not master_file.exists():
                    raise PipelineError(
                        f"No .xdrz sidecar files in {reg_dir} and no master fallback "
                        f"found at {master_file}. Run NB Calibration Registration "
                        "Integration first."
                    )
                shutil.copy2(str(master_file), str(drizzle_file))
                print(
                    f"[preprocessing] {ch}: no .xdrz files -- copied master as drizzle fallback "
                    f"-> {drizzle_file.name}"
                )
                continue

            pairs = _match_drizzle_pairs(reg_frames, drizzle_data)
            print(f"[preprocessing] {ch}: {len(pairs)} frame/drizzle pairs for 2x drizzle")

            script = generate_drizzle_integration(
                image_paths=[str(img) for img, _ in pairs],
                drizzle_paths=[str(dzl) for _, dzl in pairs],
                output_path=str(drizzle_file),
                scale=scale,
                drop_shrink=drop,
                kernel=kernel,
            )
            _run_pjsr(script, f"{ch} DrizzleIntegration", pi_exe, DRIZZLE_TIMEOUT)

            if not drizzle_file.exists():
                raise PipelineError(
                    f"DrizzleIntegration completed but output not found: {drizzle_file}"
                )

            print(f"[preprocessing] {ch}: drizzle master -> {drizzle_file.name}")

        return 0


# =============================================================================
# Stage 4: RGB Calibration + Registration + Integration
# =============================================================================


@dataclass
class RGBCalibrationStage(PipelineStage):
    """
    Full Phase 1 RGB preprocessing: calibration -> registration ->
    local normalization -> integration, for each RGB channel (R, G, B).

    Registration uses G-band calibrated frames as the reference (highest SNR
    for broadband star fields). R and B are registered to that reference.

    Calibration masters expected in config.directories.calibration_rgb:
      Master dark:  master_dark*.{xisf,fit,fits}  (must match 10s, Gain -25)
      Per-ch flat:  master_flat_<Ch>.{xisf,fit,fits}  or master_flat*.xisf
    """

    def execute(self, config: dict) -> int:
        raw_rgb      = Path(config["directories"]["raw_rgb"])
        cal_rgb      = Path(config["directories"]["calibration_rgb"])
        working      = Path(config["directories"]["working"])
        pi_exe       = _get_pi_exe(config)
        rgb_filters  = config["acquisition"]["rgb"]["filters"]  # ["R","G","B"]
        pre          = config["preprocessing"]
        pedestal     = pre.get("pedestal", 150)
        # RGB has 30-50 short subs -- WinsorizedSigmaClip is appropriate
        rej_alg      = pre.get("rgb_rejection_algorithm", "WinsorizedSigmaClip")
        esd_sig      = pre.get("esd_significance", 0.05)
        esd_out      = pre.get("esd_outliers", 0.30)
        esd_low      = 1.0    # No relaxation needed for RGB (no faint NB signal)
        local_norm   = pre.get("local_normalization", True)

        master_dark = _find_calibration_master(
            cal_rgb,
            "master_dark*.xisf", "master_dark*.fit", "master_dark*.fits",
        )
        if master_dark:
            print(f"[preprocessing] RGB master dark: {master_dark.name}")
        else:
            print("[preprocessing] No RGB master dark -- dark calibration disabled")

        for ch in rgb_filters:
            master_flat = _find_calibration_master(
                cal_rgb,
                f"master_flat_{ch}.xisf",    # exact name (legacy)
                f"master_flat_{ch}_*.xisf",  # builder naming: master_flat_R_gain-25_...xisf
                f"master_flat_{ch}.fit",
                f"master_flat_{ch}.fits",
                "master_flat*.xisf", "master_flat*.fit",
            )
            _run_channel_pipeline(
                channel=ch,
                raw_dir=raw_rgb,
                cal_dir=cal_rgb,
                working=working,
                master_dark=master_dark,
                master_flat=master_flat,
                pedestal=pedestal,
                rej_alg=rej_alg,
                esd_sig=esd_sig,
                esd_out=esd_out,
                esd_low=esd_low,
                local_norm=local_norm,
                target_prefix="NGC1499",
                registration_reference=None,  # intra-channel
                pi_exe=pi_exe,
                timeout=RGB_CAL_TIMEOUT,
            )

        return 0


# =============================================================================
# Stage 5: RGB-to-NB Frame Registration
# =============================================================================


@dataclass
class RGBToNBRegistrationStage(PipelineStage):
    """
    Register RGB master images to the Ha master reference frame.

    Uses StarAlignment with the Ha master as the reference image. This
    produces pixel-perfect alignment between the starless SHO nebula (NB track)
    and the RGB star layer (RGB track) for Phase 5 screen blend recombination.

    Input:
      working/NGC1499_{R,G,B}_master.xisf  -- from RGBCalibrationStage
      working/NGC1499_Ha_master.xisf       -- from NBCalibrationStage

    Output:
      working/NGC1499_{R,G,B}_master_registered.xisf
    """

    def execute(self, config: dict) -> int:
        working     = Path(config["directories"]["working"])
        pi_exe      = _get_pi_exe(config)
        rgb_filters = config["acquisition"]["rgb"]["filters"]
        nb_filters  = config["acquisition"]["nb"]["filters"]

        # Ha master is the astrometric reference (highest star count + SNR in NB)
        ha_channel = nb_filters[0]  # First NB filter -- expected to be "Ha"
        ha_master  = working / f"NGC1499_{ha_channel}_master.xisf"

        if not ha_master.exists():
            raise PipelineError(
                f"Ha master not found: {ha_master}\n"
                "Run 'NB Calibration Registration Integration' first."
            )

        rgb_masters = []
        for ch in rgb_filters:
            master = working / f"NGC1499_{ch}_master.xisf"
            if not master.exists():
                raise PipelineError(
                    f"RGB master not found: {master}\n"
                    "Run 'RGB Calibration Registration Integration' first."
                )
            rgb_masters.append(master)

        # Output files: <stem>_r.xisf -- rename to _master_registered.xisf
        reg_dir = working / "rgb_registered"
        reg_dir.mkdir(parents=True, exist_ok=True)

        script = generate_star_alignment(
            reference_path=str(ha_master),
            target_paths=[str(m) for m in rgb_masters],
            output_dir=str(reg_dir),
            distortion_correction=True,
            generate_drizzle_data=False,  # no drizzle needed for RGB stars
            output_postfix="_r",
        )
        _run_pjsr(script, "RGB-to-NB StarAlignment", pi_exe, RGB_CAL_TIMEOUT)

        # Rename StarAlignment outputs from <stem>_r.xisf -> NGC1499_{ch}_master_registered.xisf
        for ch, master in zip(rgb_filters, rgb_masters):
            sa_output = reg_dir / f"{master.stem}_r.xisf"
            final_out = working / f"NGC1499_{ch}_master_registered.xisf"

            if final_out.exists():
                print(f"[preprocessing] {ch} registered master already at destination")
                continue

            if not sa_output.exists():
                raise PipelineError(
                    f"StarAlignment output not found: {sa_output}\n"
                    "Check PixInsight console for errors."
                )

            sa_output.rename(final_out)
            print(f"[preprocessing] {ch}: registered -> {final_out.name}")

        return 0
