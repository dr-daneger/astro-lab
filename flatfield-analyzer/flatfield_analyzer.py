#!/usr/bin/env python3
"""
Flat-Field Calibration Frame Analyzer

Analyzes a directory of FITS flat-field frames and produces a comprehensive
diagnostic report: per-frame statistics, median-stacked master flats,
spatial illumination analysis, ADU target assessment, and noise diagnostics.

Output plots are saved to a flatfield-report/ subdirectory next to the input
directory.  A concise diagnostic table is printed to stdout.

Dependencies: numpy, scipy, matplotlib, astropy (standard astronomy stack)

Usage:
    python flatfield_analyzer.py [/path/to/flats]

Author: Dane
"""

import csv
import sys
import os
import re
import argparse
import time
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from astropy.io import fits
from scipy.ndimage import uniform_filter
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

# ── defaults ────────────────────────────────────────────────────────────────
DEFAULT_FLATS_DIR = (
    r"C:\Users\Dane\Pictures\DSOs\01_nebulae"
    r"\NGC1499 - California Nebula\01-21-2026\flats"
)

ADU_UNDER = 20_000
ADU_TARGET_LO = 20_000
ADU_TARGET_HI = 35_000
ADU_OVER = 45_000

CORNER_SIZE = 100
CENTER_SIZE = 200
BORDER_FRAC = 0.05


# ── dataclasses ─────────────────────────────────────────────────────────────
@dataclass
class FrameStats:
    filename: str
    mean: float
    median: float
    std: float
    min: float
    max: float
    snr: float


@dataclass
class FilterGroup:
    filter_name: str
    files: List[Path] = field(default_factory=list)
    frame_stats: List[FrameStats] = field(default_factory=list)
    master: Optional[np.ndarray] = None
    # master-level diagnostics filled in later
    master_mean: float = 0.0
    master_median: float = 0.0
    master_std: float = 0.0
    master_min: float = 0.0
    master_max: float = 0.0
    corner_center_ratio: float = 0.0
    peak_valley_nonuniformity: float = 0.0
    centroid_offset_px: float = 0.0
    centroid_offset_arcsec: Optional[float] = None
    plate_scale: Optional[float] = None
    adu_status: str = ""
    shot_noise_expected: float = 0.0
    residual_fpn: float = 0.0
    noise_ratio: float = 0.0
    # spatial profiles (populated during analysis)
    centroid_row: float = 0.0
    centroid_col: float = 0.0
    radial_radii: Optional[np.ndarray] = None
    radial_profile: Optional[np.ndarray] = None
    row_profile: Optional[np.ndarray] = None
    col_profile: Optional[np.ndarray] = None


# ── helpers ─────────────────────────────────────────────────────────────────
def _filter_from_filename(name: str) -> str:
    """Best-effort filter extraction from ASIAIR-style filenames."""
    m = re.search(r"_Bin\d+_([A-Za-z]+)_", name)
    if m:
        return m.group(1)
    return "Unknown"


def _read_flat(path: Path) -> Tuple[np.ndarray, fits.Header]:
    """Read a FITS flat at full 16-bit depth (uint16 via BZERO)."""
    with fits.open(path) as hdul:
        data = hdul[0].data.astype(np.float64)
        header = hdul[0].header
    return data, header


def _plate_scale_from_header(header: fits.Header) -> Optional[float]:
    """Return plate scale in arcsec/pixel if derivable, else None."""
    # Direct keyword
    for key in ("SCALE", "CDELT1", "CDELT2", "SECPIX", "PIXSCALE"):
        val = header.get(key)
        if val is not None:
            return abs(float(val)) * (3600.0 if key.startswith("CDELT") else 1.0)
    # Derive from focal length + pixel size
    fl = header.get("FOCALLEN")
    px = header.get("XPIXSZ")
    binning = header.get("XBINNING", 1)
    if fl is not None and px is not None:
        fl_mm = float(fl)
        px_um = float(px) * int(binning)
        if fl_mm > 0:
            return (px_um / fl_mm) * 206.265  # arcsec/pixel
    return None


def _adu_status(mean_adu: float) -> str:
    if mean_adu < ADU_UNDER:
        return "UNDER"
    elif mean_adu <= ADU_TARGET_HI:
        return "OK"
    elif mean_adu > ADU_OVER:
        return "OVER"
    else:
        return "HIGH"


