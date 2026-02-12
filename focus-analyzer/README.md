
# Focus Position Parser

This utility consolidates focus-position data from NINA autorun logs and FITS image headers. It produces two Excel workbooks that make it easy to explore how star size, focuser position, filter, temperature, and binning relate to one another and to track the focus points used during imaging sessions.

## Features
- Scans any directory tree for `Autorun_Log_*.txt` files and extracts autofocus runs that completed successfully.
- Parses FITS headers (both `.fit` and `.fits`) to recover the focuser position used for each light frame.
- Derives filter and observing-night metadata from the directory structure (e.g. `.../Ha_OIII/DATE_07-12-2025/lights`).
- Produces Excel workbooks with ready-to-plot measurement tables and nightly statistics.

## Requirements
- Python 3.9+
- [`pandas`](https://pandas.pydata.org/) (tested with 2.2+)
- [`astropy`](https://www.astropy.org/) (for FITS I/O)

All dependencies must be available in the same environment used to run the parser. `xlsxwriter` is optional; if installed, pandas will use it automatically, otherwise the built-in Excel writer is used.

## Directory Assumptions
The parser infers context from the filesystem:
- Filter name: taken from any path segment matching `Ha_OIII`, `OIII`, `SII`, or `no_filter` (case/spacing/punctuation is normalised).
- Night: taken from path segments like `DATE_07-12-2025`. If no explicit folder is present, the parser will fall back to date strings in the path or `DATE-OBS` from the FITS header.
- Only FITS files inside a directory that includes `lights` are treated as imaging frames.

## Usage
```
python focuspos_parser/focus_parser.py <root_dir> [--output-dir <path>] [--log-level LEVEL]
```
- `<root_dir>`: directory to scan for autorun logs and FITS files.
- `--output-dir`: optional destination for generated Excel workbooks (defaults to `<root_dir>`).
- `--log-level`: choose from `DEBUG`, `INFO`, `WARNING`, etc. Defaults to `INFO`.

### Example commands
Parse the bundled sample data and write results to `focuspos_parser/example_output`:
```
python focuspos_parser/focus_parser.py focuspos_parser/example_data --output-dir focuspos_parser/example_output --log-level DEBUG
```
Run against the full imaging archive (output is written alongside the archive root):
```
python focuspos_parser/focus_parser.py "C:\Users\Dane\Pictures\DSOs"
```

## Output Files
Two Excel workbooks are created:

1. **`focus_routine_measurements.xlsx`**
   - `measurements` sheet: one row per autofocus sample (EAF position, star size, stage, timestamps, temperature, binning, filter, night, etc.). The `eaf_position` column is positioned directly before `star_size` to support quick scatter plotting.
   - `runs` sheet: summary rows for each autofocus run with run-level metadata and the final focus position.

2. **`imaging_focus_stats.xlsx`**
   - `imaging_measurements` sheet: one row per FITS light frame with focuser position, binning, exposure, filter, night, `DATE-OBS`, and an inferred target name (taken from the folder just above the filter directory).
   - `nightly_stats` sheet: aggregates focuser positions by night and filter (count, mean, standard deviation, min, max).

All timestamps are exported as local datetimes formatted `YYYY-MM-DD HH:MM:SS`. Empty tables are emitted when no data is found to make downstream tooling easier to set up.

## Troubleshooting & Notes
- Autofocus runs that end with "Auto focus failed" are ignored.
- If the parser reports zero imaging frames, confirm that the FITS files reside inside a folder containing `lights` and that the headers include `FOCUSPOS`.
- Additional filter names can be added by extending the `FILTER_SYNONYMS` mapping in `focus_parser.py`.
- Enable `--log-level DEBUG` for detailed parsing diagnostics.

## Development
The core implementation lives in `focuspos_parser/focus_parser.py`. Key sections include:
- Log parsing (`parse_autorun_log`) for autofocus measurements.
- FITS parsing (`parse_fits_file`) for imaging metadata.
- Excel writers (`write_focus_workbook`, `write_imaging_workbook`) for final reporting.

Contributions are welcome—open an issue or submit a pull request with proposed enhancements.
