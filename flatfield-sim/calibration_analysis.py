"""
Offline calibration analysis — replicates the browser engine in Python
so we can inspect the fit numerically and decide on model improvements.

Outputs:
  calibration_report.csv   – full observed/model/residual profiles
  calibration_summary.txt  – fit statistics, pitch validation, diagnostics
"""

import numpy as np
from PIL import Image
from pathlib import Path
import csv, textwrap

# ── Constants (must match OpticalSimulator.tsx) ──────────────────
LED_PITCH    = 15       # mm
LED_COLS     = 13
LED_ROWS     = 9
DEFAULT_SIGMA = 20      # mm

# ImageJ-measured scales
LS_UM_PX   = 14.58     # Lightsource.JPG
PTFE_UM_PX = 17.30     # PTFE.JPG

IMG_DIR = Path(__file__).parent / "public" / "calibration_images"


def linearize_srgb(v: np.ndarray) -> np.ndarray:
    """Inverse sRGB gamma (simplified γ=2.2 power law)."""
    return np.power(v, 2.2)


def extract_profile(img_path: Path, um_per_px: float) -> tuple[np.ndarray, np.ndarray]:
    """Load image, find brightest row (green ch, linearized), return (x_mm, intensity)."""
    img = np.array(Image.open(img_path))
    green = img[:, :, 1].astype(np.float64) / 255.0
    green_lin = linearize_srgb(green)

    row_sums = green_lin.sum(axis=1)
    best_row = int(np.argmax(row_sums))

    profile = green_lin[best_row, :]
    w = len(profile)
    mm_per_px = um_per_px / 1000.0
    x_mm = (np.arange(w) - w / 2) * mm_per_px

    return x_mm, profile


def find_peaks(x: np.ndarray, y: np.ndarray, min_sep_mm: float) -> np.ndarray:
    """Simple peak finder with minimum separation."""
    threshold = y.max() * 0.25
    peaks = []
    for i in range(2, len(y) - 2):
        if (y[i] > threshold and
            y[i] >= y[i-1] and y[i] >= y[i+1] and
            y[i] >= y[i-2] and y[i] >= y[i+2]):
            if len(peaks) == 0 or (x[i] - x[peaks[-1]]) > min_sep_mm:
                peaks.append(i)
    return np.array(peaks)


def measure_pitch(x: np.ndarray, y: np.ndarray):
    peak_idxs = find_peaks(x, y, LED_PITCH * 0.5)
    positions = x[peak_idxs]
    if len(positions) < 2:
        return 0.0, positions
    diffs = np.diff(positions)
    return float(diffs.mean()), positions


def gaussian_grid_model(x_mm: np.ndarray, sigma: float) -> np.ndarray:
    """Sum of Gaussians from LED_ROWS × LED_COLS grid evaluated along 1-D slice."""
    rc = (LED_ROWS - 1) / 2
    cc = (LED_COLS - 1) / 2
    val = np.zeros_like(x_mm, dtype=np.float64)
    for r in range(LED_ROWS):
        for c in range(LED_COLS):
            lx = (c - cc) * LED_PITCH
            ly = (r - rc) * LED_PITCH
            val += np.exp(-((x_mm - lx)**2 + ly**2) / (2 * sigma**2))
    return val


def fit_sigma_bruteforce(x: np.ndarray, obs_norm: np.ndarray,
                          lo=2.0, hi=80.0, step=0.25):
    """Brute-force RMS minimisation over sigma (matches the browser engine)."""
    best_sigma, best_rms = DEFAULT_SIGMA, np.inf
    results = []
    for sigma in np.arange(lo, hi + step, step):
        model = gaussian_grid_model(x, sigma)
        model_norm = model / model.max()
        rms = float(np.sqrt(np.mean((obs_norm - model_norm)**2)))
        results.append((sigma, rms))
        if rms < best_rms:
            best_rms = rms
            best_sigma = sigma
    return best_sigma, best_rms, results


def fit_sigma_scipy(x: np.ndarray, obs_norm: np.ndarray, guess: float):
    """Refine with scipy.optimize.minimize_scalar for sub-step precision."""
    from scipy.optimize import minimize_scalar

    def cost(sigma):
        model = gaussian_grid_model(x, sigma)
        mn = model / model.max()
        return float(np.sqrt(np.mean((obs_norm - mn)**2)))

    res = minimize_scalar(cost, bounds=(max(1, guess - 3), guess + 3), method='bounded')
    return res.x, res.fun


# ── Alternative models ───────────────────────────────────────────