def _illumination_centroid(img: np.ndarray) -> Tuple[float, float]:
    """Weighted centroid of the illumination pattern (row, col)."""
    # Light smooth to suppress pixel noise
    smoothed = uniform_filter(img, size=50)
    total = smoothed.sum()
    rows = np.arange(smoothed.shape[0])
    cols = np.arange(smoothed.shape[1])
    row_c = (smoothed.sum(axis=1) * rows).sum() / total
    col_c = (smoothed.sum(axis=0) * cols).sum() / total
    return row_c, col_c


def _radial_profile(img: np.ndarray, center: Tuple[float, float],
                    n_bins: int = 200) -> Tuple[np.ndarray, np.ndarray]:
    """Azimuthally averaged radial profile from *center* (row, col)."""
    rows, cols = np.indices(img.shape)
    r = np.sqrt((rows - center[0]) ** 2 + (cols - center[1]) ** 2)
    r_int = r.astype(int)
    max_r = min(r_int.max(), int(np.sqrt(img.shape[0]**2 + img.shape[1]**2) / 2))
    bin_edges = np.linspace(0, max_r, n_bins + 1)
    profile = np.zeros(n_bins)
    for i in range(n_bins):
        mask = (r >= bin_edges[i]) & (r < bin_edges[i + 1])
        if mask.any():
            profile[i] = img[mask].mean()
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    return bin_centers, profile


def _corner_center_ratio(img: np.ndarray) -> float:
    h, w = img.shape
    cs = CORNER_SIZE
    corners = [
        img[:cs, :cs],
        img[:cs, w - cs:],
        img[h - cs:, :cs],
        img[h - cs:, w - cs:],
    ]
    corner_mean = np.mean([c.mean() for c in corners])
    cy, cx = h // 2, w // 2
    hs = CENTER_SIZE // 2
    center_mean = img[cy - hs : cy + hs, cx - hs : cx + hs].mean()
    return corner_mean / center_mean if center_mean != 0 else 0.0


def _peak_valley_nonuniformity(img: np.ndarray) -> float:
    """(max - min) / mean excluding outer 5% border."""
    h, w = img.shape
    bh = int(h * BORDER_FRAC)
    bw = int(w * BORDER_FRAC)
    interior = img[bh : h - bh, bw : w - bw]
    mean_val = interior.mean()
    if mean_val == 0:
        return 0.0
    return (interior.max() - interior.min()) / mean_val


# ── core analysis ───────────────────────────────────────────────────────────
def discover_files(flats_dir: Path) -> Dict[str, FilterGroup]:
    """Read all FITS in *flats_dir*, group by FILTER keyword."""
    groups: Dict[str, FilterGroup] = {}
    all_files: List[Path] = []
    for fp in flats_dir.iterdir():
        if fp.suffix.lower() in (".fit", ".fits", ".fts"):
            all_files.append(fp)
    all_files.sort()

    if not all_files:
        print(f"[ERROR] No FITS files found in {flats_dir}")
        sys.exit(1)

    print(f"Found {len(all_files)} FITS files")

    for fp in all_files:
        try:
            header = fits.getheader(fp, 0)
        except Exception as e:
            print(f"  skip {fp.name}: {e}")
            continue
        filt = header.get("FILTER")
        if filt is None:
            filt = _filter_from_filename(fp.name)
        filt = str(filt).strip()
        if filt not in groups:
            groups[filt] = FilterGroup(filter_name=filt)
        groups[filt].files.append(fp)

    for g in groups.values():
        print(f"  Filter {g.filter_name}: {len(g.files)} frames")
    return groups


def compute_per_frame_stats(group: FilterGroup) -> None:
    print(f"  Computing per-frame stats for filter {group.filter_name}...")
    for fp in group.files:
        data, _ = _read_flat(fp)
        mean_val = data.mean()
        std_val = data.std()
        group.frame_stats.append(
            FrameStats(
                filename=fp.name,
                mean=mean_val,
                median=float(np.median(data)),
                std=std_val,
                min=float(data.min()),
                max=float(data.max()),
                snr=mean_val / std_val if std_val > 0 else 0.0,
            )
        )


