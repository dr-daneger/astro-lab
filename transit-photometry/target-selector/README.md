# Exoplanet Selection Toolkit

## Overview
This repository contains a single workflow for ranking exoplanet transit events for follow-up with a small telescope. The `pick_targets.py` script downloads the ExoClock planet catalog, builds a scoring table, applies magnitude/depth/duration filters, and optionally cross-matches with a saved Swarthmore College planet list (`swarthmore-list.html`).

## Repository Layout
- `pick_targets.py` ? command-line utility that fetches ExoClock data, scores each planet, and writes CSV summaries.
- `swarthmore-list.html` ? locally saved copy of the Swarthmore transit predictions page for name matching (optional input).
- `.venv/` ? local Python virtual environment (not required; create your own if preferred).

## Requirements
- Python 3.10 or newer.
- Python packages: `pandas`, `beautifulsoup4` (requests/urllib is in the standard library).

## Environment Setup
1. Create and activate a virtual environment:
   - PowerShell: `python -m venv .venv` then `.\.venv\Scripts\Activate.ps1`
   - Cmd: `python -m venv .venv` then `.\.venv\Scripts\activate.bat`
2. Install dependencies: `pip install pandas beautifulsoup4`

## Running the Target Picker
Basic invocation (downloads catalog unless `--json-cache` provided):

```
python pick_targets.py
```

Useful options:
- `--bin-min` ? light-curve bin size in minutes for SNR estimates (default 10).
- `--beta` ? red-noise inflation factor (default 1.3).
- `--mag-limit` ? maximum preferred magnitude (default 12.5).
- `--depth-min` ? minimum transit depth in ppt (default 12).
- `--duration-min` ? minimum transit duration in hours (default 1.5).
- `--min-observations` ? require a minimum total observation count.
- `--json-cache` ? reuse a saved ExoClock JSON file instead of downloading.
- `--swarthmore-html` ? path to a saved Swarthmore HTML page for name intersection.
- `--out-prefix` ? destination folder for generated CSV files (default current directory).

Running the script produces:
- `targets_ranked.csv` ? full catalog ranked by computed score.
- `shortlist.csv` ? filtered subset using your thresholds.
- `shortlist_vs_swarthmore.csv` ? optional Swarthmore intersection (requires `--swarthmore-html`).

## Tips
- The ExoClock API may occasionally rate-limit; the script retries automatically but you can pass `--json-cache` to work offline after a successful fetch.
- Adjust the magnitude/depth thresholds to widen or narrow the candidate list depending on conditions and instrumentation.
- Keep the Swarthmore HTML export reasonably up to date to avoid missing renamed or newly added targets.
