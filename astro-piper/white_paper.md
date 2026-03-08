# Monochrome SHO + RGB Star Processing: A Complete Reference

> **System:** ASI2600MM Pro + Apertura 75Q (75mm f/5.4, 405mm FL)
> **Pipeline:** astro-piper v0.3.0
> **Last Updated:** 2026-03-08

This document is the single authoritative reference for processing narrowband SHO nebula data combined with broadband RGB star data from the system above. It explains *why* each step exists, *what* it does to your photons, *how* to execute it in PixInsight's GUI, and *where* the corresponding code lives in the astro-piper automation pipeline. Use it when running the pipeline, debugging a stage output, or processing manually at a breakpoint.

---

## Table of Contents

1. [System & Sensor Science](#1-system--sensor-science)
2. [Acquisition Strategy & Why It Matters](#2-acquisition-strategy--why-it-matters)
3. [The Two-Track Architecture](#3-the-two-track-architecture)
4. [Phase 1 — Calibration, Registration & Integration (WBPP)](#4-phase-1--calibration-registration--integration-wbpp)
5. [Phase 2 — Linear Processing](#5-phase-2--linear-processing)
6. [Phase 3 — Stretching & Palette Combination](#6-phase-3--stretching--palette-combination)
7. [Phase 4 — Nonlinear Processing (Starless Nebula)](#7-phase-4--nonlinear-processing-starless-nebula)
8. [Phase 5 — RGB Star Processing & Final Combination](#8-phase-5--rgb-star-processing--final-combination)
9. [The Math Behind the Magic](#9-the-math-behind-the-magic)
10. [Known Issues & Debugging Guide](#10-known-issues--debugging-guide)
11. [Quick Reference Tables](#11-quick-reference-tables)

---

## 1. System & Sensor Science

### 1.1 Why These Numbers Matter

Every processing decision traces back to the physics of your sensor and optics. Get these wrong and you're fighting math, not improving your image.

| Parameter | Value | What It Means |
|---|---|---|
| Pixel size | 3.76 μm | Combined with your 405mm FL, this gives a **plate scale of 1.914"/pixel**. |
| Plate scale | 1.914"/px | Your camera "sees" about 2 arcseconds per pixel. Typical seeing in Beaverton, OR is 2.5–4.0" FWHM, meaning your system is **mildly undersampled** — stars won't always be fully round because you don't have enough pixels across the seeing disk. |
| Nyquist limit | ~3.83" FWHM | You need seeing better than ~3.8" to properly sample the data. On a good night you'll beat this; on an average night you won't. This is exactly why we drizzle. |
| Field of view | 3.33° × 2.22° | Huge — the California Nebula (2.5° × 0.67°) fits comfortably with room for surrounding dark nebulosity. No mosaicing needed. |
| Full well (Gain 100) | ~50,000 e⁻ | At Gain 100 (HCG mode), read noise drops to ~1.0 e⁻ but you sacrifice well depth. Fine for narrowband where you never saturate. |
| Full well (Gain -25) | ~80,000+ e⁻ | Maximum well depth. Bright stars in broadband won't clip. This is why we use Gain -25 for RGB. |
| Dark current (-20°C) | 0.00012 e⁻/s/px | Essentially zero. In a 300s exposure, each pixel accumulates ~0.036 e⁻ of dark current — utterly negligible compared to read noise. We still take darks because they map **hot pixels**, not because the dark signal itself matters. |
| Amp glow | None | Confirmed absent on the ASI2600MM Pro. No need for special handling. |

### 1.2 The Gain 100 / Gain -25 Split

This is not arbitrary. The ASI2600MM Pro has a **hardware switchover** at Gain 100 called High Conversion Gain (HCG) mode. Below Gain 100, the sensor operates in standard mode with higher read noise (~3.3 e⁻) but more well capacity. At Gain 100+, internal capacitance changes drop read noise to ~1.0 e⁻.

- **Gain 100 for narrowband:** 3nm filters block 97%+ of sky glow. Your images are severely read-noise-limited (very few photons per pixel per frame). HCG mode's 1.0 e⁻ read noise is critical — it means the camera's own electronics contribute less noise than a single photon of sky background.
- **Gain -25 for RGB stars:** Broadband filters let everything through. Bright stars flood the sensor with photons. You need maximum well depth (80,000+ e⁻) to avoid clipping star cores to pure white, which permanently destroys color information in the brightest stars.

**In the pipeline:** `pipeline_config.json` → `acquisition.nb.gain = 100`, `acquisition.rgb.gain = -25`

---

## 2. Acquisition Strategy & Why It Matters

### 2.1 Dithering

Between exposures, the mount shifts the pointing by 5–8 pixels in a random direction. This serves two critical purposes:

1. **Hot pixel mitigation:** A hot pixel always appears at the same sensor location. With dithered data, when you stack, the hot pixel signal is at different *sky* positions in each frame and gets rejected by the stacking algorithm. Without dithering, hot pixels survive rejection.
2. **Drizzle prerequisite:** Drizzle mathematics fundamentally require that each frame samples slightly different sub-pixel positions on the sky. Without dithering, all your frames sample the same sub-pixel grid and Drizzle offers zero benefit.

### 2.2 Drizzle 2x

Your system is undersampled at ~1.91"/px. Drizzle is a technique from Hubble Space Telescope data reduction (Fruchter & Hook 2002) that reconstructs a higher-resolution image from dithered, undersampled data.

- **Scale 2x** — The output image has 4x as many pixels (double each axis). Your effective plate scale becomes **0.957"/px**, which properly samples typical seeing.
- **Drop Shrink 0.9** — Each input pixel is "shrunk" to 90% of its original size before being placed onto the output grid. Smaller drops mean sharper reconstruction but more noise (fewer input pixels contribute to each output pixel). 0.9 is conservative and safe.
- **Kernel: Square** — The simplest and most artifact-free drop shape.

**In the pipeline:** `preprocessing.drizzle_scale = 2`, `drizzle_drop_shrink = 0.9`
**In PixInsight:** StarAlignment → Enable "Generate drizzle data" → this writes `.xdrz` sidecar files. Then DrizzleIntegration uses those sidecar files when stacking.

**Result:** Rounder star profiles, smoother noise texture, and better resolution on nights with good seeing. The output XISF files are ~800MB each (4x the pixels).

### 2.3 NGC 1499 Channel Imbalance

The California Nebula has extreme brightness differences between channels:

| Channel | Relative Brightness | What You See |
|---|---|---|
| Ha (656.3nm) | ████████████████████ 100% | Blazing bright. Every 300s sub shows obvious structure. |
| SII (671.6nm) | ██████████ ~50% | Moderate. Shows surprising filamentary detail in some regions. |
| OIII (500.7nm) | ██ ~5-10% | A whisper. Barely visible in individual subs. Requires 6-12+ hours of total integration to reveal the faint emission veil. |

This imbalance is the #1 reason SHO processing is hard. Every step from here forward must account for it, or Ha will overwhelm everything and your image will be a monochrome red/orange blob.

**Integration time budget:**

| Channel | Minimum | Target | Notes |
|---|---|---|---|
| Ha | 4h (48×300s) | 6–8h | Bright; stacks quickly |
| SII | 4h (48×300s) | 6–8h | Moderate |
| OIII | 6h (72×300s) | 8–12h | **Needs disproportionately more time** |
| RGB each | 5 min (30×10s) | 8 min (50×10s) | 30+ subs for clean rejection |

---

## 3. The Two-Track Architecture

The fundamental design decision: **narrowband data provides the nebula, broadband RGB data provides the stars.** They are processed completely independently until the final screen blend.

```
    NARROWBAND TRACK (nebula)                     RGB TRACK (stars)
    ─────────────────────────                     ────────────────
    Ha/OIII/SII 300s Gain 100                     R/G/B 10s Gain -25

    Phase 1: Calibrate → Register → Drizzle       Phase 1: Calibrate → Register
    Phase 2: Crop → BgExt → BXT → Denoise         ↓
             → Split → SXT (remove stars)          Register RGB to Ha reference
    Phase 3: Stretch → LinearFit → Foraxx         ChannelCombination → SPCC
    Phase 4: SCNR → Curves → HDR → LHE            → Stretch → SXT (extract stars)
             → Final denoise                       ↓
             ↓                                     RGB_stars_only
             SHO_final_starless                    ↓
             ↓                                     ↓
             └──────── SCREEN BLEND PixelMath ─────┘
                       ~(~starless * ~stars)
                              ↓
                        FINAL IMAGE
```

**Why not just use the NB stars?** Narrowband filters capture a single emission wavelength. A star emitting broadband light looks identical through Ha, OIII, and SII — it's a point source of similar brightness in all three. When you SHO-map these, the star gets false colors (often magenta or green halos) that bear no relationship to the star's actual spectral class. Real RGB data captures the full stellar continuum (Wien's law), so a hot B star appears blue, a Sun-like G star appears yellow, and a cool M dwarf appears red — as physics demands.

**In the code:** The narrowband track flows through `preprocessing.py` → `linear_processing.py` → `stretching.py` → `nonlinear.py`. The RGB star track flows through `preprocessing.py` (separate calibration) → `star_processing.py`. They merge at `ScreenBlendStage` in `star_processing.py`.

---

## 4. Phase 1 — Calibration, Registration & Integration (WBPP)

This phase transforms raw sensor readouts into clean, aligned, stacked master images. In PixInsight, WBPP (Weighted Batch Pre-Processing) is the standard automated tool, but the pipeline does this manually using individual PJSR processes for finer control.

### 4.1 Calibration: What, Why, and How

**The Problem:** Every raw frame contains three types of contamination layered on top of the actual starlight:
1. **Bias/offset signal** — A fixed voltage the electronics add to every readout. Constant regardless of exposure time.
2. **Dark current** — Thermal electrons that accumulate in each pixel over time. Temperature-dependent.
3. **Flat field variation** — Vignetting (lens darkening toward edges), dust shadows, illumination non-uniformity.

**The Solution:** Mathematically remove each layer:

```
calibrated = (raw_light - master_dark) / master_flat
```

#### Dark Frames
- **What:** Exposures taken with the sensor cap on, same gain/exposure/temperature as your lights.
- **Why:** Subtracting the dark removes the bias voltage AND the thermal pattern AND maps hot pixels.
- **How many:** 30–50 per set. More is better for reducing noise in the master dark itself.
- **Critical match:** Gain 100 / 300s / -20°C darks for narrowband. Gain -25 / 10s / -20°C darks for RGB. **Do not mix them.**

#### Flat Frames
- **What:** Images of a uniformly illuminated field (twilight sky, flat panel, white t-shirt over the scope).
- **Why:** Dividing by the flat removes vignetting and dust donut shapes, making the background uniform.
- **How many:** 40–50 per filter. **Per-filter is mandatory** because each filter sits at a slightly different position in the optical train and has its own dust and vignetting pattern.

#### Flat Darks (Dark Flats)
- **What:** Dark frames matching the flat exposure settings (usually very short, <1s).
- **Why:** Flats have bias/dark contamination too. Flat darks remove it. Some workflows use a master bias instead.

**In PixInsight GUI (WBPP):**
1. Open WBPP from Script → Batch Processing → WeightedBatchPreProcessing.
2. Add your light frames, darks, flats, and flat darks.
3. Set **Output Pedestal** to **150**.

**In the pipeline:** `calibration.js.tmpl` → `ImageCalibration` process with `P.pedestal = 150`.

> ### THE PEDESTAL — THE MOST IMPORTANT NUMBER IN THIS DOCUMENT
>
> After dark subtraction, some background pixels will have values near zero (because the dark signal was close to the light signal in those pixels). Mathematical operations later in the pipeline (noise reduction, background extraction) can push these slightly-negative values to exactly zero, **permanently clipping them**. Once a pixel is at zero, its information is destroyed — you can never get it back.
>
> The 150 DN pedestal adds an artificial floor. Your background sky goes from ~0 to ~150 DN, which means dark subtraction results in ~150 instead of ~0. Nothing clips. This is **especially critical for OIII** where the signal is so faint that the background is barely above the dark level.
>
> **PixInsight GUI:** WBPP → Calibration panel → "Output pedestal" → set to 150.
> **Config:** `preprocessing.pedestal = 150`

### 4.2 Registration (StarAlignment)

**The Problem:** Even with precise tracking, each frame is shifted and slightly rotated relative to the others. Stars are in different pixel positions across frames.

**The Solution:** StarAlignment detects stars in each frame, matches them to a reference frame, and computes a geometric transformation (translation, rotation, scale, and optionally higher-order distortion) that maps each frame onto the reference.

**PixInsight GUI — StarAlignment settings:**

| Setting | Value | Why |
|---|---|---|
| Reference image | `Ha_master` (your strongest NB channel) | Most stars detected = most reliable astrometric solution. |
| Distortion correction | **Enabled** | Handles field rotation differences between sessions, minor flexure, or differential atmospheric refraction across the wide field. |
| Generate drizzle data | **Enabled** for NB | Writes `.xdrz` files that record sub-pixel registration for DrizzleIntegration. |
| Sensitivity | 0.50 | How aggressively to detect stars. 0.5 is default and works well. |
| Max stars | 0 (auto) | Let PI decide. |

**In the pipeline:** `registration.js.tmpl` → `StarAlignment` with `P.generateDrizzleData = true`. The Python code in `preprocessing.py` uses `generate_star_alignment_global()` to run registration as a batch, which writes `.xdrz` sidecar files.

**Cross-registration (RGB to NB):** After both tracks are calibrated and integrated, the RGB masters must be aligned to the NB reference frame (Ha). This ensures pixel-perfect alignment when you screen-blend at the very end. Run StarAlignment with `Ha_master` as the reference and your R, G, B masters as targets.

**In the pipeline:** `preprocessing.py` → `RGBtoNBRegistrationStage` calls `generate_star_alignment_global()` with the Ha reference.

### 4.3 Integration (Stacking)

**The Problem:** Each individual 300s sub has relatively low signal-to-noise ratio (SNR). You need to mathematically combine 50–100+ subs to build up signal while averaging out noise.

**How stacking works:** For each pixel position, you have N values (one per sub). The integrator:
1. **Normalizes** frames to a common brightness/scale (compensating for transparency variations).
2. **Rejects outliers** — satellite trails, cosmic rays, airplane lights. These appear in 1-2 frames but not the rest.
3. **Averages** the remaining "good" pixel values. Noise decreases by √N.

**Rejection Algorithm Selection** — This is critical and depends on how many subs you have:

| Sub Count | Use This | Why | Config Key |
|---|---|---|---|
| 3–6 subs | Percentile Clipping | Only needs a few samples to clip extremes | — |
| 5–10 | Averaged Sigma Clipping | Classic statistical rejection | — |
| 10–20 | Winsorized Sigma Clipping | More robust than basic sigma clip | `rgb_rejection_algorithm` for RGB |
| 20–50 | Linear Fit Clipping or ESD | Better outlier detection for larger datasets | — |
| **50+** | **ESD (recommended)** | Best overall for large stacks | `rejection_algorithm = "ESD"` |

**ESD (Generalized Extreme Studentized Deviate)** works by iteratively testing each pixel value against the rest using a statistical significance test. It's the gold standard for large stacks because it doesn't assume a symmetric noise distribution like sigma clipping does.

**PixInsight GUI — ImageIntegration settings:**

| Setting | Value | Why |
|---|---|---|
| Combination | Average | Standard for deep-sky. Median is only for quick previews. |
| Normalization | Additive with scaling | Compensates both additive (sky glow) and multiplicative (transparency) variations. |
| Weights | Noise evaluation (or SSWEIGHT keyword) | Frames with less noise contribute more. Bad-seeing frames are down-weighted automatically. |
| Rejection | ESD | For 50+ NB subs. |
| ESD Significance | 0.05 | 5% significance threshold — standard. |
| ESD Outliers Fraction | 0.30 | Up to 30% of pixels at any position can be rejected as outliers. |
| **ESD Low Relaxation** | **2.0** | **Critical for narrowband:** relaxes the low-side rejection threshold. This prevents ESD from mistakenly rejecting faint valid nebula signal (which looks like a "low outlier" when the frame is slightly underexposed). |
| Large Scale Pixel Rejection | Reject high | Catches satellite trails that are too bright and too wide for pixel-level ESD. |
| σ low / σ high | 4.0 / 3.0 | Only used by sigma-clipping algorithms as a fallback. |

**In the pipeline:** `integration.js.tmpl`, `preprocessing.py` → `NBIntegrationStage`

**Config:**
```json
"preprocessing": {
    "rejection_algorithm": "ESD",
    "esd_significance": 0.05,
    "esd_outliers": 0.30,
    "esd_low_relaxation": 2.0,
    "large_scale_rejection": true,
    "rgb_rejection_algorithm": "WinsorizedSigmaClip"
}
```

### 4.4 DrizzleIntegration

After registration and integration, if you enabled drizzle data generation during StarAlignment, you can now run DrizzleIntegration to reconstruct the super-resolved image.

**PixInsight GUI — DrizzleIntegration settings:**

| Setting | Value | Why |
|---|---|---|
| Scale | 2 | Double resolution (output = 0.957"/px) |
| Drop shrink | 0.9 | Conservative. Sharper drops (0.7-0.8) are possible but noisier. |
| Kernel | Square | Simplest. Lanczos is theoretically better but creates ringing artifacts on undersampled data. |

**In the pipeline:** `preprocessing.py` → `NBDrizzleStage`

> **Known Issue (D1):** The pipeline's StarAlignment `executeOn` loop workaround doesn't generate `.xdrz` files. If DrizzleIntegration finds no .xdrz files, it copies the regular ImageIntegration master unchanged (no Drizzle benefit). This is logged as priority P1 in the design doc. If you're running manually, **always use StarAlignment with `generateDrizzleData = true` via executeGlobal** to get proper `.xdrz` files.

---

## 5. Phase 2 — Linear Processing

**What "linear" means:** The pixel values in your master images are directly proportional to the number of photons that hit the sensor. Double the photons = double the pixel value. This mathematical simplicity is what makes operations like deconvolution, noise reduction, and background extraction work correctly.

**What happens when you stretch (go nonlinear):** The proportional relationship is destroyed. Faint pixels get boosted disproportionately more than bright pixels. This is necessary for *seeing* the data (your monitor is nonlinear, your eyes are nonlinear), but it means mathematical operations designed for linear data will produce incorrect results.

**Rule: All signal extraction and correction must happen while the data is still linear.**

### 5.1 DynamicCrop — BREAKPOINT 1

**Pipeline stage:** `DynamicCropStage` in `linear_processing.py`
**Config:** `preprocessing.crop_pixels = 200`

**Why:** Registration leaves ragged edges where not all frames overlap. These edges have fewer contributing frames, higher noise, and can cause artifacts in subsequent steps. Cropping removes them.

**PixInsight GUI:**
1. Open DynamicCrop (Process → Geometry → DynamicCrop).
2. Draw a crop region that excludes the ragged edges. For automated runs, the pipeline crops 200 pixels from each edge.
3. **Apply the identical crop to ALL 6 channels** (Ha, OIII, SII, R, G, B) so they remain pixel-aligned.

**Breakpoint logic:** This is BREAKPOINT 1 because the crop defines your composition/framing. The automation uses a fixed pixel margin, but when you're at this breakpoint you can choose artistic framing.

### 5.2 Background Extraction

**Pipeline stage:** `GraXpertBgExtStage` in `linear_processing.py`
**Config:** `processing.graxpert_smoothing = 0.25`

**Why:** Even through 3nm narrowband filters, your images contain background gradients from:
- Optical vignetting residuals that flats didn't perfectly correct
- Moonlight bleeding through the filter (reduced but not zero)
- Sky glow patterns (light pollution gradients)

These gradients are invisible in the linear image but get amplified 100–1000× during stretching. If you don't remove them now, your stretched image will have a splotchy, uneven background.

**GraXpert vs PixInsight DBE:** PixInsight's DynamicBackgroundExtraction (DBE) requires you to manually place sample points on "true background" regions. For NGC 1499 — which fills most of the field of view — there are very few true background regions, making DBE difficult. GraXpert uses AI to distinguish between gradients and nebulosity, which works much better for extended objects.

**PixInsight GUI (if using DBE instead of GraXpert):**
1. Process → BackgroundModeling → DynamicBackgroundExtraction.
2. Generate a grid of sample points (press the default generation button).
3. Delete samples that land on nebulosity. Keep only samples on true dark sky.
4. Set "Subtraction" mode.
5. Apply to each NB channel independently.

**GraXpert CLI (what the pipeline uses):**
```
GraXpert.exe "Ha_cropped.xisf" -cli -cmd background-extraction -correction Subtraction -smoothing 0.25 -gpu true -output "Ha_bgext.xisf"
```

**Smoothing parameter (0.25):** Controls how large-scale the gradient model is. Lower values (0.1) try to model fine-grained gradients but risk subtracting faint nebulosity. Higher values (0.5) model only the broadest gradients. For NGC 1499's extended emission, **0.25 is the sweet spot** — it was increased from the initial 0.1 after testing showed that 0.1 was subtracting real OIII signal.

> **Known Issue (D4):** The original `graxpert_smoothing = 0.1` was too aggressive for this target. If you see your faint OIII structures look *weaker* after background extraction than before, raise smoothing toward 0.3.

### 5.3 Temporary SHO Combination

**Pipeline stage:** `SHOLinearCombineStage` in `linear_processing.py`

**Why:** BlurXTerminator's AI model was trained on color (RGB) images, not monochrome ones. It uses inter-channel correlations to identify and correct aberrations. Running BXT on a single mono channel produces inferior correction compared to a combined SHO image.

**PixInsight GUI:**
1. Open PixelMath (Process → PixelMath).
2. Uncheck "Use a single expression."
3. R: `SII_bgext`, G: `Ha_bgext`, B: `OIII_bgext` (Standard Hubble palette mapping)
4. Check "Create new image", name it `SHO_linear`.
5. Execute globally.

**Channel mapping:** R=SII, G=Ha, B=OIII. This is the standard SHO (Hubble Palette) ordering. It doesn't matter that this will look "wrong" as-is — BXT doesn't care about aesthetics, it just needs correlated multi-channel data.

### 5.4 BlurXTerminator — Two-Pass Deconvolution

Deconvolution is the mathematical inverse of atmospheric and optical blurring. It takes a blurry image and recovers the "true" image that existed before the atmosphere and optics degraded it.

**Why BXT over classical Richardson-Lucy?** Classical deconvolution (RL) assumes a single, constant point spread function (PSF) across the entire field. In reality, your PSF varies: stars in the center look different from stars in the corners (coma, astigmatism). BXT uses a **non-stationary PSF model** that corrects field-variable aberrations. It also handles noise much more gracefully — classical RL amplifies noise and requires careful regularization and star masks.

#### Pass 1: Correct Only

**Pipeline stage:** `BXTCorrectOnlyStage` in `linear_processing.py`

Corrects optical aberrations (coma, astigmatism, spacing errors) across the field **without** sharpening. Think of it as "fixing what the optics got wrong" without applying any enhancement.

**PixInsight GUI — BlurXTerminator settings (Pass 1):**

| Setting | Value | Why |
|---|---|---|
| **Correct only** | ✅ Checked | This pass only corrects aberrations, doesn't sharpen. |
| Automatic PSF | ✅ Checked | Let BXT detect the PSF model from the image. |

**Just apply it.** No other settings matter when Correct Only is checked.

#### Pass 2: Sharpen — BREAKPOINT 2

**Pipeline stage:** `BXTSharpenStage` in `linear_processing.py`

Now applies actual deconvolution to resolve finer detail in both stars and nebula.

**PixInsight GUI — BlurXTerminator settings (Pass 2):**

| Setting | Value | Why |
|---|---|---|
| Correct only | ☐ Unchecked | Full deconvolution this time. |
| Automatic PSF | ✅ Checked | Still auto-detecting. |
| **Sharpen Stars** | **0.25** | Conservative. At ~2"/px after drizzle, stars are small — over-sharpening creates dark rings (ringing artifacts). Start low, increase only if stars still look bloated. |
| **Sharpen Nonstellar** | **0.40** | More aggressive on the nebula. Resolves filaments, shockfronts, and Herbig-Haro knots. The nebula has higher SNR per pixel than stars and tolerates more sharpening. |
| **Adjust Halos** | **0.05** | Minimal halo reduction. Higher values (>0.1) can create artificial dark donuts around bright stars. |

**Config:**
```json
"bxt_sharpen_stars": 0.25,
"bxt_sharpen_nonstellar": 0.40,
"bxt_adjust_halos": 0.05
```

**What to inspect at Breakpoint 2:**
- Zoom to 100% on bright stars: any dark rings (ringing)? Reduce `sharpen_stars`.
- Check nebula filaments: do fine structures look sharper and more defined? If they look "crunchy" or artificial, reduce `sharpen_nonstellar`.
- Check star halos: any dark halos where there shouldn't be? Reduce `adjust_halos` to 0.

### 5.5 Why Deconvolution MUST Come Before Noise Reduction

This is not a preference — it is a mathematical requirement.

Deconvolution is an **inverse problem**: it tries to reverse the blurring convolution. To do this, it needs to estimate where the signal stops and the noise begins. It uses the **noise statistics** of the image as a regularization constraint — essentially saying "I know the true image shouldn't have features at this noise floor level."

If you run noise reduction first, you alter the noise statistics. The deconvolution algorithm then:
- Overestimates the true image sharpness (noise floor appears lower than it really is)
- Produces **ringing artifacts** (oscillating patterns around bright edges)
- May amplify residual noise in unexpected ways

**This is why the pipeline order is BXT → Denoise → SXT, never Denoise → BXT.**

### 5.6 Channel Split

**Pipeline stage:** `ChannelSplitStage` in `linear_processing.py`

After BXT, we need to return to individual channels for per-channel noise reduction and star removal.

**PixInsight GUI:**
1. Open ChannelExtraction (Process → ColorSpaces → ChannelExtraction).
2. Set color space to RGB.
3. Check all three channels (R, G, B).
4. Set output IDs: R → `SII_processed`, G → `Ha_processed`, B → `OIII_processed`.
5. Apply to `SHO_linear`.

Remember: R=SII, G=Ha, B=OIII (same mapping we used when combining).

### 5.7 Noise Reduction (Linear)

**Pipeline stage:** `GraXpertDenoiseStage` in `linear_processing.py`
**If you have NXT:** Use `NoiseXTerminator` instead (superior results per the design doc's own comparison)

Applied **after** BXT to suppress shot noise and read noise in the linear data.

**Per-channel strength:** OIII is the faintest and noisiest channel — it gets the most aggressive noise reduction. Ha is the brightest and cleanest — it gets the lightest touch. Processing them identically would over-smooth Ha or under-smooth OIII.

| Channel | GraXpert Strength | NXT Denoise (if available) | Rationale |
|---|---|---|---|
| **Ha** | 0.40 | 0.65–0.75 | Highest SNR, needs least reduction |
| **SII** | 0.50 | 0.75–0.85 | Intermediate |
| **OIII** | 0.60 | 0.85–0.90 | Faintest. Heaviest reduction to suppress grain without destroying the veil-like structure. |

**PixInsight GUI — NoiseXTerminator (if you have the license):**
1. Open NoiseXTerminator (Process → NoiseXTerminator).
2. Set **Denoise** to the channel value above.
3. Set **Detail** to **0.15**. (Lower = preserves more fine structure but leaves more noise. 0.15 is a good starting point for linear narrowband.)
4. Apply to each processed channel individually.

**Config:**
```json
"graxpert_denoise_strength_ha": 0.40,
"graxpert_denoise_strength_sii": 0.50,
"graxpert_denoise_strength_oiii": 0.60
```

> **How NXT works internally on linear data:** NXT can't directly work on linear data (values are nearly zero, imperceptible). It internally applies an STF-like (screen transfer function) stretch, denoises in the stretched domain, then *reverses the stretch*. The output is still linear — values are still proportional to photon counts.

### 5.8 StarXTerminator (NB Star Removal)

**Pipeline stage:** `SXTStage` in `linear_processing.py`

Remove stars from each narrowband channel. The NB stars are **discarded** — all star data comes from the RGB track. We remove them now (while linear) so that stretching doesn't distort star residuals into ugly halos.

**PixInsight GUI — StarXTerminator settings:**

| Setting | Value | Why |
|---|---|---|
| Generate star image | ☐ Unchecked | We don't want the NB stars. They have false narrowband colors. |
| Unscreen | ☐ Unchecked | **Do not use unscreen on linear data.** Unscreen assumes stars were added via screen blend (nonlinear operation). On linear data, unscreen produces mathematical garbage. |

Apply to each channel: `Ha_processed` → `Ha_starless`, etc.

**In the pipeline:** `SXTStage` loops over all NB channels with `stars_output_path=None` (discarded) and `unscreen=False`.

---

## 6. Phase 3 — Stretching & Palette Combination

At this point you have three linear starless monochrome images: `Ha_starless`, `SII_starless`, `OIII_starless`. They look essentially black to the human eye because all the signal lives in the bottom 1% of the histogram.

### 6.1 Per-Channel Stretch — BREAKPOINT 3

**Pipeline stage:** `StretchNBStage` in `stretching.py` (preceded by `MeasureHistogramStage`)

**The key insight:** If you stretch all three channels identically, Ha (100% relative brightness) will dominate the image and OIII (5-10%) will be nearly invisible. You MUST stretch each channel independently, compensating for the brightness differences.

#### GeneralizedHyperbolicStretch (GHS)

GHS is the most flexible stretching tool in PixInsight. It applies a smooth S-shaped curve whose shape and intensity are controlled by three key parameters:

| GHS Parameter | PJSR Name | What It Does |
|---|---|---|
| D (stretch factor) | `stretchFactor` | Controls how aggressively the faint data is stretched. Higher = more stretch. |
| b (shape) | `localIntensity` | Controls the steepness of the transition. Higher = sharper "knee" in the curve. |
| SP (symmetry point) | `symmetryPoint` | **The anchor point.** This is where the stretch curve transitions from "compress darks" to "expand signal." Must be set to the background median of each channel's histogram peak. |

> **CRITICAL BUG NOTE:** In PJSR, the property names are `stretchFactor`, `localIntensity`, `symmetryPoint`, `inverse` — NOT `D`, `b`, `SP`, `invertTransformation`. Using the wrong names silently creates new JavaScript properties and GHS runs with all defaults (zero stretch). The GUI labels (D, b, SP) do not match the PJSR API names. The reference template `stretch.js.tmpl` uses the correct names.

**Per-channel stretch values:**

| Channel | D (stretch) | SP Source | Why |
|---|---|---|---|
| **Ha** | 4.0 | Measured median | Brightest — lightest stretch |
| **SII** | 5.0 | Measured median | Moderate |
| **OIII** | 7.0 | Measured median | Faintest — most aggressive stretch to bring out the veil |

**PixInsight GUI:**
1. Open GeneralizedHyperbolicStretch (Process → IntensityTransformations → GeneralizedHyperbolicStretch).
2. For each channel:
   a. Measure the background median: Process → Statistics → read the "Median" value. This goes into the SP field.
   b. Set the Symmetry Point to that measured value.
   c. Set D to the per-channel value above.
   d. Set b to 2.0.
   e. Apply.
3. **Check** visually that the faint structures in OIII are now at a similar visual brightness to the main Ha structures. If OIII still looks faint, increase its D. If Ha is blown out, decrease its D.

**Config:**
```json
"ghs_stretch_factor_ha": 4.0,
"ghs_stretch_factor_sii": 5.0,
"ghs_stretch_factor_oiii": 7.0,
"ghs_shape_param": 2.0
```

**The MeasureHistogramStage:** The pipeline automatically measures the background median of each bgext channel before stretching and stores the values in `working/histogram_stats.json`. StretchNBStage reads these values to set SP. If the stats file is missing, it falls back to `ghs_sp = 0.0001`.

> **Known Issue (D2/D3):** Early pipeline versions used SP=0.0001 for all channels and D=5.0 for all channels. This caused OIII to be over-stretched relative to Ha, producing the "super blue/purple" color cast. The fix (per-channel D + measured SP) is implemented in the current codebase.

### 6.2 LinearFit

**Pipeline stage:** `LinearFitStage` in `stretching.py`
**Config:** `processing.linear_fit_reference = "OIII"`

Even after per-channel stretching to equalize visual brightness, the three channels may still have significantly different median levels. LinearFit normalizes Ha and SII to match the OIII reference (the weakest channel) in terms of overall brightness and contrast, ensuring that the Foraxx palette math produces balanced color.

**PixInsight GUI:**
1. Open LinearFit (Process → ColorCalibration → LinearFit).
2. Set Reference Image to `OIII_starless_stretched`.
3. Apply to `Ha_starless_stretched`.
4. Apply to `SII_starless_stretched`.

The OIII channel is **not** modified — it's the reference. Ha and SII are scaled down to match OIII.

### 6.3 Foraxx Dynamic Palette Combination

**Pipeline stage:** `ForaxxPaletteStage` in `stretching.py`

This is where the three monochrome channels become a color image. The classic "SHO" (Hubble Palette) maps S→R, H→G, O→B. This produces an overwhelmingly green image because Ha is always the strongest channel.

**Foraxx** uses a dynamic weighting formula based on the Power of Inverted Pixels (PIP) that produces gold, cyan, and amber colors natively:

| Channel | PixelMath Expression | What It Produces |
|---|---|---|
| **R** | `(Oiii^~Oiii)*Sii + ~(Oiii^~Oiii)*Ha` | Where OIII is bright → SII contributes to red. Where OIII is faint → Ha contributes instead. Creates gold/amber. |
| **G** | `((Oiii*Ha)^~(Oiii*Ha))*Ha + ~((Oiii*Ha)^~(Oiii*Ha))*Oiii` | Dynamic weighting between Ha and OIII. Produces soft intermediate tones. |
| **B** | `Oiii` | OIII goes straight to blue. Creates the signature cyan/teal. |

In PixelMath, `~X` means `(1-X)` (inverted pixels), NOT reciprocal. And `^` is the power operator.

**PixInsight GUI:**
1. Open PixelMath (Process → PixelMath).
2. **Uncheck** "Use a single expression."
3. Type the three expressions above into R, G, B.
4. Check "Create new image", ID = `SHO_Foraxx`.
5. Color space = RGB.
6. Execute globally.

**All three input channels must be open as image windows** with view IDs matching the expression names: `Ha`, `Sii`, `Oiii` (case-sensitive in PixelMath). The pipeline's `pixelmath.js.tmpl` template opens the channels and assigns these IDs before running the PixelMath.

---

## 7. Phase 4 — Nonlinear Processing (Starless Nebula)

You now have a nonlinear, stretched, starless SHO color image. These steps fine-tune contrast, color, and dynamic range.

### 7.1 SCNR Green Removal

**Pipeline stage:** `SCNRStage` in `nonlinear.py`
**Config:** `processing.scnr_amount = 0.00`

**For Foraxx palette: SCNR amount = 0.00 (disabled).**

SCNR (Subtractive Chromatic Noise Reduction) is designed to remove green cast from standard SHO palettes where Ha→Green. In the Foraxx palette, Ha maps primarily to **Red**, not Green. The Green channel already has minimal green content (~1%).

What happens if you apply SCNR with `preserveLuminance=true` to Foraxx:
- SCNR removes the small green component
- `preserveLuminance=true` compensates by boosting R and B
- Gold/amber pixels (R>G>B) turn pure red
- Cyan/teal pixels turn pure blue/purple
- You lose the characteristic warm Foraxx tones

**Config fix applied:** `scnr_amount` was changed from 0.65 to 0.00 after the first pipeline run produced "comically blue and purple" results.

### 7.2 CurvesTransformation — BREAKPOINT 4

**Pipeline stage:** `CurvesHueStage` + `CurvesContrastSatStage` in `nonlinear.py`

This is the main **aesthetic** step where you adjust the image to taste. The automation applies a conservative default, but this is explicitly a breakpoint where you should tune manually.

**PixInsight GUI:**
1. Open CurvesTransformation (Process → IntensityTransformations → CurvesTransformation).
2. **Hue vs Hue tab:** Drag the green region (~0.33) toward yellow-orange (~0.28). This pushes any remaining green tints into the warm gold direction.
3. **Saturation tab:** Boost cyan/teal to make OIII structures pop. Reduce saturation in browns/reds if they look overdone.
4. **RGB/K intensity curve:** Apply a mild S-curve for contrast (shadows slightly down, highlights slightly up). Don't over-do it — HDR and LHE do the heavy lifting for local contrast.

### 7.3 HDRMultiscaleTransform

**Pipeline stage:** `HDRMultiscaleStage` in `nonlinear.py`
**Config:** `processing.hdrmt_layers = 6`, `hdrmt_iterations = 1`

**What it does:** Compresses the dynamic range of bright structures (NGC 1499's central Ha ridge is 50–100× brighter than the faint OIII veil). Without HDRMT, you either expose the bright core properly (and lose the faint outer structure) or expose the faint structure properly (and blow out the core). HDRMT lets you have both.

**PixInsight GUI:**
1. Open HDRMultiscaleTransform (Process → MultiscaleProcessing → HDRMultiscaleTransform).
2. Number of layers: **6** (covers large-scale dynamic range compression).
3. Iterations: **1**.
4. Apply.

> **Important: Use a luminance mask.** HDRMT should only operate on the bright nebula regions. Without a mask, it also compresses the background, amplifying noise and creating a mottled "orange peel" texture.
>
> **How to create and apply a luminance mask:**
> 1. PixelMath: `CIE_L($T)` applied to the image itself (creates a luminance copy).
> 2. Apply HistogramTransformation to the mask: clip the black point to exclude background.
> 3. Select the mask as the image's window mask.
> 4. Apply HDRMT.
> 5. Remove the mask when done.
>
> **Known Issue (D5):** The automated pipeline runs HDRMT **without** a mask because `PixelMath.executeGlobal()` for mask creation fails silently in PI headless mode. When running manually, **always apply a luminance mask first.**

### 7.4 LocalHistogramEqualization (LHE)

**Pipeline stage:** `LHEStage` in `nonlinear.py`
**Config:** `lhe_kernel_radius = 96`, `lhe_contrast_limit = 2.0`, `lhe_amount = 0.35`

**What it does:** Boosts micro-contrast in nebula filaments, making fine detail pop. It operates on local neighborhoods of pixels rather than the whole image (similar to CLAHE in medical imaging).

**PixInsight GUI:**
1. Open LocalHistogramEqualization (Process → IntensityTransformations → LocalHistogramEqualization).
2. **Kernel radius:** 96 pixels (~3 arcmin at your plate scale). This sets the "neighborhood" size.
3. **Contrast limit:** 2.0 (keep ≤2.5 to avoid over-processing).
4. **Amount:** 0.35 (35% LHE blended with original — conservative).
5. Apply.

> **Same mask warning as HDRMT:** LHE amplifies local contrast everywhere, including in the dark background where "local contrast" = noise. Apply with the same luminance mask to protect the background.

### 7.5 Final Noise Reduction (Nonlinear)

**Pipeline stage:** `GraXpertDenoiseNonlinearStage` in `nonlinear.py`
**Config:** `processing.graxpert_denoise_strength_nonlinear = 0.35`

Stretching amplified background noise. HDRMT and LHE further revealed noise patterns. A light final pass smooths these without erasing the detail that HDRMT and LHE worked to enhance.

**Use a gentle touch:** 0.35 strength (GraXpert) or NXT denoise 0.40. This is cosmetic cleanup, not primary noise reduction (that was done in Phase 2 on linear data).

---

## 8. Phase 5 — RGB Star Processing & Final Combination

### 8.1 RGB Channel Combination

**Pipeline stage:** `RGBChannelCombineStage` in `star_processing.py`

Combine your three registered+cropped R, G, B masters into a single RGB color image.

**PixInsight GUI:**
1. Open ChannelCombination (Process → ColorSpaces → ChannelCombination).
2. Set R = `R_cropped`, G = `G_cropped`, B = `B_cropped`.
3. Color space: RGB.
4. Apply globally → creates `RGB_composite`.

### 8.2 ImageSolver + SPCC (Spectrophotometric Color Calibration)

**Pipeline stage:** `SPCCStage` in `star_processing.py`

**Why SPCC?** Your RGB image has "whatever colors the camera happened to record" — which depends on the sensor's spectral response curve, the filter transmission curves, and atmospheric extinction. SPCC corrects this by comparing the measured brightness of stars in your R, G, B channels against the **Gaia DR3 spectrophotometric catalog** — a database of precisely measured star colors from space. The result: star colors that match physical reality (hot stars blue, cool stars red, Sun-like stars yellowish-white).

**Step 1 — ImageSolver (Plate Solve):**
SPCC needs to know which stars are in your image. ImageSolver matches star patterns in your image against an astrometric catalog to determine the exact sky coordinates of every pixel.

**PixInsight GUI:**
1. Script → Astrometry → ImageSolver.
2. Enter approximate center coordinates: RA 04h03m18s, Dec +36°25'18".
3. Run. It should find a solution in seconds.

**Step 2 — SPCC:**
1. Open SpectrophotometricColorCalibration (Process → ColorCalibration → SpectrophotometricColorCalibration).
2. Narrowband mode: ☐ **Unchecked** (this is broadband RGB data).
3. Apply to the RGB composite.

**In the pipeline:** `spcc.js.tmpl` with `SPCC.narrowbandMode = false`

> **Known Issue (D7):** SPCC silently fails if the WCS (plate solve) is invalid or the Gaia catalog server is unreachable. The PI console will show "Unable to compute a valid scale estimate, channel 0" but the pipeline won't crash — it just continues with uncalibrated colors. Check the pipeline log for `pi_flagged_lines` containing this error. If SPCC fails: verify ImageSolver succeeded first, check internet connectivity, try running SPCC manually in the GUI.

### 8.3 RGB Stretch

**Pipeline stage:** `RGBStretchStage` in `star_processing.py`
**Config:** `processing.ghs_rgb_stretch_factor = 3.0`

Stretch the linear RGB image to nonlinear. Use a **lighter stretch than the NB channels** (D=3.0 vs D=4.0-7.0) because:
- Stars are already relatively bright objects in linear data
- Over-stretching blows out star cores to pure white, destroying color information
- The goal is visible star colors, not maximum brightness

**PixInsight GUI:**
1. GHS with D=3.0, b=2.0, SP=measured background median.
2. Check that bright stars show color (not pure white) and faint stars are visible.

### 8.4 StarXTerminator (RGB Star Extraction)

**Pipeline stage:** `SXTRGBStage` in `star_processing.py`

Now we extract ONLY the stars from the stretched RGB image. This is the inverse of what we did in Phase 2 — there we removed stars from the nebula; here we extract stars from the background.

**PixInsight GUI — StarXTerminator settings:**

| Setting | Value | Why |
|---|---|---|
| **Generate star image** | ✅ **Checked** | We WANT the stars-only image this time. |
| **Unscreen** | ☐ Unchecked | Our RGB image was stretched, not screen-blended. Standard subtraction mode. |

Apply to the stretched RGB composite. Two outputs appear:
- **Starless RGB** — Discard this. The NB starless SHO provides the nebula.
- **Stars-only** — Keep this. It's your final star layer.

### 8.5 Star Halo Reduction (Optional)

**Pipeline stage:** `StarHaloReductionStage` in `star_processing.py` (currently a pass-through copy)

If bright stars have halos from the broadband filters or optical reflections, SETI Astro Halo Reducer can clean them up. With the 75Q's well-corrected optics and 10s exposures, this is usually unnecessary.

The pipeline currently copies `stars_only` → `stars_haloreduced` unchanged. If you want to apply halo reduction manually, process `stars_only` before this stage.

### 8.6 Screen Blend — BREAKPOINT 5

**Pipeline stage:** `ScreenBlendStage` in `star_processing.py`
**Config:** `processing.star_brightness_factor = 0.70`

The final merger: starless SHO nebula + RGB stars = complete image.

**Why Screen Blend instead of addition?**
Simple addition (`starless + stars`) causes pixel values to exceed 1.0 wherever a star overlaps bright nebulosity. Those pixels clip to pure white. Screen blend is a photographic compositing technique that prevents this:

```
Screen blend = ~(~A * ~B)
             = 1 - ((1-A) * (1-B))
```

This compresses bright+bright overlaps into the 0-1 range naturally, like how bright lights combine when photographed through multiple exposures.

**PixInsight GUI:**
1. Open PixelMath.
2. Expression: `~(~SHO_final_starless * ~(RGB_stars_haloreduced * 0.70))`
3. The `* 0.70` scales star brightness to 70% before blending. Adjust to taste:
   - 0.50 = faint, subtle stars
   - 0.70 = balanced (default)
   - 1.00 = full-brightness stars
4. Apply to the starless SHO image.

**What to check at Breakpoint 5:**
- Star colors: Do bright stars look natural (white/blue for hot, yellow for solar)? If they're monochrome or gray, SPCC may have failed.
- Star brightness: Are stars overwhelming the nebula? Reduce `star_brightness_factor`.
- Alignment: Do stars have colored fringes or appear doubled? Cross-registration failed — go back to Phase 1 step 3.
- Halos: Dark donuts around bright stars? SXT artifact. Use CloneStamp to clean up.

---

## 9. The Math Behind the Magic

### 9.1 Noise: Where It Comes From & How Stacking Helps

Every pixel measurement has noise from three sources:

```
σ_total = √(σ_sky² + σ_dark² + σ_read²)
```

| Source | Narrowband (300s, Gain 100, 3nm) | RGB (10s, Gain -25) |
|---|---|---|
| Read noise (σ_read) | 1.0 e⁻ (HCG mode) | 3.3 e⁻ (standard) |
| Dark current noise (σ_dark) | ~0.3 e⁻ at -20°C (√(0.00012 × 300)) | ~0.03 e⁻ |
| Sky shot noise (σ_sky) | ~2–5 e⁻ (reduced 50–100× by 3nm filter) | Much higher (broadband) |
| **Total per sub** | **~3–6 e⁻** | Dominated by sky + read noise |

**Stacking reduces noise by √N:**
- 50 subs: noise drops to σ/√50 ≈ σ/7 → ~0.5–0.9 e⁻/pixel
- 100 subs: σ/√100 = σ/10 → ~0.3–0.6 e⁻/pixel

**Doubling SNR requires 4× the integration time.** Going from 4h to 8h gives you √2 ≈ 1.4× better SNR. Going from 4h to 16h gives you 2×.

### 9.2 Spatial Frequency & Why Noise Reduction Works

At 1.914"/px (0.957"/px drizzled), the Nyquist spatial frequency limit is 0.5 cycles/pixel. But atmospheric seeing limits real information bandwidth to ~0.25–0.38 cycles/pixel. This means **all spatial frequencies between the seeing limit and Nyquist contain ONLY noise** — there is no real astronomical signal in that frequency range.

Noise reduction algorithms (NXT, GraXpert, MultiscaleLinearTransform) exploit this gap by suppressing high-frequency content above the seeing-limited bandwidth. When done correctly, you're removing pure noise without touching any real detail. This is why noise reduction works without destroying signal — as long as the algorithm correctly identifies the cutoff frequency.

### 9.3 The Deconvolution Ordering Rule

Deconvolution reverses blurring by solving: `true_image = deblur(observed_image, PSF, noise_model)`. The noise model is critical — the algorithm uses it as a regularization constraint to prevent amplifying noise into the solution.

If noise reduction runs first → noise statistics change → deconvolution's noise model is wrong → it either:
- **Over-sharpens** (thinks there's less noise than there really was, so pushes more "detail") → ringing artifacts
- **Under-sharpens** (can't distinguish real structure from noise reduction artifacts)

**This is a mathematical property of the inverse problem, not a tuning preference.**

---

## 10. Known Issues & Debugging Guide

### 10.1 Image Looks "Super Blue/Purple"

**Root cause:** Channel imbalance after Foraxx combination. Measurable as B/G > 1.3 and/or R/G < 0.75.

**Diagnostic steps:**
1. Open the Foraxx output in PixInsight.
2. Process → Statistics on each channel.
3. Calculate B_mean / R_mean. If > 1.5, OIII is dominating → stretch issue.

**Fixes in order of impact:**
1. Ensure GHS SP is set to the **measured** per-channel median (not hardcoded 0.0001).
2. Increase Ha stretch factor (raise D_ha) and/or decrease OIII stretch factor (lower D_oiii).
3. Check GraXpert smoothing — too low (0.1) may be subtracting real Ha signal.
4. Verify SCNR amount is 0.00 for Foraxx (not 0.65).

### 10.2 Mottled/Orange-Peel Background

**Root cause:** HDRMT and/or LHE applied without a luminance mask, amplifying background noise.

**Fix:** Apply a luminance mask (see Section 7.3) before running HDRMT and LHE. The pipeline can't do this in headless mode — this is a manual step at Breakpoint 4.

### 10.3 SPCC "Unable to compute valid scale estimate"

**Root cause:** Missing or invalid WCS solution, or catalog server unreachable.

**Fix:**
1. Run ImageSolver on the RGB composite first.
2. Check internet connectivity.
3. Ensure Gaia catalog data is downloaded in PI (Resources → Manage Catalog Data).

### 10.4 Stars Have False Colors or Halos

**Root cause:** Cross-registration failure between RGB and NB tracks.

**Fix:** Re-run StarAlignment with `Ha_master` as reference and RGB masters as targets. Enable distortion correction. Verify star positions match by blinking between aligned images.

### 10.5 No Drizzle Benefit (Images Are 1x Resolution)

**Root cause:** StarAlignment `executeOn` loop doesn't write `.xdrz` files. Pipeline copies the regular master unchanged.

**Fix:** Use `executeGlobal()` instead of `executeOn()` loop for StarAlignment, with `generateDrizzleData = true`. This is coded as `generate_star_alignment_global()` in `pjsr_generator.py`.

### 10.6 GHS Appears to Do Nothing

**Root cause:** Using wrong PJSR property names (`D`, `b`, `SP` instead of `stretchFactor`, `localIntensity`, `symmetryPoint`). JavaScript silently creates new properties without error and GHS runs with all-default (zero) stretch.

**Fix:** Use the correct property names. Check `stretch.js.tmpl` for the current correct names.

---

## 11. Quick Reference Tables

### 11.1 Complete Processing Pipeline with Config Keys

| Step | Stage | PI Process | Config Keys | Output File |
|---|---|---|---|---|
| 1 | NB Calibration | ImageCalibration | pedestal=150 | `{ch}_calibrated/` |
| 2 | NB Registration | StarAlignment | generate_drizzle_data=true | `{ch}_registered/` + .xdrz |
| 3 | NB Integration | ImageIntegration | rejection=ESD, esd_low_relaxation=2.0 | `{ch}_master.xisf` |
| 4 | NB Drizzle | DrizzleIntegration | drizzle_scale=2, drop_shrink=0.9 | `{ch}_drizzle.xisf` |
| 5 | RGB Calibration | ImageCalibration | pedestal=150 | RGB `{ch}_calibrated/` |
| 6 | RGB Registration | StarAlignment | ref=Ha_master | `{ch}_master_registered.xisf` |
| 7 | Crop **[BP1]** | DynamicCrop | crop_pixels=200 | `{ch}_cropped.xisf` |
| 8 | Background Ext | GraXpert CLI | graxpert_smoothing=0.25 | `{ch}_bgext.xisf` |
| 9 | SHO Combine | PixelMath (R=S,G=H,B=O) | — | `SHO_linear.xisf` |
| 10 | BXT Correct | BlurXTerminator | correct_only=true | `SHO_bxt_corrected.xisf` |
| 11 | BXT Sharpen **[BP2]** | BlurXTerminator | sharpen_stars=0.25, nonstellar=0.40, halos=0.05 | `SHO_bxt.xisf` |
| 12 | Channel Split | ChannelExtraction | — | `{ch}_processed.xisf` |
| 13 | Denoise (Linear) | GraXpert/NXT | Ha=0.40, SII=0.50, OIII=0.60 | `{ch}_denoised.xisf` |
| 14 | SXT (NB) | StarXTerminator | unscreen=false, stars_image=false | `{ch}_starless.xisf` |
| 15 | Stretch **[BP3]** | GHS | D: Ha=4.0, SII=5.0, OIII=7.0; SP=measured | `{ch}_starless_stretched.xisf` |
| 16 | LinearFit | LinearFit | ref=OIII | `Ha/SII_starless_linearfit.xisf` |
| 17 | Foraxx | PixelMath | (Foraxx expressions) | `SHO_foraxx.xisf` |
| 18 | SCNR | SCNR | scnr_amount=0.00 | `SHO_scnr.xisf` |
| 19 | Curves **[BP4]** | CurvesTransformation | (manual at BP) | `SHO_hue.xisf` / `SHO_curves.xisf` |
| 20 | HDRMT | HDRMultiscaleTransform | layers=6, iterations=1 | `SHO_hdr.xisf` |
| 21 | LHE | LocalHistogramEqualization | radius=96, limit=2.0, amount=0.35 | `SHO_lhe.xisf` |
| 22 | Denoise (NL) | GraXpert/NXT | strength=0.35 | `SHO_final_starless.xisf` |
| 23 | RGB Combine | ChannelCombination | — | `RGB_composite.xisf` |
| 24 | SPCC | SPCC | narrowband_mode=false | `RGB_spcc.xisf` |
| 25 | RGB Stretch | GHS | D=3.0 | `RGB_stretched.xisf` |
| 26 | SXT (RGB) | StarXTerminator | stars_image=true, unscreen=false | `RGB_stars_only.xisf` |
| 27 | Screen Blend **[BP5]** | PixelMath | star_brightness=0.70 | `NGC1499_combined.xisf` |
| 28 | Final Output | DynamicCrop | final_crop_pixels=0 | `NGC1499_final.xisf` |

### 11.2 Foraxx PixelMath Quick Copy

```
R: (Oiii^~Oiii)*Sii + ~(Oiii^~Oiii)*Ha
G: ((Oiii*Ha)^~(Oiii*Ha))*Ha + ~((Oiii*Ha)^~(Oiii*Ha))*Oiii
B: Oiii
```

### 11.3 Screen Blend Quick Copy

```
~(~SHO_final_starless * ~(RGB_stars_haloreduced * 0.70))
```

### 11.4 Breakpoint Summary

| BP | Where | What to Check |
|---|---|---|
| BP1 | After crop | Is the composition/framing what you want? |
| BP2 | After BXT sharpen | Any ringing artifacts? Star dark halos? Over-sharpened nebula? |
| BP3 | After per-channel stretch | Are channels visually balanced? Is OIII visible? Is Ha blown out? |
| BP4 | After curves | Color grading: gold/cyan balance, saturation, hue shifts |
| BP5 | After screen blend | Star brightness, alignment, halo bleed, final look |

### 11.5 Noise Reduction Algorithm Comparison

| Algorithm | When to Use | Strengths | Weaknesses |
|---|---|---|---|
| **NoiseXTerminator** | Primary (if licensed) | Best detail preservation; frequency-aware; simple | Paid license; AI black box |
| **GraXpert Denoise** | Primary (free alternative) | Free; CUDA CLI; automatable | Can soften stars; mottled residuals possible |
| **MultiscaleLinearTransform** | When you need per-scale control | Full scale-by-scale tuning; reproducible; maskable | Labor-intensive; steep learning curve |
| **TGVDenoise** | Expert fine-tuning | Theoretically optimal edge preservation | Extremely hard to configure; "orange peel" artifacts |

### 11.6 File Chain Reference

Every file produced by the pipeline follows a predictable naming chain. Use this when hunting for where things went wrong:

```
Raw sub             → NGC1499_Ha_001.fit
Calibrated          → NGC1499_Ha_001_c.xisf
Registered          → NGC1499_Ha_001_c_r.xisf  (+.xdrz sidecar)
Integrated          → NGC1499_Ha_master.xisf
Drizzled            → NGC1499_Ha_drizzle.xisf
Cropped             → NGC1499_Ha_cropped.xisf
Background ext.     → NGC1499_Ha_bgext.xisf
Combined            → NGC1499_SHO_linear.xisf
BXT corrected       → NGC1499_SHO_bxt_corrected.xisf
BXT sharpened       → NGC1499_SHO_bxt.xisf
Split               → NGC1499_Ha_processed.xisf (also SII, OIII)
Denoised            → NGC1499_Ha_denoised.xisf
Starless            → NGC1499_Ha_starless.xisf
Stretched           → NGC1499_Ha_starless_stretched.xisf
LinearFit           → NGC1499_Ha_starless_linearfit.xisf
Foraxx              → NGC1499_SHO_foraxx.xisf
SCNR                → NGC1499_SHO_scnr.xisf
Hue adjusted        → NGC1499_SHO_hue.xisf
Contrast adjusted   → NGC1499_SHO_curves.xisf
HDR compressed      → NGC1499_SHO_hdr.xisf
LHE enhanced        → NGC1499_SHO_lhe.xisf
Final starless      → NGC1499_SHO_final_starless.xisf
RGB composite       → NGC1499_RGB_composite.xisf
SPCC calibrated     → NGC1499_RGB_spcc.xisf
RGB stretched       → NGC1499_RGB_stretched.xisf
RGB starless        → NGC1499_RGB_starless.xisf (discarded)
RGB stars only      → NGC1499_RGB_stars_only.xisf
Stars halo-reduced  → NGC1499_RGB_stars_haloreduced.xisf
Screen blended      → NGC1499_combined.xisf
Final               → NGC1499_final.xisf
```
