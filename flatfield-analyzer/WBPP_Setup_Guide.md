# PixInsight WBPP Setup Guide — NGC 1499 (California Nebula)

A reference for getting WBPP to auto-match all calibration frames to science
frames in one shot, using the FITS header metadata already baked into every
file by the ASIAIR.

---

## 1. How WBPP Matching Actually Works

WBPP matches calibration frames to science frames using FITS header keywords.
Understanding these rules is the difference between one-click success and the
"why is everything broken" experience.

### Frame type detection (priority order)

1. **`IMAGETYP` header keyword** — WBPP does a case-insensitive substring
   search. Your ASIAIR files use `Light`, `Dark`, `Flat`, `Bias` which WBPP
   recognizes natively.
2. **Folder name fallback** — if `IMAGETYP` is missing, WBPP infers type from
   the containing folder name: `lights/`, `darks/`, `flats/`, `bias/`.
3. **Filename fallback** — substring search on the filename itself.

Your ASIAIR files all have correct `IMAGETYP` values, so detection should be
automatic. One caveat: WBPP also scans for the word **"master"** in `IMAGETYP`
or in the file path to identify pre-made master calibration frames. Avoid
putting raw frames in folders named "master".

### Matching criteria by calibration type

| Calibration | Must match exactly | Closest match | Not checked |
|---|---|---|---|
| **Dark -> Light** | Gain, Offset, Binning | Exposure (nearest) | Filter, Temperature* |
| **Flat -> Light** | Filter, Binning | — | Exposure, Temperature |
| **Bias -> anything** | Gain, Offset, Binning | — | Filter, Exposure |
| **Dark -> Flat** | Gain, Offset, Binning | Exposure (nearest) | Filter |

*Temperature is not matched by default. WBPP's "Optimize Master Dark" scales
the dark to compensate for temperature/exposure differences using hot pixel
statistics.

### The GAIN grouping keyword — this is the key

**By default, WBPP does NOT group by GAIN.** It only matches on Binning,
Filter, and Exposure out of the box. You **must explicitly add `GAIN` as a
Grouping Keyword** in WBPP's settings, or it will happily apply a gain=-25 dark
to a gain=100 light and produce garbage.