def build_master(group: FilterGroup) -> None:
    """Median-stack all frames into a master flat (row-chunked for memory)."""
    n = len(group.files)
    print(f"  Stacking {n} frames for filter {group.filter_name}...")
    # Read first frame to get shape
    first, _ = _read_flat(group.files[0])
    h, w = first.shape
    master = np.empty((h, w), dtype=np.float64)

    # Process in row-chunks to keep memory bounded
    chunk_rows = max(1, min(256, h))
    for r0 in range(0, h, chunk_rows):
        r1 = min(r0 + chunk_rows, h)
        slab = np.empty((n, r1 - r0, w), dtype=np.float32)
        for i, fp in enumerate(group.files):
            with fits.open(fp) as hdul:
                slab[i] = hdul[0].data[r0:r1, :].astype(np.float32)
        master[r0:r1, :] = np.median(slab, axis=0)
        del slab

    group.master = master


def analyse_master(group: FilterGroup, sample_header: fits.Header) -> None:
    """Run all spatial / noise diagnostics on the master flat."""
    m = group.master
    group.master_mean = m.mean()
    group.master_median = float(np.median(m))
    group.master_std = m.std()
    group.master_min = float(m.min())
    group.master_max = float(m.max())

    # ADU assessment
    group.adu_status = _adu_status(group.master_mean)

    # Illumination centroid & offset
    centroid = _illumination_centroid(m)
    group.centroid_row, group.centroid_col = centroid
    img_center = (m.shape[0] / 2.0, m.shape[1] / 2.0)
    dy = centroid[0] - img_center[0]
    dx = centroid[1] - img_center[1]
    group.centroid_offset_px = np.sqrt(dy**2 + dx**2)

    ps = _plate_scale_from_header(sample_header)
    group.plate_scale = ps
    if ps is not None:
        group.centroid_offset_arcsec = group.centroid_offset_px * ps

    # Corner-to-center
    group.corner_center_ratio = _corner_center_ratio(m)

    # Peak-to-valley
    group.peak_valley_nonuniformity = _peak_valley_nonuniformity(m)

    # Noise analysis
    group.residual_fpn = group.master_std
    group.shot_noise_expected = np.sqrt(group.master_mean) if group.master_mean > 0 else 0
    group.noise_ratio = (
        group.residual_fpn / group.shot_noise_expected
        if group.shot_noise_expected > 0
        else 0.0
    )

    # Spatial profiles (stored for CSV export)
    norm_master = m / m.max()
    group.radial_radii, group.radial_profile = _radial_profile(norm_master, centroid)
    group.row_profile = np.median(m, axis=1)
    group.col_profile = np.median(m, axis=0)


# ── plotting ────────────────────────────────────────────────────────────────
def _cmap():
    return "inferno"


