#!/usr/bin/env python3
"""Lightweight Mandel-Agol style numeric transit fitter."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Tuple

import numpy as np


@dataclass
class FitResult:
    yhat: np.ndarray
    rprs: float
    impact_parameter: float
    mid_offset_min: float
    chi2: float
    baseline_coeff: Tuple[float, float]
    t0_jd: float


def _build_star_grid(samples: int = 301, u1: float = 0.45, u2: float = 0.25) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    lim = 1.2
    x = np.linspace(-lim, lim, samples)
    y = np.linspace(-lim, lim, samples)
    xx, yy = np.meshgrid(x, y)
    r2 = xx ** 2 + yy ** 2
    inside = r2 <= 1.0
    mu = np.zeros_like(r2)
    mu[inside] = np.sqrt(1.0 - r2[inside])
    intensity = np.zeros_like(r2)
    intensity[inside] = 1.0 - u1 * (1.0 - mu[inside]) - u2 * (1.0 - mu[inside]) ** 2
    intensity[intensity < 0] = 0.0
    intensity /= intensity.sum()
    return xx, yy, intensity


_STAR_GRID = _build_star_grid()


@lru_cache(maxsize=64)
def _precompute_profile(rprs: float, nz: int = 240) -> Tuple[np.ndarray, np.ndarray]:
    xx, yy, intensity = _STAR_GRID
    z_max = 1.0 + rprs + 0.5
    z_values = np.linspace(0.0, z_max, nz)
    profile = np.empty_like(z_values)
    for idx, z in enumerate(z_values):
        occulter = ((xx - z) ** 2 + yy ** 2) <= rprs ** 2
        profile[idx] = 1.0 - intensity[occulter].sum()
    return z_values, profile


def _model_flux(time_jd: np.ndarray, t0_jd: float, rprs: float, impact_b: float, t_scale: float, ztab: np.ndarray, profile: np.ndarray) -> np.ndarray:
    separation = np.sqrt(np.maximum(impact_b ** 2 + ((time_jd - t0_jd) / t_scale) ** 2, 0.0))
    clipped = np.clip(separation, ztab[0], ztab[-1])
    return np.interp(clipped, ztab, profile)


def fit_numeric_ma(
    time_jd: np.ndarray,
    flux: np.ndarray,
    sigma: np.ndarray,
    ingress_jd: float,
    mid_jd: float,
    egress_jd: float,
    rprs_grid: Iterable[float] | None = None,
    impact_grid: Iterable[float] | None = None,
    delta_t_minutes: float = 20.0,
) -> FitResult:
    if time_jd.size == 0:
        raise ValueError("Cannot fit empty light curve")

    rprs_grid = tuple(rprs_grid or np.linspace(0.11, 0.15, 21))
    impact_grid = tuple(impact_grid or np.linspace(0.0, 0.85, 18))
    half_duration = (egress_jd - ingress_jd) / 2.0
    mid_guess = 0.5 * (ingress_jd + egress_jd)
    delta_days = delta_t_minutes / 1440.0
    t0_grid = np.linspace(mid_guess - delta_days, mid_guess + delta_days, 61)

    weights = np.ones_like(flux)
    valid_sigma = np.isfinite(sigma) & (sigma > 0)
    weights[valid_sigma] = 1.0 / (sigma[valid_sigma] ** 2)

    best = None
    best_chi2 = np.inf

    for rprs in rprs_grid:
        ztab, profile = _precompute_profile(rprs)
        for impact in impact_grid:
            if impact >= 1.0 + rprs:
                continue
            denominator = max(((1.0 + rprs) ** 2) - impact ** 2, 1e-8)
            t_scale = half_duration / np.sqrt(denominator)
            for t0 in t0_grid:
                model = _model_flux(time_jd, t0, rprs, impact, t_scale, ztab, profile)
                design = np.vstack([np.ones_like(time_jd), time_jd]).T
                sqrt_w = np.sqrt(weights)
                A = design * sqrt_w[:, None]
                b = (flux / np.maximum(model, 1e-6)) * sqrt_w
                coeff, *_ = np.linalg.lstsq(A, b, rcond=None)
                baseline = design @ coeff
                yhat = baseline * model
                chi2 = float(np.sum(weights * (flux - yhat) ** 2))
                if chi2 < best_chi2:
                    best_chi2 = chi2
                    best = (rprs, impact, t0, coeff, yhat)

    if best is None:
        raise RuntimeError("Transit fit search exhausted without solution")

    rprs, impact, t0, coeff, yhat = best
    offset_minutes = float((t0 - mid_jd) * 1440.0)
    return FitResult(
        yhat=yhat,
        rprs=float(rprs),
        impact_parameter=float(impact),
        mid_offset_min=offset_minutes,
        chi2=best_chi2,
        baseline_coeff=(float(coeff[0]), float(coeff[1])),
        t0_jd=float(t0),
    )