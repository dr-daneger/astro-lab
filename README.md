# astro-pipeline

A monorepo of tools for amateur exoplanet and deep-sky astrophotography — target selection, data reduction, session quality analysis, and equipment characterization.

## Workflow Overview

**Exoplanet Transit Workflow:**
```
 transit-photometry/
   target-selector         transit-photometry
   (pick targets)          (AIJ photometry + modeling)
         |                          |
         +-------> Observing <------+
```

**General Astrophotography Support:**
```
 camera-noise            flatfield-sim
 (sensor characterize)   (optical design)
       |                       |
       v                       v
 +-----------+       +-----------+
 | Bias data |       | Flat data |
 +-----------+       +-----------+
       |
       v
 NINA session
       |
       +--> session-quality (guiding, AF analysis)
       |
       +--> focus-analyzer (focus tracking)
       |
       +--> FITS data
```

## Projects

| Directory | Language | Description |
|-----------|----------|-------------|
| **transit-photometry/** | Python | Exoplanet transit workflow: target selection + AIJ photometry + light curve modeling |
| **camera-noise/** | Python | Characterize read noise vs. temperature/gain from bias frames |
| **flatfield-sim/** | TypeScript/React | Interactive flat-field illumination simulator |
| **focus-analyzer/** | Python | Parse NINA autofocus logs and FITS headers into focus-position workbooks |
| **session-quality/** | Python | HTML dashboard scoring guiding, autofocus, alt/az, and efficiency |
| **astrolib/** | Python | Shared utilities (FITS helpers, log parsers, coordinate math) |

### Transit Photometry (`transit-photometry/`)

The transit photometry module is organized by scientific workflow:

| Subdirectory | Description |
|--------------|-------------|
| **target-selector/** | Rank exoplanet transit candidates from the ExoClock catalog |
| **scripts/** | Batch reduction: AIJ reduction, detrending, light curve modeling |
| **aij/** | ReusableAstroImageJ macros for multi-aperture photometry |
| **config/** | Target configuration templates |
| **example_datasets/** | Sample data for testing the pipeline |

## Quick Start

Each project is self-contained — `cd` into a directory and follow its README. Python projects use virtual environments:

```bash
# Example: Transit photometry workflow (includes target selection)
cd transit-photometry
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
python target-selector/pick_targets.py
python scripts/batch_reduce.py --dataset <path>
```

```bash
# Example: Other tools
cd focus-analyzer
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
python focus_parser.py
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