To add it: WBPP dialog -> top toolbar -> **Grouping Keywords** -> add `GAIN`.
Also add `OFFSET` if you ever change offset between sessions (yours is always
50, but it's good practice).

Once GAIN is a grouping keyword, WBPP creates separate calibration groups for
each gain value and matches within those groups.

### What WBPP reads from your ASIAIR FITS headers

| Keyword | Your values | Used for |
|---|---|---|
| `IMAGETYP` | Light, Dark, Flat, Bias | Frame classification |
| `FILTER` | B, G, R, H, O, S, L | Flat-to-light matching |
| `GAIN` | -25, 100, 300 | Grouping (when enabled) |
| `OFFSET` | 50 (all files) | Grouping (when enabled) |
| `EXPOSURE` / `EXPTIME` | varies | Dark-to-light matching |
| `XBINNING` / `YBINNING` | 1 / 1 (all files) | All matching |
| `SET-TEMP` | -20 (all files) | Optional grouping |
| `CCD-TEMP` | -19.8 to -20.0 | Not used for matching |
| `INSTRUME` | ZWO ASI2600MM Pro | Camera identification |

---

## 2. Your Current Data — Inventory and Matching Audit

### Light frames

| Session | Filters | Gain | Exposure | N (total) |
|---|---|---|---|---|
| 01-19-2026 | H, O, S | **300** | 300s | 36 |
| 01-21-2026 | B, G, R | -25 | 10s | 60 |
| 01-21-2026 | H, O, S | 100 | 300s | 48 |
| 01-23-2026 (high wind) | H, O, S | 100 | 300s | 36 |
| 01-24-2026 | H, O, S | 100 | 300s | 36 |

### Flat frames

| Session | Filters | Gain | N (per filter) |
|---|---|---|---|
| 01-19-2026/flats | H, O, S | **100** | 50 each |
| 01-21-2026/flats | B, G, R, H, O, S | -25 (RGB), 100 (NB) | 50 each |
| 01-23-2026/flats | H, O, S | 100 | 50 each |
| 01-24-2026/flats | H, O, S | 100 | 50 each |

### Calibration library (shared darks/bias)

| Type | Gain | Exposure | Temp | N |
|---|---|---|---|---|
| Bias | -25 | 1 ms | -20 C | 22 |
| Bias | 100 | 1 ms | -20 C | 50 |
| Dark | -25 | 10s | -20 C | 34 |
| Dark | 100 | 300s | -20 C | 50 |

### Matching verdict

| Light group | Darks | Bias | Flats | Status |
|---|---|---|---|---|
| 01-19 NB (gain=300, 300s) | NONE (no gain=300 darks) | NONE (no gain=300 bias) | MISMATCH (flats are gain=100) | BROKEN |
| 01-21 RGB (gain=-25, 10s) | gain=-25 10s darks | gain=-25 bias | gain=-25 RGB flats | OK |
| 01-21 NB (gain=100, 300s) | gain=100 300s darks | gain=100 bias | gain=100 NB flats | OK |
| 01-23 NB (gain=100, 300s) | gain=100 300s darks | gain=100 bias | gain=100 NB flats | OK |
| 01-24 NB (gain=100, 300s) | gain=100 300s darks | gain=100 bias | gain=100 NB flats | OK |

### The 01-19-2026 problem

The 01-19 session shot lights at **gain=300** but flats at **gain=100**.
There are no darks or bias at gain=300 in the calibration library either.
This session cannot be properly calibrated with WBPP as-is.

**Your options:**

1. **Exclude 01-19 from the WBPP run entirely.** You have 36+48 = 84
   narrowband frames from the other three sessions at gain=100 — that's
   probably enough to work with. This is the cleanest option.
2. **Force-apply the gain=100 flats to the gain=300 lights.** The spatial
   illumination pattern (vignetting, dust) doesn't change with gain — only the
   scaling does. You can do this by loading the 01-19 lights and flats manually
   via **Add Custom** and overriding the gain value. The flat division will
   still correct the spatial pattern, but you lose proper noise calibration
   from dark/bias subtraction.
3. **Shoot new darks/bias at gain=300** to complete the calibration library.
   The flats at gain=100 can still be applied if you override, since flat
   correction is multiplicative (it divides out the illumination pattern).

> **Recommendation:** Go with option 1 for now. 84 NB frames at gain=100 with
> proper calibration will produce a better result than 120 frames with 36 of
> them poorly calibrated.

---

## 3. Ideal Directory Structure for WBPP

WBPP's "Add Directory" button recursively scans all subdirectories. It
classifies frames using `IMAGETYP` first, then folder names as fallback.
The key principles:

- **Put everything under one root** so you can point WBPP at it with a single
  "Add Directory" click
- **Use folder names that match frame types** as a safety net: `lights/`,
  `darks/`, `flats/`, `bias/`
- **Do NOT put the word "master" in any raw-frame folder path** — WBPP will
  treat those files as pre-made masters
- **Keep paths short** — PixInsight can choke on very long paths
- **Separate sessions** if you want WBPP to track them independently (optional)

### Recommended layout for your NGC 1499 data

```
NGC1499/                              <-- point WBPP "Add Directory" here
│
├── lights/
│   ├── 01-21-2026/                   <-- 01-21 RGB + NB lights
│   │   ├── Light_..._B_gain-25_....fit
│   │   ├── Light_..._G_gain-25_....fit
│   │   ├── Light_..._R_gain-25_....fit
│   │   ├── Light_..._H_gain100_....fit
│   │   ├── Light_..._O_gain100_....fit
│   │   └── Light_..._S_gain100_....fit
│   ├── 01-23-2026/                   <-- 01-23 NB lights
│   │   ├── Light_..._H_gain100_....fit
│   │   ├── Light_..._O_gain100_....fit
│   │   └── Light_..._S_gain100_....fit
│   └── 01-24-2026/                   <-- 01-24 NB lights
│       ├── Light_..._H_gain100_....fit
│       ├── Light_..._O_gain100_....fit
│       └── Light_..._S_gain100_....fit
│
├── flats/
│   ├── 01-21-2026/                   <-- best set: all 6 filters
│   │   ├── Flat_..._B_gain-25_....fit
│   │   ├── Flat_..._G_gain-25_....fit
│   │   ├── Flat_..._R_gain-25_....fit
│   │   ├── Flat_..._H_gain100_....fit
│   │   ├── Flat_..._O_gain100_....fit
│   │   └── Flat_..._S_gain100_....fit
│   ├── 01-23-2026/                   <-- NB only (H, O, S at gain=100)
│   │   └── ...
│   └── 01-24-2026/                   <-- NB only (H, O, S at gain=100)
│       └── ...
│
├── darks/
│   ├── gain-25_10s/                  <-- 34 darks matching RGB lights
│   │   └── Dark_..._gain-25_....fit
│   └── gain100_300s/                 <-- 50 darks matching NB lights
│       └── Dark_..._gain100_....fit
│
└── bias/
    ├── gain-25/                      <-- 22 bias matching RGB lights
    │   └── Bias_..._gain-25_....fit
    └── gain100/                      <-- 50 bias matching NB lights
        └── Bias_..._gain100_....fit
```

### Why this structure works

1. **Top-level folders named `lights/`, `flats/`, `darks/`, `bias/`** give
   WBPP a folder-name fallback if it ever fails to read `IMAGETYP`.
2. **Session subfolders under lights/ and flats/** keep files organized for
   you without confusing WBPP — it only cares about the frame type folder
   name, not the session subfolder name.
3. **Gain-based subfolders under darks/ and bias/** are for your
   organization only — WBPP matches by the `GAIN` header value, not
   folder names (as long as GAIN is set as a grouping keyword).
4. **The 01-19 session is excluded** because it has no matching calibration
   data at gain=300.
5. **All flats from all sessions are included.** WBPP will pick the best
   matching set per filter/gain. Having multiple sessions of flats does
   no harm — WBPP stacks all flats that match a given filter+gain into
   one master flat.
6. **Single "Add Directory" at NGC1499/** loads everything and WBPP
   auto-sorts it.

### Can you use symlinks/shortcuts instead of moving files?

Yes. If you don't want to restructure your actual files, you can create
a staging directory with **symbolic links** (or junctions on Windows) pointing
to the original locations. WBPP follows symlinks. Example:

```cmd
mklink /J "C:\astro\NGC1499\lights\01-21" "C:\Users\Dane\Pictures\DSOs\01_nebulae\NGC1499 - California Nebula\01-21-2026"
```

This way your originals stay untouched and WBPP sees the clean structure.

---

## 4. WBPP Settings Checklist

Once your files are organized (or symlinked), here's the exact WBPP
configuration:

### Step 1: Reset WBPP
- Open WBPP
- **Reset -> Full Reset** (clears all cached state from previous runs)

### Step 2: Set Grouping Keywords
- Click the **Grouping Keywords** button (top toolbar, looks like a key icon)
- Add `GAIN`
- Add `OFFSET` (safety net — yours is always 50 but protects against future
  mix-ups)
- These keywords tell WBPP to create separate calibration groups for each
  unique GAIN+OFFSET combination

### Step 3: Load files
- Click **Add Directory**
- Point it at your `NGC1499/` root folder
- WBPP recursively scans and auto-classifies everything into the Lights,
  Darks, Flats, and Bias tabs

### Step 4: Verify classification
- Check each tab (Lights, Darks, Flats, Bias) and confirm the file counts
  match what you expect:
  - **Lights:** 180 files (60 RGB + 120 NB across 3 sessions)
  - **Flats:** 600+ files (50 per filter per session)
  - **Darks:** 84 files (34 at gain=-25 + 50 at gain=100)
  - **Bias:** 72 files (22 at gain=-25 + 50 at gain=100)

### Step 5: Check the Calibration Diagram
- In the **Calibration** tab, right-click -> **Show Calibration Diagram**
- This is the single most important verification step
- Confirm that:
  - RGB lights (gain=-25) pair with gain=-25 darks, bias, and RGB flats
  - NB lights (gain=100) pair with gain=100 darks, bias, and NB flats
  - No lights are left unmatched
  - No cross-gain contamination

### Step 6: Configure calibration options
- **Calibration tab:**
  - Enable **Optimize Master Dark** (scales dark for temperature/exposure
    differences)
  - Pedestal: **Auto** (adds a small positive offset to prevent clipped
    negative pixels after dark subtraction)
- **Post-Calibration tab:**
  - Cosmetic correction: enable if desired (hot/cold pixel removal)
  - Subframe weighting: **FWHM + Eccentricity + SNR** (default is fine)
  - Exposure tolerance: leave at default unless you mixed exposure times
    within a single filter
- **Registration tab:**
  - Use **automatic** detection limit
  - Reference image: let WBPP auto-select the best frame
- **Integration tab:**
  - Rejection: **Winsorized Sigma Clipping** (good default for 12-50 frames)
  - Normalization: **Adaptive**
  - Weights: **Quality** (uses the subframe weights from post-calibration)

### Step 7: Set output directory
- Point the output to a folder outside the input tree (e.g.,
  `NGC1499/WBPP_output/`) to keep processed data separate from raw

### Step 8: Run
- Click **Run**
- WBPP will: create master bias per gain -> create master darks per
  gain/exposure -> calibrate and create master flats per filter/gain ->
  calibrate lights -> cosmetic correction -> debayer (N/A for mono) ->
  subframe weighting -> registration -> integration

---

## 5. Common Pitfalls and How to Avoid Them

### "WBPP matched my darks to the wrong lights"
**Cause:** GAIN is not set as a grouping keyword. WBPP ignores gain by
default and matches darks purely by exposure time + binning.
**Fix:** Add GAIN as a grouping keyword before loading files.

### "My flats are showing up in the Lights tab"
**Cause:** Filename contains "light" before "flat" (e.g.,
`Light_Flat_001.fit`). WBPP's substring search hits "light" first.
**Fix:** Your ASIAIR files start with `Flat_` so this shouldn't happen,
but if it does, check `IMAGETYP` with PixInsight's FITSHeader process.

### "Calibrated frames look worse than uncalibrated"
**Cause:** Gain mismatch between calibration and science frames. A
gain=-25 dark subtracted from a gain=100 light produces artifacts.
**Fix:** Verify the Calibration Diagram shows correct gain matching.

### "WBPP says it can't find matching calibration frames"
**Cause:** Usually a binning mismatch (1x1 lights vs 2x2 darks), or
GAIN grouping keyword causing an unmatched group (e.g., gain=300 lights
with no gain=300 darks).
**Fix:** Check that your calibration library covers all gain+binning
combinations used in your lights.

### "Temperature values don't match exactly"
**Not a problem.** Your SET-TEMP is -20 C across all files. CCD-TEMP
varies slightly (-19.8 to -20.0) but WBPP doesn't match on temperature
by default, and Optimize Master Dark compensates for small differences.

### "WBPP keeps using stale cache from a previous run"
**Fix:** Always do a Full Reset before starting a new processing session.
WBPP caches master frames and can reuse old ones if you don't clear it.

### "Bias filter is 'L' but my lights use B/G/R/H/O/S"
**Not a problem.** WBPP does not match bias by filter — only by gain,
offset, and binning. The `FILTER=L` in your bias headers is just what the
ASIAIR writes when no filter wheel position matters. WBPP ignores it for
bias frames.

### Long file paths
PixInsight can fail with very long paths. Your current paths like
`C:\Users\Dane\Pictures\DSOs\01_nebulae\NGC1499 - California Nebula\01-23-2026 - (high wind)\flats\Flat_133deg_925.0ms_Bin1_S_gain100_...fit`
are getting close to the limit. The symlink/junction approach above
sidesteps this by giving you a short staging path.

---

## 6. Quick Reference Card

```
WBPP Matching Rules
───────────────────────────────────────────────
FRAME TYPE DETECTION:
  1. IMAGETYP header  (Light/Dark/Flat/Bias)
  2. Folder name      (lights/darks/flats/bias)
  3. Filename         (substring search)

DARK MATCHING:    Gain + Offset + Binning (exact)
                  Exposure (closest available)

FLAT MATCHING:    Filter + Binning (exact)
                  Gain (exact, if GAIN is a grouping keyword)

BIAS MATCHING:    Gain + Offset + Binning (exact)

MUST-DO:          Add GAIN as a grouping keyword
                  Full Reset before each new run
                  Check Calibration Diagram before running
───────────────────────────────────────────────

Your Gain Groups
───────────────────────────────────────────────
GAIN=-25:  RGB lights (10s) + RGB flats + 10s darks + bias
GAIN=100:  NB lights (300s) + NB flats + 300s darks + bias
GAIN=300:  01-19 lights ONLY — no matching calibration data!
───────────────────────────────────────────────
```

---

## 7. Calibration Library Gaps

Your current library covers gain=-25 and gain=100 well. To future-proof:

| What's missing | Impact | Priority |
|---|---|---|
| Darks at gain=300, 300s | Cannot calibrate 01-19-2026 lights | High (if you want those 36 frames) |
| Bias at gain=300 | Cannot calibrate 01-19-2026 lights | High (if you want those 36 frames) |
| Darks at gain=100, 10s | No impact currently (no gain=100 short-exposure lights) | Low |
| Darks at gain=-25, 300s | No impact currently (no gain=-25 long-exposure lights) | Low |

Shooting 30-50 bias frames and 30-50 dark frames at gain=300 / -20 C /
300s would complete the library for the 01-19 session. Darks and bias
don't need the telescope or any light — just cap the camera and shoot.

---

*Last updated: 2026-02-19*