def plot_filter_report(group: FilterGroup, sample_header: fits.Header,
                       out_dir: Path) -> None:
    """Multi-panel diagnostic figure for one filter."""
    m = group.master
    norm_master = m / m.max()
    centroid = (group.centroid_row, group.centroid_col)
    img_center = (m.shape[0] / 2.0, m.shape[1] / 2.0)
    radii, profile = group.radial_radii, group.radial_profile

    fig = plt.figure(figsize=(20, 16))
    fig.suptitle(
        f"Flat-Field Diagnostic  —  Filter: {group.filter_name}  "
        f"({len(group.files)} frames)",
        fontsize=16,
        fontweight="bold",
    )
    gs = GridSpec(3, 3, figure=fig, hspace=0.35, wspace=0.30)

    # ── panel 1: illumination heatmap ────────────────────────────────────
    ax1 = fig.add_subplot(gs[0, 0:2])
    im = ax1.imshow(norm_master, cmap=_cmap(), origin="lower", aspect="auto")
    ax1.plot(centroid[1], centroid[0], "c+", ms=14, mew=2, label="Centroid")
    ax1.plot(img_center[1], img_center[0], "wx", ms=10, mew=2, label="Img center")
    ax1.set_title("Normalized Illumination (linear)")
    ax1.legend(loc="upper right", fontsize=8)
    plt.colorbar(im, ax=ax1, fraction=0.046, pad=0.04)

    # ── panel 2: radial profile ──────────────────────────────────────────
    ax2 = fig.add_subplot(gs[0, 2])
    ax2.plot(radii, profile, "k-", linewidth=1)
    ax2.set_xlabel("Radius from centroid (px)")
    ax2.set_ylabel("Normalized intensity")
    ax2.set_title("Radial Profile")
    ax2.grid(True, alpha=0.3)

    # ── panel 3: row median profile ──────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 0])
    row_profile = group.row_profile
    ax3.plot(row_profile, np.arange(m.shape[0]), "b-", linewidth=0.5)
    ax3.set_xlabel("Median ADU")
    ax3.set_ylabel("Row")
    ax3.set_title("Row Profile (horiz gradient check)")
    ax3.invert_yaxis()
    ax3.grid(True, alpha=0.3)

    # ── panel 4: column median profile ───────────────────────────────────
    ax4 = fig.add_subplot(gs[1, 1])
    col_profile = group.col_profile
    ax4.plot(np.arange(m.shape[1]), col_profile, "r-", linewidth=0.5)
    ax4.set_xlabel("Column")
    ax4.set_ylabel("Median ADU")
    ax4.set_title("Column Profile (vert gradient check)")
    ax4.grid(True, alpha=0.3)

    # ── panel 5: per-frame mean scatter ──────────────────────────────────
    ax5 = fig.add_subplot(gs[1, 2])
    means = [s.mean for s in group.frame_stats]
    ax5.plot(range(1, len(means) + 1), means, "ko-", ms=3, linewidth=0.8)
    ax5.axhline(ADU_TARGET_LO, color="orange", ls="--", lw=0.8, label=f"{ADU_TARGET_LO/1e3:.0f}k")
    ax5.axhline(ADU_TARGET_HI, color="orange", ls="--", lw=0.8, label=f"{ADU_TARGET_HI/1e3:.0f}k")
    ax5.axhline(ADU_OVER, color="red", ls="--", lw=0.8, label=f"{ADU_OVER/1e3:.0f}k")
    ax5.set_xlabel("Frame #")
    ax5.set_ylabel("Mean ADU")
    ax5.set_title("Per-Frame Mean ADU")
    ax5.legend(fontsize=7)
    ax5.grid(True, alpha=0.3)

    # ── panel 6: histogram of per-frame SNR ──────────────────────────────
    ax6 = fig.add_subplot(gs[2, 0])
    snrs = [s.snr for s in group.frame_stats]
    ax6.hist(snrs, bins=max(5, len(snrs) // 3), color="steelblue", edgecolor="k")
    ax6.set_xlabel("SNR (mean/std)")
    ax6.set_ylabel("Count")
    ax6.set_title("Per-Frame SNR Distribution")
    ax6.grid(True, alpha=0.3)

    # ── panel 7: master flat histogram ───────────────────────────────────
    ax7 = fig.add_subplot(gs[2, 1])
    ax7.hist(m.ravel(), bins=200, color="gray", edgecolor="none", log=True)
    ax7.axvline(group.master_mean, color="red", lw=1.5, label=f"mean={group.master_mean:.0f}")
    ax7.set_xlabel("ADU")
    ax7.set_ylabel("Pixel count (log)")
    ax7.set_title("Master Flat ADU Distribution")
    ax7.legend(fontsize=8)
    ax7.grid(True, alpha=0.3)

    # ── panel 8: text summary ────────────────────────────────────────────
    ax8 = fig.add_subplot(gs[2, 2])
    ax8.axis("off")
    offset_str = f"{group.centroid_offset_px:.1f} px"
    if group.centroid_offset_arcsec is not None:
        offset_str += f" / {group.centroid_offset_arcsec:.1f}\""
    else:
        offset_str += " (plate scale unknown)"

    status_color = {"OK": "green", "UNDER": "orange", "OVER": "red", "HIGH": "goldenrod"}
    noise_verdict = "OK" if group.noise_ratio < 1.5 else "EXCESS"

    lines = [
        f"Filter: {group.filter_name}",
        f"Frames stacked: {len(group.files)}",
        f"Master mean: {group.master_mean:.1f} ADU",
        f"Master median: {group.master_median:.1f} ADU",
        f"Master std: {group.master_std:.1f} ADU",
        f"ADU status: {group.adu_status}",
        f"Corner/center ratio: {group.corner_center_ratio:.4f}",
        f"Peak-valley non-unif: {group.peak_valley_nonuniformity:.4f}",
        f"Centroid offset: {offset_str}",
        f"Expected shot noise: {group.shot_noise_expected:.1f} ADU",
        f"Residual FPN (std): {group.residual_fpn:.1f} ADU",
        f"Noise ratio (FPN/shot): {group.noise_ratio:.2f}  [{noise_verdict}]",
    ]
    if group.plate_scale is not None:
        lines.insert(1, f"Plate scale: {group.plate_scale:.3f} \"/px")

    ax8.text(
        0.05, 0.95,
        "\n".join(lines),
        transform=ax8.transAxes,
        fontsize=10,
        verticalalignment="top",
        fontfamily="monospace",
        bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
    )

    out_path = out_dir / f"flat_report_{group.filter_name}.png"
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path.name}")


def plot_summary(groups: Dict[str, FilterGroup], out_dir: Path) -> None:
    """Cross-filter comparison figure."""
    names = sorted(groups.keys())
    n = len(names)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Flat-Field Summary — All Filters", fontsize=15, fontweight="bold")

    # ── mean ADU bar chart ───────────────────────────────────────────────
    ax = axes[0, 0]
    means = [groups[f].master_mean for f in names]
    colors = []
    for f in names:
        s = groups[f].adu_status
        colors.append({"OK": "green", "UNDER": "orange", "OVER": "red", "HIGH": "goldenrod"}.get(s, "gray"))
    ax.bar(names, means, color=colors, edgecolor="k")
    ax.axhline(ADU_TARGET_LO, color="orange", ls="--", lw=0.8)
    ax.axhline(ADU_TARGET_HI, color="orange", ls="--", lw=0.8)
    ax.axhline(ADU_OVER, color="red", ls="--", lw=0.8)
    ax.set_ylabel("Mean ADU")
    ax.set_title("Master Flat Mean ADU")
    ax.grid(axis="y", alpha=0.3)

    # ── corner/center ratio ──────────────────────────────────────────────
    ax = axes[0, 1]
    ratios = [groups[f].corner_center_ratio for f in names]
    ax.bar(names, ratios, color="steelblue", edgecolor="k")
    ax.axhline(1.0, color="gray", ls="--", lw=0.8)
    ax.set_ylabel("Corner / Center")
    ax.set_title("Vignetting (corner-to-center ratio)")
    ax.grid(axis="y", alpha=0.3)

    # ── non-uniformity ───────────────────────────────────────────────────
    ax = axes[1, 0]
    nu = [groups[f].peak_valley_nonuniformity for f in names]
    ax.bar(names, nu, color="salmon", edgecolor="k")
    ax.set_ylabel("(max-min)/mean")
    ax.set_title("Peak-to-Valley Non-Uniformity")
    ax.grid(axis="y", alpha=0.3)

    # ── noise ratio ──────────────────────────────────────────────────────
    ax = axes[1, 1]
    nr = [groups[f].noise_ratio for f in names]
    bar_colors = ["green" if r < 1.5 else "red" for r in nr]
    ax.bar(names, nr, color=bar_colors, edgecolor="k")
    ax.axhline(1.0, color="gray", ls="--", lw=0.8, label="Photon-limited")
    ax.axhline(1.5, color="orange", ls="--", lw=0.8, label="Excess threshold")
    ax.set_ylabel("FPN / Shot Noise")
    ax.set_title("Residual Noise Ratio")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out_path = out_dir / "flat_summary_all_filters.png"
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {out_path.name}")


# ── stdout table ────────────────────────────────────────────────────────────
def print_diagnostic_table(groups: Dict[str, FilterGroup]) -> None:
    names = sorted(groups.keys())

    hdr = (
        f"{'Filt':>5} {'N':>3} {'Mean':>8} {'Med':>8} {'Std':>7} "
        f"{'SNR':>6} {'ADU':>5} {'Corn/Ctr':>8} {'PV-NU':>7} "
        f"{'Cen-Off':>8} {'ShotN':>7} {'FPN':>7} {'N-Rat':>6}"
    )
    sep = "-" * len(hdr)

    print("\n" + sep)
    print("  FLAT-FIELD DIAGNOSTIC SUMMARY")
    print(sep)
    print(hdr)
    print(sep)

    for f in names:
        g = groups[f]
        off = f"{g.centroid_offset_px:.1f}px"
        if g.centroid_offset_arcsec is not None:
            off = f'{g.centroid_offset_arcsec:.1f}"'
        print(
            f"{g.filter_name:>5} {len(g.files):>3} "
            f"{g.master_mean:>8.0f} {g.master_median:>8.0f} {g.master_std:>7.1f} "
            f"{g.master_mean / g.master_std if g.master_std > 0 else 0:>6.1f} "
            f"{g.adu_status:>5} "
            f"{g.corner_center_ratio:>8.4f} "
            f"{g.peak_valley_nonuniformity:>7.4f} "
            f"{off:>8} "
            f"{g.shot_noise_expected:>7.1f} "
            f"{g.residual_fpn:>7.1f} "
            f"{g.noise_ratio:>6.2f}"
        )

    print(sep)

    # Per-frame detail mini-table
    print("\n  PER-FRAME STATISTICS (mean +/- std across frames)")
    print(f"  {'Filt':>5} {'Mean ADU':>12} {'Median':>12} {'Std':>10} {'SNR':>10} {'Min':>8} {'Max':>8}")
    print("  " + "-" * 70)
    for f in names:
        g = groups[f]
        fm = np.array([s.mean for s in g.frame_stats])
        fd = np.array([s.median for s in g.frame_stats])
        fs = np.array([s.std for s in g.frame_stats])
        fn = np.array([s.snr for s in g.frame_stats])
        fmin = np.array([s.min for s in g.frame_stats])
        fmax = np.array([s.max for s in g.frame_stats])
        print(
            f"  {g.filter_name:>5} "
            f"{fm.mean():>7.0f}+/-{fm.std():>4.0f} "
            f"{fd.mean():>7.0f}+/-{fd.std():>4.0f} "
            f"{fs.mean():>5.0f}+/-{fs.std():>3.0f} "
            f"{fn.mean():>5.1f}+/-{fn.std():>3.1f} "
            f"{fmin.min():>8.0f} "
            f"{fmax.max():>8.0f}"
        )
    print()


# ── CSV / FITS exports ──────────────────────────────────────────────────────
def save_per_frame_csv(groups: Dict[str, FilterGroup], out_dir: Path) -> None:
    """One row per individual flat frame."""
    path = out_dir / "per_frame_stats.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filter", "filename", "mean", "median", "std", "min", "max", "snr"])
        for filt in sorted(groups):
            for s in groups[filt].frame_stats:
                w.writerow([
                    filt, s.filename,
                    f"{s.mean:.2f}", f"{s.median:.2f}", f"{s.std:.2f}",
                    f"{s.min:.2f}", f"{s.max:.2f}", f"{s.snr:.2f}",
                ])
    print(f"  Saved {path.name}")