def lorentzian_grid_model(x_mm: np.ndarray, gamma: float) -> np.ndarray:
    """Lorentzian (Cauchy) PSF — heavier tails than Gaussian."""
    rc = (LED_ROWS - 1) / 2
    cc = (LED_COLS - 1) / 2
    val = np.zeros_like(x_mm, dtype=np.float64)
    for r in range(LED_ROWS):
        for c in range(LED_COLS):
            lx = (c - cc) * LED_PITCH
            ly = (r - rc) * LED_PITCH
            d2 = (x_mm - lx)**2 + ly**2
            val += 1.0 / (1.0 + d2 / gamma**2)
    return val


def voigt_approx_grid_model(x_mm: np.ndarray, sigma: float, gamma: float) -> np.ndarray:
    """Pseudo-Voigt approximate: eta*Lorentzian + (1-eta)*Gaussian with
    eta estimated from sigma/gamma ratio (Thompson-Cox-Hastings)."""
    f_G = 2 * sigma * np.sqrt(2 * np.log(2))
    f_L = 2 * gamma
    f = (f_G**5 + 2.69269*f_G**4*f_L + 2.42843*f_G**3*f_L**2 +
         4.47163*f_G**2*f_L**3 + 0.07842*f_G*f_L**4 + f_L**5) ** 0.2
    eta = 1.36603*(f_L/f) - 0.47719*(f_L/f)**2 + 0.11116*(f_L/f)**3
    eta = np.clip(eta, 0, 1)

    rc = (LED_ROWS - 1) / 2
    cc = (LED_COLS - 1) / 2
    val = np.zeros_like(x_mm, dtype=np.float64)
    for r in range(LED_ROWS):
        for c in range(LED_COLS):
            lx = (c - cc) * LED_PITCH
            ly = (r - rc) * LED_PITCH
            d2 = (x_mm - lx)**2 + ly**2
            gauss = np.exp(-d2 / (2 * sigma**2))
            lorentz = 1.0 / (1.0 + d2 / gamma**2)
            val += eta * lorentz + (1 - eta) * gauss
    return val


def fit_lorentzian(x, obs_norm, lo=2, hi=80, step=0.25):
    best_gamma, best_rms = 20.0, np.inf
    for gamma in np.arange(lo, hi + step, step):
        model = lorentzian_grid_model(x, gamma)
        mn = model / model.max()
        rms = float(np.sqrt(np.mean((obs_norm - mn)**2)))
        if rms < best_rms:
            best_rms = rms; best_gamma = gamma
    return best_gamma, best_rms


def fit_voigt(x, obs_norm):
    """2-D grid search over sigma and gamma."""
    from scipy.optimize import minimize
    def cost(params):
        s, g = params
        if s < 1 or g < 1:
            return 1e6
        model = voigt_approx_grid_model(x, s, g)
        mn = model / model.max()
        return float(np.sqrt(np.mean((obs_norm - mn)**2)))
    res = minimize(cost, [15, 15], bounds=[(1, 80), (1, 80)], method='L-BFGS-B')
    return res.x[0], res.x[1], res.fun


