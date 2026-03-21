# Organizing Multi-Use Scientific Toolkits: Philosophy & Architecture

**Context:** The "astro-pipeline" repo is actually a **polyphonic astronomical toolkit** — multiple independent scientific pursuits (exoplanet photometry, equipment characterization, optical modeling, session analysis) that share hardware, software patterns, and fundamental libraries. This is distinct from a "pipeline" (linear flow) and requires different organizational thinking.

---

## Part 1: Naming & Identity

### Current Problem
- "astro-pipeline" implies a sequential workflow, but the repo is actually:
  * **Exoplanet transit observation** (target selection → photometry → light curve)
  * **Optical characterization** (sensor noise, flat fields, pixel grids)
  * **Session analysis** (guiding metrics, autofocus tracking, Alt/Az constraints)
  * **Data reduction** (PixInsight automation, stretching, star removal)

These are *independent domains* that happen to use the same telescope and similar data types.

### Better Names (Ranked)
1. **astro-toolkit** — Emphasizes reusable components
2. **astro-lib-extended** — Emphasizes shared library with domain-specific tools
3. **astro-box** — Informal but clear: "a box of astronomy tools"
4. **astro-domains** — Emphasizes domain-driven design

**Recommendation:** Pick one that reflects your intent. Then organize directories by scientific domain.

---

## Part 2: Directory Organization Principles

### Principle 1: Shared Core at Root Level
```
astro-toolkit/
├─ astrolib/              # ← SHARED LIBRARY (all domains depend on this)
│  ├─ coord_utils         # Sexagesimal parsing, frame transforms
│  ├─ timekeeping         # UTC↔JD↔ISO conversions
│  ├─ fits_utils          # Header I/O, FITS validation
│  ├─ optical             # F-numbers, pixel scales, PSF models
│  ├─ photometry          # Aperture definitions, detrending
│  ├─ log_parsers         # NINA, PHD2, AIJ log patterns
│  └─ ephemeris           # Astroplan wrappers for targets
```

**Why Root Level?**
- Every domain tool imports from astrolib
- No circular dependencies possible
- Changes to shared utilities flow down to all consumers
- Single source of truth for fundamental algorithms

### Principle 2: Domain-Driven Subdirectories
```
astro-toolkit/
├─ astrolib/              # Shared
├─ exoplanet-photometry/  # DOMAIN 1: Transit observation + analysis
│  ├─ target-selector/    # Pre-observation: find targets
│  ├─ reduction/          # Post-observation: AIJ → light curve
│  └─ modeling/           # Fit transit models
├─ optical-characterization/  # DOMAIN 2: Sensor + optics
│  ├─ camera-noise/       # Read noise vs. gain/temp
│  ├─ flatfield-sim/      # Illumination profiles
│  └─ optical-modeling/   # F-number, pixel-scale grids
├─ session-analysis/      # DOMAIN 3: Observing session quality
│  ├─ guiding-metrics/    # PHD2 log analysis
│  ├─ autofocus-tracking/ # EAF position + star size
│  ├─ altaz-constraints/  # Altitude filtering
│  └─ image-quality/      # Frame HFR, centroids, flux
└─ data-reduction/        # DOMAIN 4: PixInsight automation
    ├─ pjsr-generator/    # Script generation
    ├─ stages/            # Pipeline stages (preprocess, linear, etc.)
    └─ templates/         # Reusable PixInsight macros
```

**Benefits:**
- Clear scientific purpose for each domain
- Self-contained tools can be independently useful
- Reduced cognitive load (each domain ~1-3 tools)
- Easy to find related code

### Principle 3: Intra-Domain Tool Nesting
Within each domain, related tools share a common directory:

```
exoplanet-photometry/
├─ README.md              # Workflow documentation
├─ requirements.txt       # Domain-specific dependencies
├─ target-selector/
│  ├─ pick_targets.py
│  ├─ requirements.txt    # (Usually same as domain-level)
│  └─ README.md
├─ reduction/
│  ├─ batch_reduce.py
│  ├─ generate_skews.py
│  └─ README.md
└─ modeling/
    ├─ transit_model.py
    └─ README.md
```

Why?
- Logical grouping reflects scientific workflow
- Dependencies flow in one direction (target → reduction → modeling)
- Easy to document the workflow in a single README

---

## Part 3: Shared Library (astrolib) Design