def save_filter_summary_csv(groups: Dict[str, FilterGroup], out_dir: Path) -> None:
    """One row per filter with all master-level diagnostics."""
    path = out_dir / "filter_summary.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "filter", "n_frames",
            "master_mean", "master_median", "master_std", "master_min", "master_max",
            "adu_status", "corner_center_ratio", "peak_valley_nonuniformity",
            "centroid_row_px", "centroid_col_px",
            "centroid_offset_px", "centroid_offset_arcsec",
            "plate_scale_arcsec_px",
            "shot_noise_expected", "residual_fpn", "noise_ratio",
        ])
        for filt in sorted(groups):
            g = groups[filt]
            w.writerow([
                filt, len(g.files),
                f"{g.master_mean:.2f}", f"{g.master_median:.2f}",
                f"{g.master_std:.2f}", f"{g.master_min:.2f}", f"{g.master_max:.2f}",
                g.adu_status, f"{g.corner_center_ratio:.6f}",
                f"{g.peak_valley_nonuniformity:.6f}",
                f"{g.centroid_row:.2f}", f"{g.centroid_col:.2f}",
                f"{g.centroid_offset_px:.2f}",
                f"{g.centroid_offset_arcsec:.2f}" if g.centroid_offset_arcsec is not None else "",
                f"{g.plate_scale:.4f}" if g.plate_scale is not None else "",
                f"{g.shot_noise_expected:.2f}", f"{g.residual_fpn:.2f}",
                f"{g.noise_ratio:.4f}",
            ])
    print(f"  Saved {path.name}")


