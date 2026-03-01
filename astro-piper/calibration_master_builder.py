#!/usr/bin/env python3
"""
calibration_master_builder.py -- WBPP-analogous calibration master creation

Scans a directory of raw calibration frames (bias, darks, flats, lights),
reads their FITS headers, groups them by imaging parameters, then builds
PixInsight-format (.xisf) master frames via headless PI scripts.

Master creation workflow mirrors PixInsight WBPP:
    Bias:  ImageIntegration (no calibration, NoNormalization)
    Dark:  ImageCalibration(bias) -> ImageIntegration(NoNormalization)
    Flat:  ImageCalibration(bias + optional scaled dark) ->
           ImageIntegration(Multiplicative normalization)

Usage examples:
    # Build all masters from calibration library:
    python calibration_master_builder.py \\
        --scan-dir  "C:/Users/Dane/Pictures/DSOs/~_ASI2600MM Pro Calibration Data/Library - Darks" \\
        --masters-dir "C:/Users/Dane/Pictures/DSOs/~_ASI2600MM Pro Calibration Data/Library - Darks/masters" \\
        --pi-exe "C:/Program Files/PixInsight/bin/PixInsight.exe"

    # Build flat masters from NGC1499 target directory:
    python calibration_master_builder.py \\
        --scan-dir  "C:/Users/Dane/Pictures/DSOs/01_nebulae/NGC1499 - California Nebula" \\
        --masters-dir "C:/Users/Dane/Pictures/DSOs/01_nebulae/NGC1499 - California Nebula/masters" \\
        --bias-masters-dir "C:/Users/Dane/Pictures/DSOs/~_ASI2600MM Pro Calibration Data/Library - Darks/masters" \\
        --pi-exe "C:/Program Files/PixInsight/bin/PixInsight.exe"

    # Dry run (print plan without executing):
    python calibration_master_builder.py --scan-dir ... --masters-dir ... --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from astropy.io import fits as astropy_fits
    ASTROPY_OK = True
except ImportError:
    ASTROPY_OK = False

from pi_runner import run_pjsr_inline
from pjsr_generator import (
    generate_image_calibration,
    generate_master_bias,
    generate_integrate_calibrated_frames,
)


# =============================================================================
# Constants
# =============================================================================

FITS_EXTENSIONS = {".fit", ".fits", ".fts", ".FIT", ".FITS", ".FTS"}

# Calibration frames should integrate in well under 10 minutes each step.
INTEGRATION_TIMEOUT = 1800   # 30 min
CALIBRATION_TIMEOUT = 1800   # 30 min

# Filter name normalisation: NINA sometimes writes single-char abbreviations
FILTER_EXPAND = {
    "H": "Ha",
    "O": "OIII",
    "S": "SII",
    "L": "Lum",
    "R": "R",
    "G": "G",
    "B": "B",
}


# =============================================================================
# Data model
# =============================================================================


@dataclass
class CalibrationGroup:
    """A set of FITS frames sharing identical imaging parameters."""
    image_type:  str              # "Bias", "Dark", "Flat", "Light"
    filter_name: Optional[str]    # None for bias/dark; "Ha", "R", etc. for flat/light
    exptime:     float            # seconds
    gain:        int
    offset:      int
    binning:     int
    set_temp:    Optional[float]  # °C, or None if not recorded
    paths:       list[Path] = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (
            self.image_type,
            self.filter_name,
            round(self.exptime, 3),
            self.gain,
            self.offset,
            self.binning,
        )

    @property
    def label(self) -> str:
        parts = [f"{self.image_type}"]
        if self.filter_name:
            parts.append(f"filter={self.filter_name}")
        parts.append(f"gain={self.gain}")
        parts.append(f"offset={self.offset}")
        parts.append(f"exp={self.exptime:.3f}s")
        parts.append(f"bin{self.binning}")
        if self.set_temp is not None:
            parts.append(f"{self.set_temp:.0f}C")
        parts.append(f"[{len(self.paths)} frames]")
        return "  ".join(parts)

    def master_stem(self) -> str:
        """Canonical filename stem for the master, without extension."""
        t     = self.image_type.lower().replace(" ", "_")
        f_s   = f"_{self.filter_name}" if self.filter_name else ""
        g_s   = f"_gain{self.gain}"
        o_s   = f"_offset{self.offset}"
        # Omit exposure from bias filenames (always ~0s and meaningless)
        e_s   = "" if self.image_type == "Bias" else f"_{self.exptime:.0f}s"
        b_s   = f"_bin{self.binning}"
        tmp_s = (
            f"_{self.set_temp:.0f}C".replace("-", "m")
            if self.set_temp is not None
            else ""
        )
        return f"master_{t}{f_s}{g_s}{o_s}{e_s}{b_s}{tmp_s}"


# =============================================================================
# FITS header scanning
# =============================================================================


def _read_header(path: Path) -> Optional[dict]:
    """Return a dict of selected FITS header keywords, or None on failure."""
    if not ASTROPY_OK:
        raise RuntimeError(
            "astropy is required for FITS header reading. "
            "Install with: pip install astropy"
        )
    try:
        with astropy_fits.open(str(path), memmap=False, ignore_missing_simple=True) as hdul:
            hdr = hdul[0].header
            return {
                "IMAGETYP": hdr.get("IMAGETYP"),
                "FILTER":   hdr.get("FILTER"),
                "EXPTIME":  hdr.get("EXPTIME"),
                "GAIN":     hdr.get("GAIN"),
                "OFFSET":   hdr.get("OFFSET"),
                "XBINNING": hdr.get("XBINNING", 1),
                "SET-TEMP": hdr.get("SET-TEMP"),
            }
    except Exception as exc:
        print(f"  WARNING: could not read header from {path.name}: {exc}", file=sys.stderr)
        return None


def scan_frames(directory: Path) -> dict[tuple, CalibrationGroup]:
    """
    Walk directory recursively, read every FITS header, and return groups
    keyed by (IMAGETYP, FILTER, EXPTIME, GAIN, OFFSET, XBINNING).

    Frames whose headers cannot be parsed are skipped with a warning.
    """
    groups: dict[tuple, CalibrationGroup] = {}

    fits_files = [
        p for p in directory.rglob("*")
        if p.is_file() and p.suffix in FITS_EXTENSIONS
    ]
    fits_files.sort()

    print(f"Scanning {len(fits_files)} FITS files in: {directory}")

    for path in fits_files:
        hdr = _read_header(path)
        if hdr is None:
            continue

        image_type  = str(hdr["IMAGETYP"]) if hdr["IMAGETYP"] else "Unknown"
        raw_filter  = hdr.get("FILTER")
        # Bias and dark frames don't have a meaningful filter — NINA records
        # whatever the last filter wheel position was; ignore it.
        if image_type in ("Bias", "Dark"):
            filter_name = None
        else:
            filter_name = FILTER_EXPAND.get(raw_filter, raw_filter) if raw_filter else None
        exptime     = float(hdr["EXPTIME"]) if hdr["EXPTIME"] is not None else 0.0
        gain_raw    = hdr.get("GAIN")
        gain        = int(gain_raw) if gain_raw is not None else 0
        offset_raw  = hdr.get("OFFSET")
        offset      = int(offset_raw) if offset_raw is not None else 0
        binning     = int(hdr.get("XBINNING") or 1)
        set_temp_raw = hdr.get("SET-TEMP")
        set_temp    = float(set_temp_raw) if set_temp_raw is not None else None

        grp = CalibrationGroup(
            image_type=image_type,
            filter_name=filter_name,
            exptime=exptime,
            gain=gain,
            offset=offset,
            binning=binning,
            set_temp=set_temp,
        )
        key = grp.key
        if key not in groups:
            groups[key] = grp
        groups[key].paths.append(path)

    return groups


# =============================================================================
# Master finding
# =============================================================================


def find_bias_master(
    masters_dir: Path,
    gain: int,
    offset: int,
    binning: int = 1,
) -> Optional[Path]:
    """
    Search masters_dir for a master bias matching gain/offset/binning.
    Tries exact-name patterns; returns the first match or None.
    """
    patterns = [
        f"master_bias_gain{gain}_offset{offset}_bin{binning}*.xisf",
        f"master_bias_gain{gain}_offset{offset}*.xisf",
        f"master_bias*_gain{gain}_offset{offset}_bin{binning}*.xisf",
        f"master_bias*_gain{gain}_offset{offset}*.xisf",
        f"master_bias*gain{gain}*.xisf",
    ]
    for pat in patterns:
        matches = sorted(masters_dir.glob(pat))
        if matches:
            return matches[0]
    return None


def find_dark_master(
    masters_dir: Path,
    gain: int,
    offset: int,
    exptime: float,
    binning: int = 1,
) -> Optional[Path]:
    """
    Search masters_dir for a master dark matching gain/offset/exptime/binning.
    """
    exp_s = f"{exptime:.0f}s"
    patterns = [
        f"master_dark_gain{gain}_offset{offset}_{exp_s}_bin{binning}*.xisf",
        f"master_dark_gain{gain}_offset{offset}_{exp_s}*.xisf",
        f"master_dark*_gain{gain}_offset{offset}_{exp_s}*.xisf",
        f"master_dark*gain{gain}*{exp_s}*.xisf",
    ]
    for pat in patterns:
        matches = sorted(masters_dir.glob(pat))
        if matches:
            return matches[0]
    return None


# =============================================================================
# Master building
# =============================================================================


def _run_pi(script: str, label: str, pi_exe: Optional[str], timeout: int) -> None:
    """Run a PJSR script via pi_runner and raise on failure."""
    print(f"  [PI] {label} ...")
    rc = run_pjsr_inline(script, pi_exe=pi_exe, timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"{label} failed (exit code {rc})")
    print(f"  [PI] {label} -- done")


def build_master_bias(
    group: CalibrationGroup,
    output_path: Path,
    pi_exe: Optional[str] = None,
    dry_run: bool = False,
) -> Path:
    """Create a master bias from raw bias frames via ImageIntegration."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        print(f"  EXISTS -- skipping: {output_path.name}")
        return output_path

    frame_strs = [str(p) for p in group.paths]
    script = generate_master_bias(frame_strs, str(output_path))

    if dry_run:
        print(f"  DRY RUN -- would create: {output_path.name}  ({len(group.paths)} frames)")
        return output_path

    _run_pi(script, f"Master bias {output_path.name}", pi_exe, INTEGRATION_TIMEOUT)
    if not output_path.exists():
        raise RuntimeError(f"PI ran but master bias not found: {output_path}")
    return output_path


