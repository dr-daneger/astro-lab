# Astro-Pipeline Repository Inventory

**Generated:** March 18, 2026  
**Scope:** Complete audit of functions and classes across all modules  
**Total Repository Size:** ~14,500 LOC (Python)

---

## Executive Summary

### Key Statistics
- **Total Python Files:** 46 files
- **Largest Module:** astro-piper (5,942 LOC) — PixInsight/GraXpert automation
- **Shared Library:** astrolib (1,034 LOC) — Common utilities
- **Session Quality:** session-quality (5,755 LOC) — Guiding, focus, Alt/Az analysis

### Critical Patterns

1. **High Duplication:** Coordinate transforms, FITS reading, and math utilities scattered across 4+ modules
2. **Session Quality Monolith:** dashboard.py (3,340 LOC) — Consider extracting submodules
3. **Shared Ephemeris Logic:** astrolib.ephemeris.py uses astroplan/astroquery but only consumed by target-selector
4. **Disparate Testing:** astro-piper has comprehensive tests (1,739 LOC); other modules lack structure

---

## 1. COORDINATE TRANSFORMATIONS (RA/Dec, Alt/Az, Sexagesimal)

### Files & Classes/Functions

| File | Location | Type | Functions | LOC |
|------|----------|------|-----------|-----|
| **coord_utils.py** | astrolib/ | Utility | `sexagesimal_to_degrees()` | 46 |
| **ephemeris.py** | astrolib/ | Module | `calculate_ephemeris()`, `get_targets()` | 192 |
| **altaz_analysis.py** | session-quality/astro_utils/ | Analysis | `AltAzAnalysis` class, filtering alt/az data from logs | 390 |
| **generate_skews.py** | transit-photometry/scripts/ | Helper | `_sexagesimal_to_degrees()`, `_parse_radec()` | 337 |

### Inventory Details

#### astrolib/coord_utils.py
**Purpose:** Sexagesimal coordinate parsing (HH:MM:SS → decimal degrees)  
**Functions:**
- `sexagesimal_to_degrees(value: str, *, is_ra: bool) → float` — **46 LOC**
  - Handles colon-separated or decimal formats
  - RA multiplied by 15° for hour-to-degree conversion
  - Used by: generate_skews.py (duplicated), altaz_analysis.py

**Duplication:** ⚠️ **CRITICAL**  
- `generate_skews.py` line 69: `_sexagesimal_to_degrees()` is a **near-exact duplicate**
- Both functions are standalone; no import relationship

#### astrolib/ephemeris.py
**Purpose:** Exoplanet target selection, observability windows, ephemeris lookups  
**Classes:**
- (None; functions only)

**Functions:**
- `get_targets(target_list=DEFAULT_TARGETS) → List[FixedTarget]` — **~30 LOC**
  - Uses astroplan/astroquery for target name resolution
  - Planets and DSOs (M-series, NGC)
  - Returns astroplan FixedTarget objects

- `calculate_ephemeris(location: EarthLocation, targets: list, calculation_time: Time)` — **~150 LOC**
  - Computes rise/transit/set times + Alt/Az coordinates
  - Queries Simbad for angular sizes (galaxies/nebulae)
  - Defines Min Altitude = 30°
  - Returns detailed observability window analysis

**Dependencies:** astropy.coordinates, astroplan, astroquery.simbad  
**Usage:** transit-photometry/target-selector/pick_targets.py (not directly; used via alt. workflow)

#### session-quality/astro_utils/altaz_analysis.py
**Purpose:** Parse NINA logs for Alt/Az trajectory; filter by constraints  
**Classes:**
- `FITSImageStats` — Dataclass for image metadata
- `AltAzAnalysis` — Main analyzer class

**Functions:**
- Various helper methods for altitude filtering and time calculations
- Duplicates some logic with ephemeris.py (twilight times, constraints)

**LOC:** 390  
**Duplication:** ⚠️ Twilight calculation logic overlaps with ephemeris.py

#### transit-photometry/scripts/generate_skews.py
**Purpose:** Parse AIJ aperture definitions from radec CSV → JSON skews for PixInsight  
**Classes:**
- `ApertureEntry` (dataclass) — Aperture radius triplet + target coord

**Functions:**
- `_sexagesimal_to_degrees(value: str, *, is_ra: bool) → float` — **Duplicate of coord_utils.py**
- `_parse_radec(path: Path) → List[ApertureEntry]` — **~40 LOC**
- `_read_aperture_csv(path: Path) → List[ApertureEntry]` — **~40 LOC**

**Duplication:** 🔴 **_sexagesimal_to_degrees is not imported from astrolib; it's redefined inline**

---

## 2. FITS FILE HANDLING & METADATA

### Files & Classes/Functions

| File | Location | Type | Key Functions | LOC |
|------|----------|------|----------------|-----|
| **fits_utils.py** | astrolib/ | Utility | `read_fits_header()`, `read_focus_position()`, `read_exposure_time()` | 37 |
| **camera_noise_characterization.py** | camera-noise/ | Analysis | `FITSHistogramAnalyzer` class | 1,202 |
| **focus_parser.py** | focus-analyzer/ | Parser | `parse_autorun_log()`, `parse_fits_file()`, `gather_fits_data()` | 482 |
| **flatfield_analyzer.py** | flatfield-analyzer/ | Analysis | `_read_flat()`, `FrameStats` dataclass | 645 |
| **dashboard.py** | session-quality/astro_utils/ | Analysis | FITS frame loading in star analysis pipeline | 3,340 |
| **star_analysis.py** | session-quality/astro_utils/ | Analysis | `analyze_frame()` reads FITS + HFR calculation | 590 |
| **piprocessor.py** | (if exists) | — | (referenced by astro-piper) | — |

