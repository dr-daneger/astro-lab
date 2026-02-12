"""
Per-frame star analysis using SEP (Source Extractor for Python).

Extracts per-star metrics (HFR, FWHM, eccentricity) from FITS images
and aggregates them to characterize image quality.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
import numpy as np

try:
    import sep
    SEP_AVAILABLE = True
except ImportError:
    SEP_AVAILABLE = False

try:
    from scipy.spatial import cKDTree
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False

from astropy.io import fits


@dataclass
class StarMetrics:
    """Metrics for a single detected star."""
    x: float                # X position (pixels)
    y: float                # Y position (pixels)
    hfr: float              # Half Flux Radius (pixels)
    fwhm: float             # Full Width Half Maximum (pixels)
    eccentricity: float     # a/b axis ratio (1.0 = perfectly round)
    theta: float            # Elongation angle (degrees, 0-180)
    flux: float             # Total flux (ADU)
    snr: float              # Signal-to-noise ratio


@dataclass
class FrameStarStats:
    """Aggregated star statistics for a single frame."""
    filename: str
    timestamp: Optional[datetime] = None
    filter_name: str = ""
    num_stars: int = 0
    median_hfr: float = 0.0
    std_hfr: float = 0.0
    median_fwhm: float = 0.0
    std_fwhm: float = 0.0
    median_eccentricity: float = 0.0
    std_eccentricity: float = 0.0
    mean_theta: float = 0.0         # Average elongation direction
    theta_std: float = 0.0          # Spread of angles (wind vs PE indicator)
    guide_rms: float = 0.0          # Matched from guiding data
    stars: List[StarMetrics] = field(default_factory=list)
    # New quality metrics
    relative_flux: float = 1.0      # Ratio to session median flux (cloud detection)
    hfr_uniformity: float = 0.0     # std_hfr / median_hfr (non-uniform seeing)
    tracking_type: str = "good"     # "good", "wind", or "mechanical"


@dataclass
class TrackingDiagnosis:
    """Diagnosis of tracking error type based on star shapes."""
    error_type: str   # "wind", "mechanical", "good"
    confidence: float # 0-1 confidence in diagnosis


@dataclass 
class FilterBaseline:
    """Per-filter baseline metrics for quality comparison."""
    filter_name: str
    hfr_baseline: float = 0.0
    hfr_std: float = 0.0
    ecc_baseline: float = 1.0
    ecc_std: float = 0.0
    flux_baseline: float = 0.0
    flux_std: float = 0.0
    num_frames: int = 0


@dataclass
class FrameFlags:
    """Pass/fail flags for frame quality."""
    hfr_flag: bool = False          # HFR > baseline + 3*std
    ecc_flag: bool = False          # Eccentricity > baseline + 3*std
    flux_flag: bool = False         # Relative flux < 0.8 (clouds)
    uniformity_flag: bool = False   # HFR uniformity > threshold
    tracking_type: str = "good"     # "good", "wind", "mechanical"
    
    @property
    def is_bad(self) -> bool:
        """True if any critical flag is set."""
        return any([self.hfr_flag, self.ecc_flag, self.flux_flag])


def sigma_clipped_stats(values: List[float], sigma: float = 3.0, max_iters: int = 3) -> Tuple[float, float]:
    """
    Compute sigma-clipped median and standard deviation.
    
    Args:
        values: List of values to analyze
        sigma: Number of sigma for clipping
        max_iters: Maximum clipping iterations
        
    Returns:
        (median, std) after sigma clipping
    """
    if not values:
        return 0.0, 0.0
    
    arr = np.array(values)
    for _ in range(max_iters):
        if len(arr) < 3:
            break
        med = np.median(arr)
        std = np.std(arr)
        if std <= 0:
            break
        mask = np.abs(arr - med) < sigma * std
        if np.sum(mask) < 3:
            break
        arr = arr[mask]
    
    return float(np.median(arr)), float(np.std(arr))


def diagnose_tracking(eccentricity: float, theta_std: float) -> TrackingDiagnosis:
    """
    Diagnose tracking error type based on star shape characteristics.
    
    - Good: eccentricity < 1.15
    - Mechanical (PE/flexure): high eccentricity + low theta_std (consistent direction)
    - Wind: high eccentricity + high theta_std (random direction)
    
    Args:
        eccentricity: Median star eccentricity (1.0 = round)
        theta_std: Standard deviation of elongation angles (degrees)
        
    Returns:
        TrackingDiagnosis with error type and confidence
    """
    if eccentricity < 1.15:
        return TrackingDiagnosis("good", 1.0)
    
    # High ecc + low theta_std = consistent direction = mechanical (PE/flexure)
    # High ecc + high theta_std = random direction = wind
    if theta_std < 20:  # degrees
        confidence = min(1.0, (eccentricity - 1.15) / 0.3)
        return TrackingDiagnosis("mechanical", confidence)
    else:
        confidence = min(1.0, theta_std / 45)
        return TrackingDiagnosis("wind", confidence)


def compute_relative_flux(stars: List[StarMetrics], session_median_flux: float) -> float:
    """
    Compute relative flux metric for cloud detection.
    
    Uses middle 33% brightest stars to avoid saturated/noisy extremes.
    Values < 0.8 suggest cloud dimming.
    
    Args:
        stars: List of star metrics for this frame
        session_median_flux: Median flux across all frames in session
        
    Returns:
        Ratio of frame median flux to session median (1.0 = normal)
    """
    if not stars or session_median_flux <= 0:
        return 1.0
    
    fluxes = sorted([s.flux for s in stars], reverse=True)
    n = len(fluxes)
    
    # Middle 33%: from 33rd to 66th percentile of brightness
    start = n // 3
    end = 2 * n // 3
    
    if start >= end:
        # Not enough stars, use all
        middle_fluxes = fluxes
    else:
        middle_fluxes = fluxes[start:end]
    
    if not middle_fluxes:
        return 1.0
    
    frame_median = np.median(middle_fluxes)
    return float(frame_median / session_median_flux)


def compute_filter_baselines(frame_stats: List['FrameStarStats']) -> dict:
    """
    Compute per-filter baseline metrics using sigma-clipped statistics.
    
    Args:
        frame_stats: List of FrameStarStats from star analysis
        
    Returns:
        Dict mapping filter_name to FilterBaseline
    """
    from collections import defaultdict
    
    by_filter = defaultdict(list)
    for f in frame_stats:
        if f.median_hfr > 0:
            by_filter[f.filter_name].append(f)
    
    baselines = {}
    for filter_name, frames in by_filter.items():
        hfr_vals = [f.median_hfr for f in frames]
        ecc_vals = [f.median_eccentricity for f in frames]
        
        # Compute flux from middle 33% of each frame's stars
        flux_vals = []
        for f in frames:
            if f.stars:
                fluxes = sorted([s.flux for s in f.stars], reverse=True)
                n = len(fluxes)
                start, end = n // 3, 2 * n // 3
                if start < end:
                    flux_vals.append(np.median(fluxes[start:end]))
                elif fluxes:
                    flux_vals.append(np.median(fluxes))
        
        hfr_med, hfr_std = sigma_clipped_stats(hfr_vals)
        ecc_med, ecc_std = sigma_clipped_stats(ecc_vals)
        flux_med, flux_std = sigma_clipped_stats(flux_vals)
        
        baselines[filter_name] = FilterBaseline(
            filter_name=filter_name,
            hfr_baseline=hfr_med,
            hfr_std=hfr_std,
            ecc_baseline=ecc_med,
            ecc_std=ecc_std,
            flux_baseline=flux_med,
            flux_std=flux_std,
            num_frames=len(frames)
        )
    
    return baselines


def flag_frame(frame: 'FrameStarStats', baseline: FilterBaseline) -> FrameFlags:
    """
    Compute pass/fail flags for a frame against its filter baseline.
    
    Uses 3-sigma UCL for HFR and eccentricity, 0.8 threshold for flux.
    
    Args:
        frame: FrameStarStats to evaluate
        baseline: FilterBaseline for this frame's filter
        
    Returns:
        FrameFlags with pass/fail status
    """
    # Compute UCLs (upper control limits)
    hfr_ucl = baseline.hfr_baseline + 3 * baseline.hfr_std if baseline.hfr_std > 0 else float('inf')
    ecc_ucl = baseline.ecc_baseline + 3 * baseline.ecc_std if baseline.ecc_std > 0 else float('inf')
    
    # Diagnose tracking
    tracking = diagnose_tracking(frame.median_eccentricity, frame.theta_std)
    
    return FrameFlags(
        hfr_flag=frame.median_hfr > hfr_ucl,
        ecc_flag=frame.median_eccentricity > ecc_ucl,
        flux_flag=frame.relative_flux < 0.8,
        uniformity_flag=frame.hfr_uniformity > 0.3,
        tracking_type=tracking.error_type
    )


def calculate_hfr(data: np.ndarray, x: float, y: float, 
                  max_radius: float = 25.0) -> float:
    """
    Calculate Half Flux Radius - the radius containing 50% of total flux.
    
    Uses sigma-clipped background estimation and cumulative flux method
    to find the radius where cumulative flux reaches 50%.
    
    Args:
        data: Background-subtracted image data
        x, y: Star centroid position
        max_radius: Maximum aperture radius in pixels
        
    Returns:
        HFR in pixels
    """
    x_int, y_int = int(round(x)), int(round(y))
    r_int = int(max_radius) + 1
    
    # Extract region around star
    x_min = max(0, x_int - r_int)
    x_max = min(data.shape[1], x_int + r_int + 1)
    y_min = max(0, y_int - r_int)
    y_max = min(data.shape[0], y_int + r_int + 1)
    
    if x_max - x_min < 5 or y_max - y_min < 5:
        return 0.0
    
    subdata = data[y_min:y_max, x_min:x_max].copy()
    
    # Calculate distances from centroid
    local_y, local_x = np.ogrid[:subdata.shape[0], :subdata.shape[1]]
    cx, cy = x - x_min, y - y_min
    distances = np.sqrt((local_x - cx)**2 + (local_y - cy)**2)
    
    # Mask to aperture
    mask = distances <= max_radius
    if not np.any(mask):
        return 0.0
    
    # Background: sigma-clipped median of outer annulus (70-100% of aperture)
    outer_mask = (distances > max_radius * 0.7) & (distances <= max_radius)
    if np.any(outer_mask):
        outer_values = subdata[outer_mask].copy()
        # Iterative 3-sigma clipping to reject contaminating pixels (nearby stars, cosmic rays)
        for _ in range(3):
            if len(outer_values) < 10:
                break
            median = np.median(outer_values)
            std = np.std(outer_values)
            if std <= 0:
                break
            clip_mask = np.abs(outer_values - median) < 3 * std
            if np.sum(clip_mask) < 10:
                break
            outer_values = outer_values[clip_mask]
        background = np.median(outer_values)
    else:
        background = np.median(subdata[~mask]) if np.any(~mask) else 0
    
    # Subtract background, clip negatives
    flux_data = np.maximum(subdata - background, 0)
    
    # Apply mask
    masked_flux = flux_data[mask]
    masked_dist = distances[mask]
    
    total_flux = np.sum(masked_flux)
    if total_flux <= 0:
        return 0.0
    
    # Sort by distance and find cumulative flux
    sort_idx = np.argsort(masked_dist)
    sorted_flux = masked_flux[sort_idx]
    sorted_dist = masked_dist[sort_idx]
    
    cumsum = np.cumsum(sorted_flux)
    half_flux = total_flux / 2.0
    
    # Find radius where cumulative flux >= 50%
    idx = np.searchsorted(cumsum, half_flux)
    if idx >= len(sorted_dist):
        return float(sorted_dist[-1])
    
    return float(sorted_dist[idx])


def analyze_frame(fits_path: Path, 
                  detection_threshold: float = 3.0,
                  min_area: int = 5,
                  max_stars: int = 500,
                  filter_bright_fraction: float = 0.5) -> FrameStarStats:
    """
    Run SEP source extraction on a FITS frame and compute star metrics.
    
    Args:
        fits_path: Path to FITS file
        detection_threshold: Detection threshold in sigma above background
        min_area: Minimum area in pixels for a valid source
        max_stars: Maximum number of stars to analyze (brightest)
        filter_bright_fraction: Only use stars brighter than this percentile
        
    Returns:
        FrameStarStats with aggregated metrics
    """
    if not SEP_AVAILABLE:
        raise ImportError("sep package not installed. Run: pip install sep")
    
    result = FrameStarStats(filename=fits_path.name)
    
    try:
        with fits.open(fits_path) as hdul:
            # Find image data
            data = None
            header = None
            for hdu in hdul:
                if hdu.data is not None and len(hdu.data.shape) == 2:
                    data = hdu.data.astype(np.float64)
                    header = hdu.header
                    break
            
            if data is None:
                return result
            
            # Extract metadata from header
            if header:
                # Timestamp
                date_obs = header.get('DATE-OBS', '')
                if date_obs:
                    try:
                        if 'T' in date_obs:
                            result.timestamp = datetime.fromisoformat(date_obs.replace('Z', '+00:00'))
                        else:
                            result.timestamp = datetime.strptime(date_obs, '%Y-%m-%d')
                    except (ValueError, TypeError):
                        pass
                
                # Filter
                result.filter_name = str(header.get('FILTER', '')).strip()
            
            # Ensure data is C-contiguous and byteswapped if needed
            data = np.ascontiguousarray(data)
            
            # Background estimation
            bkg = sep.Background(data)
            data_sub = data - bkg.back()
            
            # Source extraction
            objects = sep.extract(data_sub, detection_threshold, 
                                  err=bkg.globalrms, minarea=min_area)
            
            if len(objects) == 0:
                return result
            
            # Filter by flux - keep brightest stars
            flux_threshold = np.percentile(objects['flux'], 
                                          (1 - filter_bright_fraction) * 100)
            bright_mask = objects['flux'] >= flux_threshold
            objects = objects[bright_mask]
            
            # Limit number of stars
            if len(objects) > max_stars:
                flux_order = np.argsort(objects['flux'])[::-1]
                objects = objects[flux_order[:max_stars]]
            
            # Compute isolation flags using KDTree
            # Isolated stars have no neighbor within isolation_radius pixels
            isolation_radius = 50.0  # 2x the HFR aperture radius
            is_isolated = np.ones(len(objects), dtype=bool)  # Default to isolated
            
            if SCIPY_AVAILABLE and len(objects) > 1:
                positions = np.column_stack([objects['x'], objects['y']])
                tree = cKDTree(positions)
                # Query 2 nearest neighbors (first is always self with distance 0)
                distances, _ = tree.query(positions, k=2)
                min_neighbor_dist = distances[:, 1]  # Distance to closest other star
                is_isolated = min_neighbor_dist > isolation_radius
            
            # Calculate metrics for each star
            stars = []
            isolated_stars = []  # Separate list for isolated stars only
            for i, obj in enumerate(objects):
                x, y = obj['x'], obj['y']
                a, b = obj['a'], obj['b']  # Semi-major/minor axes
                theta_rad = obj['theta']   # Position angle in radians
                flux = obj['flux']
                
                # Skip invalid objects
                if a <= 0 or b <= 0 or flux <= 0:
                    continue
                
                # Eccentricity: ratio of major to minor axis
                # 1.0 = perfectly round, >1 = elongated
                eccentricity = max(a, b) / min(a, b) if min(a, b) > 0 else 1.0
                
                # FWHM from Gaussian approximation
                # For a Gaussian: FWHM = 2 * sqrt(2 * ln(2)) * sigma
                # SEP's a,b are like sigma values
                fwhm = 2.355 * np.sqrt(a * b)  # Geometric mean
                
                # Calculate HFR using flux-weighted mean radius
                hfr = calculate_hfr(data_sub, x, y)
                if hfr <= 0:
                    # Fallback: use FWHM-based estimate (less accurate for elongated stars)
                    hfr = fwhm * 0.6  # Empirical factor for flux-weighted HFR
                
                # Theta in degrees (0-180)
                theta_deg = np.degrees(theta_rad) % 180
                
                # SNR estimate
                npix = np.pi * a * b
                snr = flux / (np.sqrt(flux + npix * bkg.globalrms**2)) if npix > 0 else 0
                
                # Filter: reasonable FWHM range (0.5 to 20 pixels)
                if 0.5 < fwhm < 20 and snr > 5:
                    star = StarMetrics(
                        x=x, y=y,
                        hfr=hfr,
                        fwhm=fwhm,
                        eccentricity=eccentricity,
                        theta=theta_deg,
                        flux=flux,
                        snr=snr
                    )
                    stars.append(star)
                    # Track isolated stars separately for cleaner statistics
                    if is_isolated[i]:
                        isolated_stars.append(star)
            
            if not stars:
                return result
            
            # Use isolated stars for statistics if we have enough, otherwise fall back to all
            stats_stars = isolated_stars if len(isolated_stars) >= 20 else stars
            
            # Aggregate statistics (from isolated stars when available)
            result.num_stars = len(stats_stars)
            result.stars = stars  # Keep all stars for reference
            
            hfr_vals = [s.hfr for s in stats_stars]
            fwhm_vals = [s.fwhm for s in stats_stars]
            ecc_vals = [s.eccentricity for s in stats_stars]
            theta_vals = [s.theta for s in stats_stars]
            
            result.median_hfr = float(np.median(hfr_vals))
            result.std_hfr = float(np.std(hfr_vals))
            result.median_fwhm = float(np.median(fwhm_vals))
            result.std_fwhm = float(np.std(fwhm_vals))
            result.median_eccentricity = float(np.median(ecc_vals))
            result.std_eccentricity = float(np.std(ecc_vals))
            
            # Circular mean for angles
            theta_rad_vals = np.radians(theta_vals)
            mean_sin = np.mean(np.sin(2 * theta_rad_vals))
            mean_cos = np.mean(np.cos(2 * theta_rad_vals))
            result.mean_theta = float(np.degrees(np.arctan2(mean_sin, mean_cos) / 2) % 180)
            
            # Angular standard deviation (circular)
            R = np.sqrt(mean_sin**2 + mean_cos**2)
            result.theta_std = float(np.degrees(np.sqrt(-2 * np.log(max(R, 0.001))))) if R < 1 else 0
            
            # HFR uniformity (within-frame variation)
            if result.median_hfr > 0:
                result.hfr_uniformity = result.std_hfr / result.median_hfr
            
            # Tracking diagnosis
            tracking = diagnose_tracking(result.median_eccentricity, result.theta_std)
            result.tracking_type = tracking.error_type
            
    except Exception as e:
        # Log but don't fail - return empty stats
        print(f"Warning: Star analysis failed for {fits_path.name}: {e}")
    
    return result


def analyze_frames(fits_paths: List[Path], 
                   progress_callback=None,
                   **kwargs) -> List[FrameStarStats]:
    """
    Analyze multiple FITS frames.
    
    Args:
        fits_paths: List of paths to FITS files
        progress_callback: Optional callback(current, total) for progress
        **kwargs: Additional arguments passed to analyze_frame
        
    Returns:
        List of FrameStarStats, one per frame
    """
    results = []
    total = len(fits_paths)
    
    for i, path in enumerate(fits_paths):
        result = analyze_frame(path, **kwargs)
        results.append(result)
        
        if progress_callback:
            progress_callback(i + 1, total)
    
    # Post-process: compute relative flux for cloud detection
    # Need session median flux first
    all_middle_fluxes = []
    for r in results:
        if r.stars:
            fluxes = sorted([s.flux for s in r.stars], reverse=True)
            n = len(fluxes)
            start, end = n // 3, 2 * n // 3
            if start < end:
                all_middle_fluxes.append(np.median(fluxes[start:end]))
            elif fluxes:
                all_middle_fluxes.append(np.median(fluxes))
    
    session_median_flux = np.median(all_middle_fluxes) if all_middle_fluxes else 0.0
    
    # Update relative_flux for each frame
    for r in results:
        r.relative_flux = compute_relative_flux(r.stars, session_median_flux)
    
    return results


def compute_correlation(frame_stats: List[FrameStarStats]) -> Tuple[float, float, float]:
    """
    Compute correlation between eccentricity and guiding RMS.
    
    Returns:
        (r_value, slope, intercept) - Pearson correlation and linear fit
    """
    # Filter frames with valid data
    valid = [(f.median_eccentricity, f.guide_rms) 
             for f in frame_stats 
             if f.median_eccentricity > 0 and f.guide_rms > 0]
    
    if len(valid) < 3:
        return 0.0, 0.0, 0.0
    
    ecc = np.array([v[0] for v in valid])
    rms = np.array([v[1] for v in valid])
    
    # Pearson correlation
    r = np.corrcoef(rms, ecc)[0, 1]
    
    # Linear fit
    slope, intercept = np.polyfit(rms, ecc, 1)
    
    return float(r), float(slope), float(intercept)
