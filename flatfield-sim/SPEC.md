# Flat Field Simulator — Project Specification

> Generated from design session 2026-02-10 through 2026-02-14.
> This file is the single source of truth for design decisions, physics model,
> and feature scope. Reference it when resuming work in a new session.

---

## 1. Purpose

Design and validate the geometry of a 3D-printed flat field illumination jig
for the **Apertura 75Q** telescope + **ASI2600MM Pro** camera with a
**7x2" EFW** (electronic filter wheel). The jig couples a **Viltrox L116T**
LED panel to the dew shield through PTFE diffusion sheets so that flat field
frames taken through 3nm NB SHO and RGB-L 2" filters have minimal:

- **Grid ripple** — periodic LED pattern visibility in the flat
- **Field gradient** — center-to-edge illumination drop

## 2. Physical Design (OpenSCAD)

Source: `flatfield_jig/` in the astronomy repo. Key parameters:

| Parameter | Value | Notes |
|---|---|---|
| `dew_shield_od` | 102.5 mm | Measured outer diameter |
| `coupler_h` | 35.0 mm | Scope coupling depth |
| `internal_stop_id` | 85.0 mm | Hard stop for insertion |
| `acrylic_width` | 122.0 mm | PTFE sheet dimension |
| `acrylic_thickness` | 3.4 mm | 1/8" PTFE sheet |
| `slot_spacing` | 8.0 mm | Gap between two PTFE slots |
| `defocus_gap` | 30.0 mm | PTFE stack to dew shield entry |
| `led_mixing_gap` | 25.0 mm | LED panel to first PTFE sheet |
| `light_w × light_d` | 194 × 130 mm | Viltrox L116T panel dim |
| `wall` | 2.4 mm | PETG wall thickness |

The jig is a Z-stack: LED tray → mixing gap → PTFE slot 1 → slot spacing → PTFE slot 0 → defocus gap → dew shield coupler → (telescope dew shield 80.3mm) → sensor.

## 3. Simulator Architecture

Three-tab React/TypeScript app (Vite + Tailwind + Recharts):

### Tab 1: Explore
Interactive sliders + toggles for manual parameter exploration.
- Sliders: mixing gap, slot spacing, defocus gap
- Toggles: slot 1 (top) on/off, slot 0 (bottom) on/off
- Outputs: 2D aperture flux map (200×200 canvas), horizontal cross-section chart, ripple %, gradient %
- Purpose: Build intuition about what each parameter does

### Tab 2: Optimize
Multi-objective Pareto optimizer that exhaustively evaluates the feasible design space.
- Input: max total jig length constraint (slider)
- Computation: enumerate all geometry combinations at 5mm step, 3 diffuser states (both, slot1-only, slot0-only), compute ripple + gradient for each
- Output: Pareto front scatter plot (ripple vs gradient, log-linear), feasible cloud in gray, Pareto points in cyan
- Interaction: click Pareto table rows to select a candidate; "Apply to Explorer" button pushes geometry to the Explore tab
- Auto-selects the "balanced" Pareto point (minimum Euclidean distance to origin in normalized ripple-gradient space)
- CSV export of Pareto-optimal geometries

### Tab 3: Calibrate
Empirical model validation using captured photographs.
- Loads `Lightsource.JPG` (raw LED array) and `PTFE.JPG` (through one PTFE sheet in contact)
- Image scales: Lightsource = 14.58 µm/px, PTFE = 17.30 µm/px (ruler-crop pixel counting; different because PTFE thickness brings ruler closer to lens)
- Processing: green channel extraction → inverse sRGB gamma (γ=2.2) → center-biased row selection (central 50% of image) → ±50 row band averaging → 101px box-car smooth
- **LED pitch validation**: peak detection on Lightsource cross-section (scipy-style prominence + min-distance), measures actual LED spacing vs assumed 15mm
- **Diffuser PSF fit**: brute-force sweep of both Gaussian (σ=2–80mm) and Lorentzian (γ=2–80mm), minimizes RMS error; selects whichever model fits better
- Outputs: overlay chart (observed vs both models), residual trace, per-model fit quality, "Apply" button propagates better model’s parameter to Explore + Optimize tabs

## 4. Physics Model