### Inventory Details

#### astrolib/fits_utils.py
**Purpose:** Lightweight FITS header reading  
**Functions:**
- `read_fits_header(file_path: Path) → Dict[str, Any]` — **4 LOC**
  - Simple wrapper around astropy.io.fits.open()
  
- `get_header_value(header: fits.Header, keys: List[str]) → Optional[Any]` — **6 LOC**
  - Fallback key lookup (returns first match)
  
- `read_focus_position(file_path: Path) → Optional[int]` — **10 LOC**
  - Extracts FOCUSPOS header value
  
- `read_exposure_time(header: fits.Header) → Optional[float]` — **10 LOC**
  - Checks EXPTIME or EXPOSURE keys

**Usage:** Imported by focus_parser.py, focus-analyzer, session-quality  
**Note:** Simple and reusable; good abstraction

#### camera-noise/camera_noise_characterization.py
**Purpose:** Characterize thermal+ read noise from bias frames  
**Classes:**
- `SimpleLogger` — Internal logging wrapper (120 LOC)
- `FITSGroup` (dataclass) — Metadata for grouped bias frames
- `GaussianFitResult` (dataclass) — Fit statistics
- `FITSHistogramAnalyzer` — Main analysis class (~1,000 LOC)

**Key Methods:**
- `get_header_value()` — **Duplicate of fits_utils.py**
- `group_fits_files(directory_path: str, exptime_value: Optional[float])` — Groups by GAIN/TEMP
- `process_group(group: FITSGroup)` — Gaussian fitting to pixel count distributions
- `calculate_kde_analysis()` — Non-parametric noise density

**File Size:** 1,202 LOC (largest single file in camera-noise)  
**Duplication:** 🔴 `get_header_value()` is redefined; should import from astrolib

#### focus-analyzer/focus_parser.py
**Purpose:** Parse NINA autofocus logs + FITS headers → Excel workbooks  
**Classes:**
- `FocusMeasurement` (dataclass) — Single AF measurement (stage + EAF position + star size)
- `FocusRun` (dataclass) — Complete autofocus run (success position + metadata)

**Key Functions:**
- `parse_autorun_log(path: Path) → Tuple[List[Dict], List[Dict]]` — **~180 LOC**
  - Regex parsing for AutoFocus begin/success/failure events
  - Measurement stages (Find Focus Star, Calculate V-Curve, Calculate Focus Point)
  
- `parse_fits_file(path: Path) → Optional[Dict[str, object]]` — **~40 LOC**
  - Extracts frame count, exposure, temperature from FITS headers
  
- `gather_fits_data(root: Path) → pd.DataFrame` — **~20 LOC**
  - Walks directory tree; calls parse_fits_file() on all .fit/.fits files
  
- `detect_filter_from_path()` — Infers filter from directory names
- `detect_night_from_path()` — Extracts observation date from paths

**Dependencies:** astropy.io.fits, pandas, openpyxl  
**Usage:** Standalone CLI entry point for focus workbook generation

#### flatfield-analyzer/flatfield_analyzer.py
**Purpose:** Analyze flat-field uniformity + recommend illumination adjustments  
**Classes:**
- `FrameStats` (dataclass) — Per-frame illumination metrics (mean ADU, radiality, centroid)
- `FilterGroup` (dataclass) — Collection of flats for a single filter

**Key Functions:**
- `_read_flat(path: Path) → Tuple[np.ndarray, fits.Header]` — **10 LOC**
  - Reads primary HDU image data
  
- `_plate_scale_from_header()` — Extracts CDELT or PIXSCL for arcsec-to-pixel conversion
- `_radial_profile()` — Computes flux falloff vs. radius
- `_corner_center_ratio()` — Vignetting metric
- `_peak_valley_nonuniformity()` — CoV of illumination

**File Size:** 645 LOC  
**Duplication:** ⚠️ Some FITS reading logic overlaps with focus_parser.py; both handle similar data

#### session-quality/astro_utils/star_analysis.py
**Purpose:** HFR, flux, centroid extraction from FITS frames  
**Classes:**
- `StarMetrics`, `FrameStarStats`, `TrackingDiagnosis`, `FilterBaseline` (all dataclasses)
- `FrameFlags` (dataclass) — Quality flags per frame

**Key Functions:**
- `analyze_frame(fits_path: Path, ...) → FrameStarStats` — **~180 LOC**
  - Loads FITS header + image data
  - Centroid detection (2D Gaussian fit)
  - HFR calculation (Half-Flux Radius)
  - Flux normalization
  
- `calculate_hfr(data: np.ndarray, x: float, y: float) → float` — **~80 LOC**
  - Computes HFR from pixel array
  - Integrates flux in annuli around centroid
  
- `analyze_frames(fits_paths: List[Path]) → List[FrameStarStats]` — Batch processor

**File Size:** 590 LOC  
**Note:** Heavy use of scipy.ndimage for centroid finding; not using astropy Gaussian fitting

#### session-quality/astro_utils/dashboard.py
**Purpose:** Multi-page HTML dashboard generation for session quality  
**Classes:** Multiple large dataclasses + main analysis classes
- `ImageData`, `GuidingFrame`, `GuidingStats`, `AutofocusEvent`, etc.
- `SessionAnalyzer` — Main orchestrator (~200 LOC)
- `QualityScorer` — Scoring logic (~220 LOC)
- `DashboardGenerator` — HTML generation (~400 LOC)

**File Size:** 3,340 LOC (⚠️ **Monolithic**)  
**Functions:** ~20 internal helper functions  
**Note:** Biggest file in astro-pipeline; needs modularization

---

## 3. EPHEMERIS & CELESTIAL OBJECT POSITIONS

