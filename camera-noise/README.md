# Camera Noise Characterization Tool

A standalone Python script for characterizing the thermal dependence of camera noise for TEC (Thermoelectric Cooler) CMOS sensors used in astronomy.

## Overview

This tool analyzes FITS files (typically bias or dark frames) to:
- Group images by gain and temperature settings
- Create histograms of pixel values
- Fit Gaussian distributions to understand noise characteristics
- Calculate read noise at different temperatures and gain settings
- Generate detailed plots showing noise vs. temperature relationships

## Features

- **Automatic grouping** by exposure time, gain, and temperature
- **Electron count conversion** using EGAIN header values
- **Gaussian fitting** with error estimation
- **KDE analysis** for non-parametric noise characterization
- **Statistical read noise** calculation from image variance
- **Publication-quality plots** with detailed annotations

## Requirements

This script is fully self-contained and requires only:
- Python 3.8+
- numpy
- scipy
- matplotlib
- astropy

Install dependencies:
```bash
pip install numpy scipy matplotlib astropy
```

## Usage

### Basic Usage

```bash
python camera_noise_characterization.py -d /path/to/fits/files
```

### With Options

```bash
python camera_noise_characterization.py \
    -d /path/to/bias/frames \
    -e 0.0001 \
    -p /path/to/save/plots \
    -c /path/to/summary.csv \
    --debug
```

### Command Line Options

| Option | Description |
|--------|-------------|
| `-d, --directory` | Directory containing FITS files (required) |
| `-e, --exptime` | Filter files by exposure time in seconds |
| `-p, --plots-dir` | Directory to save plots |
| `-c, --csv-path` | Path to save summary CSV |
| `--debug` | Enable debug logging |

## Recommended Workflow

1. **Capture bias frames** at multiple temperatures and gain settings
   - Use shortest possible exposure (e.g., 0.0001s)
   - Capture 10-20 frames per temperature/gain combination
   - Cover your typical operating temperature range

2. **Organize files** in a directory structure

3. **Run analysis**:
   ```bash
   python camera_noise_characterization.py -d ./bias_frames -p ./analysis_plots
   ```

4. **Review results**:
   - Individual plots per gain/temperature combination
   - Overlay comparison plot
   - Summary CSV with all statistics

## Output

### CSV Summary
Contains for each gain/temperature combination:
- Mean and sigma in ADU and electrons
- Gaussian fit parameters with errors
- Statistical read noise estimate
- R-squared goodness of fit

### Plots
- **Individual plots**: Histogram with Gaussian fit for each combination
- **Overlay plot**: All combinations compared on same axes
- **Log-scale views**: To visualize distribution tails

## Understanding the Results

### Read Noise
The Gaussian sigma (in electrons) represents the read noise:
- Lower values = less noise = better for faint targets
- Typically decreases with lower temperatures
- Varies with gain setting

### Interpreting Fit Quality
- R² close to 1.0 indicates good Gaussian fit
- Lower R² may indicate:
  - Non-Gaussian noise (hot pixels, amp glow)
  - Mixed distributions
  - Insufficient data

## Example

```bash
# Analyze bias frames, filter by 0.0001s exposure
python camera_noise_characterization.py \
    -d "G:\Astrophotography\Camera_Characterization\ASI294MM" \
    -e 0.0001 \
    -p ./plots

# Output:
# 2025-01-24 12:00:00 - CameraNoise - INFO - Searching for FITS files...
# 2025-01-24 12:00:01 - CameraNoise - INFO - Found 150 FITS files
# 2025-01-24 12:00:01 - CameraNoise - INFO - Group: GAIN=0, TEMP=-20°C, Files: 15
# 2025-01-24 12:00:01 - CameraNoise - INFO - Group: GAIN=100, TEMP=-20°C, Files: 15
# ...
# SUCCESS: Analysis completed in 45.23 seconds
```

## Portability

This script is designed to be fully portable:
- No dependencies on other local modules
- Can be copied to any location and run independently
- All required functionality is self-contained

## License

MIT License

## Author

Dane
