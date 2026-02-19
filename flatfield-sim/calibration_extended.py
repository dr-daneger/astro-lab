"""
Extended diagnostics: profile shape, noise, and pitch auto-fit.
Tries fitting with MEASURED pitch instead of assumed 15mm.
"""
import numpy as np
from PIL import Image
from pathlib import Path
import csv

LED_PITCH_ASSUMED = 15.0
LED_COLS = 13
LED_ROWS = 9

LS_UM_PX  = 14.58
PTFE_UM_PX = 17.30

IMG_DIR = Path(__file__).parent / "public" / "calibration_images"


def linearize_srgb(v):
    return np.power(v, 2.2)


def load_profile(path, um_px):
    img = np.array(Image.open(path))
    g = img[:, :, 1].astype(np.float64) / 255.0
    gl = linearize_srgb(g)
    best_row = int(np.argmax(gl.sum(axis=1)))
    profile = gl[best_row]
    mm_px = um_px / 1000.0
    w = len(profile)
    x = (np.arange(w) - w / 2) * mm_px
    return x, profile, best_row, img.shape


def find_peaks(x, y, min_sep):
    thr = y.max() * 0.25
    pks = []
    for i in range(2, len(y) - 2):
        if (y[i] > thr and y[i] >= y[i-1] and y[i] >= y[i+1]
            and y[i] >= y[i-2] and y[i] >= y[i+2]):
            if not pks or (x[i] - x[pks[-1]]) > min_sep:
                pks.append(i)
    return np.array(pks)


def gaussian_grid_1d(x, sigma, pitch, n_cols, n_rows):
    rc = (n_rows - 1) / 2
    cc = (n_cols - 1) / 2
    val = np.zeros_like(x)
    for r in range(n_rows):
        for c in range(n_cols):
            lx = (c - cc) * pitch
            ly = (r - rc) * pitch
            val += np.exp(-((x - lx)**2 + ly**2) / (2 * sigma**2))
    return val


def lorentzian_grid_1d(x, gamma, pitch, n_cols, n_rows):
    rc = (n_rows - 1) / 2
    cc = (n_cols - 1) / 2
    val = np.zeros_like(x)
    for r in range(n_rows):
        for c in range(n_cols):
            lx = (c - cc) * pitch
            ly = (r - rc) * pitch
            d2 = (x - lx)**2 + ly**2
            val += 1.0 / (1.0 + d2 / gamma**2)
    return val