### Files & Classes/Functions

| File | Location | Type | Key Functions | LOC |
|------|----------|------|----------------|-----|
| **ephemeris.py** | astrolib/ | Module | `get_targets()`, `calculate_ephemeris()` | 192 |
| **pick_targets.py** | transit-photometry/target-selector/ | Script | `fetch_exoclock_planets()`, `build_dataframe()` | 242 |

### Inventory Details

#### astrolib/ephemeris.py (covered above in Coordinates section)

#### transit-photometry/target-selector/pick_targets.py
**Purpose:** Download exoplanet list from ExoClock API; score by observable window + depth + mag  
**Key Functions:**
- `fetch_exoclock_planets(json_cache: Optional[str]) → List[Dict[str, Any]]` — **~30 LOC**
  - calls astroquery / ExoClock REST API
  - Returns list of transit targets with ephemeris

- `build_dataframe(raw: List[Dict], ...)` — **~45 LOC**
  - Normalizes JSON structure
  - Computes observability scores
  - Filters by magnitude range

- `compute_score(depth_ppt: float, duration_h: float, mag: float) → float` — **~10 LOC**
  - Weighted score: depth × duration × (10−mag)

- `interp_noise_10min_ppt(mag: float) → float` — **~15 LOC**
  - Lookup curve for photometric noise as function of magnitude
  - Used for SNR estimation

**Dependencies:** astroquery, pandas  
**File Size:** 242 LOC  
**Note:** Standalone script; does some ephemeris work but doesn't use astrolib.ephemeris module

---

## 4. PHOTOMETRY & APERTURE OPERATIONS

### Files & Classes/Functions

| File | Location | Type | Functions | LOC |
|------|----------|------|-----------|-----|
| **batch_reduce.py** | transit-photometry/scripts/ | Reduction | `detrend_flux()`, `bin_series()`, `sg_like()`, `render_curve()` | 481 |
| **generate_skews.py** | transit-photometry/scripts/ | Helper | `ApertureEntry`, aperture CSV I/O | 337 |
| **star_analysis.py** | session-quality/astro_utils/ | Analysis | `analyze_frame()`, `calculate_hfr()` | 590 |

### Inventory Details

#### transit-photometry/scripts/batch_reduce.py
**Purpose:** Reduce AIJ-exported light curves (csv) → transit model fits + plots  
**Classes:**
- `ProcessedCurve` (dataclass) — Reduced light curve + fit result

**Key Functions:**
- `detrend_flux(time_jd: np.ndarray, flux: np.ndarray) → (np.ndarray, np.ndarray)` — **~20 LOC**
  - Linear fit to out-of-transit baseline
  - Returns normalized flux + trend
  - Uses OoT mask: time < 30% or time > 70% of window

- `bin_series(time_jd, flux, bin_sec)` — **~30 LOC**
  - Bins time series by specified duration (seconds)
  - Computes mean flux + SEM per bin

- `sg_like(time_jd, flux, window=7, order=2)` — **~25 LOC**
  - Rolling local polynomial smoothing (Savitzky-Golay approximation)
  - Uses local polynomial fits instead of scipy.signal.savgol_filter

- `compute_wrms(time_jd, flux, sem, ingress_jd, egress_jd)` → float — **~15 LOC**
  - Weighted RMS of flux in transit, normalized by error

- `render_curve()` — **~75 LOC**
  - Matplotlib figure generation with transit overlay

- `utc_to_jd()`, `parse_time_string()` — Time conversions
- `load_ephemeris()` — Loads target ephemeris from YAML
- `choose_time_column()`, `choose_flux_column()` — CSV column selection

**File Size:** 481 LOC  
**Dependencies:** numpy, pandas, astropy (for transit model), matplotlib  
**Note:** Core data reduction logic for transit photometry workflow

#### transit-photometry/scripts/generate_skews.py
**Purpose:** Aperture CSV → PixInsight macro generator  
**Classes:**
- `ApertureEntry` (dataclass) — (rap, rin, rout, target_ra, target_dec)

**Functions:**
- `_parse_radec(path: Path)` — Extract target coords from CSV rows
- `_read_aperture_csv()` — Deserialize CSV
- `_write_aperture_csv()` — Serialize CSV
- `_write_macro()` — Generate PixInsight batch macro
- `_run_macros()` — Execute macros via PixInsight.bat

**File Size:** 337 LOC  
**Note:** Integrates with PixInsight for AIJ aperture generation

---

## 5. DATA REDUCTION & DETRENDING

### Files & Classes/Functions

| File | Location | Type | Functions | LOC |
|------|----------|------|-----------|-----|
| **batch_reduce.py** | transit-photometry/scripts/ | Reduction | `detrend_flux()`, `sg_like()`, `compute_wrms()` | 481 |
| **linear_processing.py** | astro-piper/stages/ | Stage | Bias subtraction, flat correction, etc. | 421 |
| **preprocessing.py** | astro-piper/stages/ | Stage | Debayer, cosmic ray removal | 623 |
| **nonlinear.py** | astro-piper/stages/ | Stage | Dark frame subtraction | 379 |

### Inventory Details

#### astro-piper/stages/linear_processing.py
**Purpose:** Bias/dark subtraction, flat correction, master calibration  
**Classes:**
- `LinearProcessingStage` — Main orchestrator

**Functions:**
- `apply_bias_subtraction()` — Subtracts master bias frame
- `apply_flat_correction()` — Divides by normalized flat
- `load_calibration_frame()` — FITS I/O for bias/flats/darks

**File Size:** 421 LOC  
**Part of:** astro-piper processing pipeline

#### astro-piper/stages/preprocessing.py
**Purpose:** Debayer, cosmic ray removal, hot pixel masking  
**Classes:**
- `PreprocessingStage`

