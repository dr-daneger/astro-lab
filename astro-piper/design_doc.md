# Astro-Pipeline: Automated Narrowband SHO Processing Architecture

> **Project**: Hybrid Python/PixInsight automation pipeline for narrowband astrophotography
> **Target**: NGC 1499 (California Nebula) — Emission nebula, SHO palette
> **Version**: 0.3.0-draft
> **Last Updated**: 2026-03-01

---

## Table of Contents

1. [Project Motivation](#1-project-motivation)
2. [System Characterization](#2-system-characterization)
3. [Acquisition Parameters](#3-acquisition-parameters)
4. [Pipeline Architecture Overview](#4-pipeline-architecture-overview)
5. [Phase 1 — Preprocessing](#5-phase-1--preprocessing)
6. [Phase 2 — Linear Processing](#6-phase-2--linear-processing)
7. [Phase 3 — Stretching and Palette Combination](#7-phase-3--stretching-and-palette-combination)
8. [Phase 4 — Nonlinear Processing](#8-phase-4--nonlinear-processing)
9. [Phase 5 — RGB Star Processing and Final Combination](#9-phase-5--rgb-star-processing-and-final-combination)
10. [Information Theory Foundations](#10-information-theory-foundations)
11. [Automation Architecture](#11-automation-architecture)
12. [NGC 1499 Specific Strategy](#12-ngc-1499-specific-strategy)
13. [Project Deliverables and Phasing](#13-project-deliverables-and-phasing)
14. [Claude Code Kickoff Prompt](#14-claude-code-kickoff-prompt)
15. [Implementation Status and Quality Improvement Roadmap](#15-implementation-status-and-quality-improvement-roadmap)

---

## 1. Project Motivation

The operator has extreme time and wrist-budget (RSI) constraints that make manual PixInsight GUI processing unsustainable as a long-term hobby. This project automates the maximum feasible portion of the narrowband SHO processing pipeline while preserving manual breakpoints at quality-critical junctures where human aesthetic judgment is irreplaceable.

**Design principles:**
- Automate all deterministic, parameter-stable operations (calibration, registration, integration, background extraction, deconvolution, noise reduction, palette math, star recombination)
- Preserve manual breakpoints only where visual assessment changes the outcome (crop composition, stretch curve tuning, final color grading)
- Artifact-based state management: pipeline state = which output files exist on disk. Free idempotency — re-run skips completed stages. `--force` flag overrides.
- Per-channel parallel execution where channels are independent (Ha, OIII, SII through integration)
- Structured logging from day one: every PI invocation logs input files, process parameters, wall-clock time, exit code, output file hash

---

## 2. System Characterization

### Hardware

| Component | Specification |
|---|---|
| **Telescope** | Apertura 75Q refractor — 75mm aperture, 405mm FL, f/5.4, quintuplet Petzval |
| **Camera** | ZWO ASI2600MM Pro — IMX571 BSI CMOS, 3.76μm pixels, 26MP, 16-bit ADC |
| **NB Filters** | Antlia 3nm Pro Ha (656.3nm), OIII (500.7nm), SII (671.6nm) — 2" mounted |
| **RGB Filters** | Antlia RGB 2" — broadband R, G, B for star color acquisition |
| **Filter Wheel** | ZWO EFW 7-position |
| **Focuser** | ZWO EAF electronic autofocuser |
| **Mount** | ZWO AM5N harmonic drive |
| **Acquisition** | ASIAIR Mini |
| **Guide Scope** | UNIGUIDE 32mm |

### Imaging geometry

| Parameter | Value | Derivation |
|---|---|---|
| Plate scale | 1.914 arcsec/pixel | 206.265 × 3.76μm / 405mm |
| Field of view | 3.33° × 2.22° | 6248 × 4176 px × 1.914″/px |
| Nyquist-sampled seeing | ~3.8″ FWHM | 2 × 1.914″/px |
| Typical site seeing | 2.5–4.0″ FWHM | Bortle 6–7, Beaverton OR (45.5°N, 122.8°W) |
| Sampling regime | Mildly undersampled | Seeing often < 2× plate scale |
| Drizzle 2× plate scale | 0.957 arcsec/pixel | Restores closer to Nyquist for good seeing |

The system is mildly undersampled for typical seeing, making **Drizzle 2× integration beneficial** — it produces rounder star profiles and smoother noise texture. It does not recover resolution beyond the atmospheric seeing limit but improves aesthetic quality.

### Sensor characteristics at operating points

| Parameter | Gain 100 (NB) | Gain -25 (RGB Stars) |
|---|---|---|
| Read noise | ~1.0 e⁻ (HCG mode) | ~3.3 e⁻ (standard mode) |
| Full well capacity | ~50,000 e⁻ | ~80,000+ e⁻ |
| Dynamic range | ~14 stops | ~14.5 stops |
| Dark current (-20°C) | 0.00012 e⁻/s/px | 0.00012 e⁻/s/px |
| Amp glow | None (confirmed) | None |
| QE peak | 91% (BSI) | 91% |

### Processing hardware

| Component | Specification |
|---|---|
| CPU | Intel Core i9-10900K (10C/20T) |
| RAM | 64 GB DDR4 |
| NVMe (working) | Addlink S70 Lite 2TB — active processing workspace |
| HDD (archive) | 20TB — long-term storage |
| GPU | NVIDIA (for GraXpert CUDA, RC Astro TensorFlow GPU) |
| OS | Windows 10/11 |

---

## 3. Acquisition Parameters

### Narrowband channels (nebulosity source)

| Parameter | Value | Rationale |
|---|---|---|
| Gain | 100 | Activates HCG mode: read noise drops to ~1.0 e⁻ |
| Offset | 50 | Prevents ADU clipping at gain 100 |
| Exposure | 300s | >90% of max SNR per sub with 3nm at Bortle 6–7 |
| Temperature | -20°C | Minimizes dark current |
| Filters | Antlia 3nm Pro Ha, OIII, SII | 3nm FWHM bandpass |
| Dither | 5–8 px minimum | Required for Drizzle and hot pixel mitigation |

### RGB star channels (star color source)

| Parameter | Value | Rationale |
|---|---|---|
| Gain | -25 | Lowest gain — maximizes full well capacity, prevents star saturation |
| Exposure | 10s | Short enough to avoid saturating bright stars through broadband filters |
| Temperature | -20°C | Matches NB acquisition for consistent dark calibration |
| Filters | Antlia RGB 2" | Broadband R, G, B for photometric star colors |
| Subs per filter | 30–50 minimum | Adequate for clean integration with good rejection |
| Dither | Yes | Minimum 5px recommended |

**Two-source architecture rationale:** Broadband RGB captures the full stellar spectral energy distribution (Wien's law continuum), producing photometrically accurate star chromaticity. Narrowband filters sample only a single emission wavelength, yielding monochromatic stars that require synthetic color mapping (e.g., SETI Astro NB→RGB Stars). Real RGB star data eliminates this approximation entirely and is the objectively superior approach when acquisition time permits.

### NGC 1499 integration time budget

The California Nebula has extreme channel imbalance: Ha ≫ SII > OIII. OIII appears only as a faint veil requiring disproportionate integration time.

| Channel | Minimum | Target | Competition-grade |
|---|---|---|---|
| Ha | 4h (48×300s) | 6–8h | 15h+ |
| SII | 4h (48×300s) | 6–8h | 15h+ |
| OIII | 6h (72×300s) | 8–12h | 20h+ |
| RGB R | 5 min (30×10s) | 8 min (50×10s) | 8 min |
| RGB G | 5 min (30×10s) | 8 min (50×10s) | 8 min |
| RGB B | 5 min (30×10s) | 8 min (50×10s) | 8 min |

### Calibration frame library

| Frame | Gain | Offset | Temp | Exposure | Count | Notes |
|---|---|---|---|---|---|---|
| NB Darks | 100 | 50 | -20°C | 300s | 30–50 | No dark scaling. Match exactly. |
| RGB Darks | -25 | TBD | -20°C | 10s | 30–50 | Separate dark library for RGB. |
| NB Flats | 100 | 50 | N/A | Auto | 40–50 per filter | Per-filter (Ha, OIII, SII) |
| RGB Flats | -25 | TBD | N/A | Auto | 40–50 per filter | Per-filter (R, G, B) |
| Flat darks | Match flats | Match | N/A | Match | 40–50 per set | Same settings as corresponding flats, cap on |

---

## 4. Pipeline Architecture Overview

The pipeline has **two parallel preprocessing tracks** that converge in Phase 5:

```
                    ┌──────────────────────────────────────────────────────────┐
                    │              NARROWBAND TRACK (nebulosity)               │
                    │                                                          │
  Raw Ha/OIII/SII   │  Phase 1: Calibrate → Register → Integrate (×3 ch)     │
  300s, Gain 100    │  Phase 2: Crop → GraXpert → BXT → NXT → SXT split      │
                    │  Phase 3: Stretch each ch → Foraxx palette combine      │
                    │  Phase 4: SCNR → Curves → HDR → LHE → Final NR         │
                    │              ↓                                           │
                    │         STARLESS SHO IMAGE                              │
                    └────────────────────────┬─────────────────────────────────┘
                                             │
                                             │  Phase 5: Screen blend
                                             │  ~(~starless * ~stars)
                                             │
                    ┌────────────────────────┴─────────────────────────────────┐
                    │              RGB STAR TRACK (star color)                 │
                    │                                                          │
  Raw R/G/B         │  Phase 1b: Calibrate → Register → Integrate (×3 ch)    │
  10s, Gain -25     │  Register RGB master to NB reference frame              │
                    │  ChannelCombination → SPCC → Stretch → SXT             │
                    │              ↓                                           │
                    │         RGB STARS-ONLY IMAGE                            │
                    └──────────────────────────────────────────────────────────┘
```

### Processing stages with automation and breakpoint flags

| # | Stage | Phase | Track | Auto | Breakpoint | PJSR Class / Tool |
|---|---|---|---|---|---|---|
| 1 | Subframe inspection/rejection | 1 | NB | ✅ | | SubframeSelector, Blink |
| 2 | NB Calibration + Registration + Integration | 1 | NB | ✅ | | WBPP or manual pipeline |
| 3 | NB DrizzleIntegration | 1 | NB | ✅ | | DrizzleIntegration |
| 4 | RGB Calibration + Registration + Integration | 1b | RGB | ✅ | | WBPP or manual pipeline |
| 5 | RGB-to-NB frame registration | 1b | RGB | ✅ | | StarAlignment |
| 6 | DynamicCrop (all channels) | 2 | Both | ⚠️ | **BP1** | DynamicCrop |
| 7 | GraXpert background extraction (per NB ch) | 2 | NB | ✅ | | GraXpert CLI |
| 8 | Channel combination (equal-weight SHO) | 2 | NB | ✅ | | PixelMath |
| 9 | BlurXTerminator — Correct Only | 2 | NB | ✅ | | BlurXTerminator |
| 10 | BlurXTerminator — Sharpen | 2 | NB | ✅ | **BP2** | BlurXTerminator |
| 11 | NoiseXTerminator (linear) | 2 | NB | ✅ | | NoiseXTerminator |
| 12 | Channel split (back to S, H, O) | 2 | NB | ✅ | | ChannelExtraction |
| 13 | StarXTerminator (per NB channel) | 2 | NB | ✅ | | StarXTerminator |
| 14 | Stretch each starless NB channel | 3 | NB | ⚠️ | **BP3** | GHS / SETI StatStretch |
| 15 | Foraxx dynamic palette combination | 3 | NB | ✅ | | PixelMath |
| 16 | SCNR green removal | 4 | NB | ✅ | | SCNR |
| 17 | CurvesTransformation — hue shift | 4 | NB | ⚠️ | **BP4** | CurvesTransformation |
| 18 | CurvesTransformation — contrast/sat | 4 | NB | ⚠️ | | CurvesTransformation |
| 19 | HDRMultiscaleTransform | 4 | NB | ✅ | | HDRMultiscaleTransform |
| 20 | LocalHistogramEqualization | 4 | NB | ✅ | | LocalHistogramEqualization |
| 21 | NoiseXTerminator (nonlinear, light) | 4 | NB | ✅ | | NoiseXTerminator |
| 22 | RGB ChannelCombination | 5 | RGB | ✅ | | ChannelCombination |
| 23 | SPCC on RGB star image | 5 | RGB | ✅ | | SpectrophotometricColorCalibration |
| 24 | RGB star stretch | 5 | RGB | ✅ | | GHS / HistogramTransformation |
| 25 | StarXTerminator on RGB composite | 5 | RGB | ✅ | | StarXTerminator |
| 26 | Star halo reduction (optional) | 5 | RGB | ✅ | | SETI Astro Halo Reducer |
| 27 | Screen blend recombination | 5 | Merge | ✅ | **BP5** | PixelMath |
| 28 | Final crop and cleanup | 5 | Final | ⚠️ | | DynamicCrop, Blemish Blaster |

**Breakpoint legend:**
- **BP1** — Crop composition. Human decides framing.
- **BP2** — Inspect deconvolution. Check for ringing, star artifacts, over-sharpening.
- **BP3** — Stretch tuning. Per-channel histogram shape, black point, highlight compression.
- **BP4** — Color grading. Hue shifts, saturation curves, final palette taste.
- **BP5** — Star recombination. Verify star brightness, halo bleed, alignment.

---

## 5. Phase 1 — Preprocessing

### 5A. Narrowband track (Ha, OIII, SII)

**Input:** Raw uncalibrated FITS/XISF subframes + master calibration files
**Output:** Three master integrated linear images (`Ha_master.xisf`, `OIII_master.xisf`, `SII_master.xisf`) with 150 DN pedestal

WBPP is the recommended preprocessing engine. It handles calibration, cosmetic correction, debayering (N/A for mono), subframe weighting, registration, local normalization, and integration in a single automated pass.

**Critical WBPP settings:**
- Output Pedestal: **150 DN** — Prevents black-point clipping after dark subtraction. Without this, narrowband CMOS data (especially OIII, SII) will have background values clipped to zero, permanently destroying faint signal.
- Calibration: Enable flat darks, disable bias (or use superbias only)
- Registration: Generate drizzle data. Use distortion correction if available.
- Weighting: Use SSWEIGHT (SubframeSelector weight) or Noise Evaluation
- Integration: See rejection algorithm table below
- Local Normalization: Enable for all channels

**Integration rejection algorithm selection:**

| Sub count | Algorithm | Parameters |
|---|---|---|
| 3–6 | Percentile Clipping | Default |
| 5–10 | Averaged Sigma Clipping | σ low 4.0, σ high 3.0 |
| 10–20 | Winsorized Sigma Clipping | σ low 4.0, σ high 3.0, cutoff 5% |
| 20–50 | Linear Fit Clipping or ESD | ESD: significance 0.05, outliers 0.30 |
| 50+ | **ESD (recommended)** | Significance 0.05, outliers 0.30, low relaxation 2.0 |

For narrowband: increase low relaxation to 2.0 with ESD to protect faint genuine signal. Enable Large Scale Pixel Rejection → Reject High for satellite trails.

**DrizzleIntegration:** Scale 2, Drop Shrink 0.9, Kernel Square. Requires dithered data with minimum 15–20 subs per channel. Output file sizes quadruple (~800MB per channel at 2×).

### 5B. RGB star track (R, G, B)

**Input:** Raw uncalibrated RGB FITS/XISF subframes (10s, Gain -25, -20°C) + RGB master calibration files
**Output:** Three master integrated linear images (`R_master.xisf`, `G_master.xisf`, `B_master.xisf`)

Process using the same WBPP instance or a separate run with RGB-specific settings:

- Calibration: Use RGB-specific darks (10s, Gain -25) and RGB flats
- Registration: Register all RGB channels to a common reference (G channel recommended as reference for best SNR)
- Integration: With 30–50 short subs, Winsorized Sigma Clipping is appropriate
- DrizzleIntegration: Optional for RGB stars — star color accuracy doesn't benefit from Drizzle, but if applied, use same scale as NB for dimensional consistency
- Pedestal: **150 DN** (same as NB, maintain consistency)

**Critical step — Register RGB to NB reference frame:**

After integration, the RGB master composite must be aligned to the NB reference frame (Ha_master recommended as astrometric reference due to highest star count and SNR):

```javascript
var SA = new StarAlignment;
SA.referenceImage = "/data/Ha_master.xisf";  // NB reference
SA.targets = [
    [true, "/data/R_master.xisf"],
    [true, "/data/G_master.xisf"],
    [true, "/data/B_master.xisf"]
];
SA.distortionCorrection = true;  // Handle any field rotation differences
SA.executeGlobal();
```

This ensures pixel-perfect alignment between the starless NB nebula and the RGB star overlay in Phase 5.

---

## 6. Phase 2 — Linear Processing (Narrowband Track)

All operations in this phase operate on **linear** data. The deconvolution-before-noise-reduction ordering is mandatory.

### Step 6: DynamicCrop — **BREAKPOINT 1**

Remove ragged stacking edges from registration. Apply **identical crop coordinates** to all three NB channels AND all three RGB channels for alignment consistency. This is a human decision (composition/framing), so it triggers a breakpoint.

**Automation note:** Can be pre-configured with a percentage-based auto-crop (e.g., crop 5% from each edge) for fully automated runs, with the breakpoint as an optional override.

### Step 7: GraXpert Background Extraction (per NB channel)

GraXpert AI is preferred over DBE for NGC 1499 because the nebula fills most of the FOV, leaving few true-background regions for manual DBE sample placement. GraXpert's neural network distinguishes gradients from nebulosity.

**CLI automation:**
```bash
GraXpert-win64.exe "C:/data/Ha_master.xisf" -cli -cmd background-extraction -output "C:/data/Ha_bgext.xisf" -correction Subtraction -smoothing 0.1 -gpu true
```

Even with 3nm narrowband filters, background extraction is necessary because: (a) optical train vignetting creates gradients, (b) moonlight penetrates NB filters at reduced but nonzero levels, and (c) residual gradients are amplified 100–1000× during aggressive stretching.

### Step 8: Channel Combination (temporary equal-weight SHO)

Create a combined image for BXT processing. BXT's AI was trained on color images with inter-channel correlations — it produces better results on combined data than on individual channels.

```javascript
// PixelMath: create temporary equal-weight SHO for BXT
var PM = new PixelMath;
PM.expression  = "SII_bgext";   // R
PM.expression1 = "Ha_bgext";    // G
PM.expression2 = "OIII_bgext";  // B
PM.useSingleExpression = false;
PM.createNewImage = true;
PM.newImageId = "SHO_linear";
PM.executeGlobal();
```

### Step 9: BlurXTerminator — Correct Only

```javascript
var BXT1 = new BlurXTerminator;
BXT1.correct_only = true;
BXT1.automatic_psf = true;
BXT1.executeOn(view);
```

Corrects optical aberrations (residual coma, astigmatism, spacing errors, tracking drift) across the field without sharpening. The 75Q's quintuplet design is well-corrected, but atmospheric seeing and minor mechanical errors introduce field-variable PSF distortions that BXT corrects.

### Step 10: BlurXTerminator — Sharpen — **BREAKPOINT 2**

```javascript
var BXT2 = new BlurXTerminator;
BXT2.correct_only = false;
BXT2.sharpen_stars = 0.25;       // Conservative for ~2"/px
BXT2.adjust_halos = 0.05;        // Minimal; higher values risk artifacts
BXT2.sharpen_nonstellar = 0.40;  // Start moderate; increase for high-SNR
BXT2.automatic_psf = true;
BXT2.executeOn(view);
```

**Why BXT over classical Richardson-Lucy:** BXT uses a non-stationary PSF model (corrects aberrations that vary across the field), tolerates noise without amplification, does not require star masks or deringing support, and requires no manual PSF extraction. Classical RL assumes a single PSF across the entire field and amplifies noise, requiring careful masking. For undersampled narrowband data, BXT is strictly superior.

### Step 11: NoiseXTerminator (linear stage)

Applied AFTER BXT, per RC Astro's explicit documentation: noise reduction of any kind must never precede deconvolution. Deconvolution algorithms require intact noise statistics for regularization.

```javascript
var NXT = new NoiseXTerminator;
NXT.denoise = 0.80;  // Moderate-aggressive for narrowband
NXT.detail = 0.15;   // Preserve fine structure
NXT.executeOn(view);
```

NXT works on linear data by internally performing an STF-like stretch, denoising in the stretched domain, then unstretching. The `detail` parameter controls frequency separation: lower values preserve more small-scale detail at the cost of more residual noise.

### Step 12: Channel Split

```javascript
var CE = new ChannelExtraction;
CE.channels = [[true, "SII_processed"], [true, "Ha_processed"], [true, "OIII_processed"]];
CE.colorSpace = ChannelExtraction.prototype.RGB;
CE.executeOn(view);
```

### Step 13: StarXTerminator (per NB channel, linear)

Remove stars from each NB channel while data is still linear. SXT internally performs MTF stretch, processes, then reverse-stretches.

```javascript
var SXT = new StarXTerminator;
SXT.stars_image = true;   // Generate stars-only image (for diagnostic use)
SXT.unscreen = false;     // Do NOT use unscreen for linear data
SXT.executeOn(view);
```

**Important:** The NB stars-only images are generated for diagnostic purposes but are **discarded** in the final pipeline. All star data comes from the RGB track. This eliminates narrowband star color artifacts entirely.

---

## 7. Phase 3 — Stretching and Palette Combination

### Step 14: Stretch Each Starless NB Channel — **BREAKPOINT 3**

Stretch before combine is mandatory for the Foraxx dynamic palette. Two recommended approaches:

**Option A — SETI Astro Statistical Stretch (preferred for automation):**
Target Median 0.20–0.25 across all three channels for consistent brightness matching. Available as PJSR script (requires GUI adaptation for headless use — see automation section).

**Option B — GeneralizedHyperbolicStretch (GHS):**
The most flexible stretching tool. Set Symmetry Point to data peak, gradually increase stretch factor. GHS excels at stretching faint OIII structure without blowing out bright Ha core. Available as a native PI process module — fully automatable.

```javascript
var GHS = new GeneralizedHyperbolicStretch;
GHS.stretchType       = GeneralizedHyperbolicStretch.prototype.ST_GeneralisedHyperbolic;
GHS.stretchFactor     = 5.0;      // Stretch factor (D) — adjust per channel
GHS.localIntensity    = 2.0;      // Shape parameter (b)
GHS.symmetryPoint     = 0.0001;   // Symmetry point (SP) — set to per-channel median
GHS.shadowProtection  = 0.0;
GHS.highlightProtection = 1.0;
GHS.blackPoint        = 0.0;
GHS.whitePoint        = 1.0;
GHS.inverse           = false;
GHS.executeOn(view);
```

> **PJSR property name note:** The PJSR API uses `stretchFactor`/`localIntensity`/`symmetryPoint`/`inverse`, NOT the GUI shorthand `D`/`b`/`SP`/`invertTransformation`. Using the wrong names silently creates new JS properties and GHS runs with all defaults (zero stretch).

**Option C — HistogramTransformation:**
Simpler but less flexible. Set the shadows clipping point to just below the histogram peak, move the midtones slider left. Fully automatable via PJSR.

### Step 15: Foraxx Dynamic SHO Palette Combination

```javascript
// Foraxx dynamic palette — PixelMath
// NOTE: ~X means (1-X) in PI PixelMath, NOT reciprocal
var PM = new PixelMath;
PM.expression  = "(Oiii^~Oiii)*Sii + ~(Oiii^~Oiii)*Ha";
PM.expression1 = "((Oiii*Ha)^~(Oiii*Ha))*Ha + ~((Oiii*Ha)^~(Oiii*Ha))*Oiii";
PM.expression2 = "Oiii";
PM.useSingleExpression = false;
PM.createNewImage = true;
PM.newImageId = "SHO_Foraxx";
PM.executeGlobal();
```

The `X^(1-X)` Power of Inverted Pixels (PIP) creates dynamic weighting: where OIII is bright, SII contributes to red; where OIII is faint, Ha contributes instead. This produces gold/cyan Hubble-like colors without the overwhelming green cast of standard SHO mapping.

---

## 8. Phase 4 — Nonlinear Processing (Starless SHO)

### Step 16: SCNR Green Removal

```javascript
var SCNR = new SCNR;
SCNR.amount = 0.00;   // 0 = disabled for Foraxx palette
SCNR.protectionMethod = SCNR.prototype.MaximumMask;
SCNR.preserveLuminance = true;
SCNR.executeOn(view);
```

**Foraxx palette note:** SCNR is designed for classic SHO where Ha→G creates a green cast. In Foraxx, Ha→R, so the G channel already has minimal green (≈1%) after palette combination. Applying SCNR with `preserveLuminance=true` compensates by boosting R and B, which converts gold/amber pixels (R>G>B) into red and shifts cyan/teal into blue/purple — destroying Foraxx's characteristic warm colors. Set `scnr_amount=0` for Foraxx pipelines.

### Step 17: CurvesTransformation — Hue Adjustment — **BREAKPOINT 4**

Shift residual greens toward gold/orange using Hue vs Hue curve. Boost cyan (OIII) saturation. This is an aesthetic judgment — breakpoint appropriate.

### Step 18: CurvesTransformation — Contrast and Saturation

Fine-tune overall contrast, saturation, brightness. Use luminance masks to protect dark regions.

### Step 19: HDRMultiscaleTransform

```javascript
var HDRMT = new HDRMultiscaleTransform;
HDRMT.numberOfLayers = 6;
HDRMT.numberOfIterations = 1;
HDRMT.executeOn(view);
```

Compresses dynamic range in NGC 1499's bright central ridge while revealing faint outer OIII structure. **Note:** Luminance mask creation via `PixelMath.executeGlobal()` fails silently in PI headless automation mode — apply maskless in automated runs.

### Step 20: LocalHistogramEqualization

```javascript
var LHE = new LocalHistogramEqualization;
LHE.kernelRadius = 96;
LHE.contrastLimit = 2.0;
LHE.amount = 0.35;
LHE.executeOn(view);
// Note: maskless in automated runs — see HDRMT note above
```

### Step 21: NoiseXTerminator (nonlinear, light touch)

```javascript
var NXT2 = new NoiseXTerminator;
NXT2.denoise = 0.40;  // Light touch — stretching amplified noise in darks
NXT2.detail = 0.15;
NXT2.executeOn(view);
```

---

## 9. Phase 5 — RGB Star Processing and Final Combination

This phase uses the **separately acquired RGB star data** (10s, Gain -25, -20°C) rather than extracting stars from narrowband channels. This is the highest-quality star rendering approach because:

1. **True photometric star colors** — Broadband RGB captures the full Wien's law continuum, producing accurate stellar chromaticity (blue for hot O/B stars, yellow for G/K, red for M dwarfs)
2. **No synthetic color artifacts** — Eliminates the magenta/green false-color stars inherent in narrowband
3. **Unsaturated star profiles** — Low gain + short exposure prevents bloating and diffraction artifacts that plague 300s narrowband subs
4. **SPCC-calibratable** — Real RGB data can be photometrically calibrated to the Gaia BP/RP spectrophotometric catalog, producing scientifically accurate star colors

### Step 22: RGB ChannelCombination

Combine the registered R, G, B masters into a single RGB image:

```javascript
var CC = new ChannelCombination;
CC.channels = [
    [true, "R_master_registered"],
    [true, "G_master_registered"],
    [true, "B_master_registered"]
];
CC.executeGlobal();
```

### Step 23: SpectrophotometricColorCalibration (SPCC)

Apply SPCC to the RGB image for photometrically accurate star colors calibrated against the Gaia DR3 BP/RP spectrophotometric catalog:

```javascript
var SPCC = new SpectrophotometricColorCalibration;
// SPCC requires plate-solving (ImageSolver) first
SPCC.executeOn(view);
```

**Prerequisite:** ImageSolver must be run on the RGB image first (or it must inherit WCS from the NB registration). SPCC also requires downloading the Gaia catalog data via PI's online catalog system.

### Step 24: RGB Star Stretch

Stretch the linear RGB image to nonlinear. Use GHS or HistogramTransformation — the goal is a mild stretch that brings out star colors without blowing out bright stars.

```javascript
var GHS = new GeneralizedHyperbolicStretch;
GHS.stretchType      = GeneralizedHyperbolicStretch.prototype.ST_GeneralisedHyperbolic;
GHS.stretchFactor    = 3.0;      // Lighter stretch than NB — stars are already bright
GHS.localIntensity   = 2.0;
GHS.symmetryPoint    = 0.0001;   // Set to per-channel median
GHS.inverse          = false;
GHS.executeOn(view);
```

### Step 25: StarXTerminator on RGB Composite

Extract stars-only from the stretched RGB image. This produces the final star layer with natural broadband colors.

```javascript
var SXT = new StarXTerminator;
SXT.stars_image = true;   // Keep the stars-only image
SXT.unscreen = false;     // NOT unscreen — we want clean subtraction
SXT.executeOn(view);
// The stars-only image (SXT creates it automatically) is our RGB_stars_only
// The processed view (starless) is discarded — we only need the stars
```

**Alternative workflow:** If the RGB data has very low SNR between stars (which it will — 10s broadband with low gain), StarXTerminator may have difficulty. In that case, apply a mild noise reduction (NXT denoise 0.3) before SXT extraction.

### Step 26: Star Halo Reduction (Optional)

If bright stars have residual halos from the broadband filter stack, apply SETI Astro Halo Reducer or StarNet's halo removal. Usually unnecessary with the 75Q's well-corrected optics and short RGB exposures.

### Step 27: Screen Blend Recombination — **BREAKPOINT 5**

Merge the starless SHO nebula with the RGB stars using PixelMath screen blend:

```javascript
// Screen blend: ~(~starless * ~stars)
var PM = new PixelMath;
PM.expression = "~(~SHO_starless * ~RGB_stars_only)";
PM.useSingleExpression = true;
PM.executeOn(starless_view);

// With star brightness control (adjust 0.7 to taste):
// PM.expression = "~(~SHO_starless * ~(RGB_stars_only * 0.70))";
```

**Why screen blend:** Prevents blowout where stars overlap bright nebulosity. Additive blending (`starless + stars`) clips to white at star positions atop bright Ha regions. Screen blend compresses this naturally.

### Step 28: Final Crop and Cleanup

DynamicCrop to final composition. SETI Astro Blemish Blaster for any SXT artifacts. Final minor curves adjustments.

---

## 10. Information Theory Foundations

### Noise floor and signal detection

The noise floor for each pixel in a single sub is determined by:

```
σ_total = √(σ_sky² + σ_dark² + σ_read²)
        = √(N_sky + N_dark·t + σ_read²)
```

For a 300s exposure at Gain 100, 3nm Ha, Bortle 6–7:

| Noise source | Value (e⁻) | Contribution |
|---|---|---|
| Read noise | 1.0 | Fixed per readout |
| Dark current noise | ~0.3 (at -20°C) | √(0.00012 × 300) |
| Sky shot noise | ~2–5 | Dominant in broadband; reduced 50–100× by 3nm filter |
| **Total per sub** | **~3.5–6** | Per pixel |

After stacking N subs, noise reduces by √N. After 50 subs (4.2h): σ ≈ 0.5–0.9 e⁻/pixel. After 100 subs (8.3h): σ ≈ 0.35–0.6 e⁻/pixel.

SNR scales as √(total integration time): **doubling SNR requires 4× integration time.**

### Spatial frequency and the Nyquist–Shannon theorem

At 1.914″/px, the Nyquist spatial frequency limit is 0.5 cycles/pixel. Atmospheric seeing at Bortle 6–7 (2.5–4.0″ FWHM) limits actual information bandwidth to ~0.25–0.38 cycles/pixel. Noise reduction algorithms exploit this gap — they suppress high-frequency content above the seeing-limited bandwidth (where only noise exists) while preserving lower frequencies containing real astronomical signal.

**Key insight:** All spatial frequencies between the seeing-limited bandwidth and the Nyquist limit contain no recoverable astronomical information — only noise. This is the theoretical basis for why noise reduction works without destroying signal, provided it correctly identifies the cutoff frequency.

### Noise reduction algorithm comparison

| Algorithm | Strengths | Weaknesses | Best use case |
|---|---|---|---|
| **NoiseXTerminator** | Best overall detail preservation; frequency-aware; simple UI | AI black box; not reproducible across versions | Primary NR, both linear and nonlinear |
| **GraXpert Denoise** | Free; CUDA accelerated; CLI automatable | Can soften stars; reports of mottled residuals | Backup option if NXT unavailable |
| **MultiscaleLinearTransform** | Full scale-by-scale control; reproducible; maskable | Requires careful masking; labor-intensive; learning curve | When precise per-scale control needed |
| **TGVDenoise** | Theoretically optimal (PI's own comparison ranked highest) | Extremely difficult to configure; "orange peel" artifacts | Expert use only |

### The deconvolution → noise reduction ordering

Deconvolution (including BXT) is an inverse problem that reconstructs the pre-atmospheric image from the observed blurred image. It requires accurate noise statistics to regularize the solution — if noise has been artificially reduced beforehand, the deconvolution algorithm misestimates the noise floor and either over-sharpens (producing ringing artifacts) or under-sharpens (leaving residual blur). **This is not a preference — it is a mathematical requirement of the inverse problem.**

---

## 11. Automation Architecture

### Python orchestrator design

```python
# orchestrator.py — top-level architecture

class PipelineStage:
    """Base class for all pipeline stages."""
    name: str                    # Human-readable stage name
    phase: int                   # Pipeline phase (1-5)
    track: str                   # "nb", "rgb", "merge", "final"
    input_spec: list[str]        # Required input file patterns
    output_spec: list[str]       # Expected output file patterns
    breakpoint: bool             # Pause for manual intervention?
    pjsr_template: str | None    # Path to .js template (None for external tools)
    external_cmd: str | None     # CLI command for external tools (GraXpert)
    
    def validate_inputs(self) -> bool: ...
    def validate_outputs(self) -> bool: ...
    def is_complete(self) -> bool:
        """Check if outputs already exist (idempotency)."""
        return all(Path(f).exists() for f in self.output_spec)
    def execute(self, config: dict) -> int: ...

class PipelineOrchestrator:
    """State machine that sequences stages."""
    stages: list[PipelineStage]
    config: dict                 # Loaded from pipeline_config.json
    log: StructuredLogger
    
    def run(self, start_stage=None, force=False):
        for stage in self.stages:
            if stage.is_complete() and not force:
                self.log.info(f"Skipping {stage.name} — outputs exist")
                continue
            if not stage.validate_inputs():
                raise PipelineError(f"Missing inputs for {stage.name}")
            
            exit_code = stage.execute(self.config)
            
            if not stage.validate_outputs():
                raise PipelineError(f"Stage {stage.name} produced invalid outputs")
            
            if stage.breakpoint:
                self.prompt_breakpoint(stage)
    
    def prompt_breakpoint(self, stage):
        print(f"\n{'='*60}")
        print(f"  BREAKPOINT: {stage.name}")
        print(f"  Output: {stage.output_spec}")
        print(f"{'='*60}")
        choice = input("[Enter] continue | [M] open PI for manual edit | [Q] quit: ")
        if choice.lower() == 'm':
            subprocess.Popen([PI_EXE_PATH] + stage.output_spec)
            input("Press [Enter] when manual edits are saved and PI is closed...")
        elif choice.lower() == 'q':
            sys.exit(0)
```

### PixInsight invocation pattern

```python
import subprocess
from pathlib import Path

PI_EXE = r"C:\Program Files\PixInsight\bin\PixInsight.exe"

def run_pjsr(script_path: str, args: list[str] = None, timeout: int = 3600) -> int:
    """Execute a PJSR script in PixInsight automation mode."""
    cmd = [PI_EXE, "-n", "--automation-mode", "--force-exit"]
    
    if args:
        arg_str = ",".join(args)
        cmd.append(f'-r="{script_path},{arg_str}"')
    else:
        cmd.append(f'-r="{script_path}"')
    
    result = subprocess.run(
        " ".join(cmd),  # Shell=True on Windows for proper quoting
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout
    )
    return result.returncode
```

### GraXpert CLI integration

```python
def run_graxpert(input_path: str, output_path: str, 
                 operation: str = "background-extraction", 
                 gpu: bool = True, smoothing: float = 0.1) -> int:
    """Execute GraXpert via CLI."""
    cmd = [
        "GraXpert-win64.exe",
        input_path,
        "-cli",
        "-cmd", operation,
        "-output", output_path,
        "-gpu", str(gpu).lower(),
        "-correction", "Subtraction",
        "-smoothing", str(smoothing)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"GraXpert failed: {result.stderr}")
    return result.returncode
```

### PJSR dynamic script generation

```python
def generate_bxt_script(input_path: str, output_path: str, 
                        correct_only: bool = False,
                        sharpen_stars: float = 0.25,
                        sharpen_nonstellar: float = 0.40) -> str:
    """Generate a PJSR script for BlurXTerminator."""
    # CRITICAL: PJSR requires forward slashes even on Windows
    input_path = input_path.replace("\\", "/")
    output_path = output_path.replace("\\", "/")
    
    return f"""
#include <pjsr/DataType.jsh>

var window = ImageWindow.open("{input_path}")[0];
window.show();
var view = window.currentView;

var BXT = new BlurXTerminator;
BXT.correct_only = {str(correct_only).lower()};
BXT.automatic_psf = true;
BXT.sharpen_stars = {sharpen_stars};
BXT.sharpen_nonstellar = {sharpen_nonstellar};
BXT.adjust_halos = 0.05;
BXT.executeOn(view);

window.saveAs("{output_path}", false, false, false, false);
window.forceClose();
"""
```

### Configuration file schema

```json
{
    "target": {
        "name": "NGC1499",
        "common_name": "California Nebula",
        "ra": "04h03m18s",
        "dec": "+36°25′18″"
    },
    "directories": {
        "raw_nb": "D:/Astro/NGC1499/NB_raw",
        "raw_rgb": "D:/Astro/NGC1499/RGB_raw",
        "calibration_nb": "D:/Astro/Calibration/NB_gain100",
        "calibration_rgb": "D:/Astro/Calibration/RGB_gain-25",
        "working": "E:/AstroPipeline/NGC1499/working",
        "output": "E:/AstroPipeline/NGC1499/output",
        "archive": "F:/AstroArchive/NGC1499"
    },
    "acquisition": {
        "nb": {
            "gain": 100,
            "offset": 50,
            "exposure": 300,
            "temperature": -20,
            "filters": ["Ha", "OIII", "SII"],
            "filter_bandwidth_nm": 3
        },
        "rgb": {
            "gain": -25,
            "offset": "TBD",
            "exposure": 10,
            "temperature": -20,
            "filters": ["R", "G", "B"]
        }
    },
    "preprocessing": {
        "pedestal": 150,
        "drizzle_scale": 2,
        "drizzle_drop_shrink": 0.9,
        "rejection_algorithm": "ESD",
        "esd_significance": 0.05,
        "esd_low_relaxation": 2.0
    },
    "processing": {
        "bxt_sharpen_stars": 0.25,
        "bxt_sharpen_nonstellar": 0.40,
        "bxt_adjust_halos": 0.05,
        "nxt_denoise_linear": 0.80,
        "nxt_detail_linear": 0.15,
        "nxt_denoise_nonlinear": 0.40,
        "graxpert_smoothing": 0.1,
        "scnr_amount": 0.00,
        "stretch_target_median": 0.22,
        "star_brightness_factor": 0.70
    },
    "breakpoints": {
        "crop": true,
        "deconvolution_review": true,
        "stretch_review": true,
        "color_grading": true,
        "star_recombination": true
    }
}
```

### Automation feasibility matrix

| Component | Automatable | Method | Risk | Notes |
|---|---|---|---|---|
| WBPP | ⚠️ Difficult | Complex PJSR; consider manual pipeline instead | High | File list construction is GUI-embedded |
| Manual calibration | ✅ Full | PJSR executeGlobal() for each process | Low | Preferred for automation |
| StarAlignment | ✅ Full | PJSR | Low | Including RGB→NB registration |
| ImageIntegration | ✅ Full | PJSR | Low | |
| DrizzleIntegration | ✅ Full | PJSR | Low | |
| BlurXTerminator | ✅ Full | PJSR executeOn() | Low | CPU+GPU both work headless |
| NoiseXTerminator | ✅ Full | PJSR executeOn() | Low | CPU+GPU both work headless |
| StarXTerminator | ✅ Full | PJSR executeOn() | Low–Med | OOM risk on large Drizzle 2× images |
| GraXpert | ✅ Full | CLI subprocess from Python | Low | Native CLI support |
| SPCC | ✅ Full | PJSR (requires plate solve) | Medium | Catalog download may block |
| GHS | ✅ Full | PJSR process module | Low | |
| PixelMath / SCNR / Curves | ✅ Full | PJSR | Low | |
| SETI Statistical Stretch | ⚠️ Adapt | Extract engine from GUI .js source | Medium | Not designed for headless |
| SETI Halo Reducer | ⚠️ Adapt | Extract engine from GUI .js source | Medium | |

### RC Astro headless compatibility

BXT, NXT, and SXT are **native C++ process modules** (not scripts), making them fully compatible with PJSR `executeOn()` and automation mode. They do NOT require a GUI context. GPU acceleration requires:
- **Windows/Linux:** TensorFlow GPU libraries from `https://www.rc-astro.com/TensorFlow/PixInsight/GPU`
- **macOS:** CoreML (automatic)
- **Fallback:** CPU-only mode (AVX/AVX2 required) works reliably in all environments

Known issues: Anti-virus may quarantine TensorFlow DLLs. `RESOURCE_EXHAUSTED: OOM` errors with StarXTerminator on Drizzle 2× images (~800MB XISF). Mitigation: process SXT on individual channels before Drizzle, or ensure >8GB VRAM.

---

## 12. NGC 1499 Specific Strategy

The California Nebula's defining processing challenge is the **extreme Ha/SII/OIII imbalance**. Ha is extremely bright, SII shows surprising structural detail (sometimes exceeding Ha in certain filamentary regions), but OIII appears only as a faint veil. Processing must compensate at multiple stages:

1. **Unequal integration time:** Collect 1.5–2× more OIII than Ha/SII
2. **LinearFit:** Before combination, LinearFit each channel to the OIII reference (weakest channel) to prevent Ha domination
3. **Differential noise reduction:** OIII: NXT denoise 0.85–0.90; SII: 0.75–0.85; Ha: 0.65–0.75
4. **HDRMultiscaleTransform:** 6 layers with luminance mask protecting background — compresses the bright central emission ridge (50–100× brighter than faint OIII envelope)
5. **Synthetic luminance (optional):** Use Ha (or 0.7×Ha + 0.3×SII blend) as luminance via LRGBCombination applied after SHO color processing — preserves structural detail from brightest channels while SHO provides color
6. **Angular extent:** NGC 1499 is ~2.5° × 0.67°, fitting within the 3.33° × 2.22° FOV. No mosaicing required, but framing should include surrounding dark nebulosity for aesthetic context.

---

## 13. Project Deliverables and Phasing

### Development phases with clear deliverables

Each phase is a self-contained deliverable that can be tested independently. Designed for token-efficient Claude Code sessions that fit within rolling 4-hour Pro plan windows.

#### Sprint 0: Spike Tests (1 session, ~30 min)
**Deliverable:** Test report confirming headless feasibility
- [ ] Run BXT on a single XISF frame via CLI headless mode
- [ ] Run NXT on a single frame via CLI headless mode
- [ ] Run SXT on a single frame via CLI headless mode
- [ ] Run GraXpert CLI on a single frame
- [ ] Confirm `--force-exit` produces clean subprocess return code
- [ ] Document any failures or required workarounds

#### Sprint 1: Configuration and Orchestrator Skeleton (1 session)
**Deliverable:** `orchestrator.py`, `pipeline_config.json`, logging framework
- [ ] JSON config schema with validation
- [ ] PipelineStage base class
- [ ] PipelineOrchestrator state machine with breakpoint system
- [ ] Structured logging (JSON logs per stage)
- [ ] File existence checking (idempotency)
- [ ] `--force` and `--start-stage` CLI flags

#### Sprint 2: PJSR Script Generator Library (1–2 sessions)
**Deliverable:** `pjsr_generator.py` — functions that emit valid .js scripts
- [ ] Template system for generating PJSR from Python
- [ ] Forward-slash path normalization
- [ ] Script generators for: ImageCalibration, StarAlignment, ImageIntegration, DrizzleIntegration, DynamicCrop
- [ ] Script generators for: BXT, NXT, SXT, PixelMath, ChannelExtraction, ChannelCombination
- [ ] `run_pjsr()` subprocess wrapper with timeout and error handling

#### Sprint 3: Phase 1 Implementation (1–2 sessions)
**Deliverable:** Automated preprocessing for both NB and RGB tracks
- [ ] NB calibration → registration → integration pipeline
- [ ] RGB calibration → registration → integration pipeline
- [ ] RGB-to-NB cross-registration (StarAlignment to Ha reference)
- [ ] DrizzleIntegration support
- [ ] SubframeSelector weighting and rejection

#### Sprint 4: Phase 2–3 Implementation (1–2 sessions)
**Deliverable:** Linear processing through stretching
- [ ] GraXpert CLI integration for background extraction
- [ ] BXT correct-only + sharpen two-pass pipeline
- [ ] NXT linear noise reduction
- [ ] SXT star removal (per NB channel)
- [ ] GHS/StatStretch stretching
- [ ] Foraxx palette PixelMath

#### Sprint 5: Phase 4–5 Implementation (1–2 sessions)
**Deliverable:** Nonlinear processing and star recombination
- [ ] SCNR, Curves, HDR, LHE on starless SHO
- [ ] RGB star processing (SPCC, stretch, SXT extraction)
- [ ] Screen blend star recombination
- [ ] Final output and archiving

#### Sprint 6: Integration Testing and Polish (1 session)
**Deliverable:** End-to-end pipeline run on California Nebula data
- [ ] Full pipeline execution with all breakpoints
- [ ] Performance profiling (per-stage timing)
- [ ] Error recovery testing (simulate crashes, resume)
- [ ] Documentation and README

---

## 14. Claude Code Kickoff Prompt

The following block can be used as a system prompt for Claude Code to begin Sprint 1:

```
SYSTEM PROMPT — Astro-Pipeline Sprint 1

You are building a hybrid Python/PixInsight automation pipeline for narrowband 
astrophotography processing. Read the full design_doc.md in this repository for 
complete architectural context.

SPRINT 1 DELIVERABLES:
1. orchestrator.py — Main pipeline controller
   - PipelineStage base class with input/output validation and idempotency
   - PipelineOrchestrator state machine with breakpoint system
   - CLI interface: --config, --start-stage, --force, --dry-run flags
   - Structured JSON logging per stage

2. pipeline_config.json — Configuration schema
   - All parameters from the config schema in design_doc.md Section 11
   - JSON schema validation on load
   - Path normalization (forward slashes for PJSR, native for Python)

3. pi_runner.py — PixInsight subprocess management
   - run_pjsr(script_path, args, timeout) function
   - Proper --automation-mode --force-exit invocation
   - Return code parsing and error detection
   - Script argument passing via jsArguments

4. graxpert_runner.py — GraXpert CLI integration
   - run_graxpert(input, output, operation, gpu, smoothing) function
   - Subprocess with timeout and error handling

CONSTRAINTS:
- Windows paths: all PJSR-bound paths must use forward slashes
- subprocess.run() with shell=True on Windows for proper quoting
- --force-exit flag is mandatory to prevent PI hanging
- All intermediate files written to config.directories.working
- All final outputs written to config.directories.output
- Validate file existence before and after each stage
- Log: input files, parameters, wall-clock time, exit code, output file hash (SHA256)

DO NOT attempt to write PJSR scripts yet — that is Sprint 2.
Focus on the Python orchestration framework only.
```

---

## Appendix A: PJSR Process Reference

Quick reference for PJSR class names used throughout the pipeline:

| Pipeline Operation | PJSR Class | Type |
|---|---|---|
| Calibration | `ImageCalibration` | Process |
| Cosmetic correction | `CosmeticCorrection` | Process |
| Subframe selection | `SubframeSelector` | Script |
| Star alignment | `StarAlignment` | Process |
| Local normalization | `LocalNormalization` | Process |
| Image integration | `ImageIntegration` | Process |
| Drizzle integration | `DrizzleIntegration` | Process |
| Dynamic crop | `DynamicCrop` | Process |
| Background extraction | `AutomaticBackgroundExtractor` | Process |
| Deconvolution (AI) | `BlurXTerminator` | Process (module) |
| Noise reduction (AI) | `NoiseXTerminator` | Process (module) |
| Star removal (AI) | `StarXTerminator` | Process (module) |
| Channel extraction | `ChannelExtraction` | Process |
| Channel combination | `ChannelCombination` | Process |
| Pixel math | `PixelMath` | Process |
| Color calibration | `SpectrophotometricColorCalibration` | Process |
| Green removal | `SCNR` | Process |
| Curves | `CurvesTransformation` | Process |
| Histogram stretch | `HistogramTransformation` | Process |
| Hyperbolic stretch | `GeneralizedHyperbolicStretch` | Process |
| HDR compression | `HDRMultiscaleTransform` | Process |
| Local contrast | `LocalHistogramEqualization` | Process |
| Image solver | `ImageSolver` | Script |

## Appendix B: File Naming Convention

```
{target}_{channel}_{stage}_{timestamp}.xisf

Examples:
NGC1499_Ha_raw_20260115.xisf
NGC1499_Ha_calibrated.xisf
NGC1499_Ha_master.xisf
NGC1499_Ha_bgext.xisf
NGC1499_SHO_linear.xisf
NGC1499_SHO_bxt.xisf
NGC1499_SHO_nxt.xisf
NGC1499_Ha_starless.xisf
NGC1499_Ha_starless_stretched.xisf
NGC1499_SHO_foraxx.xisf
NGC1499_SHO_final_starless.xisf
NGC1499_RGB_stars_only.xisf
NGC1499_final.xisf
```

## Appendix C: Directory Structure

```
astro-pipeline/
├── design_doc.md              # This document
├── README.md                  # Project overview and quickstart
├── orchestrator.py            # Main pipeline controller
├── pipeline_config.json       # User configuration
├── pi_runner.py               # PixInsight subprocess management
├── graxpert_runner.py         # GraXpert CLI integration
├── pjsr_generator.py          # Dynamic PJSR script generation
├── stages/                    # Stage implementations
│   ├── __init__.py
│   ├── preprocessing.py       # Phase 1: calibration, registration, integration
│   ├── linear_processing.py   # Phase 2: crop, bgext, BXT, NXT, SXT
│   ├── stretching.py          # Phase 3: stretch, palette combination
│   ├── nonlinear.py           # Phase 4: SCNR, curves, HDR, LHE
│   └── star_processing.py     # Phase 5: RGB stars, SPCC, recombination
├── templates/                 # PJSR script templates
│   ├── calibration.js.tmpl
│   ├── registration.js.tmpl
│   ├── integration.js.tmpl
│   ├── bxt.js.tmpl
│   ├── nxt.js.tmpl
│   ├── sxt.js.tmpl
│   ├── pixelmath.js.tmpl
│   ├── spcc.js.tmpl
│   └── stretch.js.tmpl
├── logs/                      # Structured JSON logs
├── tests/                     # Unit and integration tests
│   ├── test_config.py
│   ├── test_orchestrator.py
│   └── test_pjsr_generator.py
└── scripts/                   # Utility scripts
    └── spike_test.py          # Sprint 0 headless feasibility tests
```

---

## 15. Implementation Status and Quality Improvement Roadmap

> **Status as of 2026-03-01**: All 29 pipeline stages implemented and end-to-end pipeline runs to completion (tested on NGC 1499). Output images are technically produced but exhibit severe color balance issues ("super blue and purple") indicating multiple upstream algorithmic deficiencies.

### 15.1 Plan vs Implementation Delta

The table below documents every significant deviation between the design doc specification and the current implementation.

| # | Design Spec | Current Implementation | Impact | Priority |
|---|---|---|---|---|
| **D1** | DrizzleIntegration 2× using .xdrz sidecar files from StarAlignment | StarAlignment uses `executeOn` loop (workaround for broken `targets` setter in current PI version). `executeOn` does not write `.xdrz` sidecar files. `NBDrizzleStage` detects missing `.xdrz` and **copies the regular ImageIntegration master as-is** — zero Drizzle benefit. | All channels are 1× resolution. Undersampling penalty not recovered. | **P1** |
| **D2** | GHS SP measured from histogram peak per channel | SP hardcoded to `0.0001` for all channels. NGC 1499 OIII background after GraXpert has a different pedestal level than Ha. Identical SP causes differential stretch behavior. | OIII is over-stretched relative to Ha; SII differs from both. Contributes to color imbalance. | **P2** |
| **D3** | GHS stretch factor D tuned per channel | Single `ghs_stretch_factor` (5.0) applied to Ha, OIII, and SII identically. NGC 1499's 50–100× Ha/OIII brightness ratio means OIII needs much more aggressive stretch than Ha to reach comparable display brightness. | Ha dominates Foraxx combination; OIII appears faint/suppressed. Blue/purple cast. | **P2** |
| **D4** | GraXpert smoothing tuned for extended FOV-filling emission | `graxpert_smoothing = 0.1` (design doc default). NGC 1499 fills the FOV; 0.1 is too fine-grained and may mistake large-scale faint emission for background gradient. | GraXpert subtracts real nebula signal at fine scales. Permanently destroys faint OIII. | **P2** |
| **D5** | HDRMT and LHE applied with luminance mask protecting background | Applied unmasked (`executeOn(view)` with no mask set). HDRMT compresses dynamic range globally including background noise. LHE amplifies local contrast including noise texture in dark areas. | Background noise amplified. Mottled noise pattern in dark regions. Faint OIII structure lost. | **P2** |
| **D6** | NoiseXTerminator (NXT) — "Best overall" noise reduction | **NXT license not available.** GraXpert Denoise used as free CLI-automatable substitute at all NR stages (linear per-channel, nonlinear final). Per design doc: "can soften stars; reports of mottled residuals." | Cannot be fixed without purchasing NXT license. GraXpert is required for now. See Section 15.2. | Blocked |
| **D7** | SPCC on RGB star image for photometrically accurate colors | SPCC runs but **silently fails** due to catalog connectivity or WCS issues ("Unable to compute a valid scale estimate, channel 0"). Failure only visible in PI console logs. No hard error raised. | RGB star colors are uncalibrated. Star chromaticity is wrong (hardware-default color balance). | **P3** |
| **D8** | GHS SP for RGB star stretch tuned separately | Same `0.0001` hardcoded. RGB master background statistics differ significantly from NB (much brighter stars, different background profile). | RGB star stretch may be severely under or over-stretched. Affects star halo brightness in final blend. | **P3** |
| **D9** | StarXTerminator generates stars-only from RGB: discard starless | Implemented correctly — stars-only image saved, starless discarded. ✅ | None. | — |
| **D10** | Star halo reduction via SETI Astro Halo Reducer | Implemented as a no-op stub. The `generate_star_halo_reduction` function in `pjsr_generator.py` emits a comment only. | Star halos from bright foreground stars not reduced. Minor aesthetic concern for this target. | **P4** |
| **D11** | CurvesTransformation hue/saturation with human-tuned curves | Default S-curve and hue shift hardcoded. Intended as a starting point for manual adjustment at BP4. | Without breakpoint review, default curves may be wildly wrong for this specific data. BP4 is active — correct approach is human tuning at breakpoint. | — |
| **D12** | PI console output logged and error patterns flagged | **Implemented this session**: `get_last_pi_output()` module function, `.pi.log` / `.graxpert.log` sidecar files per stage, `pi_flagged_lines` in JSON stage logs. ✅ | None. | — |

### 15.2 Tool Licensing: NXT vs GraXpert Denoise

**The design doc classifies NoiseXTerminator (NXT) as "Best overall" for noise reduction** (Section 10), with GraXpert Denoise as a "Backup option if NXT unavailable."

**Current status: NXT license not available.** RC Astro NXT requires a paid license from `https://www.rc-astro.com/`. Until purchased, **GraXpert Denoise is the required substitute for all NR stages:**

- Linear per-channel NR (replacing NXT linear 0.80/0.15): `GraXpertDenoiseLinearStage` (per-channel, strengths 0.40/0.50/0.60)
- Nonlinear final NR (replacing NXT nonlinear 0.40/0.15): `GraXpertDenoiseNonlinearStage` (strength 0.35)

**Known GraXpert Denoise limitations vs NXT:**
- Can soften stars at high strength (>0.6)
- Reports of mottled residuals in some datasets
- Generally requires lower strength than equivalent NXT settings to achieve similar results

**When NXT is available**, update `pipeline_config.json` to add NXT parameters and create `NXTStage` wrappers using the existing `generate_noise_xterminator` functions in `pjsr_generator.py`. The GraXpert stages can be disabled per-run.

**Table: Noise Reduction Algorithm Comparison (Updated)**

| Algorithm | Status | Strengths | Weaknesses |
|---|---|---|---|
| **NoiseXTerminator (NXT)** | License required | Best detail preservation; frequency-aware; simple | Paid license; AI black box |
| **GraXpert Denoise** | **Active (in use)** | Free; CUDA CLI; automatable | Softer stars; mottled residuals possible |
| **MultiscaleLinearTransform** | Available (PJSR) | Per-scale control; reproducible; maskable | Labor-intensive per-scale tuning |
| **TGVDenoise** | Available (PJSR) | Theoretically optimal | Extremely difficult to configure; orange peel |

### 15.3 Aesthetic Quality Quantification

**The human problem**: Output images look "super blue and purple, kinda comically bad." This is a subjective observation, but it maps to specific, measurable phenomena.

**Core insight**: Human aesthetic taste in astrophotography reduces to a relatively small set of channel balance and tonal distribution constraints. Most "bad" images fail on a handful of measurable metrics.

#### 15.3.1 Channel Balance Metrics

The primary cause of blue/purple cast in SHO + RGB star composites is **channel amplitude imbalance after Foraxx + SCNR + LinearFit**. Measurable:

```
channel_ratio_RG = median(R_channel) / median(G_channel)   # should be ~0.9–1.1
channel_ratio_BG = median(B_channel) / median(G_channel)   # should be ~0.8–1.0
dominant_hue     = argmax([R_mean, G_mean, B_mean])         # blue dominant → B >> R, G
color_cast_score = std([R_mean, G_mean, B_mean]) / mean([R_mean, G_mean, B_mean])
                                                             # < 0.15 = balanced, > 0.30 = strong cast
```

For the "super blue/purple" problem specifically:
- `B_mean / R_mean > 1.5` → extreme blue cast
- `B_mean / G_mean > 1.3` → extreme blue-green imbalance
- `R_mean / G_mean < 0.7` → Ha severely under-represented → Foraxx red channel suppressed

The root cause in our pipeline: **OIII (B in Foraxx) is being stretched more aggressively than Ha (influences R) due to identical SP values across channels with very different background levels.** OIII background is darker, so SP=0.0001 relative to the pedestal is a larger fraction, causing disproportionate stretch.

#### 15.3.2 Histogram Quality Metrics

```
background_clip_fraction  = fraction of pixels at 0.0   # > 0.02 → clipping
highlight_clip_fraction   = fraction of pixels at 1.0   # > 0.005 → star blowout
background_median         # after stretch, should be 0.18–0.26
dynamic_range_utilization = percentile(99.5) - percentile(0.5)  # should be > 0.7
stretch_efficiency = background_median / highlight_clip_fraction  # balance metric
```

#### 15.3.3 Star Quality Metrics

```
star_fwhm_ratio = median(RGB_stars_fwhm) / median(NB_stars_fwhm)  # should be ~1.0 (aligned)
star_color_temperature = color_temperature_estimate(R_stars, G_stars, B_stars)
star_saturation = median(saturation of star pixels)  # should be > 0.3 for visible colors
```

#### 15.3.4 Automated Quality Gate

The pipeline should compute and log these metrics after key stages (especially after Foraxx combination and after final star recombination). Thresholds:

```python
QUALITY_THRESHOLDS = {
    "channel_ratio_bg":      (0.75, 1.35),   # B/G ratio acceptable range
    "channel_ratio_rg":      (0.80, 1.25),   # R/G ratio acceptable range
    "background_median":     (0.15, 0.30),   # post-stretch background level
    "highlight_clip":        (0.000, 0.005), # fraction clipped to white
    "background_clip":       (0.000, 0.020), # fraction clipped to black
    "color_cast_score":      (0.000, 0.25),  # channel std/mean
}
```

If any metric falls outside its acceptable range, the pipeline emits a **QUALITY_WARN** in the JSON stage log and console output. This translates the "comically bad" human observation into an automated diagnostic that can catch issues before they propagate downstream.

**Implementation**: A `QualityAnalysisStage` (or inline post-stage function) that computes these using PI's `Statistics` process and `PixelMath` to extract per-channel data. Results written to `working/quality_metrics_{stage}.json`.

#### 15.3.5 What "comically blue" means mathematically

The specific issue (super blue/purple) is most likely caused by a combination of:

1. **OIII over-stretch** (D1/D2/D3 combined): OIII gets more stretch than Ha/SII → dominates the Foraxx B channel → blue cast
2. **LinearFit over-correction**: LinearFit scales Ha+SII DOWN to match the (artificially over-stretched) OIII level → further reduces red contribution
3. **SPCC failure on RGB** (D7): Without photometric calibration, RGB star colors are undefined → star layer may add blue/purple
4. **GraXpert background over-subtraction at low smoothing** (D4): Fine-scale artifacts may preferentially affect Ha vs OIII differently

**Diagnostic procedure**: Measure `B_mean / R_mean` and `B_mean / G_mean` at the Foraxx output, before SCNR. If B/R > 1.5, the stretch is the culprit. If B/R is ~1.0 but the combined image still looks bad, investigate the star layer.

### 15.4 Priority Fix Roadmap

The following improvements are ordered by impact on image quality and independence from other fixes.

#### P1: Drizzle — Enable real .xdrz generation

**Problem**: StarAlignment `executeOn` loop does not produce `.xdrz` sidecar files. NBDrizzleStage detects empty `.xdrz` directory and copies the regular ImageIntegration master unchanged.

**Fix**: Add `generate_star_alignment_global()` variant in `pjsr_generator.py` using `P.targets = [...]; P.executeGlobal()` with `P.outputDirectory` set. This approach writes `.xdrz` files alongside registered frames in the output directory. Use this for NB preprocessing when `drizzle_scale > 1`.

**Fallback**: If `executeGlobal` fails (PI version bug), `NBDrizzleStage` already handles the no-`.xdrz` case gracefully. The pipeline continues — Drizzle is a quality improvement, not required for a result.

**Expected benefit**: Rounder star profiles, smoother noise texture, slightly improved resolution for the best-seeing frames.

#### P2 (most impactful): Per-channel GHS stretch with measured SP

**Problem**: SP=0.0001 is hardcoded for all three NB channels. The actual background pedestal after GraXpert background extraction differs per channel. OIII has a darker background than Ha; if SP is set at the Ha background level, it is above OIII's background peak, causing GHS to apply the hyperbolic stretch starting before the data (over-stretching OIII).

**Fix**:
1. Add `MeasureHistogramStage`: new stage that runs a PJSR `Statistics` script on each bgext output, measures background median, writes to `working/histogram_stats.json`
2. Modify `StretchNBStage`: read `histogram_stats.json`, use per-channel measured median as SP
3. Add per-channel D config: `ghs_stretch_factor_ha`, `ghs_stretch_factor_oiii`, `ghs_stretch_factor_sii` (default all to current `ghs_stretch_factor`)

**Expected benefit**: Proper per-channel stretch calibration. Primary fix for the blue/purple color cast.

#### P2 (concurrent): Increase GraXpert smoothing for extended emission

**Problem**: `graxpert_smoothing = 0.1` is too fine for NGC 1499. The nebula fills the entire 3.33° × 2.22° FOV, with extended Ha and SII emission across the full frame. At smoothing=0.1, GraXpert's AI may interpret large-scale emission gradients as background, subtracting real signal.

**Fix**: Increase `graxpert_smoothing` from 0.1 to 0.25 in `pipeline_config.json`. The range 0.2–0.3 is recommended for targets with extended emission. This is a one-line config change with meaningful quality impact.

**Expected benefit**: Preserves faint outer OIII emission that was being subtracted as "gradient."

#### P2 (concurrent): Luminance mask for HDRMT and LHE

**Problem**: HDRMT and LHE run without any mask. Applied globally, HDRMT compresses background noise along with the nebula signal. LHE amplifies local contrast in the dark background, producing mottled noise texture.

**Fix**: Modify `generate_hdr_multiscale()` and `generate_local_histogram_equalization()` in `pjsr_generator.py` to:
1. Create a luminance mask from the image's own data (PixelMath luminance extraction)
2. Set the mask on the window before applying the operation
3. Remove the mask and delete the mask window after

The mask is the image's own luminance — bright nebula regions (where we want HDRMT/LHE to operate) appear white; dark background (which we want to protect) appears black. Since `maskInverted = false`, the operation applies to white (bright) regions only.

**Expected benefit**: Background noise not amplified. Faint OIII structure revealed by HDRMT/LHE rather than buried in noise.

#### P3: Fix SPCC failure detection and diagnostics

**Problem**: SPCC silently fails ("Unable to compute a valid scale estimate") due to WCS/catalog issues. The PI log now captures this (as of the logging fix), but the pipeline doesn't act on it.

**Fix**: Check for `pi_flagged_lines` after the SPCC stage in the orchestrator. If SPCC flags "Unable to compute" or "failed", emit a WARN and proceed without SPCC rather than continuing with a silently broken calibration. Also: ensure WCS/plate solve data is present in the Ha master (which is used as the RGB registration reference).

#### P3: Measure background after stretch for quality gate

**Problem**: No automated check that the stretch result is reasonable before LinearFit and Foraxx.

**Fix**: Implement the quality metrics described in Section 15.3. Run after `StretchNBStage` and after `ForaxxPaletteStage`. Write metrics to JSON and emit QUALITY_WARN if thresholds exceeded. This converts the "comically bad" observation to an automated diagnostic.

---

## 16. Code Audit — Rev 0.3.0 (2026-03-01)

Audit performed after first-light run on NGC 1499. Items removed immediately are marked **REMOVED**. Medium and low candidates are documented for next rev.

### Items Removed

| ID | File | Lines | What Was Removed | Why |
|----|------|-------|-----------------|-----|
| A1 | `pjsr_generator.py` | 784 | Dead variable `out_stem = Path(output_path).stem` in `generate_image_integration()` | Assigned, never read |
| A2 | `pjsr_generator.py` | 753–754 | Stale docstring reference to `generate_large_scale_rejection_script()` | Function does not exist; large-scale rejection is a parameter, not a separate function |
| A3 | `pjsr_generator.py` | 1099–1106 | NXT docstring claiming Phase 4 use; per-channel tuning notes with wrong values | Phase 4 uses GraXpert denoising, not NXT; per-channel NXT values were from an earlier design |
| A4 | `design_doc.md` | §14 GHS code | Wrong GHS PJSR property names (`D`, `b`, `SP`, `invertTransformation`) in all code examples | Correct names are `stretchFactor`, `localIntensity`, `symmetryPoint`, `inverse`; wrong names silently no-op |
| A5 | `design_doc.md` | §16 SCNR | Incorrect rationale ("Ha mapped to green") and stale default amount (0.65) | Foraxx puts Ha→R; SCNR with `preserveLuminance=true` destroys gold/amber; set to 0.00 |
| A6 | `design_doc.md` | §19–20 HDRMT/LHE | "Apply with luminance mask" comments | `PixelMath.executeGlobal()` fails silently in PI headless mode; masks removed from implementation |
| A7 | `pipeline_config.json` | config example in design_doc | `scnr_amount: 0.65` | Updated to 0.00 per Foraxx findings |

### Items Fixed / Wired

| ID | File | What Was Fixed | Why |
|----|------|---------------|-----|
| B1 | `stages/stretching.py` | `LinearFitStage` now reads `linear_fit_reference` from config | Config key existed but code hardcoded `"OIII"` |
| B2 | `pipeline_config.json` | Added `rgb_rejection_algorithm: "WinsorizedSigmaClip"` | `preprocessing.py` looked up this key with a hardcoded fallback; config had no backing value |

### Medium Candidates (Next Rev)

| ID | File | Lines | Description | Recommended Action |
|----|------|-------|-------------|-------------------|
| M1 | `pjsr_generator.py` | 2333–2425 | `write_reference_templates()` generates doc-only `.js.tmpl` files to `templates/`. Has 4 passing tests. Never called at runtime. | Keep as dev-documentation utility but add `__main__` entry point so it can be invoked explicitly to refresh templates |
| M2 | `templates/*.tmpl` | all | Nine pre-written template files in `templates/`. Never read at runtime — purely documentation artifacts generated by `write_reference_templates()`. | Regenerate from `write_reference_templates()` if templates get stale; add a CI check that templates match generator output |
| M3 | `stages/preprocessing.py` | 622 | `rgb_rejection_algorithm` looked up separately from `rejection_algorithm`. Now wired in config (B2), but the two keys diverge silently if one is changed without the other. | Consider a single `rejection_algorithm` key with an optional RGB override, or document that they must be maintained separately |
| M4 | `pjsr_generator.py` | 525–599 | `generate_star_alignment()` — docstring doesn't explain when to use `executeOn()` variant vs `executeGlobal()` variant | Add usage guidance: `executeGlobal()` for multi-frame batch, `executeOn()` for single-view workaround |
| M5 | `design_doc.md` | §21 Step 21 | Still says "NoiseXTerminator (nonlinear, light touch)" as Phase 4 Stage 21 — superseded by GraXpert denoising | Update stage table and step description to reflect GraXpert nonlinear denoising |
| M6 | `design_doc.md` | §5B line ~260 | "Optional for RGB stars — drizzle doesn't benefit star color accuracy" but code always sets `generate_drizzle_data=False` for RGB | Document that RGB drizzle is explicitly disabled; clarify it's not a config option |

### Low Candidates (Cosmetic / Future)

| ID | File | Lines | Description | Recommended Action |
|----|------|-------|-------------|-------------------|
| L1 | `scripts/spike_test.py` | throughout | One-off spike test from Sprint 0; exception handling duplicated in every test function; parameter names may have drifted from final API (e.g. `SXT.stars_image` vs `SXT.stars`) | Review before using as reference; consider removing or converting to proper test |
| L2 | `pjsr_generator.py` | 14 | Phase 2 module-level header still lists "NoiseXTerminator" — NXT generates scripts but GraXpert runs denoising | Update header comment |
| L3 | `tests/test_pjsr_generator.py` | 801–835 | `TestWriteReferenceTemplates` calls `write_reference_templates()` which uses `nxt_denoise_linear`/`nxt_detail_linear` config keys not in `pipeline_config.json`; function uses hardcoded fallback defaults | Add the keys to config or document that the template writer uses its own representative defaults |

---
