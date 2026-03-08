# Astro-Pipeline: The Manual Processing & Debugging Guide

This guide is a 1-to-1 translation of the `astro-piper` automated pipeline into manual PixInsight GUI steps. Use this document as your primary reference when you hit a **Breakpoint** in the script, when you need to run the data manually to tune parameters, or when debugging the pipeline's output.

---

## Phase 1: Preprocessing (WBPP)

The pipeline separates data into two distinct tracks: **Narrowband** (Nebula) and **RGB** (Stars).

### Step 1: Narrowband Track (Ha, OIII, SII)
Open **WeightedBatchPreprocessing (WBPP)** and load your Gain 100 / 300s narrowband data.
1. **Calibration:** 
   * Load corresponding Darks (300s, Gain 100), Flats, and Flat Darks.
   * **CRITICAL:** Set **Output Pedestal to 150 DN**. Without this, your dark subtraction will clip background pixels to 0, permanently destroying faint OIII/SII signal.
2. **Integration (Rejection):** 
   * For stacks of 50+ subs, use **ESD (Generalized Extreme Studentized Deviate)** to cleanly reject cosmic rays and satellite trails while protecting nebulosity.
   * Increase low relaxation to 2.0.
3. **Drizzle:** Enable Drizzle generation. Set **Scale: 2.0** and **Drop Shrink: 0.9** using a Square kernel. This recovers your 1.914"/px mildly undersampled data.
4. Execute WBPP to get `Ha_master`, `OIII_master`, and `SII_master`.

### Step 2: RGB Star Track (R, G, B)
Run a separate WBPP instance (or a separate tab) for your Gain -25 / 10s broadband data.
1. **Calibration:** Load corresponding short-exposure Darks, Flats, and Flat Darks.
2. **Pedestal:** Also set to **150 DN** to match the narrowband data.
3. Execute WBPP to get `R_master`, `G_master`, and `B_master`.