### Core Principle: Atomic Reusability
Each module in astrolib should be:
1. **Focused:** One clear responsibility (not a catch-all "utils")
2. **Testable:** Isolated from other modules
3. **Documented:** Docstrings + examples for each function
4. **Dependency-light:** Minimize external deps (astropy OK, pandas only if unavoidable)

### Proposed astrolib Structure

```
astrolib/
├─ coord_utils.py
│  └─ sexagesimal_to_degrees(value, is_ra) → float
│  └─ [future: other parsers]

├─ coordinate_transforms.py        # NEW
│  └─ ra_dec_to_altaz(...)
│  └─ altaz_to_ra_dec(...)
│  └─ apply_precession(...)

├─ timekeeping.py                  # NEW
│  └─ parse_utc_to_jd(year, month, day, hour, minute, second) → float
│  └─ parse_iso_to_jd(iso_string) → float
│  └─ parse_nina_timestamp(raw_ts) → datetime
│  └─ jd_to_iso(jd) → str

├─ optical.py                      # NEW
│  └─ class OpticalSystem
│      ├─ __init__(focal_length, f_number, pixel_scale, aperture)
│      ├─ plate_scale_arcsec() → float
│      ├─ psf_profile(wavelength, seeing) → (grid, intensity)
│      └─ pixel_grid_extent_arcsec() → float

├─ photometry.py                   # NEW
│  └─ detrend_flux(time, flux) → (flux_normed, trend)
│  └─ bin_series(time, flux, bin_seconds) → (time_binned, flux_binned, sem)
│  └─ compute_wrms(time, flux, sem, ingress, egress) → float
│  └─ class ApertureGrid
│      ├─ from_csv(path)
│      └─ to_json(path)

├─ fits_utils.py
│  └─ read_fits_header(path, keys) → dict
│  └─ validate_fits(path) → bool

├─ math_utils.py
│  └─ gaussian(amplitude, x, x0, sigma) → float
│  └─ compute_rms(data) → float
│  └─ moving_average(data, window) → np.ndarray
│  └─ piecewise_linear_interp(x_data, y_data, x) → float

├─ log_parsers.py
│  └─ class NINALogParser
│      ├─ parse_autorun(path) → List[AutorunSession]
│      ├─ parse_autofocus(path) → List[AutofocusEvent]
│  └─ class PHD2LogParser
│  └─ class AIJLogParser

├─ ephemeris.py
│  └─ get_targets(target_list) → List[FixedTarget]
│  └─ calculate_ephemeris(...) → EphemerisResult

├─ equipment.py
│  └─ load_equipment_specs(path) → dict

└─ report.py
    └─ generate_report_filename() → str
    └─ save_markdown_report(content, path) → None
```

### Guiding Maxim
**"If a function is used in two places, it lives in astrolib."**

This prevents duplication and forces thoughtful design. If a function is too domain-specific to be in astrolib, it stays in its domain tool.

---

## Part 4: Consolidation Strategy

### Phase 1: Quick Wins (Duplicates Elimination)
Target: 50-100 LOC removed from domains; functions moved to astrolib

**Actions:**
1. Move `sexagesimal_to_degrees()` to astrolib
2. Move math utilities (gaussian, RMS, moving_average) to astrolib
3. Standardize FITS header reading across all modules
4. All domains import from astrolib instead of redefining

**Cost:** 2-3 hours  
**Benefit:** Consistency + future maintenance ease

### Phase 2: Intermediate Refactoring (Monolith Splitting)
Target: Reduce dashboard.py from 3,340 → ~800 LOC

**Actions:**
1. Extract image_analysis.py (HFR, centroid calculations)
2. Extract guiding_analysis.py (PHD2 log parsing)
3. Extract html_generation.py (report rendering)
4. Extract quality_scoring.py (decision logic)

**Cost:** 6-8 hours  
**Benefit:** Testable, maintainable, reusable components

### Phase 3: New astrolib Modules
Target: Expand astrolib with coordinate transforms, time handling, optical modeling

**Actions:**
1. Create astrolib/coordinate_transforms.py (Alt/Az ↔ RA/Dec)
2. Create astrolib/timekeeping.py (UTC, JD, ISO conversions)
3. Create astrolib/optical.py (unify optical modeling from fnumber_pixel_grid + flatfield-sim)
4. Create astrolib/photometry.py (detrending, binning, WRMS)

