#!/usr/bin/env python3
"""
F-number / pixel-grid optical modeling.

Simulates how focal length, aperture (f-number), and sensor resolution
affect the distribution of flux from extended and point sources across
a pixel grid. Produces three figures:

  1. Extended source grid  (focal length x f-number)
  2. Point source grid     (focal length x f-number)
  3. Resolution comparison (pixel count sweep at fixed optics)

Output PNGs are saved to ./output/ alongside this script.

Usage:
    python fnumber_pixel_grid.py [--show]
"""

import argparse
from pathlib import Path

import numpy as np
import matplotlib
import matplotlib.pyplot as plt


# ---------------------------------------------------------------------------
# Simulation helpers
# ---------------------------------------------------------------------------

def simulate_ext(focal_length, f_number, sensor_size=36.0, npix=200,
                 a_ang=0.02, b_ang=0.01):
    """Extended source: elliptical Gaussian angular profile."""
    D = focal_length / f_number
    A = np.pi * (D / 2) ** 2
    pixel_mm = sensor_size / npix
    x_mm = (np.arange(npix) - npix / 2) * pixel_mm
    y_mm = (np.arange(npix) - npix / 2) * pixel_mm
    X_mm, Y_mm = np.meshgrid(x_mm, y_mm)
    X_ang = X_mm / focal_length
    Y_ang = Y_mm / focal_length
    ell = np.exp(-((X_ang / a_ang) ** 2 + (Y_ang / b_ang) ** 2))
    dOmega = (pixel_mm / focal_length) ** 2
    ext = ell * dOmega * A
    return ext, D, pixel_mm


def simulate_star(focal_length, f_number, sensor_size=36.0, npix=200,
                  star_flux=1.0, sigma_pix=5):
    """Point source: Gaussian PSF scaled by collecting area."""
    D = focal_length / f_number
    A = np.pi * (D / 2) ** 2
    Xp, Yp = np.meshgrid(np.arange(npix) - npix / 2,
                          np.arange(npix) - npix / 2)
    psf = np.exp(-(Xp ** 2 + Yp ** 2) / (2 * sigma_pix ** 2))
    psf /= psf.sum()
    star = star_flux * A * psf
    pixel_mm = sensor_size / npix
    return star, D, pixel_mm