# ── Main ─────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("FLAT-FIELD CALIBRATION ANALYSIS")
    print("=" * 60)

    # 1. Load images
    ls_path   = IMG_DIR / "Lightsource.JPG"
    ptfe_path = IMG_DIR / "PTFE.JPG"
    if not ls_path.exists() or not ptfe_path.exists():
        print(f"ERROR: calibration images not found in {IMG_DIR}")
        return

    print(f"\nLoading {ls_path.name} ({LS_UM_PX} µm/px) ...")
    ls_x, ls_y = extract_profile(ls_path, LS_UM_PX)
    print(f"  Profile length: {len(ls_x)} px, x-range: [{ls_x[0]:.1f}, {ls_x[-1]:.1f}] mm")

    print(f"Loading {ptfe_path.name} ({PTFE_UM_PX} µm/px) ...")
    ptfe_x, ptfe_y = extract_profile(ptfe_path, PTFE_UM_PX)
    print(f"  Profile length: {len(ptfe_x)} px, x-range: [{ptfe_x[0]:.1f}, {ptfe_x[-1]:.1f}] mm")

    # 2. LED pitch validation
    pitch, peak_pos = measure_pitch(ls_x, ls_y)
    print(f"\n── LED PITCH VALIDATION ──")
    print(f"  Peaks found: {len(peak_pos)}")
    print(f"  Measured pitch: {pitch:.3f} mm  (expected {LED_PITCH} mm)")
    print(f"  Deviation: {abs(pitch - LED_PITCH):.3f} mm ({abs(pitch - LED_PITCH)/LED_PITCH*100:.1f}%)")

    # 3. Normalise PTFE profile
    ptfe_norm = ptfe_y / ptfe_y.max()

    # 4. Gaussian fit (brute-force, matching browser engine)
    print(f"\n── GAUSSIAN FIT (brute-force, 0.25mm steps) ──")
    g_sigma, g_rms, g_sweep = fit_sigma_bruteforce(ptfe_x, ptfe_norm)
    print(f"  Best σ: {g_sigma:.2f} mm")
    print(f"  RMS error: {g_rms:.6f}")

    # Refine with scipy
    g_sigma_fine, g_rms_fine = fit_sigma_scipy(ptfe_x, ptfe_norm, g_sigma)
    print(f"  Refined σ: {g_sigma_fine:.3f} mm  (RMS {g_rms_fine:.6f})")

    # 5. Lorentzian fit
    print(f"\n── LORENTZIAN FIT ──")
    l_gamma, l_rms = fit_lorentzian(ptfe_x, ptfe_norm)
    print(f"  Best γ: {l_gamma:.2f} mm")
    print(f"  RMS error: {l_rms:.6f}")

    # 6. Pseudo-Voigt fit
    print(f"\n── PSEUDO-VOIGT FIT (σ + γ) ──")
    v_sigma, v_gamma, v_rms = fit_voigt(ptfe_x, ptfe_norm)
    f_G = 2 * v_sigma * np.sqrt(2 * np.log(2))
    f_L = 2 * v_gamma
    f = (f_G**5 + 2.69269*f_G**4*f_L + 2.42843*f_G**3*f_L**2 +
         4.47163*f_G**2*f_L**3 + 0.07842*f_G*f_L**4 + f_L**5) ** 0.2
    eta = 1.36603*(f_L/f) - 0.47719*(f_L/f)**2 + 0.11116*(f_L/f)**3
    print(f"  σ: {v_sigma:.3f} mm,  γ: {v_gamma:.3f} mm")
    print(f"  Mixing ratio η: {eta:.4f}  (0=pure Gaussian, 1=pure Lorentzian)")
    print(f"  RMS error: {v_rms:.6f}")

    # 7. Model comparison summary
    print(f"\n── MODEL COMPARISON ──")
    print(f"  {'Model':<20} {'Params':>6} {'RMS':>10} {'Improvement':>14}")
    print(f"  {'-'*50}")
    models = [
        ("Gaussian",       1, g_rms_fine),
        ("Lorentzian",     1, l_rms),
        ("Pseudo-Voigt",   2, v_rms),
    ]
    for name, npar, rms in models:
        imp = (g_rms_fine - rms) / g_rms_fine * 100 if rms < g_rms_fine else 0
        print(f"  {name:<20} {npar:>6} {rms:>10.6f} {imp:>13.1f}%")

    # 8. Residual structure analysis
    best_model_name = min(models, key=lambda m: m[2])[0]
    if best_model_name == "Gaussian":
        best_model = gaussian_grid_model(ptfe_x, g_sigma_fine)
    elif best_model_name == "Lorentzian":
        best_model = lorentzian_grid_model(ptfe_x, l_gamma)
    else:
        best_model = voigt_approx_grid_model(ptfe_x, v_sigma, v_gamma)
    best_model_norm = best_model / best_model.max()
    residual = ptfe_norm - best_model_norm

    # Compute spatial structure of residual
    center_mask = np.abs(ptfe_x) < 30  # within ±30mm of center
    edge_mask = np.abs(ptfe_x) > 60    # edges
    mid_mask = (~center_mask) & (~edge_mask)

    rms_center = np.sqrt(np.mean(residual[center_mask]**2)) if center_mask.sum() > 0 else 0
    rms_mid    = np.sqrt(np.mean(residual[mid_mask]**2)) if mid_mask.sum() > 0 else 0
    rms_edge   = np.sqrt(np.mean(residual[edge_mask]**2)) if edge_mask.sum() > 0 else 0
    mean_center = float(np.mean(residual[center_mask])) if center_mask.sum() > 0 else 0
    mean_edge   = float(np.mean(residual[edge_mask])) if edge_mask.sum() > 0 else 0

    print(f"\n── RESIDUAL SPATIAL STRUCTURE (best model: {best_model_name}) ──")
    print(f"  Center (|x|<30mm): RMS={rms_center:.6f}, mean={mean_center:+.6f}")
    print(f"  Mid (30-60mm):     RMS={rms_mid:.6f}")
    print(f"  Edge (|x|>60mm):   RMS={rms_edge:.6f}, mean={mean_edge:+.6f}")
    if mean_center > 0 and mean_edge < 0:
        print(f"  → Model UNDER-estimates center, OVER-estimates edges (narrower than model)")
    elif mean_center < 0 and mean_edge > 0:
        print(f"  → Model OVER-estimates center, UNDER-estimates edges (wider tails than model)")

    # Periodicity in residual (ripple remnants)
    from numpy.fft import rfft, rfftfreq
    active = np.abs(ptfe_x) < 80
    res_active = residual[active]
    dx = float(np.median(np.diff(ptfe_x[active])))
    fft_mag = np.abs(rfft(res_active - res_active.mean()))
    freqs = rfftfreq(len(res_active), d=dx)
    # find dominant non-DC frequency
    fft_mag[0] = 0  # ignore DC
    dom_idx = np.argmax(fft_mag)
    dom_freq = freqs[dom_idx]
    dom_period = 1.0 / dom_freq if dom_freq > 0 else np.inf

    print(f"\n── RESIDUAL PERIODICITY ──")
    print(f"  Dominant spatial frequency: {dom_freq:.4f} /mm")
    print(f"  Dominant period: {dom_period:.2f} mm  (LED pitch = {LED_PITCH} mm)")
    if abs(dom_period - LED_PITCH) < 3:
        print(f"  → Residual is dominated by LED-pitch ripple — model smooths too much or too little")
    else:
        print(f"  → Residual NOT correlated with LED pitch — likely a shape (envelope) mismatch")

    # 9. Write full CSV
    csv_path = Path(__file__).parent / "calibration_report.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["x_mm", "observed_norm", "gaussian_model", "lorentzian_model",
                     "voigt_model", "resid_gauss", "resid_lorentz", "resid_voigt"])
        gauss_full = gaussian_grid_model(ptfe_x, g_sigma_fine)
        gauss_full_n = gauss_full / gauss_full.max()
        lor_full = lorentzian_grid_model(ptfe_x, l_gamma)
        lor_full_n = lor_full / lor_full.max()
        voigt_full = voigt_approx_grid_model(ptfe_x, v_sigma, v_gamma)
        voigt_full_n = voigt_full / voigt_full.max()
        # Downsample for tractable CSV (every 10th pixel)
        step = 10
        for i in range(0, len(ptfe_x), step):
            w.writerow([
                f"{ptfe_x[i]:.4f}",
                f"{ptfe_norm[i]:.6f}",
                f"{gauss_full_n[i]:.6f}",
                f"{lor_full_n[i]:.6f}",
                f"{voigt_full_n[i]:.6f}",
                f"{ptfe_norm[i] - gauss_full_n[i]:.6f}",
                f"{ptfe_norm[i] - lor_full_n[i]:.6f}",
                f"{ptfe_norm[i] - voigt_full_n[i]:.6f}",
            ])
    print(f"\n✓ Wrote {csv_path}")

    # 10. Write summary file
    summary_path = Path(__file__).parent / "calibration_summary.txt"
    with open(summary_path, "w") as f:
        f.write(textwrap.dedent(f"""\
        FLAT-FIELD CALIBRATION SUMMARY
        ==============================
        Date: 2026-02-14
        Images: Lightsource.JPG ({LS_UM_PX} µm/px), PTFE.JPG ({PTFE_UM_PX} µm/px)
        
        LED PITCH VALIDATION
          Peaks found: {len(peak_pos)}
          Measured: {pitch:.3f} mm  (expected {LED_PITCH} mm, dev {abs(pitch-LED_PITCH):.3f} mm)
        
        MODEL FIT COMPARISON
          Gaussian    σ={g_sigma_fine:.3f}mm             RMS={g_rms_fine:.6f}
          Lorentzian  γ={l_gamma:.3f}mm                  RMS={l_rms:.6f}
          Voigt       σ={v_sigma:.3f}mm  γ={v_gamma:.3f}mm  RMS={v_rms:.6f}  η={eta:.4f}
        
        BEST MODEL: {best_model_name}
        
        RESIDUAL SPATIAL STRUCTURE
          Center (|x|<30mm): RMS={rms_center:.6f}  mean={mean_center:+.6f}
          Mid (30-60mm):     RMS={rms_mid:.6f}
          Edge (|x|>60mm):   RMS={rms_edge:.6f}  mean={mean_edge:+.6f}
        
        RESIDUAL DOMINANT PERIOD: {dom_period:.2f} mm  (LED={LED_PITCH}mm)
        """))
    print(f"✓ Wrote {summary_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
