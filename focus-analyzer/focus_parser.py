
"""Focus position parser for autorun logs and FITS headers."""
import argparse
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
from astropy.io import fits


FILTER_SYNONYMS: Dict[str, str] = {
    "ha_oiii": "Ha_OIII",
    "optolong_l_extreme_ha_oiii": "Ha_OIII",
    "optolong_l_extreme": "Ha_OIII",
    "optolong_l_extreme_haoiii": "Ha_OIII",
    "oiii": "OIII",
    "sii": "SII",
    "no_filter": "no_filter",
}

TIMESTAMP_RE = re.compile(r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+(?P<msg>.+)$")
AUTORUN_BEGIN_RE = re.compile(r"\[Autorun\|Begin\]\s+(?P<target>.+)$", re.IGNORECASE)
SHOOTING_RE = re.compile(r"Shooting .*? Bin\s*(?P<bin>\d+)", re.IGNORECASE)
AUTOFOCUS_BEGIN_RE = re.compile(
    r"\[AutoFocus\|Begin\].*?exposure\s+(?P<exp>[\d.]+)s.*?Bin\s*(?P<bin>\d+).*?temperature\s+(?P<temp>[-\d.]+)",
    re.IGNORECASE,
)
MEASUREMENT_RE = re.compile(
    r"^(?P<stage>(?:Find Focus Star|Calculate V-Curve|Calculate Focus Point)):(?:.*?star size\s+(?P<starsize>[\d.]+))?\s*,\s*EAF position\s+(?P<eaf>\d+)",
    re.IGNORECASE,
)
AUTOFOCUS_SUCCESS_RE = re.compile(r"Auto focus succeeded, the focused position is\s+(?P<pos>\d+)", re.IGNORECASE)
AUTOFOCUS_FAIL_RE = re.compile(r"Auto focus failed", re.IGNORECASE)


@dataclass
class FocusMeasurement:
    timestamp: Optional[datetime]
    stage: str
    star_size: Optional[float]
    eaf_position: int
    raw_line: str


@dataclass
class FocusRun:
    run_id: str
    log_path: Path
    start_time: Optional[datetime]
    success_time: Optional[datetime]
    target: Optional[str]
    temperature_c: Optional[float]
    focus_exposure_seconds: Optional[float]
    focus_bin: Optional[int]
    imaging_bin: Optional[int]
    filter_name: Optional[str]
    night_date: Optional[str]
    final_focus_position: Optional[int]
    measurements: List[FocusMeasurement] = field(default_factory=list)


def normalize_part(part: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", part.lower()).strip("_")
    return cleaned


def detect_filter_from_path(path: Path) -> Optional[str]:
    for part in reversed(path.parts):
        normalized = normalize_part(part)
        if normalized in FILTER_SYNONYMS:
            return FILTER_SYNONYMS[normalized]
    return None


def detect_night_from_path(path: Path) -> Optional[str]:
    date_pattern = re.compile(r"date[_-](?P<mm>\d{2})[-_](?P<dd>\d{2})[-_](?P<yyyy>\d{4})", re.IGNORECASE)
    for part in path.parts:
        match = date_pattern.search(part)
        if match:
            try:
                dt = datetime.strptime(
                    f"{match.group('yyyy')}-{match.group('mm')}-{match.group('dd')}", "%Y-%m-%d"
                )
                return dt.date().isoformat()
            except ValueError:
                continue
    # fallback to YYYYMMDD inside filename/path
    fallback_pattern = re.compile(r"(20\d{2})(\d{2})(\d{2})")
    joined = "_".join(path.parts)
    match = fallback_pattern.search(joined)
    if match:
        try:
            dt = datetime.strptime("-".join(match.groups()), "%Y-%m-%d")
            return dt.date().isoformat()
        except ValueError:
            return None
    return None


def parse_timestamp(raw_ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(raw_ts, "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return None


def parse_autorun_log(path: Path) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    measurement_rows: List[Dict[str, object]] = []
    run_rows: List[Dict[str, object]] = []

    filter_name = detect_filter_from_path(path)
    path_night = detect_night_from_path(path)

    current_run: Optional[FocusRun] = None
    run_counter = 0
    last_target: Optional[str] = None
    last_imaging_bin: Optional[int] = None

    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.rstrip()
            if not line:
                continue
            timestamp_match = TIMESTAMP_RE.match(line)
            if timestamp_match:
                timestamp = parse_timestamp(timestamp_match.group("ts"))
                message = timestamp_match.group("msg")
            else:
                timestamp = None
                message = line

            autorun_match = AUTORUN_BEGIN_RE.search(message)
            if autorun_match:
                last_target = autorun_match.group("target").strip()

            shooting_match = SHOOTING_RE.search(message)
            if shooting_match:
                try:
                    last_imaging_bin = int(shooting_match.group("bin"))
                except (TypeError, ValueError):
                    last_imaging_bin = None

            if "[AutoFocus|Begin]" in message:
                run_counter += 1
                run_id = f"{path.name}#AF{run_counter}"
                begin_match = AUTOFOCUS_BEGIN_RE.search(message)
                focus_exposure = None
                focus_bin = None
                temperature_c = None
                if begin_match:
                    try:
                        focus_exposure = float(begin_match.group("exp"))
                    except (TypeError, ValueError):
                        focus_exposure = None
                    try:
                        focus_bin = int(begin_match.group("bin"))
                    except (TypeError, ValueError):
                        focus_bin = None
                    temp_raw = begin_match.group("temp")
                    if temp_raw is not None:
                        temp_raw = temp_raw.replace("℃", "").replace("C", "")
                        try:
                            temperature_c = float(temp_raw)
                        except ValueError:
                            temperature_c = None
                current_run = FocusRun(
                    run_id=run_id,
                    log_path=path,
                    start_time=timestamp,
                    success_time=None,
                    target=last_target,
                    temperature_c=temperature_c,
                    focus_exposure_seconds=focus_exposure,
                    focus_bin=focus_bin,
                    imaging_bin=last_imaging_bin,
                    filter_name=filter_name,
                    night_date=path_night,
                    final_focus_position=None,
                )
                continue

            if current_run is None:
                continue

            # parse measurement lines
            measurement_match = MEASUREMENT_RE.search(message)
            if measurement_match:
                stage = measurement_match.group("stage")
                try:
                    eaf_position = int(measurement_match.group("eaf"))
                except (TypeError, ValueError):
                    eaf_position = None
                star_size_val: Optional[float]
                starsize_raw = measurement_match.group("starsize")
                if starsize_raw:
                    try:
                        star_size_val = float(starsize_raw)
                    except ValueError:
                        star_size_val = None
                else:
                    star_size_val = None
                if eaf_position is not None:
                    current_run.measurements.append(
                        FocusMeasurement(
                            timestamp=timestamp,
                            stage=stage,
                            star_size=star_size_val,
                            eaf_position=eaf_position,
                            raw_line=message,
                        )
                    )
                continue

            if AUTOFOCUS_SUCCESS_RE.search(message):
                match = AUTOFOCUS_SUCCESS_RE.search(message)
                final_focus = None
                if match:
                    try:
                        final_focus = int(match.group("pos"))
                    except (TypeError, ValueError):
                        final_focus = None
                current_run.final_focus_position = final_focus
                current_run.success_time = timestamp

                # Determine fallback night if missing
                if current_run.night_date is None and current_run.start_time:
                    current_run.night_date = current_run.start_time.date().isoformat()
                elif current_run.night_date is None and timestamp:
                    current_run.night_date = timestamp.date().isoformat()

                # Create measurement rows
                for idx, measurement in enumerate(current_run.measurements, start=1):
                    measurement_rows.append(
                        {
                            "log_name": path.name,
                            "log_path": str(path),
                            "focus_run_id": current_run.run_id,
                            "measurement_index": idx,
                            "measurement_stage": measurement.stage,
                            "measurement_timestamp": measurement.timestamp,
                            "eaf_position": measurement.eaf_position,
                            "star_size": measurement.star_size,
                            "final_focus_position": current_run.final_focus_position,
                            "focus_run_start": current_run.start_time,
                            "focus_run_success": current_run.success_time,
                            "focus_temperature_c": current_run.temperature_c,
                            "focus_exposure_seconds": current_run.focus_exposure_seconds,
                            "focus_exposure_bin": current_run.focus_bin,
                            "imaging_plan_bin": current_run.imaging_bin,
                            "filter_name": current_run.filter_name,
                            "night_date": current_run.night_date,
                            "target": current_run.target,
                            "raw_line": measurement.raw_line,
                        }
                    )

                run_rows.append(
                    {
                        "focus_run_id": current_run.run_id,
                        "log_name": path.name,
                        "log_path": str(path),
                        "start_timestamp": current_run.start_time,
                        "success_timestamp": current_run.success_time,
                        "target": current_run.target,
                        "temperature_c": current_run.temperature_c,
                        "focus_exposure_seconds": current_run.focus_exposure_seconds,
                        "focus_exposure_bin": current_run.focus_bin,
                        "imaging_plan_bin": current_run.imaging_bin,
                        "final_focus_position": current_run.final_focus_position,
                        "measurement_count": len(current_run.measurements),
                        "filter_name": current_run.filter_name,
                        "night_date": current_run.night_date,
                    }
                )
                current_run = None
                continue

            if AUTOFOCUS_FAIL_RE.search(message):
                logging.debug("Discarding autofocus run %s due to failure", current_run.run_id)
                current_run = None

    return measurement_rows, run_rows


def gather_autorun_data(root: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    all_measurements: List[Dict[str, object]] = []
    all_runs: List[Dict[str, object]] = []
    for log_path in sorted(root.rglob("Autorun_Log_*.txt")):
        logging.info("Parsing log %s", log_path)
        measurements, runs = parse_autorun_log(log_path)
        all_measurements.extend(measurements)
        all_runs.extend(runs)
    measurement_df = pd.DataFrame(all_measurements)
    run_df = pd.DataFrame(all_runs)
    for df in (measurement_df, run_df):
        if df.empty:
            continue
        for col in ["measurement_timestamp", "focus_run_start", "focus_run_success", "start_timestamp", "success_timestamp"]:
            if col in df.columns and not df[col].empty:
                df[col] = pd.to_datetime(df[col])
        if "night_date" in df.columns:
            df["night_date"] = df["night_date"].astype("string")
    return measurement_df, run_df


def detect_target_from_path(path: Path, filter_name: Optional[str]) -> Optional[str]:
    if filter_name is None:
        return None
    parts = list(path.parts)
    lower_parts = [normalize_part(p) for p in parts]
    try:
        index = next(i for i, p in enumerate(lower_parts) if FILTER_SYNONYMS.get(p) == filter_name)
    except StopIteration:
        return None
    if index > 0:
        return parts[index - 1]
    return None


def parse_fits_file(path: Path) -> Optional[Dict[str, object]]:
    filter_name = detect_filter_from_path(path)
    night_date = detect_night_from_path(path)
    if "lights" not in [part.lower() for part in path.parts]:
        return None
    try:
        header = fits.getheader(path, 0)
    except Exception as exc:  # pragma: no cover - defensive
        logging.warning("Failed to read FITS header for %s: %s", path, exc)
        return None
    focus_pos = header.get("FOCUSPOS")
    if focus_pos is None:
        return None
    try:
        focus_pos_val = int(focus_pos)
    except (TypeError, ValueError):
        logging.debug("Non-integer FOCUSPOS for %s: %r", path, focus_pos)
        return None
    xbin = header.get("XBINNING")
    try:
        xbin_val = int(xbin) if xbin is not None else None
    except (TypeError, ValueError):
        xbin_val = None
    exposure = header.get("EXPTIME") or header.get("EXPOSURE")
    try:
        exposure_val = float(exposure) if exposure is not None else None
    except (TypeError, ValueError):
        exposure_val = None
    date_obs = header.get("DATE-OBS")
    if night_date is None and date_obs:
        try:
            night_date = date_obs.split("T")[0]
        except Exception:  # pragma: no cover - defensive
            pass
    target_name = detect_target_from_path(path.parent, filter_name)
    return {
        "file_path": str(path),
        "file_name": path.name,
        "filter_name": filter_name,
        "night_date": night_date,
        "focus_position": focus_pos_val,
        "xbinning": xbin_val,
        "exposure_seconds": exposure_val,
        "date_obs": date_obs,
        "target": target_name,
    }


def gather_fits_data(root: Path) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for fits_path in sorted(root.rglob("*.fit")):
        row = parse_fits_file(fits_path)
        if row:
            rows.append(row)
    for fits_path in sorted(root.rglob("*.fits")):
        row = parse_fits_file(fits_path)
        if row:
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "night_date" in df.columns:
        df["night_date"] = df["night_date"].astype("string")
    return df


def write_focus_workbook(output_path: Path, measurements: pd.DataFrame, runs: pd.DataFrame) -> None:
    with pd.ExcelWriter(output_path) as writer:
        if measurements.empty:
            pd.DataFrame(columns=[
                "log_name",
                "focus_run_id",
                "measurement_index",
                "measurement_stage",
                "star_size",
                "eaf_position",
                "final_focus_position",
                "focus_temperature_c",
                "focus_exposure_seconds",
                "focus_exposure_bin",
                "imaging_plan_bin",
                "filter_name",
                "night_date",
                "target",
            ]).to_excel(writer, index=False, sheet_name="measurements")
        else:
            measurements_sorted = measurements.sort_values(
                ["log_name", "focus_run_id", "measurement_index"]
            )
            measurements_export = measurements_sorted.copy()
            for col in ["measurement_timestamp", "focus_run_start", "focus_run_success"]:
                if col in measurements_export.columns:
                    measurements_export[col] = measurements_export[col].dt.strftime("%Y-%m-%d %H:%M:%S")
            measurements_export.to_excel(writer, index=False, sheet_name="measurements")

        if runs.empty:
            pd.DataFrame(columns=[
                "focus_run_id",
                "log_name",
                "start_timestamp",
                "success_timestamp",
                "target",
                "temperature_c",
                "focus_exposure_seconds",
                "focus_exposure_bin",
                "imaging_plan_bin",
                "final_focus_position",
                "measurement_count",
                "filter_name",
                "night_date",
            ]).to_excel(writer, index=False, sheet_name="runs")
        else:
            runs_sorted = runs.sort_values(["log_name", "focus_run_id"])
            runs_export = runs_sorted.copy()
            for col in ["start_timestamp", "success_timestamp"]:
                if col in runs_export.columns:
                    runs_export[col] = runs_export[col].dt.strftime("%Y-%m-%d %H:%M:%S")
            runs_export.to_excel(writer, index=False, sheet_name="runs")


def write_imaging_workbook(output_path: Path, fits_df: pd.DataFrame) -> None:
    with pd.ExcelWriter(output_path) as writer:
        if fits_df.empty:
            measurement_cols = [
                "file_path",
                "file_name",
                "filter_name",
                "night_date",
                "focus_position",
                "xbinning",
                "exposure_seconds",
                "date_obs",
                "target",
            ]
            pd.DataFrame(columns=measurement_cols).to_excel(writer, index=False, sheet_name="imaging_measurements")
            summary_cols = [
                "night_date",
                "filter_name",
                "count",
                "mean",
                "std",
                "min",
                "max",
            ]
            pd.DataFrame(columns=summary_cols).to_excel(writer, index=False, sheet_name="nightly_stats")
            return

        fits_sorted = fits_df.sort_values(["night_date", "filter_name", "file_name"])
        fits_sorted.to_excel(writer, index=False, sheet_name="imaging_measurements")

        if "focus_position" in fits_df.columns:
            stats = (
                fits_df.dropna(subset=["focus_position"])
                .groupby(["night_date", "filter_name"], dropna=False)["focus_position"]
                .agg(["count", "mean", "std", "min", "max"])
                .reset_index()
            )
        else:
            stats = pd.DataFrame()

        stats.to_excel(writer, index=False, sheet_name="nightly_stats")


def run_parser(root: Path, output_dir: Path) -> None:
    measurements, runs = gather_autorun_data(root)
    fits_df = gather_fits_data(root)

    output_dir.mkdir(parents=True, exist_ok=True)
    focus_output = output_dir / "focus_routine_measurements.xlsx"
    imaging_output = output_dir / "imaging_focus_stats.xlsx"

    logging.info("Writing focus workbook to %s", focus_output)
    write_focus_workbook(focus_output, measurements, runs)
    logging.info("Writing imaging workbook to %s", imaging_output)
    write_imaging_workbook(imaging_output, fits_df)

    logging.info(
        "Generated %d focus measurements across %d runs", len(measurements.index) if not measurements.empty else 0, len(runs.index) if not runs.empty else 0
    )
    logging.info("Processed %d FITS imaging frames", len(fits_df.index) if not fits_df.empty else 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse autofocus logs and FITS headers to analyze focus positions.")
    parser.add_argument(
        "root",
        type=Path,
        help="Root directory to scan for Autorun logs and FITS files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to place generated Excel workbooks (defaults to root).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(levelname)s: %(message)s")
    root = args.root.expanduser().resolve()
    if args.output_dir is None:
        output_dir = root
    else:
        output_dir = args.output_dir.expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Root directory {root} does not exist")
    run_parser(root, output_dir)


if __name__ == "__main__":
    main()