def build_master_dark(
    group: CalibrationGroup,
    output_path: Path,
    master_bias_path: Optional[Path] = None,
    pi_exe: Optional[str] = None,
    dry_run: bool = False,
) -> Path:
    """
    Create a master dark:
      1. ImageCalibration: bias-subtract each dark frame into a temp dir
      2. ImageIntegration: integrate calibrated darks (NoNormalization)

    If no master_bias_path is provided, step 1 is skipped (rare: CMOS cameras
    with very low bias drift may not need bias subtraction for darks).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        print(f"  EXISTS -- skipping: {output_path.name}")
        return output_path

    frame_strs  = [str(p) for p in group.paths]
    bias_str    = str(master_bias_path) if master_bias_path else None

    if dry_run:
        bias_note = f"  bias={master_bias_path.name}" if master_bias_path else "  no bias"
        print(f"  DRY RUN -- would create: {output_path.name}  ({len(group.paths)} frames){bias_note}")
        return output_path

    if master_bias_path:
        # Step 1: bias-subtract darks into temp dir
        temp_dir = output_path.parent / f"_tmp_dark_cal_{group.gain}_{group.exptime:.0f}s"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            cal_script = generate_image_calibration(
                light_paths=frame_strs,
                output_dir=str(temp_dir),
                master_bias_path=bias_str,
                master_dark_path=None,
                master_flat_path=None,
                pedestal=0,
                output_postfix="_bc",
                output_extension=".xisf",
            )
            _run_pi(cal_script, f"Bias-subtract darks for {output_path.name}", pi_exe, CALIBRATION_TIMEOUT)

            cal_frames = sorted(temp_dir.glob("*.xisf"))
            if not cal_frames:
                raise RuntimeError(f"No calibrated dark frames produced in {temp_dir}")
            integrate_paths = [str(f) for f in cal_frames]
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
    else:
        print("  WARNING: no master bias provided -- integrating raw darks without bias subtraction")
        integrate_paths = frame_strs

    # Step 2: integrate
    int_script = generate_integrate_calibrated_frames(
        integrate_paths, str(output_path), normalization="NoNormalization"
    )
    _run_pi(int_script, f"Integrate darks -> {output_path.name}", pi_exe, INTEGRATION_TIMEOUT)

    # Cleanup temp
    if master_bias_path:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if not output_path.exists():
        raise RuntimeError(f"PI ran but master dark not found: {output_path}")
    return output_path


def build_master_flat(
    group: CalibrationGroup,
    output_path: Path,
    master_bias_path: Optional[Path] = None,
    master_dark_path: Optional[Path] = None,
    pi_exe: Optional[str] = None,
    dry_run: bool = False,
) -> Path:
    """
    Create a master flat:
      1. ImageCalibration: bias (+ optional scaled dark) subtract each flat
      2. ImageIntegration: integrate calibrated flats (Multiplicative normalization)
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        print(f"  EXISTS -- skipping: {output_path.name}")
        return output_path

    frame_strs = [str(p) for p in group.paths]
    bias_str   = str(master_bias_path) if master_bias_path else None
    dark_str   = str(master_dark_path) if master_dark_path else None

    if dry_run:
        notes = []
        if master_bias_path:
            notes.append(f"bias={master_bias_path.name}")
        if master_dark_path:
            notes.append(f"dark={master_dark_path.name}")
        note_str = ("  " + "  ".join(notes)) if notes else "  no calibration"
        print(f"  DRY RUN -- would create: {output_path.name}  ({len(group.paths)} frames){note_str}")
        return output_path

    if master_bias_path or master_dark_path:
        # Step 1: calibrate flats
        temp_dir = output_path.parent / f"_tmp_flat_cal_{group.filter_name}_{group.gain}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            cal_script = generate_image_calibration(
                light_paths=frame_strs,
                output_dir=str(temp_dir),
                master_bias_path=bias_str,
                master_dark_path=dark_str,
                master_flat_path=None,
                pedestal=0,
                output_postfix="_bc",
                output_extension=".xisf",
            )
            _run_pi(cal_script, f"Calibrate flats for {output_path.name}", pi_exe, CALIBRATION_TIMEOUT)

            cal_frames = sorted(temp_dir.glob("*.xisf"))
            if not cal_frames:
                raise RuntimeError(f"No calibrated flat frames produced in {temp_dir}")
            integrate_paths = [str(f) for f in cal_frames]
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
    else:
        print("  WARNING: no master bias provided -- integrating raw flats without calibration")
        integrate_paths = frame_strs

    # Step 2: integrate with multiplicative normalization
    int_script = generate_integrate_calibrated_frames(
        integrate_paths, str(output_path), normalization="Multiplicative"
    )
    _run_pi(int_script, f"Integrate flats -> {output_path.name}", pi_exe, INTEGRATION_TIMEOUT)

    if master_bias_path or master_dark_path:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if not output_path.exists():
        raise RuntimeError(f"PI ran but master flat not found: {output_path}")
    return output_path