### Sigma accumulation
Starting from the LED panel, sigma grows through the optical stack:
```
σ = mixingGap × 0.3                         // free-space divergence (UNCALIBRATED)
if slot1: σ = √(σ² + PTFE_BASE_WIDTH²)      // diffuser sheet (to be calibrated)
σ += slotSpacing × 0.3                       // inter-slot divergence (UNCALIBRATED)
if slot0: σ = √(σ² + PTFE_BASE_WIDTH²)      // diffuser sheet (to be calibrated)
σ += (defocusGap + DEW_SHIELD_LENGTH) × 0.15 // final throw (UNCALIBRATED)
```

### Grid ripple
Sum of Gaussian contributions from all 13×9 LEDs (pitch 15mm) evaluated at:
- Peak point: directly above an LED center
- Trough point: halfway between two LED centers (half-pitch offset)
- `ripple = (I_peak - I_trough) / (I_peak + I_trough) × 100%`

Analytical approximation (Poisson summation): `ripple ≈ 2 × exp(-π²σ²/p²)`
For ripple < 0.1%, need σ > p/π × √ln(2/0.001) ≈ 36.4mm at p=15mm.

### Field gradient
Same Gaussian sum evaluated at aperture center vs edge (37.5mm offset, = half of 75mm aperture):
- `gradient = (I_center - I_edge) / I_center × 100%`
- Currently only checks along Y-axis; diagonal may be slightly worse

### Known model limitations
1. PTFE_BASE_WIDTH (default 20mm) is uncalibrated → Calibrate tab fixes this
2. Free-space divergence coefficients (0.3, 0.15) are fabricated → gradient predictions may be off by 2-3x
3. No inverse-square falloff for off-axis LEDs → gradient optimism
4. LED emission assumed isotropic, real Viltrox LEDs are ~lambertian (120° half-angle)
5. Ripple is insensitive to divergence errors; gradient is highly sensitive

### Risk analysis (from session)
At the SCAD geometry (25/8/30mm, both slots):
- σ_total = 47.6mm; PTFE contributes 31.1mm, divergence contributes 16.5mm (35%)
- If divergence coefficients are 50% wrong: ripple stays <0.02%, gradient swings 3.7%–9.6%
- Conclusion: calibrating PTFE σ is high-value (fixes ripple model completely); divergence calibration is lower priority (gradient only, and gradient is correctable in flat division)

## 5. Calibration Images

Located in: `public/calibration_images/`

| File | Description | Scale | Capture |
|---|---|---|---|
| `Lightsource.JPG` | Raw Viltrox L116T, no diffuser | 14.58 µm/px | iPhone 17 Pro, 0.5x ultrawide, leveled jig, max brightness |
| `PTFE.JPG` | Same, with 1/8" PTFE in contact | 17.30 µm/px | Same setup, PTFE directly on panel surface |

Both: same camera height, same exposure settings, dark room. Scale calibrated by cropping a ruler image to a precise known distance and counting the total pixels. Scale difference is from PTFE sheet thickness (3.175mm) bringing the ruler calibration plane closer to the lens.

## 6. Constants

```typescript
const LED_PITCH = 15;           // mm, LED center-to-center spacing
const LED_COLS = 13;            // Viltrox L116T column count
const LED_ROWS = 9;             // Viltrox L116T row count
const PTFE_BASE_WIDTH = 20;     // mm, DEFAULT diffuser sigma (overridden by calibration)
const DEW_SHIELD_LENGTH = 80.3; // mm, Apertura 75Q dew shield
const APERTURE_DIAMETER = 75;   // mm, telescope aperture
const FIELD_SIZE = 200;         // px, simulation canvas resolution
const GEOMETRY_STEP = 5;        // mm, optimizer grid step

// Calibration scales (ruler-crop pixel counting)
const LIGHTSOURCE_SCALE = 0.01458; // mm/px
const PTFE_SCALE = 0.01730;        // mm/px
```

## 7. Tech Stack

- Vite 8 (beta) + React 19 + TypeScript 5.9
- Tailwind CSS 4 (via @tailwindcss/postcss)
- Recharts 3.7 (LineChart, ScatterChart)
- No image processing libraries (Canvas API only)
- No server/backend — pure client-side computation

## 8. Future Work (not in current scope)

- Calibrate free-space divergence: second PTFE capture at 25mm standoff
- DNG/RAW image support for true linear data
- Web Worker for 200×200 field computation (currently lags on low-end machines)
- 2D contour heatmap in Optimize tab (mixingGap × defocusGap axes)
- OpenSCAD parameter export: push optimized geometry back to SCAD variables
- Sensor-plane model: account for telescope optics remapping aperture illumination
