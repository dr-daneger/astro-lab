# astro-pipeline

A monorepo of tools for amateur exoplanet and deep-sky astrophotography — target selection, data reduction, session quality analysis, and equipment characterization.

## Workflow Overview

```
 target-selector          camera-noise            flatfield-sim
 (pick targets)           (sensor characterize)   (optical design)
       |                         |                       |
       v                         v                       v
 +-----------+   NINA session   +-----------+   +-----------+
 | Observing | ===============> | FITS data | + | Flat data |
 +-----------+                  +-----------+   +-----------+
       |                              |
       v                              v
 session-quality             focus-analyzer
 (guiding, AF, alt/az)      (focus position tracking)
                                      |
                                      v
                             transit-photometry
                             (AIJ photometry + transit modelling)
```

## Projects

| Directory | Language | Description |
|-----------|----------|-------------|
| **target-selector/** | Python | Rank exoplanet transit candidates from the ExoClock catalog |
| **camera-noise/** | Python | Characterize read noise vs. temperature/gain from bias frames |
| **flatfield-sim/** | TypeScript/React | Interactive flat-field illumination simulator |
| **focus-analyzer/** | Python | Parse NINA autofocus logs and FITS headers into focus-position workbooks |
| **session-quality/** | Python | HTML dashboard scoring guiding, autofocus, alt/az, and efficiency |
| **transit-photometry/** | Python | Generate AstroImageJ aperture-grid macros and model transit light curves |
| **astrolib/** | Python | Shared utilities (FITS helpers, log parsers, coordinate math) |

## Quick Start

Each project is self-contained — `cd` into a directory and follow its README. Python projects use virtual environments:

```bash
cd target-selector
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
python pick_targets.py
```

The TypeScript project (flatfield-sim) uses npm:

```bash
cd flatfield-sim
npm install
npm run dev
```

## Shared Library (astrolib/)

`astrolib/` contains utilities extracted from the individual projects:

- **fits_utils.py** — FITS header reading helpers
- **log_parsers.py** — NINA Autorun log regex patterns and timestamp parsing
- **coord_utils.py** — Sexagesimal-to-degrees conversion
- **math_utils.py** — `gaussian()`, `compute_rms()`, `moving_average()`

Projects are currently self-contained and do not import from `astrolib`. Refactoring to use the shared library will happen incrementally.

## What's Not Committed

The `.gitignore` excludes FITS data, virtual environments, `node_modules`, generated Excel/CSV outputs, and cached API data. See `.gitignore` for the full list.

## License

[MIT](LICENSE)
