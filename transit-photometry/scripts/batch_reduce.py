#!/usr/bin/env python3
"""Batch reduction of AIJ CSVs into dataset-local summaries and plots."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from transit_model import FitResult, fit_numeric_ma


DAY_SECONDS = 86400.0


@dataclass
class ProcessedCurve:
    path: Path
    r_ap: float
    r_in: float
    r_out: float
    time_jd: np.ndarray
    flux: np.ndarray
    sem: np.ndarray
    wrms_oot: float
    fit: FitResult
    sg: np.ndarray


def parse_radii_from_name(name: str) -> Tuple[float, float, float]:
    import re

    match = re.search(r"Ap(\d+(?:-\d+)?)_In(\d+(?:-\d+)?)_Out(\d+(?:-\d+)?)", name)
    if not match:
        return (math.nan, math.nan, math.nan)
    to_float = lambda token: float(token.replace("-", ".")) if token else math.nan
    return tuple(to_float(match.group(i)) for i in range(1, 4))


def utc_to_jd(year: int, month: int, day: int, hour: int, minute: int, second: float) -> float:
    if month <= 2:
        year -= 1
        month += 12
    A = year // 100
    B = 2 - A + (A // 4)
    day_fraction = (hour + (minute + second / 60.0) / 60.0) / 24.0
    jd = (int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + day_fraction + B - 1524.5)
    return float(jd)


def parse_time_string(value: str) -> float:
    text = (value or "").strip()
    if not text:
        raise ValueError("Empty ephemeris time string")
    if text.endswith("Z"):
        text = text[:-1]
    text = text.replace("T", " ")
    parts = text.split()
    if len(parts) != 2:
        raise ValueError(f"Unexpected ephemeris format: {value}")
    year, month, day = (int(tok) for tok in parts[0].split("-"))
    time_bits = parts[1].split(":")
    while len(time_bits) < 3:
        time_bits.append("0")
    hour = int(time_bits[0])
    minute = int(time_bits[1])
    second = float(time_bits[2])
    return utc_to_jd(year, month, day, hour, minute, second)


def load_ephemeris(explicit: Optional[str], dataset: Path) -> Tuple[float, float, float]:
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    else:
        dataset_override = dataset / "ephemeris.yaml"
        if dataset_override.exists():
            candidates.append(dataset_override)
        candidates.append(dataset.parents[0] / "config" / "target.yaml")

    for candidate in candidates:
        if not candidate.exists():
            continue
        data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        eph = data["ephemeris_utc"] if "ephemeris_utc" in data else data
        ingress_key = "ingress_utc" if "ingress_utc" in eph else "ingress"
        mid_key = "mid_utc" if "mid_utc" in eph else "mid"
        egress_key = "egress_utc" if "egress_utc" in eph else "egress"
        try:
            ingress = parse_time_string(eph[ingress_key])
            mid = parse_time_string(eph[mid_key])
            egress = parse_time_string(eph[egress_key])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Invalid ephemeris in {candidate}: {exc}") from exc
        return ingress, mid, egress

    raise FileNotFoundError("No ephemeris information found (checked dataset and config/target.yaml)")


def choose_time_column(df: pd.DataFrame, choice: str) -> str:
    if choice != "auto":
        if choice not in df.columns:
            raise KeyError(f"Time column '{choice}' not present in CSV")
        return choice
    for candidate in ("JD_UTC", "BJD_TDB"):
        if candidate in df.columns:
            return candidate
    raise KeyError("No JD_UTC or BJD_TDB column found; specify --time-col explicitly")


def choose_flux_column(df: pd.DataFrame, choice: str) -> str:
    if choice != "auto":
        if choice not in df.columns:
            raise KeyError(f"Flux column '{choice}' not present in CSV")
        return choice
    preferred = "rel_flux_T1"
    if preferred in df.columns:
        return preferred
    for column in df.columns:
        if column.startswith("rel_flux_"):
            return column
    raise KeyError("No rel_flux_* columns found; specify --flux-col explicitly")


def detrend_flux(time_jd: np.ndarray, flux: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    if time_jd.size < 3:
        return flux.copy(), np.ones_like(flux)
    tmin, tmax = float(np.min(time_jd)), float(np.max(time_jd))
    window = tmax - tmin
    if window <= 0:
        return flux.copy(), np.ones_like(flux)
    lower = tmin + 0.3 * window
    upper = tmin + 0.7 * window
    oot_mask = (time_jd < lower) | (time_jd > upper)
    if not np.any(oot_mask):
        oot_mask = np.ones_like(time_jd, dtype=bool)
    A = np.vstack([np.ones(np.sum(oot_mask)), time_jd[oot_mask]]).T
    coef, _, _, _ = np.linalg.lstsq(A, flux[oot_mask], rcond=None)
    trend = coef[0] + coef[1] * time_jd
    trend[np.abs(trend) < 1e-9] = 1.0
    return flux / trend, trend


def bin_series(time_jd: np.ndarray, flux: np.ndarray, bin_sec: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    if time_jd.size == 0:
        return np.array([]), np.array([]), np.array([])
    t0 = time_jd.min()
    bins = np.floor((time_jd - t0) * DAY_SECONDS / bin_sec).astype(int)
    unique_bins = np.unique(bins)
    t_bin: List[float] = []
    f_bin: List[float] = []
    s_bin: List[float] = []
    for b in unique_bins:
        mask = bins == b
        data = flux[mask]
        finite = np.isfinite(data)
        if not np.any(finite):
            continue
        tb = time_jd[mask][finite]
        fb = data[finite]
        mean = float(np.mean(fb))
        t_bin.append(float(np.mean(tb)))
        f_bin.append(mean)
        if fb.size > 1:
            sem = float(np.nanstd(fb, ddof=1) / math.sqrt(fb.size))
        else:
            sem = math.nan
        s_bin.append(sem)
    return np.array(t_bin), np.array(f_bin), np.array(s_bin)


def sg_like(time_jd: np.ndarray, flux: np.ndarray, window: int = 7, order: int = 2) -> np.ndarray:
    n = flux.size
    if n == 0:
        return np.array([])
    window = max(window, order + 3)
    if window % 2 == 0:
        window += 1
    window = min(window, n if n % 2 == 1 else n - 1 if n > 2 else n)
    if window < order + 2:
        window = order + 2
    half = window // 2
    smoothed = np.zeros_like(flux)
    for idx in range(n):
        lo = max(0, idx - half)
        hi = min(n, idx + half + 1)
        segment_time = time_jd[lo:hi]
        segment_flux = flux[lo:hi]
        deg = min(order, segment_time.size - 1)
        if deg <= 0:
            smoothed[idx] = segment_flux.mean()
        else:
            coeff = np.polyfit(segment_time, segment_flux, deg)
            smoothed[idx] = np.polyval(coeff, time_jd[idx])
    return smoothed


def compute_wrms(time_jd: np.ndarray, flux: np.ndarray, sem: np.ndarray, ingress_jd: float, egress_jd: float) -> float:
    if flux.size == 0:
        return math.nan
    oot = (time_jd < ingress_jd) | (time_jd > egress_jd)
    if not np.any(oot):
        return math.nan
    weights = np.ones_like(flux)
    finite_sem = np.isfinite(sem) & (sem > 0)
    weights[finite_sem] = 1.0 / (sem[finite_sem] ** 2)
    weights[~np.isfinite(weights)] = 0.0
    sel_weights = weights[oot]
    sel_flux = flux[oot]
    if np.sum(sel_weights) <= 0:
        return math.nan
    mean = float(np.sum(sel_weights * sel_flux) / np.sum(sel_weights))
    wrms = float(np.sqrt(np.sum(sel_weights * (sel_flux - mean) ** 2) / np.sum(sel_weights)))
    return wrms


def render_curve(
    curve: ProcessedCurve,
    out_main: Path,
    out_res: Path,
    ingress_jd: float,
    mid_jd: float,
    egress_jd: float,
    residual_baseline: str,
    title: str,
) -> None:
    x_minutes = (curve.time_jd - mid_jd) * 1440.0
    ingress_min = (ingress_jd - mid_jd) * 1440.0
    egress_min = (egress_jd - mid_jd) * 1440.0
    fit_curve = curve.fit.yhat
    sg_curve = curve.sg

    plt.figure(figsize=(10.5, 5.2))
    ax = plt.gca()
    ax.axvspan(ingress_min, egress_min, color="C7", alpha=0.12)
    for marker in (ingress_min, 0.0, egress_min):
        ax.axvline(marker, ls="--", lw=1.0, color="0.2", alpha=0.7)
    ax.errorbar(
        x_minutes,
        curve.flux,
        yerr=curve.sem,
        fmt="o",
        ms=5,
        alpha=0.9,
        capsize=4,
        elinewidth=1.0,
        label="10-min mean ± SEM",
    )
    ax.plot(x_minutes, fit_curve, "-", lw=2.4, label="Transit model")
    ax.plot(x_minutes, sg_curve, "-", lw=1.6, label="Savitzky-like smooth")
    tick_min = math.floor(min(x_minutes.min(), ingress_min) / 30.0) * 30.0
    tick_max = math.ceil(max(x_minutes.max(), egress_min) / 30.0) * 30.0
    ticks = np.arange(tick_min, tick_max + 1, 30.0)
    ax.set_xticks(ticks)
    ax.set_xlabel("Minutes from mid-transit (UTC)")
    ax.set_ylabel("Relative flux")
    ax.set_title(title)
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(1.1)
    ax.tick_params(direction="in", top=True, right=True)
    ax.grid(True, ls="--", lw=0.8, alpha=0.7)
    ax.legend(loc="best")
    plt.tight_layout()
    plt.savefig(out_main, dpi=240)
    plt.close()

    baseline = sg_curve if residual_baseline == "sg" else fit_curve
    residuals = curve.flux - baseline
    plt.figure(figsize=(10.5, 3.2))
    ax = plt.gca()
    ax.axhline(0.0, color="0.2", lw=1.1)
    ax.errorbar(
        x_minutes,
        residuals,
        yerr=curve.sem,
        fmt="o",
        ms=4,
        alpha=0.9,
        capsize=4,
        elinewidth=1.0,
    )
    ax.set_xticks(ticks)
    ax.set_xlabel("Minutes from mid-transit (UTC)")
    ax.set_ylabel("Residual")
    ax.set_title(f"Residuals — {title}")
    for spine in ax.spines.values():
        spine.set_color("black")
        spine.set_linewidth(1.1)
    ax.tick_params(direction="in", top=True, right=True)
    ax.grid(True, ls="--", lw=0.8, alpha=0.7)
    plt.tight_layout()
    plt.savefig(out_res, dpi=240)
    plt.close()


def export_log_json(path: Path, curve: ProcessedCurve, bins: int) -> None:
    data = {
        "rprs": curve.fit.rprs,
        "impact_parameter": curve.fit.impact_parameter,
        "t0_offset_min": curve.fit.mid_offset_min,
        "chi2": curve.fit.chi2,
        "oot_wrms": curve.wrms_oot,
        "n_bins": bins,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Batch reduce AIJ CSV photometry for a dataset",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dataset", required=True, help="Dataset directory to process")
    parser.add_argument("--bin-sec", type=float, default=600.0, help="Bin size in seconds")
    parser.add_argument(
        "--use-ephemeris",
        help="Optional ephemeris override (path). Defaults to dataset/ephemeris.yaml or config/target.yaml",
    )
    parser.add_argument(
        "--time-col",
        choices=("JD_UTC", "BJD_TDB", "auto"),
        default="auto",
        help="Time column to consume from AIJ CSV",
    )
    parser.add_argument(
        "--flux-col",
        default="auto",
        help="Flux column to consume (default rel_flux_T1 or first rel_flux_*)",
    )
    parser.add_argument(
        "--residual-baseline",
        choices=("model", "sg"),
        default="model",
        help="Reference curve for residual plots",
    )
    parser.add_argument(
        "--export-log-json",
        nargs="?",
        const="outputs/run_log.json",
        help="Optional JSON export with fit metrics (writes to dataset/outputs by default)",
    )

    args = parser.parse_args(argv)

    dataset = Path(args.dataset).expanduser().resolve()
    if not dataset.exists():
        parser.error(f"Dataset directory does not exist: {dataset}")

    csv_dir = dataset / "csv"
    if not csv_dir.exists():
        parser.error(f"CSV directory not found: {csv_dir}")

    outputs_dir = dataset / "outputs"
    per_skew_dir = outputs_dir / "per_skew"
    per_skew_dir.mkdir(parents=True, exist_ok=True)

    ingress_jd, mid_jd, egress_jd = load_ephemeris(args.use_ephemeris, dataset)

    csv_files = sorted(csv_dir.glob("MA_*.csv"))
    if not csv_files:
        print(f"No MA_*.csv files found in {csv_dir}")
        return

    processed: List[ProcessedCurve] = []
    skipped: List[Tuple[Path, str]] = []

    for csv_path in csv_files:
        try:
            df = pd.read_csv(csv_path)
        except Exception as exc:  # noqa: BLE001
            skipped.append((csv_path, f"Failed to read CSV: {exc}"))
            continue
        try:
            time_col = choose_time_column(df, args.time_col)
            flux_col = choose_flux_column(df, args.flux_col)
        except KeyError as exc:
            skipped.append((csv_path, str(exc)))
            continue

        time_series = pd.to_numeric(df[time_col], errors="coerce").to_numpy()
        flux_series = pd.to_numeric(df[flux_col], errors="coerce").to_numpy()
        mask = np.isfinite(time_series) & np.isfinite(flux_series)
        time_series = time_series[mask]
        flux_series = flux_series[mask]
        if time_series.size < 5:
            skipped.append((csv_path, "Insufficient finite samples after filtering"))
            continue

        flux_det, _trend = detrend_flux(time_series, flux_series)
        t_bin, f_bin, s_bin = bin_series(time_series, flux_det, args.bin_sec)
        if t_bin.size < 3:
            skipped.append((csv_path, "Too few binned points"))
            continue

        sg_curve = sg_like(t_bin, f_bin)
        wrms = compute_wrms(t_bin, f_bin, s_bin, ingress_jd, egress_jd)

        try:
            fit = fit_numeric_ma(t_bin, f_bin, s_bin, ingress_jd, mid_jd, egress_jd)
        except Exception as exc:  # noqa: BLE001
            skipped.append((csv_path, f"Transit fit failed: {exc}"))
            continue

        rap, rin, rout = parse_radii_from_name(csv_path.name)
        processed.append(
            ProcessedCurve(
                path=csv_path,
                r_ap=rap,
                r_in=rin,
                r_out=rout,
                time_jd=t_bin,
                flux=f_bin,
                sem=s_bin,
                wrms_oot=wrms,
                fit=fit,
                sg=sg_curve,
            )
        )

    if not processed:
        for path, reason in skipped:
            print(f"[skip] {path.name}: {reason}")
        print("No light curves processed successfully")
        return

    curve_lookup: Dict[str, ProcessedCurve] = {curve.path.name: curve for curve in processed}

    rows: List[Dict[str, float]] = []
    for curve in processed:
        rows.append(
            {
                "file": curve.path.name,
                "r_ap": curve.r_ap,
                "r_in": curve.r_in,
                "r_out": curve.r_out,
                "n_bins": curve.time_jd.size,
                "OOT_WRMS": curve.wrms_oot,
                "rprs": curve.fit.rprs,
                "impact_parameter": curve.fit.impact_parameter,
                "t0_offset_min": curve.fit.mid_offset_min,
                "chi2": curve.fit.chi2,
            }
        )

    summary_df = pd.DataFrame(rows).sort_values("OOT_WRMS", na_position="last").reset_index(drop=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    summary_path = outputs_dir / "summary.csv"
    summary_df.to_csv(summary_path, index=False)

    median_r_out = summary_df["r_out"].median()
    slice_df = summary_df[np.isclose(summary_df["r_out"], median_r_out, atol=0.6)]
    if not slice_df.empty:
        pivot = slice_df.pivot_table(index="r_in", columns="r_ap", values="OOT_WRMS")
        plt.figure(figsize=(8.0, 6.0))
        im = plt.imshow(
            pivot.values,
            origin="lower",
            aspect="auto",
            extent=[pivot.columns.min(), pivot.columns.max(), pivot.index.min(), pivot.index.max()],
            cmap="viridis",
        )
        plt.colorbar(im, label="OOT WRMS")
        plt.xlabel("Aperture radius (pix)")
        plt.ylabel("Inner annulus radius (pix)")
        plt.title(f"WRMS heatmap (r_out~{median_r_out:.1f} pix)")
        plt.tight_layout()
        plt.savefig(outputs_dir / "heatmap_WRMS.png", dpi=240)
        plt.close()

    best_curve = curve_lookup[summary_df.iloc[0]["file"]]
    median_curve = curve_lookup[summary_df.iloc[len(summary_df) // 2]["file"]]
    worst_curve = curve_lookup[summary_df.iloc[len(summary_df) - 1]["file"]]

    def curve_title(curve: ProcessedCurve) -> str:
        return f"{curve.path.name} — Ap {curve.r_ap:.1f} | In {curve.r_in:.1f} | Out {curve.r_out:.1f}"

    render_curve(
        best_curve,
        outputs_dir / "composite_best.png",
        outputs_dir / "residuals_best.png",
        ingress_jd,
        mid_jd,
        egress_jd,
        args.residual_baseline,
        "Best skew",
    )

    for tag, curve in (("best", best_curve), ("median", median_curve), ("worst", worst_curve)):
        render_curve(
            curve,
            per_skew_dir / f"{tag}_main.png",
            per_skew_dir / f"{tag}_residuals.png",
            ingress_jd,
            mid_jd,
            egress_jd,
            args.residual_baseline,
            f"{tag.capitalize()} skew",
        )

    for curve in processed:
        stem = curve.path.stem
        render_curve(
            curve,
            per_skew_dir / f"{stem}_main.png",
            per_skew_dir / f"{stem}_residuals.png",
            ingress_jd,
            mid_jd,
            egress_jd,
            args.residual_baseline,
            curve_title(curve),
        )

    if args.export_log_json:
        log_path = Path(args.export_log_json)
        if not log_path.is_absolute():
            log_path = outputs_dir / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        export_log_json(log_path, best_curve, best_curve.time_jd.size)

    for path, reason in skipped:
        print(f"[skip] {path.name}: {reason}")

    figures_written = [
        outputs_dir / "summary.csv",
        outputs_dir / "heatmap_WRMS.png",
        outputs_dir / "composite_best.png",
        outputs_dir / "residuals_best.png",
    ]
    print(
        f"Processed {len(processed)} CSVs (skipped {len(skipped)}). "
        f"Summary: {summary_path}. Figures: {', '.join(str(p) for p in figures_written if p.exists())}."
    )


if __name__ == "__main__":
    main()