**Functions:**
- `debayer_image()` — CFA → RGB conversion
- `remove_cosmic_rays()` — Uses sigma-clipping or median filtering
- `apply_hot_pixel_mask()`

**File Size:** 623 LOC

#### astro-piper/stages/nonlinear.py
**Purpose:** Non-linear data reduction (DBE, shadow mask)  
**Dynamic Background Elimination (DBE)** + mask propagation

**File Size:** 379 LOC

---

## 6. NOISE ANALYSIS & CHARACTERIZATION

### Files & Classes/Functions

| File | Location | Type | Key Functions | LOC |
|------|----------|------|----------------|-----|
| **camera_noise_characterization.py** | camera-noise/ | Analysis | `FITSHistogramAnalyzer`, Gaussian fitting | 1,202 |
| **math_utils.py** | astrolib/ | Utility | `gaussian()`, `compute_rms()` | 16 |
| **utils.py** | session-quality/astro_utils/ | Utility | `gaussian()`, `compute_rms()`, `moving_average()` | 67 |

### Inventory Details

#### camera-noise/camera_noise_characterization.py (covered above)

#### astrolib/math_utils.py
**Purpose:** Reusable math functions  
**Functions:**
- `gaussian(x: np.ndarray, amplitude, mean, std) → np.ndarray` — **5 LOC**
  - Evaluate 1-D Gaussian at Array x

- `compute_rms(values: np.ndarray) → float` — **3 LOC**
  - RMS = sqrt(mean(x²))

- `moving_average(data: np.ndarray, window: int) → np.ndarray` — **5 LOC**
  - Convolution with boxcar kernel

**File Size:** 16 LOC  
**Duplication:** ⚠️ **Identical copies in session-quality/astro_utils/utils.py**

#### session-quality/astro_utils/utils.py
**Purpose:** Utilities for session analysis  
**Functions:**
- `gaussian()` — **DUPLICATE of mathutils.py **
- `compute_rms()` — **DUPLICATE of math_utils.py**
- `moving_average()` — **DUPLICATE of math_utils.py**
- `read_fits_header()` — **DUPLICATE of astrolib/fits_utils.py**
- Other: datetime parsing, directory validation, etc.

**File Size:** 67 LOC  
**Duplication:** 🔴 Multiple functions copied rather than imported

---

## 7. OPTICAL MODELING (Focal Ratios, F-numbers, Pixel Grids)

### Files & Classes/Functions

| File | Location | Type | Functions | LOC |
|------|----------|------|-----------|-----|
| **fnumber_pixel_grid.py** | optical-modeling/ | Simulation | `simulate_ext()`, `simulate_star()`, `label_arcsec()` | 168 |
| **calibration_analysis.py** | flatfield-sim/ | Analysis | `gaussian_grid_model()`, `lorentzian_grid_model()` | 306 |

### Inventory Details

#### optical-modeling/fnumber_pixel_grid.py
**Purpose:** Visualize PSF vs. focal length / f-number / pixel resolution  
**Functions:**
- `simulate_ext(focal_length, f_number, ...) → (grid, D, pixel_mm)` — **~20 LOC**
  - Extended source: elliptical Gaussian in focal plane

- `simulate_star(focal_length, f_number, ...) → (grid, D, pixel_mm)` — **~15 LOC**
  - Point source: PSF scaled by collecting area A = π(D/2)²

- `label_arcsec(ax, pxmm, fl, npix, grid_step)` — **~25 LOC**
  - Overlay arcsecond scale + grid on matplotlib axis

- `main(show=False)` — **~150 LOC**
  - Generates 3 figures: extended grid, point source grid, resolution sweep

**File Size:** 168 LOC  
**Output:** PNG images showing flux distribution across pixel grids

#### flatfield-sim/calibration_analysis.py
**Purpose:** Fit flatfield profiles (Gaussian vs. Lorentzian vs. Voigt)  
**Functions:**
- `gaussian_grid_model(x_mm: np.ndarray, sigma) → np.ndarray` — **~15 LOC**
- `lorentzian_grid_model(x_mm: np.ndarray, gamma) → np.ndarray` — **~15 LOC**
- `voigt_approx_grid_model(x_mm, sigma, gamma)` — **~23 LOC**
- `fit_sigma_bruteforce()` — Grid search for Gaussian width
- `fit_sigma_scipy()` — SciPy curve_fit for Gaussian
- `fit_lorentzian()` — Brute force grid search
- `fit_voigt()` — SciPy optimizer

**File Size:** 306 LOC  
**Dependencies:** numpy, scipy.optimize, matplotlib

---

## 8. DATA I/O & FILE MANAGEMENT

### Files & Classes/Functions

| File | Location | Type | Functions | LOC |
|------|----------|------|-----------|-----|
| **fits_utils.py** | astrolib/ | Utility | FITS header I/O | 37 |
| **log_parsers.py** | astrolib/ | Utility | NINA log regex patterns | 40 |
| **focus_parser.py** | focus-analyzer/ | Parser | FITS frame + log parsing + Excel export | 482 |
| **report.py** | astrolib/ | Utility | Markdown/PDF report generation | 95 |

### Inventory Details

#### astrolib/log_parsers.py
**Purpose:** NINA Autorun log regex patterns (shared)  
**Regex Patterns:**
- `TIMESTAMP_RE` — YYYY/MM/DD HH:MM:SS format
- `AUTORUN_BEGIN_RE` — [Autorun|Begin] target name
- `SHOOTING_RE` — Extracts binning from shooting descriptor
- `AUTOFOCUS_BEGIN_RE` — Extracts exp, bin, temperature
- `MEASUREMENT_RE` — AF measurement stages (star size, EAF position)
- `AUTOFOCUS_SUCCESS_RE` — Final focused position
- `AUTOFOCUS_FAIL_RE` — AF failure flag