def save_spatial_profiles_csv(groups: Dict[str, FilterGroup], out_dir: Path) -> None:
    """Radial, row, and column profiles for each filter — one CSV per filter."""
    profiles_dir = out_dir / "spatial_profiles"
    profiles_dir.mkdir(exist_ok=True)

    for filt in sorted(groups):
        g = groups[filt]

        # Radial profile
        path = profiles_dir / f"radial_profile_{filt}.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["radius_px", "normalized_intensity"])
            for r, v in zip(g.radial_radii, g.radial_profile):
                w.writerow([f"{r:.2f}", f"{v:.6f}"])

        # Row profile (median ADU collapsed along columns)
        path = profiles_dir / f"row_profile_{filt}.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["row", "median_adu"])
            for i, v in enumerate(g.row_profile):
                w.writerow([i, f"{v:.2f}"])

        # Column profile (median ADU collapsed along rows)
        path = profiles_dir / f"col_profile_{filt}.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["col", "median_adu"])
            for i, v in enumerate(g.col_profile):
                w.writerow([i, f"{v:.2f}"])

    print(f"  Saved spatial profiles to {profiles_dir.name}/")


def save_master_fits(group: FilterGroup, sample_header: fits.Header,
                     out_dir: Path) -> None:
    """Write the median-stacked master flat as a FITS file with diagnostic headers."""
    masters_dir = out_dir / "master_flats"
    masters_dir.mkdir(exist_ok=True)

    hdr = sample_header.copy()
    # Add analysis metadata
    hdr["IMAGETYP"] = "Master Flat"
    hdr["NCOMBINE"] = (len(group.files), "number of frames median-stacked")
    hdr["MSTR_MN"] = (round(group.master_mean, 2), "master mean ADU")
    hdr["MSTR_MD"] = (round(group.master_median, 2), "master median ADU")
    hdr["MSTR_SD"] = (round(group.master_std, 2), "master std ADU")
    hdr["CORNCTRT"] = (round(group.corner_center_ratio, 6), "corner-to-center ratio")
    hdr["PV_NONUN"] = (round(group.peak_valley_nonuniformity, 6), "(max-min)/mean non-uniformity")
    hdr["CENT_ROW"] = (round(group.centroid_row, 2), "illumination centroid row px")
    hdr["CENT_COL"] = (round(group.centroid_col, 2), "illumination centroid col px")
    hdr["CENTOFFP"] = (round(group.centroid_offset_px, 2), "centroid offset from center px")
    if group.centroid_offset_arcsec is not None:
        hdr["CENTOFFS"] = (round(group.centroid_offset_arcsec, 2), "centroid offset arcsec")
    hdr["SHOTNOIS"] = (round(group.shot_noise_expected, 2), "expected shot noise ADU")
    hdr["RES_FPN"] = (round(group.residual_fpn, 2), "residual fixed-pattern noise ADU")
    hdr["NOISERAT"] = (round(group.noise_ratio, 4), "FPN / shot noise ratio")
    hdr["ADUSTAT"] = (group.adu_status, "ADU target assessment")
    # Record constituent filenames (up to 50 to avoid header overflow)
    for i, fp in enumerate(group.files[:50]):
        hdr[f"FLAT{i:04d}"] = (fp.stem[:60], f"flat #{i+1}")

    path = masters_dir / f"master_flat_{group.filter_name}.fits"
    hdu = fits.PrimaryHDU(data=group.master.astype(np.float32), header=hdr)
    hdu.writeto(path, overwrite=True)
    print(f"  Saved {path.name}")


