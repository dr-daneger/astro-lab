"""
Re-analysis with CORRECT pixel scale.
Scale is precise (ruler-crop calibration technique).
Focus: fix row selection, fix peak detection, understand true profile shape.
"""
import numpy as np
from PIL import Image
from pathlib import Path
from scipy.ndimage import uniform_filter1d, median_filter
from scipy.signal import find_peaks as scipy_find_peaks

IMG_DIR = Path(__file__).parent / "public" / "calibration_images"

LED_PITCH = 15.0   # mm
LED_COLS = 13
LED_ROWS = 9
LS_UM_PX   = 14.58
PTFE_UM_PX = 17.30


def linearize(v):
    return np.power(v, 2.2)


def load_green_linearized(path):
    img = np.array(Image.open(path))
    g = img[:, :, 1].astype(np.float64) / 255.0
    return linearize(g), img.shape


def pick_center_row(green_lin, margin_frac=0.25):
    """Find brightest row, but only search the central 50% of the image."""
    h = green_lin.shape[0]
    lo = int(h * margin_frac)
    hi = int(h * (1 - margin_frac))
    sums = green_lin[lo:hi, :].sum(axis=1)
    return lo + int(np.argmax(sums))


def extract_profile(green_lin, row, mm_per_px):
    y = green_lin[row, :]
    w = len(y)
    x = (np.arange(w) - w / 2) * mm_per_px
    return x, y


def gaussian_grid_1d(x, sigma, pitch=LED_PITCH, n_cols=LED_COLS, n_rows=LED_ROWS):
    cc = (n_cols - 1) / 2
    rc = (n_rows - 1) / 2
    val = np.zeros_like(x)
    for r in range(n_rows):
        for c in range(n_cols):
            lx = (c - cc) * pitch
            ly = (r - rc) * pitch
            val += np.exp(-((x - lx)**2 + ly**2) / (2 * sigma**2))
    return val


def lorentzian_grid_1d(x, gamma, pitch=LED_PITCH, n_cols=LED_COLS, n_rows=LED_ROWS):
    cc = (n_cols - 1) / 2
    rc = (n_rows - 1) / 2
    val = np.zeros_like(x)
    for r in range(n_rows):
        for c in range(n_cols):
            lx = (c - cc) * pitch
            ly = (r - rc) * pitch
            d2 = (x - lx)**2 + ly**2
            val += 1.0 / (1.0 + d2 / gamma**2)
    return val