**Functions:**
- `parse_timestamp(raw_ts: str) → Optional[datetime]` — **8 LOC**

**File Size:** 40 LOC  
**Usage:** focus-analyzer/focus_parser.py, session-quality modules  
**Note:** Good shared pattern library

#### astrolib/report.py
**Purpose:** Report generation (Markdown → PDF via pandoc)  
**Functions:**
- `ensure_report_dir()` — Create reports/ directory
- `generate_report_filename()` — Timestamp-based filename
- `save_markdown_report()` — Write .md file
- `convert_md_to_pdf()` — Invoke pandoc
- `generate_and_save_reports()` — Orchestrator

**File Size:** 95 LOC  
**Dependencies:** pypandoc  
**Usage:** astro-agent.py, session-quality dashboard

#### focus-analyzer/focus_parser.py
**Purpose:** Parse NINA logs + FITS headers → Excel workbooks  
**Output:** Two sheets:
  - Measurements (one row per AF measurement)
  - Runs (one row per AF run summary)

**File Size:** 482 LOC  
**Stand-alone Entry Point:** Yes (CLI arg parsing included)

---

## 9. TIME/DATE HANDLING

### Files & Classes/Functions

| File | Location | Type | Functions | LOC |
|------|----------|------|-----------|-----|
| **log_parsers.py** | astrolib/ | Utility | `parse_timestamp()` | 40 |
| **batch_reduce.py** | transit-photometry/scripts/ | Script | `utc_to_jd()`, `parse_time_string()` | 481 |
| **utils.py** | session-quality/astro_utils/ | Utility | `parse_datetime()`, `format_time_delta()` | 67 |
| **astro_logger.py** | session-quality/astro_utils/ | Utility | Timestamp logging | 316 |

### Inventory Details

#### astrolib/log_parsers.py
- `parse_timestamp(raw_ts: str) → Optional[datetime]` — YYYY/MM/DD HH:MM:SS format

#### transit-photometry/scripts/batch_reduce.py
- `utc_to_jd(year, month, day, hour, minute, second) → float` — **~10 LOC**
  - Gregorian → Julian Day (includes leap second corrections)
  
- `parse_time_string(value: str) → float` — **~20 LOC**
  - ISO 8601 (YYYY-MM-DD HH:MM:SS.SSZ) → JD
  - Used for ephemeris time parsing

#### session-quality/astro_utils/utils.py
- `parse_datetime(dt_str, formats)` — Multi-format parser
- `format_time_delta(seconds) → str` — Human-readable duration (HH:MM:SS)

---

## 10. PLOTTING & VISUALIZATION UTILITIES

### Files & Classes/Functions

| File | Location | Type | Functions | LOC |
|------|----------|------|-----------|-----|
| **batch_reduce.py** | transit-photometry/scripts/ | Script | `render_curve()` | 481 |
| **fnumber_pixel_grid.py** | optical-modeling/ | Script | `label_arcsec()`, `main()` | 168 |
| **flatfield_analyzer.py** | flatfield-analyzer/ | Analysis | `plot_filter_report()`, `plot_summary()` | 645 |
| **dashboard.py** | session-quality/astro_utils/ | Analysis | `DashboardGenerator` class (HTML) | 3,340 |
| **calibration_analysis.py** | flatfield-sim/ | Script | Various plotting functions | 306 |

### Inventory Details

#### transit-photometry/scripts/batch_reduce.py
- `render_curve(time_jd, flux, npix, ...)` → Path
  - Matplotlib figure: light curve + transit model overlay
  - Saves PNG, JSON log

#### flatfield-analyzer/flatfield_analyzer.py
- `plot_filter_report(group, sample_header, out_dir)` — **~130 LOC**
  - Per-filter visualization: 4-panel grid
  - Flat master, radial profile, corner vignetting, histogram
  
- `plot_summary(groups, out_dir)` — **~60 LOC**
  - Multi-filter summary: mean ADU vs. filter
  
- `_cmap()` — Custom colormap for master flats

#### session-quality/astro_utils/dashboard.py
- `DashboardGenerator` class — Produces multi-page HTML
  - GIF/MP4 frame animations
  - Guiding statistics plots (matplotlib → base64)
  - Session timeline

---

## Cross-Module Dependency Graph

```
┌─────────────────────────────────────────────────────────────┐
│                       astrolib (shared)                      │
│  ├─ coord_utils.py         [sexagesimal→degrees]            │
│  ├─ fits_utils.py          [FITS header I/O]                │
│  ├─ log_parsers.py         [NINA log regex]                 │
│  ├─ math_utils.py          [gaussian, RMS, MA]              │
│  ├─ ephemeris.py           [astroplan targets]              │
│  ├─ equipment.py           [telescope params]               │
│  ├─ astro_agent.py         [LLM-based planning]             │
│  └─ report.py              [MD→PDF via pandoc]              │
└─────────────────────────────────────────────────────────────┘
         ↑           ↑            ↑            ↑
         │           │            │            │
    ┌────┴────┐  ┌──┴──┐  ┌──────┴────┐  ┌───┴────┐
    │  transit│  │focus│  │  session- │  │flatfield
    │ photom  │  │analy│  │  quality  │  │analyzer
    │         │  │zer  │  │           │  │
    └────┬─────┘  └──┬──┘  └───┬──────┘  └────┬────┘
         │           │         │              │
    ┌────┴──────┐    │    ┌────┴──┐       ┌───┴─────┐
    │ batch_    │    └────┤ utils │       │ camera- │
    │ reduce.py │         └───────┘       │ noise   │
    │ generate_ │                         └─────────┘
    │ skews.py  │
    │ pick_     │
    │ targets   │
    └───────────┘

    ┌─────────────────────────────────────────────┐
    │   astro-piper (PixInsight automation)       │
    │  ├─ pjsr_generator.py  [PI script gen]      │
    │  ├─ stages/            [processing pipeline]│
    │  └─ orchestrator.py    [main driver]        │
    └─────────────────────────────────────────────┘

    ┌──────────────────────────────────┐
    │  flatfield-sim (optical model)    │
    │  ├─ calibration_analysis.py       │
    │  ├─ calibration_extended.py       │
    └──────────────────────────────────┘

    ┌──────────────────────────────────┐
    │    optical-modeling               │
    │    └─ fnumber_pixel_grid.py       │
    └──────────────────────────────────┘
```

