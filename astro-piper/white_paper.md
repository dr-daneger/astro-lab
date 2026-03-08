# White Paper: Optimal Processing of Monochrome Astrophotography Data

## Introduction
Processing monochrome astrophotography data—specifically combining heavy narrowband (SHO) nebulosity with broadband (RGB) star data—is as much an exercise in information theory as it is in visual art. Drawing from the architectural designs of the automated `astro-piper` pipeline, this white paper outlines the most scientifically sound and aesthetically optimal methodology for processing data acquired with modern CMOS sensors and fast, well-corrected optics.

This guide targets the following specific acquisition profile:
- **Camera:** ASI2600MM Pro (IMX571 BSI CMOS, 16-bit ADC, 3.76μm pixels)
- **Telescope:** Apertura 75Q (75mm aperture, 405mm focal length, f/5.4)
- **Narrowband (SHO) Data:** 300s exposures at Gain 100, -20°C (optimized for read noise of ~1.0 e⁻)
- **RGB Star Data:** 10s exposures at Gain -25 (Unity Gain), -20°C (optimized for maximum full well capacity and unsaturated star colors)
- **Acquisition Strategy:** Dithered and Drizzled 2x to recover mild undersampling due to the focal length and pixel size combination.

---

## 1. The Two-Track Philosophy: Separating Nebulosity from Stars

The conceptual core of this pipeline physically separates the image into two mathematical components: **Nebula** and **Stars**.

Applying narrowband processing techniques to stars results in mangled, false-color stars with chromatic artifacts (magenta/green halos). By capturing completely separate, unsaturated broadband RGB data solely for the stars, and using narrowband SHO data solely for the nebula, we ensure:
1. Photometrically accurate stellar color profiles based on real Wien's law continuum data.
2. Undistorted, artifact-free emission nebula structures.
3. Completely independent stretching and noise modification strategies.

In the `astro-piper` codebase, this is handled by splitting out the star processing (`star_processing.py`) from the main nonlinear nebula processing (`nonlinear.py`).

---

## 2. Phase 1: Preprocessing & The WBPP Flow (Calibration & Registration)

The pre-processing phase (handled by WBPP in PixInsight or the equivalent calibration scripts in the pipeline) maps raw 2D arrays of electrons into a clean, calibrated, and aligned master linear space.

### Calibration (`calibration.js.tmpl`)
Every pixel on a sensor contains thermal dark current, read noise, and optical path defects (dust motes, vignetting). 
- **Dark Subtraction:** Master Darks (matched exactly by exposure, gain, and temperature: Gain 100/300s for NB, Gain -25/10s for RGB) are subtracted to remove the camera's thermal signature. Although the 2600MM inherently lacks amp glow, darks are still critical for removing hot pixels.
- **Flat Division:** Master Flats define the optical transmission of the telescope. Dividing the light frame by the flat frame mathematically flattens vignetting and deletes dust shadows.
- **Pedestal Addition:** A critical step for modern CMOS sensors. Adding a pedestal (e.g., 100-150 DN) during calibration prevents the background sky level from clipping to 0 during dark subtraction. If clipping occurs, faint signal in starved channels (like OIII or SII) is permanently deleted.

### Cosmetic Correction & Registration (`registration.js.tmpl`)
Defective pixels (dead/hot) are mathematically removed utilizing a master dark as a map or mathematical heuristics. Following this, the pipeline utilizes **StarAlignment** to map each frame's astrometric geometry to a reference image.
- **Cross-Registration:** The integrated RGB masters are directly aligned to the primary narrowband master (typically Ha, representing the highest SNR astrometric reference). This ensures pixel-perfect layering at the end of the pipeline.

### Integration (Stacking) & Drizzle (`integration.js.tmpl`)
Images are stacked together using statistical rejection algorithms. 
- **Rejection:** The pipeline relies on mathematical rejection algorithms (like ESD - Generalized Extreme Studentized Deviate) to isolate satellite tracks and cosmic rays while preserving faint valid signal.
- **DrizzleIntegration:** At 405mm focal length and 3.76μm pixels, the system operates at ~1.91"/px—mildly undersampled compared to optimal atmospheric seeing. Because the acquisition strategy utilizes **dithering** (random shifts between exposures), Drizzle 2x shifts the data into closer alignment with the Nyquist sampling theorem, creating rounder star profiles and tighter noise textures.

---

## 3. Phase 2: Linear Data Processing (`linear_processing.py`)

The transition from Phase 1 to Phase 2 represents the shift from mathematical calibration to signal manipulation. **Linear data** means the values in the image still perfectly proportionally map to the physical photon counts captured by the sensor. 

*Rule of Information Theory: Deconvolution must occur on linear data, and it must occur before ANY noise reduction.*

