#!/usr/bin/env python3
"""Generate AIJ macro jobs for a dataset-driven transit pipeline (RA/Dec aware)."""

from __future__ import annotations

import argparse
import csv
import itertools
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / "aij" / "jobs"
RUN_TEMPLATE = ROOT / "aij" / "Run_Radec_Photometry_Template.ijm"
GLOB_TEMPLATE = ROOT / "aij" / "Dir_FITS_Glob.ijm"

DEFAULT_R_AP = "2.5,3.0,3.5,4.0,4.5,5.0,5.5,6.0"
DEFAULT_R_IN = "9.0,9.5,10.0,10.5,11.0,12.0,13.0,14.0,15.0,16.0"
DEFAULT_R_OUT = "19.0,19.5,20.0,20.5,21.0,22.0,23.0,24.0,25.0,26.0"
CONVERTED_APERTURES = "apertures_converted.csv"
JOB_TEMPLATE = """
// Auto-generated AIJ job macro
print("[job] dataset: {{DATASET}}\n[job] radii (ap,in,out): {{R_AP}} {{R_IN}} {{R_OUT}}");
macroArgs = "fitsDir={{FITS_DIR}}\\n";
macroArgs += "pattern={{PATTERN}}\\n";
macroArgs += "radec={{RADEC_PATH}}\\n";
macroArgs += "ap={{R_AP}}\\n";
macroArgs += "in={{R_IN}}\\n";
macroArgs += "out={{R_OUT}}\\n";
macroArgs += "csvOut={{CSV_DIR}}\\n";
macroArgs += "outName={{OUT_NAME}}\\n";
macroArgs += "globMacro={{GLOB_MACRO}}";
runMacro("{{RUN_TEMPLATE}}", macroArgs);
"""


@dataclass
class ApertureEntry:
    label: str
    type: str
    ra_deg: float
    dec_deg: float
    include: bool
    mag: Optional[float]


def _forward_slashes(path: Path) -> str:
    return str(path).replace("\\", "/")


def _radius_list(raw: str, flag: str) -> List[float]:
    values: List[float] = []
    for token in raw.split(","):
        tok = token.strip()
        if not tok:
            continue
        try:
            values.append(float(tok))
        except ValueError as exc:
            raise ValueError(f"{flag} includes a non-numeric value: '{tok}'") from exc
    if not values:
        raise ValueError(f"{flag} requires at least one radius value")
    return values


def _sexagesimal_to_degrees(value: str, *, is_ra: bool) -> float:
    token = value.strip().replace(" ", "")
    if not token:
        raise ValueError("Empty coordinate value")
    if ":" not in token:
        try:
            numeric = float(token)
        except ValueError as exc:
            raise ValueError(f"Unable to parse coordinate '{value}'") from exc
        if is_ra:
            return numeric * 15.0 if abs(numeric) <= 24 else numeric
        return numeric
    parts = token.split(":")
    if len(parts) != 3:
        raise ValueError(f"Expected hh:mm:ss or dd:mm:ss format, got '{value}'")
    h_or_d, m, s = parts
    try:
        hours_deg = float(h_or_d)
        minutes = float(m)
        seconds = float(s)
    except ValueError as exc:
        raise ValueError(f"Non-numeric coordinate component in '{value}'") from exc
    sign = 1.0
    if not is_ra and hours_deg < 0:
        sign = -1.0
        hours_deg = abs(hours_deg)
    base = hours_deg + minutes / 60.0 + seconds / 3600.0
    if is_ra:
        return base * 15.0
    return sign * base


def _parse_radec(path: Path) -> List[ApertureEntry]:
    entries: List[ApertureEntry] = []
    target_count = 0
    comp_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [segment.strip() for segment in line.split(",")]
            if len(parts) < 2:
                continue
            ra_str, dec_str = parts[0], parts[1]
            ref_flag = parts[2] if len(parts) >= 3 else ""
            mag_str = parts[4] if len(parts) >= 5 else ""
            ra_deg = _sexagesimal_to_degrees(ra_str, is_ra=True)
            dec_deg = _sexagesimal_to_degrees(dec_str, is_ra=False)
            is_target = (ref_flag == "0") or (ref_flag == "")
            if is_target:
                target_count += 1
                label = f"T{target_count}"
                entry_type = "target"
            else:
                comp_count += 1
                label = f"C{comp_count}"
                entry_type = "comparison"
            include = entry_type == "target" or True
            try:
                mag = float(mag_str)
                if mag > 90:
                    mag = None
            except (TypeError, ValueError):
                mag = None
            entries.append(
                ApertureEntry(
                    label=label,
                    type=entry_type,
                    ra_deg=ra_deg,
                    dec_deg=dec_deg,
                    include=include,
                    mag=mag,
                )
            )
    if target_count == 0:
        raise ValueError(f"No target (Ref Star=0) rows found in {path}")
    return entries