---

## Duplication & Overlap Analysis

### 🔴 CRITICAL Duplications

| Function | Locations | Impact | Recommendation |
|----------|-----------|--------|-----------------|
| **sexagesimal_to_degrees()** | astrolib/coord_utils.py (46 LOC) + transit-photometry/scripts/generate_skews.py (69 LOC) | Code duplication; inconsistent behavior | Consolidate: Move to astrolib, import in generate_skews.py |
| **gaussian()** | astrolib/math_utils.py (5 LOC) + session-quality/astro_utils/utils.py (5 LOC) | Trivial copy | Consolidate: session-quality imports from astrolib |
| **compute_rms()** | astrolib/math_utils.py (3 LOC) + session-quality/astro_utils/utils.py (3 LOC) | Trivial copy | Consolidate |
| **moving_average()** | astrolib/math_utils.py (5 LOC) + session-quality/astro_utils/utils.py (5 LOC) | Trivial copy | Consolidate |
| **read_fits_header()** | astrolib/fits_utils.py (4 LOC) + camera-noise and session-quality/astro_utils/utils.py | Multiple copies | Consolidate: Use astrolib version across all modules |
| **get_header_value()** | astrolib/fits_utils.py (6 LOC) + camera-noise/camera_noise_characterization.py | Duplicated pattern match | Consolidate |

### ⚠️ MODERATE Overlaps (Similar Logic, Different Implementations)

| Category | Files | Issue | Suggestion |
|----------|-------|-------|-----------|
| **Timestamp parsing** | log_parsers.py, batch_reduce.py, focus_parser.py | Multiple implementations for NINA log timestamps | Create a unified TimeParser module in astrolib |
| **FITS frame loading** | focus_parser.py, star_analysis.py, flatfield_analyzer.py | Each role-specific; acceptable | Leave as-is (domain-specific); but standardize on astropy.io.fits |
| **Twilight/constraint logic** | ephemeris.py, altaz_analysis.py | Both compute astronomical twilight times | Document which is authoritative; consolidate if both used in session-quality |
| **Coordinate systems** | Multiple transit-photometry + session-quality files | RA/Dec ↔ Alt/Az transforms scattered | Create astrolib.coordinate_transforms.py with unified FrameTransform class |

### 🟡 STRUCTURAL Issues

1. **session-quality/astro_utils/dashboard.py (3,340 LOC)** — Monolithic
   - **Recommendation:** Split into:
     - `image_analysis.py` (frame statistics, HFR)
     - `guiding_analysis.py` (PHD2 log parsing + metrics)
     - `html_templates.py` (report generation)
     - `scoring.py` (quality scoring logic)

2. **camera-noise/camera_noise_characterization.py (1,202 LOC)** — Standalone script
   - **Recommendation:** Refactor into:
     - `fits_grouper.py` — Organize by GAIN/TEMP
     - `gaussian_fitter.py` — Fit pixel distributions
     - `main.py` — CLI orchestrator

3. **astro-piper/pjsr_generator.py (2,003 LOC)** — PixInsight script generator
   - **Recommendation:** This is acceptable given PixInsight's domain-specific nature
   - But consider extracting submodules: template_engine.py, symbol_resolver.py

---

## Module-by-Module Size & Complexity

| Module | Total LOC | Files | Avg Size | Complexity | Recommendation |
|--------|-----------|-------|----------|------------|-----------------|
| **astro-piper** | 5,942 | 13 files | 457 LOC/f | HIGH | Comprehensive testing ✓; pjsr_generator is complex but necessary |
| **session-quality** | 5,755 | 10 files | 576 LOC/f | HIGH | Refactor dashboard.py; extract test suite |
| **camera-noise** | 1,202 | 1 file | 1,202 LOC | HIGH | Modularize into 3-4 files |
| **flatfield-analyzer** | 645 | 1 file | 645 LOC | MEDIUM | Acceptable size; consider test coverage |
| **focus-analyzer** | 482 | 1 file | 482 LOC | MEDIUM | Acceptable; add pytest suite |
| **flatfield-sim** | 772 | 3+ files | 257 LOC/f | MEDIUM | Well-structured |
| **transit-photometry** | 1,061 | 5 files | 212 LOC/f | MEDIUM | Well-organized; pick_targets.py is isolated |
| **astrolib** | 1,034 | 9 files | 115 LOC/f | LOW | Excellent shared library; add more utilities |
| **optical-modeling** | 168 | 1 file | 168 LOC | LOW | Acceptable; single-purpose script |
| **flatfield-sim** | — | — | — | — | Supports calibration; low priority |

**Total:** ~16,700 LOC

---

## Opportunities for Shared Utilities

### Priority 1: Consolidate Duplicated Functions (Quick Win)