# =============================================================================
# Orchestration
# =============================================================================


def build_all_masters(
    scan_dir: Path,
    masters_dir: Path,
    bias_masters_dir: Optional[Path] = None,
    pi_exe: Optional[str] = None,
    dry_run: bool = False,
    image_types: Optional[set[str]] = None,
) -> dict[str, list[Path]]:
    """
    Scan scan_dir, determine which masters need building, and build them in
    dependency order: bias → dark → flat.

    Args:
        scan_dir:         Directory to scan for raw calibration frames.
        masters_dir:      Output directory for created master files.
        bias_masters_dir: Extra directory to search for pre-existing bias
                          masters (useful when scanning a target directory
                          for flats that needs bias from the library).
        pi_exe:           PixInsight executable path.
        dry_run:          Print plan without running PI.
        image_types:      Restrict to these IMAGETYP values (None = all).

    Returns:
        dict with keys "bias", "dark", "flat" mapping to lists of created
        master paths (or planned paths in dry-run mode).
    """
    groups = scan_frames(scan_dir)

    # Split by type
    bias_groups  = {k: g for k, g in groups.items() if g.image_type == "Bias"}
    dark_groups  = {k: g for k, g in groups.items() if g.image_type == "Dark"}
    flat_groups  = {k: g for k, g in groups.items() if g.image_type in ("Flat", "Master Flat")}

    if image_types:
        bias_groups = {k: g for k, g in bias_groups.items() if g.image_type in image_types}
        dark_groups = {k: g for k, g in dark_groups.items() if g.image_type in image_types}
        flat_groups = {k: g for k, g in flat_groups.items() if g.image_type in image_types}

    # Skip "Master Flat" IMAGETYP — those are already masters
    flat_groups = {k: g for k, g in flat_groups.items() if g.image_type == "Flat"}

    masters_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, list[Path]] = {"bias": [], "dark": [], "flat": []}

    # ── 1. Bias masters ────────────────────────────────────────────────────────
    print(f"\n=== BIAS MASTERS ({len(bias_groups)} groups) ===")
    bias_master_map: dict[tuple, Path] = {}  # (gain, offset, bin) -> master path

    for key, grp in sorted(bias_groups.items(), key=lambda kv: str(kv[1].label)):
        print(f"\n  {grp.label}")
        out = masters_dir / (grp.master_stem() + ".xisf")
        try:
            master = build_master_bias(grp, out, pi_exe=pi_exe, dry_run=dry_run)
            bias_master_map[(grp.gain, grp.offset, grp.binning)] = master
            result["bias"].append(master)
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)

    # Also load any pre-existing bias masters from masters_dir and bias_masters_dir
    for search_dir in {masters_dir, bias_masters_dir}:
        if search_dir is None or not search_dir.is_dir():
            continue
        for xisf in search_dir.glob("master_bias*.xisf"):
            stem = xisf.stem
            parts = stem.split("_")
            gain_val = offset_val = bin_val = None
            for part in parts:
                if part.startswith("gain"):
                    try:
                        gain_val = int(part[4:])
                    except ValueError:
                        pass
                elif part.startswith("offset"):
                    try:
                        offset_val = int(part[6:])
                    except ValueError:
                        pass
                elif part.startswith("bin"):
                    try:
                        bin_val = int(part[3:])
                    except ValueError:
                        pass
            if gain_val is not None:
                bin_val = bin_val or 1
                key_exact = (gain_val, offset_val, bin_val)
                key_loose = (gain_val, None, bin_val)
                bias_master_map.setdefault(key_exact, xisf)
                bias_master_map.setdefault(key_loose, xisf)

    # ── 2. Dark masters ────────────────────────────────────────────────────────
    print(f"\n=== DARK MASTERS ({len(dark_groups)} groups) ===")
    dark_master_map: dict[tuple, Path] = {}  # (gain, offset, exptime, bin) -> path

    for key, grp in sorted(dark_groups.items(), key=lambda kv: str(kv[1].label)):
        print(f"\n  {grp.label}")

        # Find matching bias
        bias_key = (grp.gain, grp.offset, grp.binning)
        bias_master = bias_master_map.get(bias_key)
        if bias_master is None:
            # Try without offset constraint
            bias_master = bias_master_map.get((grp.gain, None, grp.binning))
        if bias_master is None:
            print(f"  WARNING: no master bias found for gain={grp.gain} -- dark will be unbiased")

        out = masters_dir / (grp.master_stem() + ".xisf")
        try:
            master = build_master_dark(
                grp, out,
                master_bias_path=bias_master,
                pi_exe=pi_exe,
                dry_run=dry_run,
            )
            dark_master_map[(grp.gain, grp.offset, round(grp.exptime, 3), grp.binning)] = master
            result["dark"].append(master)
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)

    # Also load pre-existing dark masters from bias_masters_dir
    if bias_masters_dir and bias_masters_dir.is_dir():
        for xisf in bias_masters_dir.glob("master_dark*.xisf"):
            stem = xisf.stem
            parts_d = {p.split("gain")[0]: p for p in stem.split("_") if "gain" in p}
            # crude: just register the file so flat stage can find it
            # more precise lookup is done in find_dark_master()
            pass

    # ── 3. Flat masters ────────────────────────────────────────────────────────
    print(f"\n=== FLAT MASTERS ({len(flat_groups)} groups) ===")

    # Determine where to look for bias/dark masters for flats
    search_dirs = [masters_dir]
    if bias_masters_dir and bias_masters_dir.is_dir():
        search_dirs.insert(0, bias_masters_dir)

    for key, grp in sorted(flat_groups.items(), key=lambda kv: str(kv[1].label)):
        print(f"\n  {grp.label}")

        # Find matching bias master
        bias_master = None
        for sdir in search_dirs:
            bias_master = find_bias_master(sdir, grp.gain, grp.offset, grp.binning)
            if bias_master:
                break
        if bias_master is None:
            print(f"  WARNING: no master bias found for gain={grp.gain}")

        # Find matching dark master (for very short flats, dark usually skipped)
        dark_master = None
        if grp.exptime > 1.0:   # only apply dark if flat exposure > 1 second
            for sdir in search_dirs:
                dark_master = find_dark_master(sdir, grp.gain, grp.offset, grp.exptime, grp.binning)
                if dark_master:
                    break

        if dark_master:
            print(f"  Using dark: {dark_master.name}")
        else:
            print(f"  No matching dark (exp={grp.exptime:.3f}s) -- flat calibrated with bias only")

        out = masters_dir / (grp.master_stem() + ".xisf")
        try:
            master = build_master_flat(
                grp, out,
                master_bias_path=bias_master,
                master_dark_path=dark_master,
                pi_exe=pi_exe,
                dry_run=dry_run,
            )
            result["flat"].append(master)
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n=== SUMMARY ===")
    for kind, paths in result.items():
        print(f"  {kind:<5}: {len(paths)} master(s)")
        for p in paths:
            status = "created" if p.exists() else ("planned" if dry_run else "MISSING")
            print(f"           {status}  {p.name}")

    return result