def _read_aperture_csv(path: Path) -> List[ApertureEntry]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {"label", "type", "ra_deg", "dec_deg", "include"}
        headers = {h.strip() for h in (reader.fieldnames or [])}
        if not expected.issubset(headers):
            raise ValueError(
                "Aperture CSV must contain columns: label,type,ra_deg,dec_deg,mag,include"
            )
        entries: List[ApertureEntry] = []
        for row in reader:
            if not row:
                continue
            label = (row.get("label") or "").strip()
            if not label:
                continue
            raw_type = (row.get("type") or "").strip().lower()
            if raw_type not in {"target", "comparison"}:
                raise ValueError(f"Row for '{label}' has invalid type '{row.get('type')}'")
            try:
                ra_deg = float((row.get("ra_deg") or "").strip())
                dec_deg = float((row.get("dec_deg") or "").strip())
            except ValueError as exc:
                raise ValueError(f"Row for '{label}' must include numeric ra_deg/dec_deg") from exc
            include_str = (row.get("include") or "").strip().lower()
            include = include_str in {"true", "1", "yes", "y"}
            mag_val = (row.get("mag") or "").strip()
            try:
                mag = float(mag_val) if mag_val else None
            except ValueError:
                mag = None
            entries.append(
                ApertureEntry(
                    label=label,
                    type=raw_type,
                    ra_deg=ra_deg,
                    dec_deg=dec_deg,
                    include=include,
                    mag=mag,
                )
            )
    return entries


def _write_aperture_csv(entries: Iterable[ApertureEntry], output_path: Path) -> Path:
    fieldnames = ["label", "type", "ra_deg", "dec_deg", "mag", "include"]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            writer.writerow(
                {
                    "label": entry.label,
                    "type": entry.type,
                    "ra_deg": f"{entry.ra_deg:.8f}",
                    "dec_deg": f"{entry.dec_deg:.8f}",
                    "mag": "" if entry.mag is None else f"{entry.mag:.3f}",
                    "include": "true" if entry.include else "false",
                }
            )
    return output_path


def _discover_aperture_file(dataset_path: Path, override: Optional[str]) -> Path:
    if override:
        candidate = Path(override).expanduser().resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"Aperture file not found: {candidate}")
        return candidate
    for name in ("apertures.csv", "apertures.radec"):
        candidate = dataset_path / name
        if candidate.exists():
            return candidate
    radec_files = sorted(dataset_path.glob("*.radec"))
    if radec_files:
        return radec_files[0]
    csv_files = sorted(dataset_path.glob("*.csv"))
    if csv_files:
        return csv_files[0]
    raise FileNotFoundError(
        "No apertures catalogue found. Expected apertures.csv / apertures.radec inside the dataset or provide --apertures."
    )


def _load_apertures(path: Path, dataset_path: Path) -> Tuple[ApertureEntry, List[ApertureEntry], Path]:
    suffix = path.suffix.lower()
    if suffix == ".radec":
        entries = _parse_radec(path)
        csv_path = dataset_path / CONVERTED_APERTURES
        _write_aperture_csv(entries, csv_path)
    else:
        entries = _read_aperture_csv(path)
        csv_path = path

    targets = [entry for entry in entries if entry.type == "target"]
    if not targets:
        raise ValueError("Aperture catalogue must include at least one target row")
    target = targets[0]
    comparisons = [entry for entry in entries if entry.type == "comparison"]
    return target, comparisons, csv_path


def _radius_slug(value: float) -> str:
    return f"{value:.1f}".replace(".", "-")


def _out_filename(rap: float, rin: float, rout: float) -> str:
    return f"MA_Ap{rap:.1f}_In{rin:.1f}_Out{rout:.1f}.csv".replace("-", "-")