# ── main ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze calibration flat frames and produce a diagnostic report."
    )
    parser.add_argument(
        "flats_dir",
        nargs="?",
        default=DEFAULT_FLATS_DIR,
        help="Directory containing flat-frame FITS files",
    )
    args = parser.parse_args()

    flats_dir = Path(args.flats_dir)
    if not flats_dir.is_dir():
        print(f"[ERROR] Not a directory: {flats_dir}")
        sys.exit(1)

    out_dir = flats_dir.parent / "flatfield-report"
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    print(f"\n=== Flat-Field Analyzer ===")
    print(f"Input : {flats_dir}")
    print(f"Output: {out_dir}\n")

    # 1. Discover & group
    groups = discover_files(flats_dir)

    # 2. Per-filter analysis
    sample_headers: Dict[str, fits.Header] = {}
    for filt, group in sorted(groups.items()):
        _, sample_header = _read_flat(group.files[0])
        sample_headers[filt] = sample_header

        compute_per_frame_stats(group)
        build_master(group)
        analyse_master(group, sample_header)
        plot_filter_report(group, sample_header, out_dir)
        save_master_fits(group, sample_header, out_dir)

    # 3. Cross-filter summary
    plot_summary(groups, out_dir)

    # 4. CSV exports
    print("\nExporting CSVs...")
    save_per_frame_csv(groups, out_dir)
    save_filter_summary_csv(groups, out_dir)
    save_spatial_profiles_csv(groups, out_dir)

    # 5. Console output
    print_diagnostic_table(groups)

    elapsed = time.time() - t0
    print(f"Completed in {elapsed:.1f}s.  Reports saved to {out_dir}\n")


if __name__ == "__main__":
    main()