# =============================================================================
# CLI
# =============================================================================


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build PixInsight-format calibration masters from raw frame libraries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--scan-dir", required=True,
        help="Directory to scan for raw calibration frames (walked recursively).",
    )
    p.add_argument(
        "--masters-dir", required=True,
        help="Output directory where master .xisf files will be written.",
    )
    p.add_argument(
        "--bias-masters-dir", default=None,
        help="Extra directory to search for pre-existing bias/dark masters "
             "(used when scanning a target dir for flats).",
    )
    p.add_argument(
        "--pi-exe", default=None,
        help="Path to PixInsight executable. Falls back to PIXINSIGHT_EXE env var.",
    )
    p.add_argument(
        "--types", nargs="+", default=None,
        metavar="TYPE",
        help="Restrict to specific IMAGETYP values (e.g. Bias Dark Flat). "
             "Default: all types.",
    )
    p.add_argument(
        "--dry-run", "-n", action="store_true",
        help="Print the build plan without running PixInsight.",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    pi_exe = args.pi_exe or os.environ.get("PIXINSIGHT_EXE")
    if not pi_exe and not args.dry_run:
        print(
            "ERROR: --pi-exe is required (or set PIXINSIGHT_EXE env var).\n"
            "Use --dry-run to preview the build plan without PI.",
            file=sys.stderr,
        )
        sys.exit(1)

    scan_dir    = Path(args.scan_dir)
    masters_dir = Path(args.masters_dir)
    bias_dir    = Path(args.bias_masters_dir) if args.bias_masters_dir else None

    if not scan_dir.is_dir():
        print(f"ERROR: scan directory not found: {scan_dir}", file=sys.stderr)
        sys.exit(1)

    image_types = set(args.types) if args.types else None

    build_all_masters(
        scan_dir=scan_dir,
        masters_dir=masters_dir,
        bias_masters_dir=bias_dir,
        pi_exe=pi_exe,
        dry_run=args.dry_run,
        image_types=image_types,
    )


if __name__ == "__main__":
    main()