### Background Extraction
Lunar moonlight and residual optical gradients contaminate narrowband images. The pipeline utilizes AI-driven gradient removal (e.g., **GraXpert** CLI automation or PixInsight's AutomaticBackgroundExtraction/DynamicBackgroundExtraction). This identifies and chemically subtracts the background atmospheric signal without damaging the target emission. 

### Deconvolution: BlurXTerminator (BXT)
Deconvolution is the mathematical process of removing atmospheric and optical blur. BXT utilizes a non-stationary PSF (Point Spread Function) system, applying local corrections across the field. 
In the pipeline, this maps to the `bxt.js.tmpl` execution.
1. **Correct Only:** First pass to physically correct optical aberrations (coma, astigmatism).
2. **Sharpen:** Second pass to compress star sizes and resolve non-stellar structures like nebula shockfronts. 
*Note:* Deconvolution algorithms map existing noise to define detail boundaries. If noise is removed before this stage, the algorithm acts blindly, causing ringing artifacts. Thus, BXT is run *before* NXT.

### Star Erasure: StarXTerminator (SXT)
The pipeline linearly splits the channels and attacks the stars (`sxt.js.tmpl`). SXT is utilized on the SHO channels to completely remove stars, outputting purely starless emission layers. 
For the RGB track, SXT is run *to generate a stars-only image*, completely isolating our true-color stars from their background. This is crucial for avoiding bloated, discolored stars during the heavy SHO stretching.

### Linear Noise Reduction: NoiseXTerminator (NXT)
Once deconvolution maps the detail and stars are removed, noise can be carefully suppressed (`nxt.js.tmpl`). Utilizing NXT at this stage operates on the linear data, removing high-frequency atmospheric shot noise while maintaining the structural data defined by the 3nm bandpasses.

---

## 4. Phase 3: The Non-Linear Transition (Stretching / `stretching.py`)

A linear image is mathematically accurate but visually black because human vision is non-linear. "Stretching" maps the darkest 1% of linear data (where 99% of astronomical signal lives) over a much broader visual range.

### Intelligent Histogram Transformation (`stretch.js.tmpl`)
Narrowband components intrinsically vary in strength (Ha is overwhelmingly dominant, OIII is a whisper). If stretched identically via an autostretch, Ha will engulf the visual spectrum. 
The pipeline utilizes **Generalized Hyperbolic Stretch (GHS)** or statistical stretching (like HistogramTransformation) matched to the *per-channel* background median (Symmetry Point). By independently tailoring the stretch factor for each channel, faint OIII veils achieve visual parity with dense Ha cores natively prior to color mapping.

---

## 5. Phase 4: Non-Linear Math and Recombination (`nonlinear.py` & `pixelmath.js.tmpl`)

With starless SHO data properly stretched and mapped into a color image, fine aesthetic tuning is applied.

### Dynamic Palette Formulation (PixelMath)
Instead of standard Hubble SHO routing (where Ha is mapped directly to Green causing overwhelming green casts), dynamic PixelMath blending can be used. For instance, creating synthetic luminance from the Ha and OIII channels, or dynamically routing Ha into red and OIII into blue, while using SII to modulate the intersection. The `pixelmath.js.tmpl` step handles blending these channels optimally.

### True Color Star Calibration (`spcc.js.tmpl`)
Returning to our isolated RGB star layer... The pipeline utilizes PixInsight's **Spectrophotometric Color Calibration (SPCC)** (`star_processing.py`). SPCC mathematically compares the stars in your image against the Gaia DR3 spectrophotometric database, shifting the red/green/blue channels to physically align perfectly with the target's natural heat emission (Wien's Law). 

### The Final Recombination Blend
The final step is to merge the synthetic starless SHO nebula background with the photometrically accurate RGB stars. 
The pipeline mandates a **Screen Blend** via PixelMath: `~(~SHO_starless * ~RGB_stars_only)`. 
Unlike standard addition (`A + B`) which causes bright nebula cores intersecting with bright stars to clip the histogram to white, a screen blend mathematically compresses the addition, mimicking how physical light overlays, producing natural intersections untouched by artifacting.

## Summary Checklist for Maximum Efficacy:
1. **Acquisition:** Short broadband RGB exposures for stars (prevent saturation); long narrowband exposures for nebulosity (dig out the noise floor).
2. **Calibration:** Pedestal generation during calibration is mandatory to preserve faint data upon dark subtraction.
3. **Information Preservation:** Linear deconvolution (BXT) fundamentally requires intact noise values to function; never run NXT before BXT.
4. **Stretching:** Symmetrical stretch points cause color imbalances; always stretch off custom-measured background medians per-channel to equalize faint OIII with aggressive Ha.
5. **Recombination:** Use PixelMath screen blending for combining stars and nebula to avoid clipping.