```python
# NEW: astrolib/timekeeping.py
def parse_utc_to_jd(year: int, month: int, day: int, hour: int, minute: int, second: float) -> float:
    """Gregorian UTC → Julian Day"""
    ...

def parse_iso_to_jd(iso_string: str) -> float:
    """ISO 8601 → Julian Day"""
    ...

def parse_nina_timestamp(raw_ts: str) -> datetime:
    """NINA log format YYYY/MM/DD HH:MM:SS"""
    ...

# NEW: astrolib/coordinate_transforms.py
def sexagesimal_to_degrees(value: str, *, is_ra: bool) -> float:
    """Consolidate from multiple locations"""
    ...

def ra_dec_to_altaz(ra: float, dec: float, observer_lat: float, observer_lon: float, obstime: Time) -> Tuple[float, float]:
    """Unified coordinate transformation"""
    ...
```

**Impact:** Eliminate 50+ LOC of duplication; standardize behavior across modules

### Priority 2: Extract session-quality Submodules

```
session-quality/
├─ astro_utils/
│  ├─ core/              # NEW: Core analysis classes
│  │  ├─ image_analyzer.py     (extract from dashboard.py → star_analysis.py)
│  │  ├─ guiding_analyzer.py   (extract from dashboard.py → phd2_analysis.py)
│  │  └─ autofocus_analyzer.py (keep focus_parser.py)
│  ├─ reporting/         # NEW: Report generation
│  │  ├─ html_generator.py     (extract from dashboard.py)
│  │  └─ quality_scorer.py     (extract from dashboard.py)
│  └─ dashboard.py       # (orchestrator; call new modules)
```

**Impact:** Reduce dashboard.py from 3,340 → ~800 LOC; improve testability

### Priority 3: Create astrolib.optical submodule

```python
# astrolib/optical.py
class OpticalSystem:
    """Focal length, f-number, aperture, pixel grid"""
    def __init__(self, focal_length_mm: float, f_number: float, pixel_scale_um: float, sensor_size_mm: float):
        ...
    
    def pixel_scale_arcsec(self) -> float:
        """Compute plate scale"""
        ...
    
    def psf_profile(self, wavelength_nm: float, seeing_arcsec: float) -> Tuple[np.ndarray, np.ndarray]:
        """PSF on pixel grid"""
        ...
```

**Impact:** Consolidates fnumber_pixel_grid.py + flatfield-sim logic; reusable across modules

---

## Shared Library (astrolib) Recommendations

### Current State
- ✓ Good: Lightweight, focused on astronomy fundamentals
- ✓ Good: No heavy dependencies (relies on astropy, astroquery, astroplan)
- ⚠️ Missing: Coordinate frame transforms (RA/Dec ↔ Alt/Az)
- ⚠️ Missing: Unified time handling (JD, MJD, UTC, ISO)
- ⚠️ Missing: Optical system modeling

### Proposed astrolib Expansion

```
astrolib/
├─ __init__.py
├─ coord_utils.py        (sexagesimal parsing) ← CONSOLIDATE INPUT
├─ coordinate_transforms.py  (NEW: Alt/Az, RA/Dec conversions)
├─ timekeeping.py        (NEW: UTC↔JD, datetime handling)
├─ optical.py            (NEW: Optics modeling)
├─ fits_utils.py         (KEEP: Header I/O)      ← CONSOLIDATE INPUT
├─ math_utils.py         (KEEP: Gaussian, RMS, MA)  ← CONSOLIDATE INPUT
├─ log_parsers.py        (KEEP: NINA patterns)
├─ ephemeris.py          (KEEP: astroplan targets)
├─ equipment.py          (KEEP: Telescope specs)
├─ astro_agent.py        (KEEP: LLM integration)
└─ report.py             (KEEP: MD→PDF)
```

---

## Test Coverage Audit

| Module | Test Files | Test LOC | Coverage | Status |
|--------|------------|----------|----------|--------|
| **astro-piper** | 8 test files | 1,739 LOC | ~70% | ✓ Good |
| **session-quality** | None | 0 LOC | Unknown | ✗ Missing |
| **transit-photometry** | None | 0 LOC | Unknown | ✗ Missing |
| **focus-analyzer** | None | 0 LOC | Unknown | ✗ Missing |
| **camera-noise** | None | 0 LOC | Unknown | ✗ Missing |
| **flatfield-analyzer** | None | 0 LOC | Unknown | ✗ Missing |
| **astrolib** | None | 0 LOC | Unknown | ✗ Missing |

**Recommendation:** Add pytest suites for:
1. astrolib (utilities → baseline tests)
2. focus-analyzer (deterministic log parsing)
3. camera-noise (statistical fitting)

---

## Summary: What to Consolidate

### 🔴 IMMEDIATE (Next Sprint)

1. **Eliminate Function Duplication**
   - Move `sexagesimal_to_degrees()` to astrolib
   - Move `gaussian()`, `compute_rms()`, `moving_average()` to astrolib
   - Update all imports

2. **Standardize FITS I/O**
   - Use astrolib/fits_utils.py wherever FITS headers are read
   - Remove duplicates from camera-noise, session-quality

3. **Extract Time Handling**
   - Create astrolib/timekeeping.py
   - Consolidate: utc_to_jd(), parse_iso_to_jd(), parse_nina_timestamp()

### 🟡 SHORT-TERM (2-4 Weeks)

1. **Refactor session-quality/dashboard.py**
   - Split into 4 modules (image_analysis, guiding_analysis, scoring, html_generation)
   - Add test suite

2. **Modularize camera-noise**
   - Extract FITSGrouper, GaussianFitter, main orchestrator
   - Add test suite

3. **Add Coordinate Transforms to astrolib**
   - Consolidate Alt/Az ↔ RA/Dec logic
   - Add documentation on reference frames (ICRS, AltAz, etc.)

### 🟢 MEDIUM-TERM (1-2 Months)