def _write_macro(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _run_macros(macros: Sequence[Path], exec_path: Path) -> None:
    for macro in macros:
        try:
            subprocess.run([str(exec_path), "-macro", str(macro)], check=True)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"AIJ execution failed for {macro.name}: {exc}")


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate AstroImageJ macros (RA/Dec driven).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dataset", required=True, help="Path to dataset directory")
    parser.add_argument("--apertures", help="Explicit path to apertures catalogue (.csv or .radec)")
    parser.add_argument(
        "--fits-root",
        default="raw",
        help="Subdirectory inside the dataset containing FITS images (e.g., raw, reduced, debayered_green)",
    )
    parser.add_argument("--r-ap", default=DEFAULT_R_AP, help="Comma-separated aperture radii (pixels)")
    parser.add_argument("--r-in", default=DEFAULT_R_IN, help="Comma-separated inner annulus radii (pixels)")
    parser.add_argument("--r-out", default=DEFAULT_R_OUT, help="Comma-separated outer annulus radii (pixels)")
    parser.add_argument("--min-annulus-width", type=float, default=8.0, help="Minimum background annulus width (r_out - r_in)")
    parser.add_argument("--min-gap", type=float, default=2.0, help="Minimum gap between aperture radius and inner annulus (r_in - r_ap)")
    parser.add_argument("--run-aij", action="store_true", help="Execute each generated macro via AIJ in headless mode")
    parser.add_argument("--aij-exec", help="Path to AstroImageJ executable (required with --run-aij)")

    args = parser.parse_args(argv)

    if not RUN_TEMPLATE.exists() or not GLOB_TEMPLATE.exists():
        parser.error("Required template macros not found under aij/ (run setup scripts first)")

    dataset_path = Path(args.dataset).expanduser().resolve()
    if not dataset_path.exists():
        parser.error(f"Dataset directory does not exist: {dataset_path}")

    try:
        r_ap_list = _radius_list(args.r_ap, "--r-ap")
        r_in_list = _radius_list(args.r_in, "--r-in")
        r_out_list = _radius_list(args.r_out, "--r-out")
    except ValueError as exc:
        parser.error(str(exc))

    try:
        aperture_src = _discover_aperture_file(dataset_path, args.apertures)
    except FileNotFoundError as exc:
        parser.error(str(exc))

    try:
        target, comparisons, apertures_csv_path = _load_apertures(aperture_src, dataset_path)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    fits_dir = (dataset_path / args.fits_root).resolve()
    if not fits_dir.exists():
        parser.error(f"FITS directory not found: {fits_dir}")

    csv_dir = dataset_path / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    JOBS_DIR.mkdir(parents=True, exist_ok=True)

    combinations: List[Tuple[float, float, float]] = []
    for rap, rin, rout in itertools.product(r_ap_list, r_in_list, r_out_list):
        if (rout - rin) < args.min_annulus_width:
            continue
        if (rin - rap) < args.min_gap:
            continue
        combinations.append((rap, rin, rout))

    if not combinations:
        parser.error("No radius combinations survived the min-annulus-width and min-gap constraints")

    manual_macros: List[Path] = []
    run_template_path = _forward_slashes(RUN_TEMPLATE.resolve())
    glob_template_path = _forward_slashes(GLOB_TEMPLATE.resolve())
    fits_dir_str = _forward_slashes(fits_dir)
    csv_dir_str = _forward_slashes(csv_dir.resolve())
    radec_path_str = _forward_slashes(apertures_csv_path.resolve())

    for rap, rin, rout in combinations:
        out_name = _out_filename(rap, rin, rout)
        replacements = {
            "DATASET": _forward_slashes(dataset_path),
            "FITS_DIR": fits_dir_str,
            "PATTERN": "*.fits",
            "RADEC_PATH": radec_path_str,
            "R_AP": f"{rap:.1f}",
            "R_IN": f"{rin:.1f}",
            "R_OUT": f"{rout:.1f}",
            "CSV_DIR": csv_dir_str,
            "OUT_NAME": out_name,
            "RUN_TEMPLATE": run_template_path,
            "GLOB_MACRO": glob_template_path,
        }
        macro_body = JOB_TEMPLATE
        for key, value in replacements.items():
            macro_body = macro_body.replace(f"{{{{{key}}}}}", value)
        macro_name = f"job_{args.fits_root}_Ap{_radius_slug(rap)}_In{_radius_slug(rin)}_Out{_radius_slug(rout)}.ijm"
        macro_path = JOBS_DIR / macro_name
        _write_macro(macro_path, macro_body)
        manual_macros.append(macro_path)

    if args.run_aij:
        if not args.aij_exec:
            parser.error("--run-aij requires --aij-exec to specify the AstroImageJ executable")
        exec_path = Path(args.aij_exec).expanduser().resolve()
        if not exec_path.exists():
            parser.error(f"AIJ executable not found: {exec_path}")
        _run_macros(manual_macros, exec_path)

    print(f"Dataset: {dataset_path}")
    print(f"Aperture source: {aperture_src}")
    if aperture_src.suffix.lower() == ".radec":
        print(f"Converted catalogue stored at: {dataset_path / CONVERTED_APERTURES}")
    print(f"FITS directory: {fits_dir}")
    print(f"CSV output directory: {csv_dir.resolve()}")
    print(f"Run template: {RUN_TEMPLATE}")
    print(f"Glob template: {GLOB_TEMPLATE}")
    print(f"Macros written to: {JOBS_DIR} (total={len(manual_macros)})")


if __name__ == "__main__":
    main()
