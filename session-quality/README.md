# Astro Run Quality Controller

A Python package for analyzing the quality of astronomy imaging sessions. This toolkit provides comprehensive analysis of guiding performance, autofocus events, and observing conditions, with an interactive HTML dashboard and quality scoring.

## Features

- **HTML Dashboard**: Beautiful, interactive HTML report with charts and tables
- **Quality Scoring**: Weighted quality score (0-100) based on multiple metrics
- **Alt/Az Tracking**: Altitude and azimuth over time with interactive charts
- **Focus Analysis**: Per-filter focus position tracking and HFR statistics
- **Temperature Monitoring**: Focuser temperature stability analysis
- **Guiding Quality**: Star lost events and settle success tracking
- **Session Efficiency**: Calculate actual imaging time vs total session time

## Installation

1. Clone or download this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Quick Start

### Generate Astro Run Quality Report

Run the analysis on a session folder to generate an HTML dashboard:

```bash
python run_night_quality.py -d "/path/to/session/folder"
```

This will:
1. Parse Autorun logs for autofocus and guiding events
2. Extract data from FITS headers (filter, focus position, temperature, etc.)
3. Calculate Alt/Az coordinates for each image
4. Generate quality scores for 7 different metrics
5. Create an interactive HTML dashboard with charts

The dashboard includes:
- **Overall Quality Score** (0-100) with weighted breakdown
- **Alt/Az Timeline** - Interactive chart showing altitude/azimuth over time
- **Temperature Chart** - Focuser temperature throughout the session
- **Focus Position by Filter** - Bar chart comparing filters
- **Per-Filter Statistics** - Table with exposure counts, focus, and HFR data
- **Autofocus Events** - Detailed table of all AF runs

### Individual Analyses

#### PHD2 Guiding Analysis

```bash
python run_phd2_analysis.py -d "/path/to/logs" -p ./plots
```

#### Autofocus Analysis

```bash
python run_autofocus_analysis.py -d "/path/to/logs" -p ./plots
```

#### Alt/Az Analysis

```bash
python run_altaz_analysis.py -d "/path/to/fits/files" -p ./plots
```

## Configuration

Edit `config.yaml` to customize settings:

```yaml
phd2:
  pixel_scale_arcsec: 6.45  # Your guide camera pixel scale
  pixel_size_um: 3.8

altaz:
  # Update for your location
  latitude: 45.5145
  longitude: -122.848
  elevation: 60.0
  timezone: "America/Los_Angeles"
  min_altitude: 30.0
```

## Package Structure

```
astro_run_quality_controller/
├── astro_utils/                # Core library
│   ├── __init__.py
│   ├── astro_logger.py         # Enhanced logging
│   ├── config.py               # Configuration handling
│   ├── utils.py                # Utility functions
│   ├── phd2_analysis.py        # PHD2 guiding analysis
│   ├── autofocus_analysis.py   # Autofocus event analysis
│   └── altaz_analysis.py       # Alt/Az coordinate analysis
├── run_night_quality.py        # Combined analysis runner
├── run_phd2_analysis.py        # PHD2 analysis CLI
├── run_autofocus_analysis.py   # Autofocus analysis CLI
├── run_altaz_analysis.py       # Alt/Az analysis CLI
├── config.yaml                 # Configuration file
├── requirements.txt            # Dependencies
└── README.md                   # This file
```

## Expected Session Folder Structure

For best results, your imaging session folder should contain:

```
session_folder/
├── PHD2_GuideLog_YYYY-MM-DD_HHMMSS.txt
├── Autorun_Log_YYYY-MM-DD_HHMMSS.txt
├── Light_Target_*.fits (or *.fit)
└── ...
```

## Command Line Options

### run_night_quality.py

| Option | Description |
|--------|-------------|
| `-d, --session-dir` | Directory containing session data (required) |
| `-o, --output` | Custom path for HTML output (default: session_dir/astro_qc_report_DATE.html) |
| `-c, --config` | Path to custom YAML config file |
| `--debug` | Enable debug logging |

### Examples

```bash
# Basic usage - generates report in session folder
python run_night_quality.py -d "G:\Astrophotography\2026-01-23"

# Custom output path
python run_night_quality.py -d /path/to/session -o my_report.html

# With custom config
python run_night_quality.py -d /path/to/session -c custom_config.yaml
```

## Quality Scoring

The overall quality score (0-100) is calculated from these weighted metrics:

| Metric | Weight | Description |
|--------|--------|-------------|
| **Altitude** | 15% | Higher altitude = less atmosphere, better seeing. Penalizes low-altitude imaging. |
| **Autofocus** | 15% | Autofocus success rate. 100% success = full score. |
| **Guiding** | 20% | Tracks star lost events and settle success. Star lost events penalized heavily. |
| **Temperature** | 10% | Focuser temperature stability. Less variation = better focus consistency. |
| **Focus Stability** | 15% | Consistency of focus position within each filter. |
| **HFR/Seeing** | 15% | Half Flux Radius from autofocus. Lower = sharper stars. |
| **Efficiency** | 10% | Percentage of session time spent actually imaging. |

## Dependencies

- Python 3.8+
- numpy
- pandas
- matplotlib
- astropy
- scipy
- pyyaml
- rich (optional, for enhanced console output)

## Programmatic Usage

```python
from astro_utils import Config, PHD2Analysis, AutofocusAnalysis, AltAzAnalysis
from pathlib import Path

# Load configuration
config = Config(Path("config.yaml"))

# Run PHD2 analysis
phd2 = PHD2Analysis(config, Path("/path/to/logs"))
phd2.analyze_session()
phd2.plot_guiding_performance(Path("./plots"))

# Run Alt/Az analysis
altaz = AltAzAnalysis(config, Path("/path/to/fits"))
altaz.analyze_session()
altaz.save_csv()
```

## Output

Each analysis produces:
- Console output with summary statistics
- CSV files with detailed results
- Plots (if plot directory specified)

## License

MIT License

## Author

Dane