1. **Create astrolib/optical.py**
   - Unify optical system modeling (fnumber_pixel_grid.py + flatfield-sim)

2. **Add test suites** to remaining modules

3. **Documentation audit** — Ensure each module has clear docstrings

---

## File Manifest

### astrolib/ (Shared Library)
```
astrolib/
├─ __init__.py                    (1 LOC)
├─ coord_utils.py                 (46 LOC)     ← Input for consolidation
├─ fits_utils.py                  (37 LOC)     ← Input for consolidation
├─ log_parsers.py                 (40 LOC)
├─ math_utils.py                  (16 LOC)     ← Input for consolidation
├─ ephemeris.py                   (192 LOC)
├─ equipment.py                   (82 LOC)
├─ astro_agent.py                 (546 LOC)
└─ report.py                       (95 LOC)
```

### transit-photometry/ (Exoplanet Transit Workflow)
```
transit-photometry/
├─ target-selector/
│  ├─ _check_data.py              (23 LOC)
│  └─ pick_targets.py             (242 LOC)    ← Uses 3x sexagesimal logic
├─ scripts/
│  ├─ batch_reduce.py             (481 LOC)    ← Reduction + fitting
│  ├─ generate_skews.py           (337 LOC)    ← AIJ apertures → PixInsight
│  └─ transit_model.py            (102 LOC)    ← Mandel-Agol transit model
```

### session-quality/ (Observing Session Analysis)
```
session-quality/
├─ run_night_quality.py           (176 LOC)
├─ run_phd2_analysis.py           (89 LOC)
├─ run_autofocus_analysis.py      (89 LOC)
├─ run_altaz_analysis.py          (98 LOC)
└─ astro_utils/
   ├─ __init__.py                 (28 LOC)
   ├─ astro_logger.py             (316 LOC)    ← Logging
   ├─ config.py                   (87 LOC)     ← Config loader
   ├─ utils.py                    (67 LOC)     ← DUPLICATES astrolib functions
   ├─ dashboard.py                (3,340 LOC)  ← 🔴 MONOLITHIC; needs refactor
   ├─ phd2_analysis.py            (392 LOC)    ← Guiding stats
   ├─ autofocus_analysis.py       (302 LOC)    ← AF event analysis
   ├─ altaz_analysis.py           (390 LOC)    ← Alt/Az filtering
   └─ star_analysis.py            (590 LOC)    ← HFR, centroid, flux
```

### camera-noise/
```
camera-noise/
└─ camera_noise_characterization.py (1,202 LOC)  ← 🔴 MONOLITHIC
    (Includes: SimpleLogger, FITSGroup, GaussianFitResult, FITSHistogramAnalyzer)
```

### focus-analyzer/
```
focus-analyzer/
└─ focus_parser.py                (482 LOC)
    (Includes: FocusMeasurement, FocusRun dataclasses + parsing logic)
```

### flatfield-analyzer/
```
flatfield-analyzer/
└─ flatfield_analyzer.py          (645 LOC)
    (Includes: FrameStats, FilterGroup dataclasses + vignetting analysis)
```

### flatfield-sim/
```
flatfield-sim/
├─ calibration_analysis.py        (306 LOC)    ← Gaussian/Lorentzian profile fitting
├─ calibration_extended.py        (241 LOC)
└─ calibration_corrected_analysis.py (225 LOC)
```

### optical-modeling/
```
optical-modeling/
└─ fnumber_pixel_grid.py          (168 LOC)    ← PSF vs. optics simulator
```

### astro-piper/ (PixInsight Automation)
```
astro-piper/
├─ pjsr_generator.py              (2,003 LOC)  ← PixInsight script generation
├─ orchestrator.py                (546 LOC)
├─ calibration_master_builder.py  (616 LOC)
├─ graxpert_runner.py             (284 LOC)
├─ pi_runner.py                   (245 LOC)
├─ stages/
│  ├─ __init__.py                 (360 LOC)
│  ├─ preprocessing.py            (623 LOC)
│  ├─ linear_processing.py        (421 LOC)
│  ├─ nonlinear.py                (379 LOC)
│  ├─ star_processing.py          (347 LOC)
│  └─ stretching.py               (325 LOC)
├─ scripts/
│  └─ spike_test.py               (268 LOC)
├─ tests/
│  ├─ test_config.py              (137 LOC)
│  ├─ test_preprocessing.py       (790 LOC)
│  ├─ test_linear_processing.py   (503 LOC)
│  ├─ test_nonlinear.py           (325 LOC)
│  ├─ test_star_processing.py     (413 LOC)
│  ├─ test_stretching.py          (347 LOC)
│  ├─ test_orchestra.py           (167 LOC)
│  └─ test_pjsr_generator.py      (656 LOC)
```

---

## Conclusion

### Key Findings

1. **Duplication occurs at 3 levels:**
   - **Trivial functions** (math, utilities) — Consolidate imports
   - **Pattern matching** (regex, timestamp parsing) — Create shared validators
   - **Domain logic** (optics, photometry) — Refactor into reusable classes

2. **astro-piper is well-structured** with comprehensive tests; use as a model

3. **session-quality needs refactoring** — dashboard.py at 3,340 LOC is unmaintainable

4. **astrolib is underutilized** — Expand with coordinate transforms, time handling, optical models

### Recommended Priority Order

1. **Week 1:** Consolidate duplicated functions (Quick wins; ~50 LOC elimination)
2. **Week 2:** Refactor session-quality (Improves maintainability)
3. **Week 3:** Add coordinate transforms to astrolib (Unifies frame handling)
4. **Month 2:** Extract tests for non-astro-piper modules

---

**Next Steps:** Generate implementation plan for Phase 1 consolidation.