def label_arcsec(ax, pxmm, fl, npix, grid_step=20):
    """Overlay arcsecond scale and pixel grid on an axis."""
    pix_scale = pxmm / fl * 206265  # arcsec/pixel
    ticks = np.linspace(0, npix, 5)
    labels = [f'{(t - npix / 2) * pix_scale:.0f}"' for t in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, ha="center")
    ax.set_yticks(ticks)
    ax.set_yticklabels(labels, va="center")
    ax.set_xlabel("Arcsec", labelpad=8)
    ax.set_ylabel("Arcsec", labelpad=8)
    ax.tick_params(direction="out", length=4, pad=4)
    ax.grid(False)
    ax.set_xticks(np.arange(0, npix, grid_step), minor=True)
    ax.set_yticks(np.arange(0, npix, grid_step), minor=True)
    ax.grid(which="minor", color="white", linestyle="-",
            linewidth=0.5, alpha=0.3)
    center = npix / 2
    ax.axhline(center, color="white", linestyle="-", linewidth=0.5, alpha=0.3)
    ax.axvline(center, color="white", linestyle="-", linewidth=0.5, alpha=0.3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(show=False):
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)

    # Use non-interactive backend when not showing
    if not show:
        matplotlib.use("Agg")

    f_vals = [200, 400, 800]
    fn_vals = [8, 5, 4]
    grid_step = 20
    npix = 200

    # Precompute all combinations
    ext_data = {}
    star_data = {}
    for fl in f_vals:
        for fn in fn_vals:
            ext_data[(fl, fn)] = simulate_ext(fl, fn, npix=npix)
            star_data[(fl, fn)] = simulate_star(fl, fn, npix=npix)

    vmax_ext = max(e.max() for e, _, _ in ext_data.values())
    vmax_star = max(s.max() for s, _, _ in star_data.values())

    # ---- Figure 1: Extended source grid ----
    fig1, axs1 = plt.subplots(len(f_vals), len(fn_vals),
                              figsize=(12, 12), constrained_layout=True)
    for i, fl in enumerate(f_vals):
        for j, fn in enumerate(fn_vals):
            ext, D, pxmm = ext_data[(fl, fn)]
            ax = axs1[i, j]
            im = ax.imshow(ext, origin="lower", vmin=0, vmax=vmax_ext,
                           interpolation="nearest")
            ax.set_title(f"Ext\nf={fl}mm\nf/{int(fl / D)}\nD={D:.0f}mm",
                         pad=6)
            total = ext.sum()
            avg = total / (ext > ext.max() * 0.01).sum()
            ax.text(5, 10, f"Tot:{total:.1f}\nAvg:{avg:.4f}", color="white",
                    bbox=dict(facecolor="black", alpha=0.6), fontsize=9)
            label_arcsec(ax, pxmm, fl, npix, grid_step)
    fig1.suptitle("Extended Source: focal vs aperture (pixel grid only)",
                  y=1.02)
    fig1.colorbar(im, ax=axs1, orientation="vertical", fraction=0.02,
                  pad=0.01, label="Flux")
    fig1.savefig(out_dir / "extended_source_grid.png", dpi=150,
                 bbox_inches="tight")
    print(f"  Saved {out_dir / 'extended_source_grid.png'}")

    # ---- Figure 2: Point source grid ----
    fig2, axs2 = plt.subplots(len(f_vals), len(fn_vals),
                              figsize=(12, 12), constrained_layout=True)
    for i, fl in enumerate(f_vals):
        for j, fn in enumerate(fn_vals):
            star, D, pxmm = star_data[(fl, fn)]
            ax = axs2[i, j]
            im2 = ax.imshow(star, origin="lower", vmin=0, vmax=vmax_star,
                            interpolation="nearest")
            ax.set_title(f"Star\nf={fl}mm\nf/{int(fl / D)}\nD={D:.0f}mm",
                         pad=6)
            total = star.sum()
            avg = total / (star > star.max() * 0.01).sum()
            ax.text(5, 10, f"Tot:{total:.1f}\nAvg:{avg:.2f}", color="white",
                    bbox=dict(facecolor="black", alpha=0.6), fontsize=9)
            label_arcsec(ax, pxmm, fl, npix, grid_step)
    fig2.suptitle("Point Source: focal vs aperture (pixel grid only)", y=1.02)
    fig2.colorbar(im2, ax=axs2, orientation="vertical", fraction=0.02,
                  pad=0.01, label="Flux")
    fig2.savefig(out_dir / "point_source_grid.png", dpi=150,
                 bbox_inches="tight")
    print(f"  Saved {out_dir / 'point_source_grid.png'}")

    # ---- Figure 3: Resolution sweep ----
    res_vals = [25, 50, 100]
    fig3, axs3 = plt.subplots(1, len(res_vals), figsize=(12, 4),
                              constrained_layout=True)
    for ax, res in zip(axs3, res_vals):
        ext, D, pxmm = simulate_ext(400, 5, npix=res)
        im3 = ax.imshow(ext, origin="lower", vmin=0, vmax=ext.max(),
                        interpolation="nearest")
        ax.set_title(f"Res={res}px\nf=400mm\nf/5\nD={D:.0f}mm", pad=6)
        total = ext.sum()
        avg = total / (ext > ext.max() * 0.01).sum()
        ax.text(2, 3, f"Tot:{total:.1f}\nAvg:{avg:.4f}", color="white",
                bbox=dict(facecolor="black", alpha=0.6), fontsize=9)
        label_arcsec(ax, pxmm, 400, res, grid_step=max(1, res // 5))
        fig3.colorbar(im3, ax=ax, orientation="vertical", fraction=0.046,
                      pad=0.02, label="Flux")
    fig3.suptitle("Resolution Sweep: normalized per panel (pixel grid only)",
                  y=1.05)
    fig3.savefig(out_dir / "resolution_sweep.png", dpi=150,
                 bbox_inches="tight")
    print(f"  Saved {out_dir / 'resolution_sweep.png'}")

    if show:
        plt.show()
    else:
        plt.close("all")

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="F-number / pixel-grid optical modeling")
    parser.add_argument("--show", action="store_true",
                        help="Display plots interactively (default: save only)")
    args = parser.parse_args()
    main(show=args.show)