### Step 3: Cross-Registration
*Before combining anything, you must align the RGB data to the Narrowband data.*
1. Open **StarAlignment**.
2. **Reference Image:** Select your `Ha_master` (it has the highest SNR).
3. **Target Images:** Add your `R_master`, `G_master`, and `B_master`.
4. Enable **Distortion Correction** (if there's any field rotation).
5. Execute. You now have `R_registered`, `G_registered`, and `B_registered`.

---

## Phase 2: Linear Processing (Narrowband Track)

You are now working with linear (unstretched) data. **Do not run any noise reduction until after deconvolution.**

### Step 4: Dynamic Crop [BREAKPOINT 1]
1. Open **DynamicCrop**.
2. Draw a crop box that removes ragged stacking edges.
3. Apply this **exact same crop** using the blue triangle to ALL 6 masters (Ha, OIII, SII, and the 3 registered RGBs).

### Step 5: Background Extraction
Because your 75Q fills the FOV with the California Nebula, PI's DBE can struggle to find true background.
1. Run **GraXpert** (or use the GraXpert PI script).
2. Set **Correction:** Subtraction.
3. Set **Smoothing:** ~0.10.
4. Apply to Ha, OIII, and SII independently.

### Step 6: Temporary SHO Combination
BlurXTerminator (BXT) works best on multi-channel correlated data.
1. Open **ChannelCombination** (or PixelMath).
2. Assign R=`SII`, G=`Ha`, B=`OIII`.
3. Apply globally. *(Name the output `SHO_linear`)*

### Step 7: BlurXTerminator - Correct Only
1. Open **BlurXTerminator**.
2. Check **Correct Only**.
3. Check **Automatic PSF**.
4. Apply to `SHO_linear` to physically correct coma, astigmatism, and chromatic tracking errors.

### Step 8: BlurXTerminator - Sharpen [BREAKPOINT 2]
1. Re-open **BlurXTerminator**.
2. Uncheck **Correct Only**.
3. Set **Sharpen Stars** to `0.25` (Conservative for your ~2"/px sampling).
4. Set **Sharpen Nonstellar** to `0.40`.
5. Set **Adjust Halos** to `0.05`.
6. Apply. *Inspect the image for ringing or over-sharpened artifacts here.*

### Step 9: NoiseXTerminator (Linear)
Now that BXT has mapped the structural edges using the noise, you can denoise.
1. Open **NoiseXTerminator**.
2. Set **Denoise** to `0.80` (narrowband tolerates heavier linear reduction).
3. Set **Detail** to `0.15`.
4. Apply to `SHO_linear`.

### Step 10: SXT Channel Split & Star Removal
1. Open **ChannelExtraction** and split `SHO_linear` back into its 3 linear channels: `SII_processed`, `Ha_processed`, `OIII_processed`.
2. Open **StarXTerminator**.
3. Uncheck *Generate Star Image* (we don't want narrowband stars).
4. Uncheck *Unscreen* (because the image is still linear).
5. Apply sequentially to S, H, and O to make them purely starless.

---

## Phase 3: Stretching & Palette Combination

### Step 11: Per-Channel Stretching [BREAKPOINT 3]
*Do not autostretch these together, or the Ha will physically drown out the OIII.*
1. Open **GeneralizedHyperbolicStretch (GHS)** or **HistogramTransformation**.
2. Measure the background median of each channel.
3. In GHS, set the **Symmetry Point** to that channel's median. 
4. Carefully stretch each channel so the faint structures in OIII visually match the density of the Ha structures. 

### Step 12: Foraxx Dynamic Palette Combine
1. Open **PixelMath**.
2. Uncheck *Use single expression*.
3. **R/K:** `(Oiii^~Oiii)*Sii + ~(Oiii^~Oiii)*Ha`
4. **G:** `((Oiii*Ha)^~(Oiii*Ha))*Ha + ~((Oiii*Ha)^~(Oiii*Ha))*Oiii`
5. **B:** `Oiii`
6. Create a new image called `SHO_Foraxx`.

*(Note: In PixelMath, `~X` is internal shorthand for `1-X`)*

---

## Phase 4: Non-Linear Tweaks (Starless Nebula)

### Step 13: Green Removal & Curves [BREAKPOINT 4]
1. Note on **SCNR**: Foraxx maps Ha to Red, not Green. If you use SCNR on a Foraxx image, it will destroy the gold/amber colors. Leave SCNR disabled (Amount = `0.00`).
2. Open **CurvesTransformation**. Adjust Hue vs Hue to bend any remaining greens into gold. Boost Cyan for the OIII. Adjust overall saturation to taste.

### Step 14: Contrast Compression
1. Open **HDRMultiscaleTransform** (HDRMT).
2. Set Layers to `6`, Iterations to `1`.
3. Apply to compress the bright central core of the nebula without losing the faint outer fringes.
4. Open **LocalHistogramEqualization** (LHE).
5. Set Radius to `96`, Contrast Limit to `2.0`, Amount to `0.35`. Apply to pop the micro-contrast.

### Step 15: Final Light Touch Denoise
1. Open **NoiseXTerminator**.
2. Set **Denoise** to `0.40` and **Detail** to `0.15`.
3. Apply to smooth over any noise amplified by HDRMT and LHE.

---

## Phase 5: True-Color RGB Stars & Final Combination

### Step 16: RGB Star Integration
1. Open **ChannelCombination**.
2. Load your linearly cropped and registered `R`, `G`, `B` masters from Step 4.
3. Apply globally to create your linear RGB image.

### Step 17: ImageSolver & SPCC
1. Run **ImageSolver** script on your RGB image so it knows its astrometric coordinates.
2. Open **SpectrophotometricColorCalibration (SPCC)**.
3. Apply to the RGB image. It will use the Gaia DR3 database to physically align the star colors to Wien's Law (accurate blue, white, yellow, and red stars).

### Step 18: RGB Stretch & Extraction
1. Open **HistogramTransformation** (or GHS).
2. Stretch the RGB image *mildly*. The goal is visible colors without blowing out star cores to pure white.
3. Open **StarXTerminator**.
4. Check **Generate Star Image**.
5. Check **Unscreen Stars**. *(SXT handles non-linear extraction best with unscreen).*
6. Apply to the stretched RGB image. 
7. **Keep the newly generated `stars_only` image. Discard the starless RGB background.**

### Step 19: Screen Blend Recombination [BREAKPOINT 5]
You now have the perfect starless SHO Foraxx nebula and photometrically perfect RGB stars. Time to merge.
1. Open **PixelMath**.
2. Expression: `~(~SHO_Foraxx * ~RGB_stars_only)`
3. *Optional brightness control: `~(~SHO_Foraxx * ~(RGB_stars_only * 0.70))`*
4. Apply to the `SHO_Foraxx` image to gracefully add the stars back in without clipping the highlights.

### Step 20: Final Polish
Apply final **DynamicCrop** to clean any edges and use the **CloneStamp** for any residual SXT artifacts.