def main():
    ls_x, ls_y, ls_row, ls_shape = load_profile(IMG_DIR / "Lightsource.JPG", LS_UM_PX)
    ptfe_x, ptfe_y, ptfe_row, ptfe_shape = load_profile(IMG_DIR / "PTFE.JPG", PTFE_UM_PX)

    print("=" * 60)
    print("EXTENDED DIAGNOSTICS")
    print("=" * 60)

    print(f"\nImage shapes: LS={ls_shape}, PTFE={ptfe_shape}")
    print(f"Brightest rows: LS={ls_row}, PTFE={ptfe_row}")

    # ── Pitch analysis with multiple min_sep values ──
    print("\n── LIGHTSOURCE PEAK ANALYSIS ──")
    for min_sep in [3.0, 5.0, 7.0, 10.0, 12.0]:
        pks = find_peaks(ls_x, ls_y, min_sep)
        positions = ls_x[pks]
        if len(positions) >= 2:
            diffs = np.diff(positions)
            pitch = diffs.mean()
            print(f"  min_sep={min_sep:5.1f}mm → {len(pks):2d} peaks, pitch={pitch:.3f}mm (std={diffs.std():.3f}mm)")
            print(f"    Span: [{positions[0]:.1f}, {positions[-1]:.1f}]mm = {positions[-1]-positions[0]:.1f}mm total")

    # ── Noise analysis ──
    print("\n── PROFILE NOISE ANALYSIS ──")
    for label, x, y in [("Lightsource", ls_x, ls_y), ("PTFE", ptfe_x, ptfe_y)]:
        # High-frequency noise: difference between adjacent pixels
        hf = np.diff(y)
        # Smooth with a running median (window ~50px ≈ 1mm)
        from scipy.ndimage import median_filter
        smooth = median_filter(y, size=51)
        noise = y - smooth
        signal = smooth.max() - smooth.min()
        snr = signal / np.std(noise) if np.std(noise) > 0 else 0
        print(f"  {label}:")
        print(f"    Peak value: {y.max():.4f}")
        print(f"    HF noise std (pixel-to-pixel): {np.std(hf):.6f}")
        print(f"    After median filter (51px): signal range={signal:.4f}, noise std={np.std(noise):.6f}")
        print(f"    SNR: {snr:.1f}")

    # ── Try fitting with MEASURED pitch instead of assumed 15mm ──
    print("\n── FIT WITH AUTO-DETECTED PITCH ──")
    # Use min_sep=10mm peaks from lightsource to get coarse pitch
    ls_pks_coarse = find_peaks(ls_x, ls_y, 10.0)
    if len(ls_pks_coarse) >= 2:
        measured_pitch = float(np.diff(ls_x[ls_pks_coarse]).mean())
    else:
        measured_pitch = 8.16  # fallback from prior run

    print(f"  Using measured pitch = {measured_pitch:.3f}mm")

    # Need to figure out the right grid dimensions for this pitch
    # If pitch is ~8mm and image spans ~140mm, that's ~17 columns
    # But the Viltrox might be ~24×5 or similar
    # Let's try different grid configs
    ptfe_norm = ptfe_y / ptfe_y.max()

    print("\n  Grid search: trying multiple column counts with measured pitch")
    results = []
    for n_cols in [9, 11, 13, 15, 17, 19, 21, 23, 25]:
        for n_rows in [5, 7, 9, 11, 13]:
            n_leds = n_cols * n_rows
            if n_leds < 90 or n_leds > 150:  # L116T has 116 LEDs
                continue
            best_sigma, best_rms = 4.0, 1e9
            for sigma in np.arange(1.0, 40.0, 0.5):
                model = gaussian_grid_1d(ptfe_x, sigma, measured_pitch, n_cols, n_rows)
                mn = model / model.max() if model.max() > 0 else model
                rms = float(np.sqrt(np.mean((ptfe_norm - mn)**2)))
                if rms < best_rms:
                    best_rms = rms
                    best_sigma = sigma
            results.append((n_cols, n_rows, n_cols * n_rows, best_sigma, best_rms))

    results.sort(key=lambda r: r[4])
    print(f"  {'Cols':>4} {'Rows':>4} {'LEDs':>5} {'σ_mm':>7} {'RMS':>10}")
    for c, r, n, s, rms in results[:15]:
        print(f"  {c:4d} {r:4d} {n:5d} {s:7.2f} {rms:10.6f}")

    # ── Also try with assumed 15mm pitch but wider grid search ──
    print("\n── FIT WITH ASSUMED 15mm PITCH (wider σ range) ──")
    assumed_results = []
    for sigma in np.arange(1, 80, 0.5):
        model = gaussian_grid_1d(ptfe_x, sigma, 15.0, 13, 9)
        mn = model / model.max() if model.max() > 0 else model
        rms = float(np.sqrt(np.mean((ptfe_norm - mn)**2)))
        assumed_results.append((sigma, rms))

    assumed_results.sort(key=lambda r: r[1])
    print("  Top 5 sigma values at 15mm/13x9:")
    for s, rms in assumed_results[:5]:
        print(f"    σ={s:.1f}mm  RMS={rms:.6f}")

    # ── Profile smoothness at different kernel sizes ──
    print("\n── EFFECT OF PROFILE SMOOTHING ON FIT ──")
    from scipy.ndimage import uniform_filter1d
    for kern_px in [1, 21, 51, 101, 201, 501]:
        smooth_y = uniform_filter1d(ptfe_y, kern_px) if kern_px > 1 else ptfe_y
        smooth_norm = smooth_y / smooth_y.max()
        # Fit Gaussian at measured pitch
        best_s, best_r = 4, 1e9
        for sigma in np.arange(1, 50, 0.25):
            model = gaussian_grid_1d(ptfe_x, sigma, measured_pitch, 13, 9)
            mn = model / model.max()
            rms = float(np.sqrt(np.mean((smooth_norm - mn)**2)))
            if rms < best_r:
                best_r = rms; best_s = sigma
        kern_mm = kern_px * PTFE_UM_PX / 1000
        print(f"  kernel={kern_px:4d}px ({kern_mm:5.1f}mm) → σ={best_s:.2f}mm  RMS={best_r:.6f}")

    # ── Check if scale is off by looking at peak-to-peak in pixels ──
    print("\n── RAW PIXEL PEAK SPACING ──")
    # Find peaks in pixel coordinates directly
    pks_px = find_peaks(ls_x, ls_y, 3.0)
    if len(pks_px) >= 2:
        pixel_positions = pks_px  # these are array indices
        pixel_diffs = np.diff(pixel_positions)
        print(f"  Peak indices (first 20): {pixel_positions[:20]}")
        print(f"  Pixel spacings: {pixel_diffs[:20]}")
        print(f"  Mean pixel spacing: {pixel_diffs.mean():.1f} ± {pixel_diffs.std():.1f} px")
        print(f"  At {LS_UM_PX} µm/px → {pixel_diffs.mean() * LS_UM_PX / 1000:.2f} mm")
        print(f"  At {LS_UM_PX} µm/px, 15mm pitch would be {15000/LS_UM_PX:.0f} px spacing")
        print(f"  Measured/Expected ratio: {pixel_diffs.mean() / (15000/LS_UM_PX):.3f}")
        corrected_scale = 15.0 / pixel_diffs.mean() * 1000  # µm/px if pitch is really 15mm
        print(f"  IF pitch=15mm, corrected scale would be: {corrected_scale:.2f} µm/px")
        # Also check PTFE image peaks
        ptfe_pks = find_peaks(ptfe_x, ptfe_y, 3.0)
        if len(ptfe_pks) >= 2:
            ptfe_px_diffs = np.diff(ptfe_pks)
            print(f"\n  PTFE peak pixel spacings (first 20): {ptfe_px_diffs[:20]}")
            print(f"  Mean: {ptfe_px_diffs.mean():.1f} ± {ptfe_px_diffs.std():.1f} px")
            ptfe_corrected = 15.0 / ptfe_px_diffs.mean() * 1000
            print(f"  IF pitch=15mm, corrected PTFE scale: {ptfe_corrected:.2f} µm/px")

    # ── Final: fit with corrected scale ──
    print("\n── FIT WITH CORRECTED SCALE (assuming real pitch=15mm) ──")
    if len(pks_px) >= 2:
        ls_mean_px_spacing = float(np.diff(pks_px).mean())
        # The real scale should map this spacing to 15mm
        real_ls_mm_px = 15.0 / ls_mean_px_spacing
        real_ptfe_mm_px = real_ls_mm_px * (PTFE_UM_PX / LS_UM_PX)  # scale ratio preserved

        # Re-extract PTFE profile with corrected scale
        ptfe_x_corr = (np.arange(len(ptfe_y)) - len(ptfe_y) / 2) * real_ptfe_mm_px
        ptfe_norm_corr = ptfe_y / ptfe_y.max()

        print(f"  Corrected PTFE scale: {real_ptfe_mm_px * 1000:.2f} µm/px (was {PTFE_UM_PX} µm/px)")
        print(f"  PTFE x-range: [{ptfe_x_corr[0]:.1f}, {ptfe_x_corr[-1]:.1f}] mm")

        best_sigma_corr, best_rms_corr = 4, 1e9
        sweep_corr = []
        for sigma in np.arange(1, 80, 0.25):
            model = gaussian_grid_1d(ptfe_x_corr, sigma, 15.0, 13, 9)
            mn = model / model.max()
            rms = float(np.sqrt(np.mean((ptfe_norm_corr - mn)**2)))
            sweep_corr.append((sigma, rms))
            if rms < best_rms_corr:
                best_rms_corr = rms
                best_sigma_corr = sigma

        print(f"  Gaussian: σ={best_sigma_corr:.2f}mm  RMS={best_rms_corr:.6f}")

        # Also Lorentzian
        best_gamma_corr, best_rms_lor = 4, 1e9
        for gamma in np.arange(1, 80, 0.25):
            model = lorentzian_grid_1d(ptfe_x_corr, gamma, 15.0, 13, 9)
            mn = model / model.max()
            rms = float(np.sqrt(np.mean((ptfe_norm_corr - mn)**2)))
            if rms < best_rms_lor:
                best_rms_lor = rms
                best_gamma_corr = gamma
        print(f"  Lorentzian: γ={best_gamma_corr:.2f}mm  RMS={best_rms_lor:.6f}")

        # Smoothed version
        from scipy.ndimage import uniform_filter1d
        for kern in [101, 201, 501]:
            sy = uniform_filter1d(ptfe_y, kern)
            sn = sy / sy.max()
            bs, br = 4, 1e9
            for sigma in np.arange(1, 80, 0.25):
                model = gaussian_grid_1d(ptfe_x_corr, sigma, 15.0, 13, 9)
                mn = model / model.max()
                rms = float(np.sqrt(np.mean((sn - mn)**2)))
                if rms < br:
                    br = rms; bs = sigma
            kern_mm = kern * real_ptfe_mm_px
            print(f"  Gaussian (smoothed {kern}px / {kern_mm:.1f}mm): σ={bs:.2f}mm  RMS={br:.6f}")

        # Write corrected-scale CSV for the best fit
        model_best = gaussian_grid_1d(ptfe_x_corr, best_sigma_corr, 15.0, 13, 9)
        model_best_n = model_best / model_best.max()
        csv_path = Path(__file__).parent / "calibration_corrected.csv"
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["x_mm", "observed_norm", "gaussian_model_corr", "residual_corr"])
            step = 10
            for i in range(0, len(ptfe_x_corr), step):
                w.writerow([
                    f"{ptfe_x_corr[i]:.4f}",
                    f"{ptfe_norm_corr[i]:.6f}",
                    f"{model_best_n[i]:.6f}",
                    f"{ptfe_norm_corr[i] - model_best_n[i]:.6f}",
                ])
        print(f"\n  Wrote {csv_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