def main():
    print("=" * 60)
    print("CORRECTED ANALYSIS — SCALE IS PRECISE")
    print("=" * 60)

    # ── Load ──
    ls_g, ls_shape = load_green_linearized(IMG_DIR / "Lightsource.JPG")
    ptfe_g, ptfe_shape = load_green_linearized(IMG_DIR / "PTFE.JPG")

    ls_mm_px = LS_UM_PX / 1000
    ptfe_mm_px = PTFE_UM_PX / 1000

    # ── Fix row selection ──
    ls_row = pick_center_row(ls_g)
    ptfe_row = pick_center_row(ptfe_g)
    print(f"\nImage shapes: LS={ls_shape}, PTFE={ptfe_shape}")
    print(f"Selected rows (center-biased): LS={ls_row}/{ls_shape[0]}, PTFE={ptfe_row}/{ptfe_shape[0]}")

    ls_x, ls_y = extract_profile(ls_g, ls_row, ls_mm_px)
    ptfe_x, ptfe_y = extract_profile(ptfe_g, ptfe_row, ptfe_mm_px)

    print(f"\nLS x-range: [{ls_x[0]:.1f}, {ls_x[-1]:.1f}] mm ({len(ls_x)} px)")
    print(f"PTFE x-range: [{ptfe_x[0]:.1f}, {ptfe_x[-1]:.1f}] mm ({len(ptfe_x)} px)")
    print(f"At 15mm pitch, one LED spacing = {15.0/ls_mm_px:.0f} px (LS), {15.0/ptfe_mm_px:.0f} px (PTFE)")

    expected_ls_spacing_px = 15.0 / ls_mm_px
    expected_ptfe_spacing_px = 15.0 / ptfe_mm_px

    # ── Profile statistics ──
    for label, x, y in [("LS", ls_x, ls_y), ("PTFE", ptfe_x, ptfe_y)]:
        print(f"\n── {label} PROFILE STATS ──")
        print(f"  max={y.max():.4f}, mean={y.mean():.4f}, median={np.median(y):.4f}")
        # noise from high-pass filter
        smooth = median_filter(y, size=51)
        noise = y - smooth
        print(f"  Pixel-to-pixel noise std: {np.std(np.diff(y)):.6f}")
        print(f"  Noise std vs median-smoothed: {np.std(noise):.6f}")

    # ── Peak detection: use scipy with proper distance ──
    print("\n── LIGHTSOURCE PEAKS (scipy, distance=LED pitch) ──")
    # Smooth first to kill noise peaks
    ls_smooth = uniform_filter1d(ls_y, size=51)
    peaks_ls, props_ls = scipy_find_peaks(ls_smooth, 
                                           distance=int(expected_ls_spacing_px * 0.7),
                                           prominence=ls_smooth.max() * 0.05)
    ls_peak_x = ls_x[peaks_ls]
    print(f"  Peaks found: {len(peaks_ls)}")
    if len(peaks_ls) >= 2:
        diffs = np.diff(ls_peak_x)
        print(f"  Positions (mm): {np.round(ls_peak_x, 1)}")
        print(f"  Spacings (mm): {np.round(diffs, 2)}")
        print(f"  Mean spacing: {diffs.mean():.3f} mm (expected {LED_PITCH}mm)")
        print(f"  Std: {diffs.std():.3f} mm")
        print(f"  Deviation from expected: {abs(diffs.mean() - LED_PITCH):.3f} mm ({abs(diffs.mean()-LED_PITCH)/LED_PITCH*100:.1f}%)")
    print(f"  Array span (peak-to-peak): {ls_peak_x[-1]-ls_peak_x[0]:.1f}mm")
    print(f"  Expected array span (12×15): {(LED_COLS-1)*LED_PITCH}mm")

    print("\n── PTFE PEAKS (scipy, distance=LED pitch) ──")
    ptfe_smooth = uniform_filter1d(ptfe_y, size=51)
    peaks_ptfe, _ = scipy_find_peaks(ptfe_smooth,
                                      distance=int(expected_ptfe_spacing_px * 0.7),
                                      prominence=ptfe_smooth.max() * 0.02)
    ptfe_peak_x = ptfe_x[peaks_ptfe]
    print(f"  Peaks found: {len(peaks_ptfe)}")
    if len(peaks_ptfe) >= 2:
        diffs_ptfe = np.diff(ptfe_peak_x)
        print(f"  Positions (mm): {np.round(ptfe_peak_x, 1)}")
        print(f"  Spacings (mm): {np.round(diffs_ptfe, 2)}")
        print(f"  Mean spacing: {diffs_ptfe.mean():.3f} mm")

    # ── FIT: use smoothed PTFE profile ──
    print("\n── GAUSSIAN FIT ON SMOOTHED PTFE ──")
    for kern in [1, 101, 301, 501, 1001]:
        sy = uniform_filter1d(ptfe_y, kern) if kern > 1 else ptfe_y
        sn = sy / sy.max()
        best_s, best_r = 4, 1e9
        for sigma_t in np.arange(2, 80, 0.25):
            model = gaussian_grid_1d(ptfe_x, sigma_t)
            mn = model / model.max()
            rms = float(np.sqrt(np.mean((sn - mn)**2)))
            if rms < best_r:
                best_r = rms; best_s = sigma_t
        kern_mm = kern * ptfe_mm_px
        print(f"  smooth={kern:5d}px ({kern_mm:5.1f}mm) → σ={best_s:.2f}mm  RMS={best_r:.6f}")

    print("\n── LORENTZIAN FIT ON SMOOTHED PTFE ──")
    for kern in [1, 101, 301, 501, 1001]:
        sy = uniform_filter1d(ptfe_y, kern) if kern > 1 else ptfe_y
        sn = sy / sy.max()
        best_g, best_r = 4, 1e9
        for gamma_t in np.arange(2, 80, 0.25):
            model = lorentzian_grid_1d(ptfe_x, gamma_t)
            mn = model / model.max()
            rms = float(np.sqrt(np.mean((sn - mn)**2)))
            if rms < best_r:
                best_r = rms; best_g = gamma_t
        kern_mm = kern * ptfe_mm_px
        print(f"  smooth={kern:5d}px ({kern_mm:5.1f}mm) → γ={best_g:.2f}mm  RMS={best_r:.6f}")

    # ── Check profile envelope vs model ──
    # Use heavy smoothing (1mm = ~58px) to see envelope shape
    print("\n── PROFILE ENVELOPE COMPARISON (1mm smooth) ──")
    kern_1mm = max(1, int(1.0 / ptfe_mm_px))
    ptfe_env = uniform_filter1d(ptfe_y, kern_1mm)
    ptfe_env_n = ptfe_env / ptfe_env.max()

    # Sample centre, edges
    cx = np.abs(ptfe_x)
    m_c = cx < 10
    m_30 = (cx > 25) & (cx < 35)
    m_60 = (cx > 55) & (cx < 65)
    print(f"  Centre (|x|<10mm): mean={ptfe_env_n[m_c].mean():.4f}")
    if m_30.sum(): print(f"  Ring (25-35mm):    mean={ptfe_env_n[m_30].mean():.4f}")
    if m_60.sum(): print(f"  Ring (55-65mm):    mean={ptfe_env_n[m_60].mean():.4f}")

    # Best model at these points
    best_s = 35.5  # from earlier analysis
    g_model = gaussian_grid_1d(ptfe_x, best_s)
    g_model_n = g_model / g_model.max()
    print(f"\n  Gaussian(σ={best_s}) model at same zones:")
    print(f"  Centre: {g_model_n[m_c].mean():.4f}")
    if m_30.sum(): print(f"  Ring 25-35mm: {g_model_n[m_30].mean():.4f}")
    if m_60.sum(): print(f"  Ring 55-65mm: {g_model_n[m_60].mean():.4f}")

    # Try a multi-row average instead of single row
    print("\n── MULTI-ROW AVERAGE (±50 rows around center) ──")
    band = 50
    r0 = max(0, ptfe_row - band)
    r1 = min(ptfe_g.shape[0], ptfe_row + band + 1)
    ptfe_band = ptfe_g[r0:r1, :].mean(axis=0)
    ptfe_band_n = ptfe_band / ptfe_band.max()

    best_s_band, best_r_band = 4, 1e9
    for sigma_t in np.arange(2, 80, 0.25):
        model = gaussian_grid_1d(ptfe_x, sigma_t)
        mn = model / model.max()
        rms = float(np.sqrt(np.mean((ptfe_band_n - mn)**2)))
        if rms < best_r_band:
            best_r_band = rms; best_s_band = sigma_t
    print(f"  Gaussian: σ={best_s_band:.2f}mm  RMS={best_r_band:.6f}")

    best_g_band, best_rl_band = 4, 1e9
    for gamma_t in np.arange(2, 80, 0.25):
        model = lorentzian_grid_1d(ptfe_x, gamma_t)
        mn = model / model.max()
        rms = float(np.sqrt(np.mean((ptfe_band_n - mn)**2)))
        if rms < best_rl_band:
            best_rl_band = rms; best_g_band = gamma_t
    print(f"  Lorentzian: γ={best_g_band:.2f}mm  RMS={best_rl_band:.6f}")

    # ── HEAVY smooth + band average → best possible fit ──
    print("\n── BEST-EFFORT FIT (band average + 501px smooth) ──")
    ptfe_best = uniform_filter1d(ptfe_band, 501)
    ptfe_best_n = ptfe_best / ptfe_best.max()
    for model_name, model_fn in [("Gaussian", gaussian_grid_1d), ("Lorentzian", lorentzian_grid_1d)]:
        best_p, best_r = 4, 1e9
        for param in np.arange(2, 80, 0.25):
            m = model_fn(ptfe_x, param)
            mn = m / m.max()
            rms = float(np.sqrt(np.mean((ptfe_best_n - mn)**2)))
            if rms < best_r:
                best_r = rms; best_p = param
        print(f"  {model_name}: param={best_p:.2f}mm  RMS={best_r:.6f}")
        # residual structure
        m = model_fn(ptfe_x, best_p)
        mn = m / m.max()
        res = ptfe_best_n - mn
        print(f"    Residual: mean={res.mean():.6f}, std={res.std():.6f}")
        print(f"    |x|<20 mean={res[np.abs(ptfe_x)<20].mean():+.6f}")
        print(f"    |x|>50 mean={res[np.abs(ptfe_x)>50].mean():+.6f}")

    # ── Write summary CSV of best-effort profiles ──
    import csv
    csv_path = Path(__file__).parent / "calibration_corrected.csv"
    g_m = gaussian_grid_1d(ptfe_x, best_s_band)
    g_mn = g_m / g_m.max()
    l_m = lorentzian_grid_1d(ptfe_x, best_g_band)
    l_mn = l_m / l_m.max()
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["x_mm","observed_band_norm","gauss_model","lorentz_model","resid_gauss","resid_lorentz"])
        for i in range(0, len(ptfe_x), 20):
            w.writerow([f"{ptfe_x[i]:.3f}", f"{ptfe_band_n[i]:.6f}",
                         f"{g_mn[i]:.6f}", f"{l_mn[i]:.6f}",
                         f"{ptfe_band_n[i]-g_mn[i]:.6f}", f"{ptfe_band_n[i]-l_mn[i]:.6f}"])
    print(f"\nWrote {csv_path}")
    print("Done.")


if __name__ == "__main__":
    main()