**Cost:** 8-12 hours (design + implementation)  
**Benefit:** Reusable across multiple domains; solid foundation for future tools

---

## Part 5: Future-Proofing

### When to Consolidate Further
**Do consolidate** if:
- The same logic appears in 2+ tools
- You're copying-pasting code across domains
- The function is general enough for a new tool to use

**Don't consolidate** if:
- The logic is deeply domain-specific (e.g., PixInsight PJSR generation)
- It adds complexity to astrolib
- It introduces a heavy external dependency (pandas, scipy) that other tools don't need

### Potential New Tools That Would Benefit
Candidate modules for future tools / consolidation:

1. **Flatfield + Vignetting Corrector** — Combine flatfield-analyzer + flatfield-sim + optical.py
2. **Focus Optimization** — Extend focus-analyzer to run optimal exposure plans
3. **Photometric Pipeline Manager** — Wrap transit-photometry + astro-piper
4. **Export to AstroImageJ** — Unified aperture/flat generation tool

Each would import liberally from astrolib, keeping domain code minimal.

---

## Part 6: Code Review Criteria for astrolib

When adding to astrolib, enforce:

1. **Single Responsibility:** One clear purpose; no kitchen-sink utilities
2. **Documentation:** Every public function has a docstring with examples
3. **Type Hints:** All function signatures typed (return types too)
4. **Tests:** At least one pytest for each function
5. **No Circular Imports:** astrolib modules should not import each other (flat hierarchy)
6. **Minimal Dependencies:** Prefer astropy, numpy, scipy; avoid pandas unless absolutely needed

---

## Summary: Architectural Checklist

### Current State (Problems)
- ❌ "astro-pipeline" is a misnomer (not a pipeline)
- ❌ Duplicated code across 4+ modules (sexagesimal_to_degrees, gaussian, etc.)
- ❌ session-quality/dashboard.py is unmaintainable at 3,340 LOC
- ❌ astrolib is underutilized; not a true shared foundation

### Recommended State (Targets)
- ✅ Rename to "astro-toolkit" or "astro-box"
- ✅ Organize by scientific domain (exoplanet, optical, session, reduction)
- ✅ astrolib is the true foundation; all tools depend on it
- ✅ No duplicated functions across tools
- ✅ Large monoliths split into focused modules
- ✅ All modules have tests

### Implementation Timeline
- **Week 1:** Phase 1 (Quick wins; consolidate duplicates)
- **Week 2-3:** Phase 2 (Refactor dashboard.py)
- **Week 4+:** Phase 3 (New astrolib modules)

---

## Example: How a New Tool Would Use This Structure

Imagine you want to build a **"Photometric Variability Monitor"** that tracks long-term magnitude trends.

```python
# photometric-variability-monitor/ (new domain tool)
import logging
from pathlib import Path
from astrolib import (
    fits_utils,           # Read FITS headers
    photometry,           # Detrend flux
    coordinate_transforms, # Check Alt/Az constraints
    timekeeping,          # Convert timestamps to JD
    log_parsers,          # Parse observation logs
)

class VariabilityMonitor:
    def __init__(self, config_path: Path):
        self.config = load_config(config_path)
        self.logger = logging.getLogger(__name__)
    
    def process_lightcurve(self, fits_files: List[Path]):
        """Load FITS → extract flux → detrend → fit trend."""
        times_jd = []
        fluxes = []
        
        for fits_file in fits_files:
            header = fits_utils.read_fits_header(fits_file, ['DATE-OBS', 'EXPOSURE'])
            time_jd = timekeeping.parse_iso_to_jd(header['DATE-OBS'])
            flux = extract_flux_from_fits(fits_file)
            
            times_jd.append(time_jd)
            fluxes.append(flux)
        
        times_jd = np.array(times_jd)
        fluxes = np.array(fluxes)
        
        # Detrend atmospheric effects
        flux_detrended, trend = photometry.detrend_flux(times_jd, fluxes)
        
        # Fit variability trend
        result = fit_variability_trend(times_jd, flux_detrended)
        return result
```

**Key point:** This tool is lean because it relies on astrolib for fundamentals. The actual logic is only ~50 LOC.

---

**Final Note:** This structure works because astrolib is **stable** (infrequently changed) and **general** (applies to all domains). Domain tools are **volatile** (frequently modified) and **specific** (focus on one scientific goal). The separation of concerns is clean and scalable.
