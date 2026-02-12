"""HTML Dashboard Generator for Astro Run Quality Controller.

Enhanced version with:
- Time-series plots with filter zones (SHO palette)
- PHD2 guiding error analysis with RMS statistics
- Moon phase, distance, and sky contribution
- Sky background estimation from FITS data
- HFR tracking per image with error bars
- External weather/seeing data lookup
- Comprehensive quality scoring
"""

import re
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import urllib.request
import urllib.error

import numpy as np
from astropy.io import fits
from astropy.coordinates import SkyCoord, EarthLocation, AltAz, get_body
from astropy.time import Time
import astropy.units as u

try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo

from .config import Config
from .astro_logger import Logger
from .star_analysis import (
    FrameStarStats, analyze_frame, compute_correlation, SEP_AVAILABLE,
    compute_filter_baselines, flag_frame, FilterBaseline, FrameFlags
)


# =============================================================================
# Constants
# =============================================================================

# SQM (Sky Quality Meter) conversion constant - tunable for calibration
# Typical range: 21-22 for standard photometric baselines
SQM_ZERO_POINT = 22.0


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class ImageData:
    """Data extracted from a single FITS file."""
    filename: str
    filepath: str
    timestamp: datetime
    filter_name: str
    exposure_time: float
    gain: int
    ccd_temp: float
    set_temp: float
    focus_position: int
    ra_deg: float
    dec_deg: float
    alt_deg: float = 0.0
    az_deg: float = 0.0
    object_name: str = ""
    camera: str = ""
    telescope: str = ""
    # Sky background fields
    sky_background: float = 0.0      # Mode-based sky in ADU
    sky_background_std: float = 0.0
    sky_sqm: float = 0.0             # Sky quality in mag/arcsec^2
    imaging_pixel_scale: float = 0.0 # arcsec/pixel for this image
    hfr: float = 0.0  # If available in header
    fwhm: float = 0.0
    stars_detected: int = 0
    moon_distance_deg: float = 0.0
    moon_alt_deg: float = 0.0
    moon_phase: float = 0.0  # 0=new, 0.5=full
    # Per-image guiding stats
    guide_rms_ra: float = 0.0
    guide_rms_dec: float = 0.0
    guide_rms_total: float = 0.0
    guide_frame_count: int = 0
    # Raw guiding deltas during exposure (for error bar calculation)
    guide_frames_ra: List[float] = field(default_factory=list)
    guide_frames_dec: List[float] = field(default_factory=list)


@dataclass
class GuidingFrame:
    """Single guiding frame from PHD2 log."""
    frame_num: int
    timestamp_rel: float  # Seconds since guiding start
    timestamp_abs: Optional[datetime] = None  # Absolute timestamp
    dx: float = 0.0  # Total error X (pixels)
    dy: float = 0.0  # Total error Y (pixels)
    ra_raw: float = 0.0  # RA raw distance
    dec_raw: float = 0.0  # DEC raw distance
    ra_guide: float = 0.0  # RA guide distance (after algo)
    dec_guide: float = 0.0  # DEC guide distance
    star_mass: float = 0.0
    snr: float = 0.0
    hfd: float = 0.0


@dataclass
class GuidingStats:
    """Statistics for a guiding period."""
    start_time: datetime
    end_time: datetime
    total_frames: int
    rms_ra: float  # arcsec
    rms_dec: float  # arcsec
    rms_total: float  # arcsec
    peak_ra: float
    peak_dec: float
    avg_snr: float
    avg_hfd: float


@dataclass
class AutofocusEvent:
    """Data from an autofocus run."""
    timestamp: datetime
    filter_name: str
    temperature: float
    success: bool
    final_position: int
    best_hfr: float
    trigger: str
    hfr_measurements: List[Tuple[int, float]] = field(default_factory=list)


@dataclass
class GuideEvent:
    """Guide-related event."""
    timestamp: datetime
    event_type: str


@dataclass
class FilterStats:
    """Statistics per filter."""
    filter_name: str
    image_count: int
    total_exposure_time: float
    avg_focus_position: float
    std_focus_position: float
    focus_positions: List[int]
    avg_hfr: float = 0.0
    std_hfr: float = 0.0
    hfr_values: List[float] = field(default_factory=list)
    avg_sky_background: float = 0.0
    std_sky_background: float = 0.0
    sky_backgrounds: List[float] = field(default_factory=list)
    avg_sky_sqm: float = 0.0
    std_sky_sqm: float = 0.0
    sky_sqm_values: List[float] = field(default_factory=list)


@dataclass
class WeatherData:
    """External weather data for the session."""
    temperature_c: float = 0.0
    humidity_pct: float = 0.0
    wind_speed_kmh: float = 0.0
    wind_gust_kmh: float = 0.0
    cloud_cover_pct: float = 0.0
    seeing_arcsec: float = 0.0
    transparency: str = ""
    source: str = ""


@dataclass
class QualityScore:
    """Quality score with breakdown."""
    name: str
    score: float
    weight: float
    description: str
    details: str


# =============================================================================
# Filter Color Palette (SHO)
# =============================================================================

FILTER_COLORS = {
    'H': 'rgba(0, 255, 0, 0.15)',      # Green for Ha (SHO mapping)
    'Ha': 'rgba(0, 255, 0, 0.15)',
    'S': 'rgba(255, 0, 0, 0.15)',      # Red for SII
    'SII': 'rgba(255, 0, 0, 0.15)',
    'O': 'rgba(0, 100, 255, 0.15)',    # Blue for OIII
    'OIII': 'rgba(0, 100, 255, 0.15)',
    'L': 'rgba(200, 200, 200, 0.15)',  # Grey for Luminance
    'R': 'rgba(255, 100, 100, 0.15)',  # Red
    'G': 'rgba(100, 255, 100, 0.15)',  # Green
    'B': 'rgba(100, 100, 255, 0.15)',  # Blue
}

FILTER_BORDER_COLORS = {
    'H': '#00ff00', 'Ha': '#00ff00',
    'S': '#ff0000', 'SII': '#ff0000',
    'O': '#0064ff', 'OIII': '#0064ff',
    'L': '#c8c8c8',
    'R': '#ff6464', 'G': '#64ff64', 'B': '#6464ff',
}


# =============================================================================
# Session Analyzer
# =============================================================================

class SessionAnalyzer:
    """Analyzes an imaging session from logs and FITS files."""
    
    def __init__(self, config: Config, session_dir: Path):
        self.config = config
        self.session_dir = Path(session_dir)
        self.logger = Logger("SessionAnalyzer")
        
        # Data storage
        self.images: List[ImageData] = []
        self.autofocus_events: List[AutofocusEvent] = []
        self.guide_events: List[GuideEvent] = []
        self.guiding_frames: List[GuidingFrame] = []
        self.guiding_stats: Optional[GuidingStats] = None
        self.guiding_start: Optional[datetime] = None  # PHD2 guiding start time
        self.filter_stats: Dict[str, FilterStats] = {}
        self.weather: Optional[WeatherData] = None
        self.frame_star_stats: List[FrameStarStats] = []  # Per-frame star analysis
        self.star_correlation: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # (r, slope, intercept)
        
        # Session metadata
        self.session_start: Optional[datetime] = None
        self.session_end: Optional[datetime] = None
        self.target_name: str = ""
        self.target_ra: str = ""
        self.target_dec: str = ""
        self.pixel_scale: float = 6.45  # arcsec/pixel default
        
        # Observer location
        self.observer_location = EarthLocation(
            lat=config.altaz.latitude * u.deg,
            lon=config.altaz.longitude * u.deg,
            height=config.altaz.elevation * u.m
        )
        try:
            self.timezone = zoneinfo.ZoneInfo(config.altaz.timezone)
        except Exception:
            self.timezone = zoneinfo.ZoneInfo("UTC")
    
    def analyze(self, star_analysis: bool = False) -> None:
        """Run complete session analysis.
        
        Args:
            star_analysis: If True, run per-frame star detection (slower but detailed)
        """
        self.logger.info(f"Analyzing session: {self.session_dir}")
        
        # Parse Autorun log
        autorun_log = self._find_autorun_log()
        if autorun_log:
            self._parse_autorun_log(autorun_log)
        
        # Parse PHD2 log for guiding data
        phd2_log = self._find_phd2_log()
        if phd2_log:
            self._parse_phd2_log(phd2_log)
        
        # Process FITS files
        self._process_fits_files()
        
        # Correlate guiding data with each image
        self._correlate_guiding_with_images()
        
        # Calculate moon positions for all images
        self._calculate_moon_data()
        
        # Calculate filter statistics
        self._calculate_filter_stats()
        
        # Fetch weather data
        self._fetch_weather_data()
        
        # Optional: Run per-frame star analysis
        if star_analysis:
            self._run_star_analysis()
        
        self.logger.success(f"Analysis complete: {len(self.images)} images, "
                           f"{len(self.autofocus_events)} AF events, "
                           f"{len(self.guiding_frames)} guide frames")
    
    def _find_autorun_log(self) -> Optional[Path]:
        """Find Autorun log file."""
        logs = list(self.session_dir.glob("Autorun_Log*.txt"))
        if logs:
            return sorted(logs)[-1]
        return None
    
    def _find_phd2_log(self) -> Optional[Path]:
        """Find PHD2 guide log file."""
        logs = list(self.session_dir.glob("PHD2_GuideLog*.txt"))
        if logs:
            return sorted(logs)[-1]
        return None
    
    def _parse_autorun_log(self, log_path: Path) -> None:
        """Parse Autorun log for events."""
        self.logger.info(f"Parsing Autorun log: {log_path.name}")
        
        current_af: Optional[AutofocusEvent] = None
        current_filter = None
        
        # First pass: detect initial filter
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if "Filter change" in line:
                    match = re.search(r'Filter change,\s+(\w+)\s+change to', line)
                    if match:
                        current_filter = match.group(1)
                        break
        
        if current_filter is None:
            current_filter = "L"
        
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                ts_match = re.match(r'(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})\s+(.*)', line)
                if not ts_match:
                    continue
                
                ts_str, content = ts_match.groups()
                try:
                    timestamp = datetime.strptime(ts_str, "%Y/%m/%d %H:%M:%S")
                    timestamp = timestamp.replace(tzinfo=self.timezone)
                except ValueError:
                    continue
                
                if self.session_start is None:
                    self.session_start = timestamp
                self.session_end = timestamp
                
                if "Target RA:" in content:
                    target_match = re.search(r'Target RA:([^\s]+)\s+DEC:([^\s]+)', content)
                    if target_match:
                        self.target_ra = target_match.group(1)
                        self.target_dec = target_match.group(2)
                
                if "Filter change" in content:
                    filter_match = re.search(r'change.*to\s+(\w+)', content)
                    if filter_match:
                        current_filter = filter_match.group(1)
                
                if "[AutoFocus|Begin]" in content:
                    trigger = "start"
                    if "filter changed" in content:
                        trigger = "filter_change"
                    elif "Meridian" in content:
                        trigger = "meridian_flip"
                    
                    temp_match = re.search(r'temperature\s+([\d.]+)', content)
                    temp = float(temp_match.group(1)) if temp_match else 0.0
                    
                    current_af = AutofocusEvent(
                        timestamp=timestamp,
                        filter_name=current_filter,
                        temperature=temp,
                        success=False,
                        final_position=0,
                        best_hfr=0.0,
                        trigger=trigger
                    )
                
                elif current_af and ("Calculate V-Curve:" in content or "Calculate Focus Point:" in content):
                    hfr_match = re.search(r'star size\s+([\d.]+)', content)
                    pos_match = re.search(r'EAF position\s+(\d+)', content)
                    if hfr_match and pos_match:
                        hfr = float(hfr_match.group(1))
                        pos = int(pos_match.group(1))
                        current_af.hfr_measurements.append((pos, hfr))
                
                elif current_af and "Auto focus succeeded" in content:
                    pos_match = re.search(r'position is\s+(\d+)', content)
                    if pos_match:
                        current_af.final_position = int(pos_match.group(1))
                    current_af.success = True
                    if current_af.hfr_measurements:
                        current_af.best_hfr = min(h for _, h in current_af.hfr_measurements)
                    self.autofocus_events.append(current_af)
                    current_af = None
                
                elif current_af and "[AutoFocus|End]" in content and "failed" in content.lower():
                    current_af.success = False
                    self.autofocus_events.append(current_af)
                    current_af = None
                
                if "[Guide]" in content:
                    if "star lost" in content.lower():
                        self.guide_events.append(GuideEvent(timestamp, "star_lost"))
                    elif "Settle Timeout" in content:
                        self.guide_events.append(GuideEvent(timestamp, "settle_timeout"))
                    elif "Settle Done" in content:
                        self.guide_events.append(GuideEvent(timestamp, "settle_done"))
                
                if "[Autorun|Begin]" in content:
                    name_match = re.search(r'\[Autorun\|Begin\]\s+(.+?)\s+Start', content)
                    if name_match:
                        self.target_name = name_match.group(1)
    
    def _parse_phd2_log(self, log_path: Path) -> None:
        """Parse PHD2 guide log for guiding errors with absolute timestamps.
        
        PHD2 logs contain multiple "Guiding Begins" events (after dithering,
        meridian flips, star lost recovery). Each restart resets the relative
        timestamp to 0, so we track each session's start time.
        """
        self.logger.info(f"Parsing PHD2 log: {log_path.name}")
        
        current_session_start: Optional[datetime] = None
        first_session_start: Optional[datetime] = None
        frames: List[GuidingFrame] = []
        session_count = 0
        
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                
                # Parse pixel scale
                if "Pixel scale" in line:
                    scale_match = re.search(r'Pixel scale\s*=\s*([\d.]+)', line)
                    if scale_match:
                        self.pixel_scale = float(scale_match.group(1))
                
                # Parse guiding start time - updates for EACH session restart
                if "Guiding Begins at" in line:
                    time_match = re.search(r'Guiding Begins at\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
                    if time_match:
                        current_session_start = datetime.strptime(time_match.group(1), "%Y-%m-%d %H:%M:%S")
                        current_session_start = current_session_start.replace(tzinfo=self.timezone)
                        session_count += 1
                        if first_session_start is None:
                            first_session_start = current_session_start
                
                # Parse frame data
                # Frame,Time,mount,dx,dy,RARawDistance,DECRawDistance,RAGuideDistance,DECGuideDistance,...
                if line and line[0].isdigit() and ',' in line:
                    parts = line.split(',')
                    if len(parts) >= 17:
                        try:
                            rel_time = float(parts[1])
                            # Calculate absolute timestamp from CURRENT session start
                            abs_time = None
                            if current_session_start:
                                abs_time = current_session_start + timedelta(seconds=rel_time)
                            
                            frame = GuidingFrame(
                                frame_num=int(parts[0]),
                                timestamp_rel=rel_time,
                                timestamp_abs=abs_time,
                                dx=float(parts[3]) if parts[3] else 0.0,
                                dy=float(parts[4]) if parts[4] else 0.0,
                                ra_raw=float(parts[5]) if parts[5] else 0.0,
                                dec_raw=float(parts[6]) if parts[6] else 0.0,
                                ra_guide=float(parts[7]) if parts[7] else 0.0,
                                dec_guide=float(parts[8]) if parts[8] else 0.0,
                                star_mass=float(parts[14]) if parts[14] else 0.0,
                                snr=float(parts[15]) if parts[15] else 0.0,
                            )
                            frames.append(frame)
                        except (ValueError, IndexError):
                            continue
        
        # Sort frames by ABSOLUTE timestamp to ensure correct temporal ordering
        frames.sort(key=lambda f: f.timestamp_abs if f.timestamp_abs else datetime.min.replace(tzinfo=self.timezone))
        self.guiding_frames = frames
        self.guiding_start = first_session_start
        
        if first_session_start:
            self.logger.info(f"PHD2: {session_count} guiding sessions, {len(frames)} frames")
        
        # Calculate guiding statistics with sigma clipping
        if frames and first_session_start:
            ra_errors = np.array([f.ra_raw * self.pixel_scale for f in frames])
            dec_errors = np.array([f.dec_raw * self.pixel_scale for f in frames])
            
            # Sigma clipping to remove outliers (4 sigma)
            ra_valid, dec_valid, valid_mask = self._sigma_clip_guiding(ra_errors, dec_errors)
            total_valid = np.sqrt(ra_valid**2 + dec_valid**2)
            
            outliers_removed = len(frames) - np.sum(valid_mask)
            if outliers_removed > 0:
                self.logger.info(f"Removed {outliers_removed} guiding outliers from RMS calculation")
            
            # Use last frame's absolute timestamp for end time
            last_frame_time = frames[-1].timestamp_abs if frames[-1].timestamp_abs else first_session_start
            
            self.guiding_stats = GuidingStats(
                start_time=first_session_start,
                end_time=last_frame_time,
                total_frames=len(frames),
                rms_ra=np.sqrt(np.mean(ra_valid**2)) if len(ra_valid) > 0 else 0,
                rms_dec=np.sqrt(np.mean(dec_valid**2)) if len(dec_valid) > 0 else 0,
                rms_total=np.sqrt(np.mean(total_valid**2)) if len(total_valid) > 0 else 0,
                peak_ra=np.max(np.abs(ra_valid)) if len(ra_valid) > 0 else 0,
                peak_dec=np.max(np.abs(dec_valid)) if len(dec_valid) > 0 else 0,
                avg_snr=np.mean([f.snr for f in frames]),
                avg_hfd=np.mean([f.hfd for f in frames if f.hfd > 0]) if any(f.hfd > 0 for f in frames) else 0.0
            )
            
            self.logger.info(f"Guiding RMS: RA={self.guiding_stats.rms_ra:.2f}\", "
                           f"DEC={self.guiding_stats.rms_dec:.2f}\", "
                           f"Total={self.guiding_stats.rms_total:.2f}\"")
    
    def _sigma_clip_guiding(self, ra_errors: np.ndarray, dec_errors: np.ndarray, 
                            sigma: float = 4) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Apply sigma clipping to guiding errors, return clipped data and mask."""
        def clip_mask(data):
            median = np.median(data)
            mad = np.median(np.abs(data - median))
            std_est = mad * 1.4826
            return np.abs(data - median) < (sigma * std_est)
        
        ra_mask = clip_mask(ra_errors)
        dec_mask = clip_mask(dec_errors)
        valid_mask = ra_mask & dec_mask
        
        return ra_errors[valid_mask], dec_errors[valid_mask], valid_mask
    
    def _get_guiding_for_exposure(self, exp_start: datetime, exp_duration: float) -> Tuple[float, float, float, int, List[float], List[float]]:
        """Get guiding RMS and raw deltas for a specific exposure time window.
        
        Returns: (rms_ra, rms_dec, rms_total, frame_count, ra_values, dec_values)
        """
        if not self.guiding_frames or not self.guiding_start:
            return 0.0, 0.0, 0.0, 0, [], []
        
        # Normalize timezones - make both offset-aware or both offset-naive
        exp_start_ts = exp_start
        if exp_start.tzinfo is None:
            # FITS timestamp is naive, assume it's UTC and add timezone
            try:
                exp_start_ts = exp_start.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
            except Exception:
                pass
        
        exp_end = exp_start_ts + timedelta(seconds=exp_duration)
        
        # Find frames within the exposure window
        matching_frames = []
        for f in self.guiding_frames:
            if f.timestamp_abs:
                try:
                    # Handle timezone comparison safely
                    f_time = f.timestamp_abs
                    if f_time.tzinfo is not None and exp_start_ts.tzinfo is not None:
                        if exp_start_ts <= f_time <= exp_end:
                            matching_frames.append(f)
                    else:
                        # Compare as naive (strip tzinfo)
                        f_naive = f_time.replace(tzinfo=None)
                        exp_naive = exp_start.replace(tzinfo=None) if hasattr(exp_start, 'replace') else exp_start
                        exp_end_naive = exp_naive + timedelta(seconds=exp_duration)
                        if exp_naive <= f_naive <= exp_end_naive:
                            matching_frames.append(f)
                except Exception:
                    continue
        
        if len(matching_frames) < 3:  # Need minimum frames for meaningful RMS
            return 0.0, 0.0, 0.0, len(matching_frames), [], []
        
        ra_errors = np.array([f.ra_raw * self.pixel_scale for f in matching_frames])
        dec_errors = np.array([f.dec_raw * self.pixel_scale for f in matching_frames])
        
        # Apply sigma clipping
        ra_valid, dec_valid, _ = self._sigma_clip_guiding(ra_errors, dec_errors)
        
        if len(ra_valid) == 0:
            return 0.0, 0.0, 0.0, len(matching_frames), [], []
        
        rms_ra = np.sqrt(np.mean(ra_valid**2))
        rms_dec = np.sqrt(np.mean(dec_valid**2))
        rms_total = np.sqrt(rms_ra**2 + rms_dec**2)
        
        # Return both RMS stats AND raw sigma-clipped values
        return rms_ra, rms_dec, rms_total, len(matching_frames), list(ra_valid), list(dec_valid)
    
    def _process_fits_files(self) -> None:
        """Process all FITS files in session directory."""
        fits_files = list(self.session_dir.rglob("*.fit")) + list(self.session_dir.rglob("*.fits"))
        fits_files = [f for f in fits_files if "Light" in f.name]
        fits_files.sort()
        
        self.logger.info(f"Processing {len(fits_files)} FITS files")
        
        for fpath in fits_files:
            try:
                img_data = self._parse_fits_file(fpath)
                if img_data:
                    self.images.append(img_data)
            except Exception as e:
                self.logger.debug(f"Error processing {fpath.name}: {e}")
    
    def _parse_fits_file(self, fpath: Path) -> Optional[ImageData]:
        """Parse a single FITS file with enhanced data extraction."""
        with fits.open(fpath) as hdul:
            header = hdul[0].header
            data = hdul[0].data
            
            date_obs = header.get('DATE-OBS', '')
            if not date_obs:
                return None
            
            try:
                timestamp = datetime.fromisoformat(date_obs.replace('Z', '+00:00'))
                # DATE-OBS is typically UTC - convert to local timezone for consistent display
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
                timestamp = timestamp.astimezone(self.timezone)
            except ValueError:
                return None
            
            ra_deg = float(header.get('RA', 0))
            dec_deg = float(header.get('DEC', 0))
            alt_deg, az_deg = self._calc_altaz(timestamp, ra_deg, dec_deg)
            
            # Calculate sky background using histogram mode (more robust than median)
            sky_bg, sky_std = self._estimate_sky_background_mode(data)
            
            # Calculate pixel scale from FITS headers
            pixel_scale = self._calc_pixel_scale(header)
            
            # Get exposure parameters for SQM calculation
            gain = int(header.get('GAIN', 0))
            exposure_time = float(header.get('EXPTIME', 1))
            
            # Convert sky background to SQM (mag/arcsec^2)
            sky_sqm = self._calculate_sqm(sky_bg, exposure_time, gain, pixel_scale)
            
            # Try to get HFR/FWHM from header if available
            hfr = float(header.get('HFR', 0)) if 'HFR' in header else 0.0
            fwhm = float(header.get('FWHM', 0)) if 'FWHM' in header else 0.0
            stars = int(header.get('STARS', 0)) if 'STARS' in header else 0
            
            return ImageData(
                filename=fpath.name,
                filepath=str(fpath),
                timestamp=timestamp,
                filter_name=str(header.get('FILTER', 'L')).strip(),
                exposure_time=exposure_time,
                gain=gain,
                ccd_temp=float(header.get('CCD-TEMP', 0)),
                set_temp=float(header.get('SET-TEMP', 0)),
                focus_position=int(header.get('FOCUSPOS', 0)),
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                alt_deg=alt_deg,
                az_deg=az_deg,
                object_name=str(header.get('OBJECT', '')),
                camera=str(header.get('INSTRUME', '')),
                telescope=str(header.get('TELESCOP', '')),
                sky_background=sky_bg,
                sky_background_std=sky_std,
                sky_sqm=sky_sqm,
                imaging_pixel_scale=pixel_scale,
                hfr=hfr,
                fwhm=fwhm,
                stars_detected=stars
            )
    
    def _calc_altaz(self, timestamp: datetime, ra_deg: float, dec_deg: float) -> Tuple[float, float]:
        """Calculate Alt/Az for given RA/Dec at timestamp."""
        try:
            obs_time = Time(timestamp)
            coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
            altaz_frame = AltAz(obstime=obs_time, location=self.observer_location)
            altaz = coord.transform_to(altaz_frame)
            return float(altaz.alt.deg), float(altaz.az.deg)
        except Exception:
            return 0.0, 0.0
    
    def _calc_pixel_scale(self, header) -> float:
        """Calculate imaging pixel scale from FITS headers.
        
        pixel_scale = (pixel_size_um / focal_length_mm) * 206.265
        """
        focal_length = float(header.get('FOCALLEN', 0))
        pixel_size_um = float(header.get('XPIXSZ', 0))
        
        if focal_length > 0 and pixel_size_um > 0:
            return (pixel_size_um / focal_length) * 206.265
        
        # Fallback to default (1.92 arcsec/pixel for 403mm + 3.76um)
        return 1.92
    
    def _estimate_sky_background_mode(self, data: np.ndarray) -> Tuple[float, float]:
        """Estimate sky background using histogram mode (most common value).
        
        Mode is more robust than median for astronomy images since the
        sky background is the most frequent pixel value, naturally ignoring stars.
        """
        if data is None:
            return 0.0, 0.0
        
        # Flatten the data
        flat = data.flatten().astype(float)
        
        # Remove extreme outliers (< 1st percentile or > 99th percentile)
        p1, p99 = np.percentile(flat, [1, 99])
        flat = flat[(flat >= p1) & (flat <= p99)]
        
        if len(flat) == 0:
            return 0.0, 0.0
        
        # Use histogram to find mode
        # Bin count based on data range (256-1024 bins typical for 16-bit data)
        data_range = np.max(flat) - np.min(flat)
        if data_range == 0:
            return float(np.mean(flat)), 0.0
        
        n_bins = min(1024, max(256, int(data_range / 10)))
        
        hist, bin_edges = np.histogram(flat, bins=n_bins)
        
        # Mode is the center of the most populated bin
        mode_idx = np.argmax(hist)
        sky_mode = (bin_edges[mode_idx] + bin_edges[mode_idx + 1]) / 2
        
        # Estimate std using MAD of values near the mode (within 3 bin widths)
        bin_width = bin_edges[1] - bin_edges[0]
        near_mode = flat[np.abs(flat - sky_mode) < 3 * bin_width]
        
        if len(near_mode) > 0:
            sky_std = float(np.median(np.abs(near_mode - sky_mode)) * 1.4826)
        else:
            sky_std = 0.0
        
        return float(sky_mode), sky_std
    
    def _calculate_sqm(self, sky_adu: float, exposure_time: float,
                       gain: int, pixel_scale_arcsec: float) -> float:
        """Convert sky background ADU to SQM (mag/arcsec^2).
        
        Args:
            sky_adu: Sky background in ADU (mode of histogram)
            exposure_time: Exposure time in seconds
            gain: Camera gain setting (0-600 for ASI cameras)
            pixel_scale_arcsec: Imaging pixel scale in arcsec/pixel
        
        Returns:
            SQM value in mag/arcsec^2 (higher = darker sky)
        """
        if sky_adu <= 0 or exposure_time <= 0 or pixel_scale_arcsec <= 0:
            return 0.0
        
        # ZWO ASI2600MM Pro gain formula (0.1 dB per unit, strictly logarithmic)
        # e_per_adu = 0.768 * (10 ** (-gain / 200.0))
        e_per_adu = 0.768 * (10 ** (-gain / 200.0))
        
        # Step 1: ADU to electrons
        electrons = sky_adu * e_per_adu
        
        # Step 2: Flux per arcsec^2
        flux_per_sec = electrons / exposure_time
        flux_per_arcsec2 = flux_per_sec / (pixel_scale_arcsec ** 2)
        
        # Step 3: Convert to magnitude
        if flux_per_arcsec2 <= 0:
            return 0.0
        
        sqm = SQM_ZERO_POINT - 2.5 * np.log10(flux_per_arcsec2)
        
        return float(sqm)
    
    def _correlate_guiding_with_images(self) -> None:
        """Calculate guiding statistics for each image's exposure window."""
        if not self.guiding_frames or not self.guiding_start:
            self.logger.debug("No guiding data to correlate with images")
            return
        
        images_with_guiding = 0
        for img in self.images:
            rms_ra, rms_dec, rms_total, count, ra_vals, dec_vals = self._get_guiding_for_exposure(
                img.timestamp, img.exposure_time
            )
            img.guide_rms_ra = rms_ra
            img.guide_rms_dec = rms_dec
            img.guide_rms_total = rms_total
            img.guide_frame_count = count
            img.guide_frames_ra = ra_vals
            img.guide_frames_dec = dec_vals
            if count > 0:
                images_with_guiding += 1
        
        self.logger.info(f"Correlated guiding data with {images_with_guiding}/{len(self.images)} images")
    
    def _calculate_moon_data(self) -> None:
        """Calculate moon position and phase for all images."""
        for img in self.images:
            try:
                obs_time = Time(img.timestamp)
                
                # Get moon position
                moon = get_body('moon', obs_time, self.observer_location)
                moon_altaz = moon.transform_to(AltAz(obstime=obs_time, location=self.observer_location))
                img.moon_alt_deg = float(moon_altaz.alt.deg)
                
                # Calculate angular distance from target
                target = SkyCoord(ra=img.ra_deg * u.deg, dec=img.dec_deg * u.deg, frame="icrs")
                moon_icrs = moon.transform_to('icrs')
                img.moon_distance_deg = float(target.separation(moon_icrs).deg)
                
                # Calculate moon phase (illumination fraction)
                sun = get_body('sun', obs_time, self.observer_location)
                sun_icrs = sun.transform_to('icrs')
                elongation = moon_icrs.separation(sun_icrs).deg
                img.moon_phase = (1 - np.cos(np.radians(elongation))) / 2
                
            except Exception as e:
                self.logger.debug(f"Error calculating moon data: {e}")
    
    def _calculate_filter_stats(self) -> None:
        """Calculate statistics per filter."""
        filter_images: Dict[str, List[ImageData]] = {}
        for img in self.images:
            if img.filter_name not in filter_images:
                filter_images[img.filter_name] = []
            filter_images[img.filter_name].append(img)
        
        filter_af: Dict[str, List[AutofocusEvent]] = {}
        for af in self.autofocus_events:
            if af.filter_name not in filter_af:
                filter_af[af.filter_name] = []
            filter_af[af.filter_name].append(af)
        
        for filter_name, imgs in filter_images.items():
            focus_positions = [img.focus_position for img in imgs if img.focus_position > 0]
            sky_backgrounds = [img.sky_background for img in imgs if img.sky_background > 0]
            sky_sqm_values = [img.sky_sqm for img in imgs if img.sky_sqm > 0]
            
            hfr_values = []
            if filter_name in filter_af:
                for af in filter_af[filter_name]:
                    if af.success and af.best_hfr > 0:
                        hfr_values.append(af.best_hfr)
            
            self.filter_stats[filter_name] = FilterStats(
                filter_name=filter_name,
                image_count=len(imgs),
                total_exposure_time=sum(img.exposure_time for img in imgs),
                avg_focus_position=np.mean(focus_positions) if focus_positions else 0,
                std_focus_position=np.std(focus_positions) if len(focus_positions) > 1 else 0,
                focus_positions=focus_positions,
                avg_hfr=np.mean(hfr_values) if hfr_values else 0,
                std_hfr=np.std(hfr_values) if len(hfr_values) > 1 else 0,
                hfr_values=hfr_values,
                avg_sky_background=np.mean(sky_backgrounds) if sky_backgrounds else 0,
                std_sky_background=np.std(sky_backgrounds) if len(sky_backgrounds) > 1 else 0,
                sky_backgrounds=sky_backgrounds,
                avg_sky_sqm=np.mean(sky_sqm_values) if sky_sqm_values else 0,
                std_sky_sqm=np.std(sky_sqm_values) if len(sky_sqm_values) > 1 else 0,
                sky_sqm_values=sky_sqm_values
            )
    
    def _fetch_weather_data(self) -> None:
        """Fetch historical weather data from Open-Meteo API."""
        if not self.session_start:
            return
        
        try:
            date_str = self.session_start.strftime("%Y-%m-%d")
            lat = self.config.altaz.latitude
            lon = self.config.altaz.longitude
            
            url = (f"https://archive-api.open-meteo.com/v1/archive?"
                   f"latitude={lat}&longitude={lon}&start_date={date_str}&end_date={date_str}"
                   f"&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,cloud_cover")
            
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode())
                
                if 'hourly' in data:
                    hourly = data['hourly']
                    # Get data for session hour
                    hour_idx = self.session_start.hour
                    
                    self.weather = WeatherData(
                        temperature_c=hourly['temperature_2m'][hour_idx] if hourly.get('temperature_2m') else 0,
                        humidity_pct=hourly['relative_humidity_2m'][hour_idx] if hourly.get('relative_humidity_2m') else 0,
                        wind_speed_kmh=hourly['wind_speed_10m'][hour_idx] if hourly.get('wind_speed_10m') else 0,
                        wind_gust_kmh=hourly['wind_gusts_10m'][hour_idx] if hourly.get('wind_gusts_10m') else 0,
                        cloud_cover_pct=hourly['cloud_cover'][hour_idx] if hourly.get('cloud_cover') else 0,
                        source="Open-Meteo Archive"
                    )
                    self.logger.info(f"Weather: {self.weather.temperature_c:.1f}C, "
                                   f"Wind: {self.weather.wind_speed_kmh:.1f} km/h, "
                                   f"Clouds: {self.weather.cloud_cover_pct:.0f}%")
        except Exception as e:
            self.logger.debug(f"Could not fetch weather data: {e}")
    
    def _run_star_analysis(self) -> None:
        """Run per-frame star detection and analysis using SEP."""
        if not SEP_AVAILABLE:
            self.logger.warning("SEP not installed - skipping star analysis")
            return
        
        fits_files = list(self.session_dir.glob("*.fit")) + list(self.session_dir.glob("*.fits"))
        if not fits_files:
            return
        
        self.logger.info(f"Running star analysis on {len(fits_files)} frames...")
        
        # Create lookup for per-image guiding RMS
        image_rms = {}
        for img in self.images:
            if img.timestamp:
                image_rms[img.timestamp] = img.guide_rms_total
        
        # Analyze each frame
        self.frame_star_stats = []
        for i, fits_path in enumerate(sorted(fits_files)):
            if (i + 1) % 10 == 0 or i == len(fits_files) - 1:
                self.logger.info(f"  Analyzing frame {i + 1}/{len(fits_files)}...")
            
            stats = analyze_frame(fits_path)
            
            # Match guiding RMS by timestamp
            if stats.timestamp:
                # Find closest image timestamp (handle timezone-aware/naive comparison)
                min_diff = timedelta(minutes=5)
                stats_ts_naive = stats.timestamp.replace(tzinfo=None) if stats.timestamp.tzinfo else stats.timestamp
                for img_ts, rms in image_rms.items():
                    if not img_ts:
                        continue
                    img_ts_naive = img_ts.replace(tzinfo=None) if img_ts.tzinfo else img_ts
                    try:
                        diff = abs(stats_ts_naive - img_ts_naive)
                        if diff < min_diff:
                            min_diff = diff
                            stats.guide_rms = rms
                    except TypeError:
                        continue
            
            self.frame_star_stats.append(stats)
        
        # Compute correlation between eccentricity and guiding RMS
        self.star_correlation = compute_correlation(self.frame_star_stats)
        r_val = self.star_correlation[0]
        
        # Log summary
        valid_frames = [f for f in self.frame_star_stats if f.num_stars > 0]
        if valid_frames:
            avg_hfr = np.mean([f.median_hfr for f in valid_frames])
            avg_ecc = np.mean([f.median_eccentricity for f in valid_frames])
            avg_stars = np.mean([f.num_stars for f in valid_frames])
            self.logger.info(f"Star analysis: {len(valid_frames)} valid frames, "
                           f"avg {avg_stars:.0f} stars/frame, "
                           f"HFR={avg_hfr:.2f}px, Ecc={avg_ecc:.2f}")
            self.logger.info(f"Eccentricity vs RMS correlation: r={r_val:.3f}")


# =============================================================================
# Quality Scorer
# =============================================================================

class QualityScorer:
    """Calculate quality scores for an imaging session."""
    
    def __init__(self, analyzer: SessionAnalyzer):
        self.analyzer = analyzer
    
    def calculate_scores(self) -> List[QualityScore]:
        """Calculate all quality scores."""
        scores = []
        scores.append(self._score_altitude())
        scores.append(self._score_autofocus())
        scores.append(self._score_guiding())
        scores.append(self._score_temperature())
        scores.append(self._score_focus_stability())
        scores.append(self._score_hfr())
        scores.append(self._score_weather())
        scores.append(self._score_moon())
        return scores
    
    def calculate_overall_score(self, scores: List[QualityScore]) -> float:
        """Calculate weighted overall score."""
        total_weight = sum(s.weight for s in scores)
        weighted_sum = sum(s.score * s.weight for s in scores)
        return weighted_sum / total_weight if total_weight > 0 else 0
    
    def _score_altitude(self) -> QualityScore:
        """Score based on altitude throughout session."""
        if not self.analyzer.images:
            return QualityScore("Altitude", 0, 0.10, "Target altitude", "No data")
        
        altitudes = [img.alt_deg for img in self.analyzer.images]
        min_alt = min(altitudes)
        avg_alt = np.mean(altitudes)
        
        if avg_alt >= 60:
            score = 100
        elif avg_alt >= 20:
            score = (avg_alt - 20) * (100 / 40)
        else:
            score = 0
        
        details = f"Min: {min_alt:.1f} deg, Avg: {avg_alt:.1f} deg, Max: {max(altitudes):.1f} deg"
        return QualityScore("Altitude", score, 0.10,
                           "Higher altitude = less atmosphere", details)
    
    def _score_autofocus(self) -> QualityScore:
        """Score autofocus success rate."""
        if not self.analyzer.autofocus_events:
            return QualityScore("Autofocus", 100, 0.10, "AF success rate", "No AF events")
        
        total = len(self.analyzer.autofocus_events)
        success = sum(1 for af in self.analyzer.autofocus_events if af.success)
        rate = (success / total) * 100
        
        details = f"{success}/{total} successful ({rate:.0f}%)"
        return QualityScore("Autofocus", rate, 0.10,
                           "Autofocus success rate", details)
    
    def _score_guiding(self) -> QualityScore:
        """Score guiding quality based on RMS error."""
        if not self.analyzer.guiding_stats:
            # Fall back to event-based scoring
            if not self.analyzer.images:
                return QualityScore("Guiding", 50, 0.20, "Guiding quality", "No data")
            
            star_lost = sum(1 for e in self.analyzer.guide_events if e.event_type == "star_lost")
            num_images = len(self.analyzer.images)
            star_lost_rate = star_lost / num_images if num_images > 0 else 0
            score = max(0, 100 - star_lost_rate * 200)
            details = f"Star lost: {star_lost}/{num_images} images"
            return QualityScore("Guiding", score, 0.20,
                               "Guiding stability", details)
        
        # Score based on RMS (excellent < 1", good < 2", poor > 3")
        rms = self.analyzer.guiding_stats.rms_total
        
        if rms < 1.0:
            score = 100
        elif rms < 2.0:
            score = 100 - (rms - 1.0) * 30
        elif rms < 3.0:
            score = 70 - (rms - 2.0) * 40
        else:
            score = max(0, 30 - (rms - 3.0) * 15)
        
        details = (f"RMS: {rms:.2f}\" (RA: {self.analyzer.guiding_stats.rms_ra:.2f}\", "
                  f"DEC: {self.analyzer.guiding_stats.rms_dec:.2f}\")")
        return QualityScore("Guiding RMS", score, 0.20,
                           "Lower RMS = better tracking", details)
    
    def _score_temperature(self) -> QualityScore:
        """Score temperature stability."""
        if not self.analyzer.autofocus_events:
            return QualityScore("Temperature", 100, 0.08, "Temp stability", "No data")
        
        temps = [af.temperature for af in self.analyzer.autofocus_events if af.temperature > 0]
        if not temps:
            return QualityScore("Temperature", 100, 0.08, "Temp stability", "No data")
        
        temp_range = max(temps) - min(temps)
        
        if temp_range < 1:
            score = 100
        elif temp_range < 10:
            score = 100 - (temp_range - 1) * (100 / 9)
        else:
            score = 0
        
        details = f"Range: {temp_range:.1f} C ({min(temps):.1f} to {max(temps):.1f})"
        return QualityScore("Temperature", score, 0.08,
                           "Temperature stability", details)
    
    def _score_focus_stability(self) -> QualityScore:
        """Score focus position stability."""
        if not self.analyzer.filter_stats:
            return QualityScore("Focus Stability", 100, 0.10, "Focus consistency", "No data")
        
        stds = [s.std_focus_position for s in self.analyzer.filter_stats.values()
                if s.std_focus_position > 0]
        
        if not stds:
            return QualityScore("Focus Stability", 100, 0.10, "Focus consistency", "Consistent")
        
        avg_std = np.mean(stds)
        
        if avg_std < 5:
            score = 100
        elif avg_std < 50:
            score = 100 - (avg_std - 5) * (100 / 45)
        else:
            score = 0
        
        details = f"Avg std: {avg_std:.1f} steps"
        return QualityScore("Focus Stability", score, 0.10,
                           "Focus position consistency", details)
    
    def _score_hfr(self) -> QualityScore:
        """Score HFR quality (star sharpness)."""
        all_hfr = []
        for stats in self.analyzer.filter_stats.values():
            all_hfr.extend(stats.hfr_values)
        
        if not all_hfr:
            return QualityScore("HFR/Seeing", 75, 0.15, "Star quality", "No HFR data")
        
        avg_hfr = np.mean(all_hfr)
        
        # Good seeing: HFR < 3, Poor: > 6
        if avg_hfr < 3:
            score = 100
        elif avg_hfr < 6:
            score = 100 - (avg_hfr - 3) * (100 / 3)
        else:
            score = max(0, 30 - (avg_hfr - 6) * 10)
        
        details = f"Avg HFR: {avg_hfr:.2f} (range: {min(all_hfr):.2f} - {max(all_hfr):.2f})"
        return QualityScore("HFR/Seeing", score, 0.15,
                           "Star sharpness - lower is better", details)
    
    def _score_weather(self) -> QualityScore:
        """Score based on weather conditions."""
        if not self.analyzer.weather:
            return QualityScore("Weather", 75, 0.12, "Weather conditions", "No data")
        
        w = self.analyzer.weather
        score = 100
        
        # Penalize high wind
        if w.wind_speed_kmh > 30:
            score -= min(50, (w.wind_speed_kmh - 30))
        elif w.wind_speed_kmh > 15:
            score -= (w.wind_speed_kmh - 15)
        
        # Penalize wind gusts
        if w.wind_gust_kmh > 40:
            score -= min(30, (w.wind_gust_kmh - 40) * 0.5)
        
        # Penalize clouds
        score -= w.cloud_cover_pct * 0.3
        
        # Penalize high humidity
        if w.humidity_pct > 80:
            score -= (w.humidity_pct - 80) * 0.5
        
        score = max(0, score)
        
        details = f"Wind: {w.wind_speed_kmh:.0f} km/h, Gusts: {w.wind_gust_kmh:.0f} km/h, Clouds: {w.cloud_cover_pct:.0f}%"
        return QualityScore("Weather", score, 0.12,
                           "Wind, clouds, humidity", details)
    
    def _score_moon(self) -> QualityScore:
        """Score based on moon interference."""
        if not self.analyzer.images:
            return QualityScore("Moon", 100, 0.05, "Moon interference", "No data")
        
        # Get average moon data
        avg_distance = np.mean([img.moon_distance_deg for img in self.analyzer.images])
        avg_phase = np.mean([img.moon_phase for img in self.analyzer.images])
        avg_moon_alt = np.mean([img.moon_alt_deg for img in self.analyzer.images])
        
        score = 100
        
        # Moon below horizon = no penalty
        if avg_moon_alt < 0:
            details = "Moon below horizon"
            return QualityScore("Moon", 100, 0.05, "Moon interference", details)
        
        # Penalize based on phase and distance
        moon_impact = avg_phase * (1 - min(1, avg_distance / 60))
        score = 100 - (moon_impact * 80)
        
        phase_pct = avg_phase * 100
        details = f"Phase: {phase_pct:.0f}%, Distance: {avg_distance:.0f} deg, Alt: {avg_moon_alt:.0f} deg"
        return QualityScore("Moon", max(0, score), 0.05,
                           "Moon illumination and proximity", details)


# =============================================================================
# Dashboard Generator
# =============================================================================

class DashboardGenerator:
    """Generate HTML dashboard from session analysis."""
    
    def __init__(self, analyzer: SessionAnalyzer):
        self.analyzer = analyzer
        self.scorer = QualityScorer(analyzer)
    
    def generate(self, output_path: Path) -> Path:
        """Generate HTML dashboard."""
        scores = self.scorer.calculate_scores()
        overall_score = self.scorer.calculate_overall_score(scores)
        
        html = self._build_html(scores, overall_score)
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return output_path
    
    def _build_html(self, scores: List[QualityScore], overall_score: float) -> str:
        """Build complete HTML document with enhanced visualizations."""
        
        session_date = ""
        session_duration = ""
        if self.analyzer.session_start and self.analyzer.session_end:
            session_date = self.analyzer.session_start.strftime("%Y-%m-%d")
            duration = (self.analyzer.session_end - self.analyzer.session_start).total_seconds() / 3600
            session_duration = f"{duration:.1f} hours"
        
        # Prepare all chart data
        timeline_data = self._prepare_timeline_data()
        guiding_data = self._prepare_guiding_data()
        filter_zones = self._prepare_filter_zones()
        
        html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Astro Run Quality Controller - {session_date}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation"></script>
    <style>
        :root {{
            --bg-primary: #0a0a0f;
            --bg-secondary: #0d1117;
            --bg-card: #161b22;
            --bg-card-hover: #1c2128;
            --text-primary: #e6edf3;
            --text-secondary: #8b949e;
            --accent: #e94560;
            --accent-glow: rgba(233, 69, 96, 0.4);
            --success: #4ecca3;
            --success-glow: rgba(78, 204, 163, 0.4);
            --warning: #f0b429;
            --warning-glow: rgba(240, 180, 41, 0.4);
            --info: #58a6ff;
            --info-glow: rgba(88, 166, 255, 0.4);
            --ha-color: #00ff88;
            --ha-glow: rgba(0, 255, 136, 0.5);
            --oiii-color: #00aaff;
            --oiii-glow: rgba(0, 170, 255, 0.5);
            --sii-color: #ff4466;
            --sii-glow: rgba(255, 68, 102, 0.5);
            --border-glow: rgba(88, 166, 255, 0.15);
            --card-border: rgba(255, 255, 255, 0.05);
        }}
        
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
            padding: 20px;
            min-height: 100vh;
            position: relative;
            overflow-x: hidden;
        }}
        
        /* Animated star field background */
        body::before {{
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            background: 
                radial-gradient(1px 1px at 10% 10%, rgba(255,255,255,0.8) 50%, transparent 50%),
                radial-gradient(1px 1px at 20% 80%, rgba(255,255,255,0.6) 50%, transparent 50%),
                radial-gradient(1.5px 1.5px at 30% 30%, rgba(255,255,255,0.9) 50%, transparent 50%),
                radial-gradient(1px 1px at 40% 70%, rgba(255,255,255,0.5) 50%, transparent 50%),
                radial-gradient(1.5px 1.5px at 50% 20%, rgba(255,255,255,0.7) 50%, transparent 50%),
                radial-gradient(1px 1px at 60% 90%, rgba(255,255,255,0.6) 50%, transparent 50%),
                radial-gradient(2px 2px at 70% 40%, rgba(255,255,255,1) 50%, transparent 50%),
                radial-gradient(1px 1px at 80% 60%, rgba(255,255,255,0.5) 50%, transparent 50%),
                radial-gradient(1.5px 1.5px at 90% 10%, rgba(255,255,255,0.8) 50%, transparent 50%),
                radial-gradient(1px 1px at 15% 50%, rgba(255,255,255,0.4) 50%, transparent 50%),
                radial-gradient(1px 1px at 25% 25%, rgba(255,255,255,0.6) 50%, transparent 50%),
                radial-gradient(1.5px 1.5px at 35% 85%, rgba(255,255,255,0.7) 50%, transparent 50%),
                radial-gradient(1px 1px at 45% 45%, rgba(255,255,255,0.5) 50%, transparent 50%),
                radial-gradient(2px 2px at 55% 75%, rgba(255,255,255,0.9) 50%, transparent 50%),
                radial-gradient(1px 1px at 65% 15%, rgba(255,255,255,0.4) 50%, transparent 50%),
                radial-gradient(1px 1px at 75% 95%, rgba(255,255,255,0.6) 50%, transparent 50%),
                radial-gradient(1.5px 1.5px at 85% 35%, rgba(255,255,255,0.8) 50%, transparent 50%),
                radial-gradient(1px 1px at 95% 55%, rgba(255,255,255,0.5) 50%, transparent 50%);
            background-size: 250px 250px;
            animation: twinkle 8s ease-in-out infinite alternate;
            z-index: -1;
        }}
        
        @keyframes twinkle {{
            0% {{ opacity: 0.3; }}
            50% {{ opacity: 0.6; }}
            100% {{ opacity: 0.4; }}
        }}
        
        /* Nebula gradient overlay */
        body::after {{
            content: '';
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            background: 
                radial-gradient(ellipse at 20% 20%, rgba(233, 69, 96, 0.03) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 80%, rgba(0, 170, 255, 0.03) 0%, transparent 50%),
                radial-gradient(ellipse at 50% 50%, rgba(0, 255, 136, 0.02) 0%, transparent 60%);
            z-index: -1;
        }}
        
        .container {{ max-width: 1600px; margin: 0 auto; position: relative; z-index: 1; }}
        
        header {{
            text-align: center;
            padding: 40px 0;
            margin-bottom: 30px;
            position: relative;
            background: linear-gradient(180deg, rgba(233, 69, 96, 0.1) 0%, transparent 100%);
            border-radius: 20px;
        }}
        
        header::before {{
            content: '';
            position: absolute;
            bottom: 0;
            left: 10%;
            right: 10%;
            height: 2px;
            background: linear-gradient(90deg, transparent, var(--accent), transparent);
            box-shadow: 0 0 20px var(--accent-glow), 0 0 40px var(--accent-glow);
        }}
        
        h1 {{ 
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-primary); 
            font-size: 2.8em; 
            font-weight: 700;
            margin-bottom: 10px;
            text-shadow: 0 0 30px var(--accent-glow);
            letter-spacing: 3px;
        }}
        
        h2 {{ 
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-primary); 
            margin: 20px 0 15px; 
            font-size: 1.1em;
            font-weight: 400;
            letter-spacing: 2px;
            text-transform: uppercase;
        }}
        
        .meta {{ 
            color: var(--text-secondary); 
            font-size: 1.2em;
            font-weight: 300;
        }}
        
        .grid {{ 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); 
            gap: 20px; 
            margin-bottom: 30px;
        }}
        
        .card {{
            background: linear-gradient(145deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
            border-radius: 16px;
            padding: 24px;
            border: 1px solid var(--card-border);
            box-shadow: 
                0 4px 20px rgba(0,0,0,0.4),
                inset 0 1px 0 rgba(255,255,255,0.03);
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }}
        
        .card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--border-glow), transparent);
        }}
        
        .card:hover {{
            transform: translateY(-2px);
            box-shadow: 
                0 8px 30px rgba(0,0,0,0.5),
                0 0 20px var(--border-glow),
                inset 0 1px 0 rgba(255,255,255,0.05);
            border-color: var(--border-glow);
        }}
        
        .card-full {{ grid-column: 1 / -1; }}
        
        .score-display {{
            text-align: center;
            padding: 20px;
        }}
        
        /* Circular score gauge */
        .score-ring {{
            position: relative;
            width: 180px;
            height: 180px;
            margin: 0 auto 15px;
        }}
        
        .score-ring svg {{
            transform: rotate(-90deg);
        }}
        
        .score-ring-bg {{
            fill: none;
            stroke: var(--bg-secondary);
            stroke-width: 12;
        }}
        
        .score-ring-fill {{
            fill: none;
            stroke-width: 12;
            stroke-linecap: round;
            transition: stroke-dashoffset 1s ease-out;
            filter: drop-shadow(0 0 8px currentColor);
        }}
        
        .score-value {{
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
        }}
        
        .overall-score {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 3.5em;
            font-weight: 900;
            color: {self._score_color(overall_score)};
            text-shadow: 0 0 30px {self._score_color(overall_score)}80;
            line-height: 1;
        }}
        
        .score-label {{ 
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-secondary); 
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 2px;
        }}
        
        .score-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }}
        
        .score-table th, .score-table td {{
            padding: 12px 10px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        
        .score-table th {{ 
            font-family: 'JetBrains Mono', monospace;
            color: var(--info);
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 1px;
            font-weight: 400;
        }}
        
        .score-table tr:hover {{
            background: rgba(255,255,255,0.02);
        }}
        
        .score-bar {{
            background: var(--bg-primary);
            border-radius: 10px;
            height: 20px;
            overflow: hidden;
            border: 1px solid rgba(255,255,255,0.05);
        }}
        
        .score-bar-fill {{
            height: 100%;
            border-radius: 10px;
            position: relative;
            overflow: hidden;
        }}
        
        .score-bar-fill::after {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
            animation: shimmer 2s infinite;
        }}
        
        @keyframes shimmer {{
            0% {{ transform: translateX(-100%); }}
            100% {{ transform: translateX(100%); }}
        }}
        
        .chart-container {{
            position: relative;
            height: 280px;
            margin-top: 10px;
            background: rgba(0,0,0,0.2);
            border-radius: 12px;
            padding: 10px;
        }}
        
        .chart-container-tall {{
            height: 350px;
        }}
        
        .chart-controls {{
            display: flex;
            gap: 20px;
            align-items: center;
            padding: 8px 12px;
            background: rgba(0,0,0,0.3);
            border-radius: 8px;
            margin-bottom: 8px;
            font-size: 0.85rem;
        }}
        
        .chart-controls label {{
            display: flex;
            align-items: center;
            gap: 8px;
            color: var(--text-secondary);
        }}
        
        .chart-controls input[type="range"] {{
            width: 100px;
            height: 6px;
            -webkit-appearance: none;
            appearance: none;
            background: rgba(255,255,255,0.1);
            border-radius: 3px;
            cursor: pointer;
        }}
        
        .chart-controls input[type="range"]::-webkit-slider-thumb {{
            -webkit-appearance: none;
            appearance: none;
            width: 14px;
            height: 14px;
            border-radius: 50%;
            background: var(--info);
            cursor: pointer;
            box-shadow: 0 0 8px var(--info-glow);
        }}
        
        .chart-controls input[type="range"]::-moz-range-thumb {{
            width: 14px;
            height: 14px;
            border-radius: 50%;
            background: var(--info);
            cursor: pointer;
            border: none;
        }}
        
        .chart-controls span {{
            min-width: 35px;
            font-family: 'JetBrains Mono', monospace;
            color: var(--info);
        }}
        
        .stat-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
            gap: 12px;
            margin-top: 12px;
        }}
        
        .stat-item {{
            background: linear-gradient(145deg, var(--bg-primary) 0%, rgba(0,0,0,0.4) 100%);
            padding: 15px 12px;
            border-radius: 12px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.03);
            transition: all 0.3s ease;
        }}
        
        .stat-item:hover {{
            transform: scale(1.02);
            border-color: var(--border-glow);
        }}
        
        .stat-value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.6em;
            font-weight: 700;
            color: var(--success);
            text-shadow: 0 0 15px var(--success-glow);
        }}
        
        .stat-value.warning {{ 
            color: var(--warning); 
            text-shadow: 0 0 15px var(--warning-glow);
        }}
        .stat-value.bad {{ 
            color: var(--accent); 
            text-shadow: 0 0 15px var(--accent-glow);
        }}
        
        .stat-label {{ 
            color: var(--text-secondary); 
            font-size: 0.8em;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-top: 4px;
        }}
        
        /* Enhanced filter badges with glow */
        .filter-badge {{
            display: inline-block;
            padding: 5px 14px;
            border-radius: 20px;
            font-family: 'JetBrains Mono', monospace;
            font-weight: 600;
            font-size: 0.8em;
            margin: 3px;
            letter-spacing: 1px;
            transition: all 0.3s ease;
        }}
        
        .filter-badge:hover {{
            transform: scale(1.05);
        }}
        
        .filter-H, .filter-Ha {{ 
            background: rgba(0,255,136,0.15); 
            color: var(--ha-color);
            border: 1px solid rgba(0,255,136,0.3);
            box-shadow: 0 0 10px var(--ha-glow), inset 0 0 10px rgba(0,255,136,0.1);
        }}
        .filter-O, .filter-OIII {{ 
            background: rgba(0,170,255,0.15); 
            color: var(--oiii-color);
            border: 1px solid rgba(0,170,255,0.3);
            box-shadow: 0 0 10px var(--oiii-glow), inset 0 0 10px rgba(0,170,255,0.1);
        }}
        .filter-S, .filter-SII {{ 
            background: rgba(255,68,102,0.15); 
            color: var(--sii-color);
            border: 1px solid rgba(255,68,102,0.3);
            box-shadow: 0 0 10px var(--sii-glow), inset 0 0 10px rgba(255,68,102,0.1);
        }}
        .filter-L {{ 
            background: rgba(200,200,200,0.1); 
            color: #c8c8c8;
            border: 1px solid rgba(200,200,200,0.2);
        }}
        .filter-R {{ 
            background: rgba(255,100,100,0.15); 
            color: #ff7777;
            border: 1px solid rgba(255,100,100,0.3);
            box-shadow: 0 0 10px rgba(255,100,100,0.3);
        }}
        .filter-G {{ 
            background: rgba(100,255,100,0.15); 
            color: #77ff77;
            border: 1px solid rgba(100,255,100,0.3);
            box-shadow: 0 0 10px rgba(100,255,100,0.3);
        }}
        .filter-B {{ 
            background: rgba(100,100,255,0.15); 
            color: #7777ff;
            border: 1px solid rgba(100,100,255,0.3);
            box-shadow: 0 0 10px rgba(100,100,255,0.3);
        }}
        
        .legend {{
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            margin-top: 15px;
            justify-content: center;
        }}
        
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.9em;
            padding: 5px 12px;
            background: rgba(0,0,0,0.2);
            border-radius: 20px;
        }}
        
        .legend-color {{
            width: 14px;
            height: 14px;
            border-radius: 50%;
            box-shadow: 0 0 8px currentColor;
        }}
        
        .weather-card {{
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            justify-content: space-around;
            margin-top: 15px;
            padding: 15px;
            background: rgba(0,0,0,0.2);
            border-radius: 12px;
        }}
        
        .weather-item {{
            text-align: center;
            min-width: 80px;
        }}
        
        .weather-value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.4em;
            font-weight: 600;
        }}
        
        /* Moon icon styling */
        .moon-info {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            margin-top: 15px;
            padding: 12px;
            background: linear-gradient(90deg, rgba(255,215,0,0.05), rgba(255,215,0,0.1), rgba(255,215,0,0.05));
            border-radius: 25px;
            border: 1px solid rgba(255,215,0,0.2);
        }}
        
        .moon-icon {{
            font-size: 1.5em;
        }}
        
        footer {{
            text-align: center;
            padding: 30px;
            color: var(--text-secondary);
            margin-top: 40px;
            position: relative;
        }}
        
        footer::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 20%;
            right: 20%;
            height: 1px;
            background: linear-gradient(90deg, transparent, var(--border-glow), transparent);
        }}
        
        footer p {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85em;
            letter-spacing: 2px;
        }}
        
        /* Glowing accents */
        .glow-success {{ color: var(--success); text-shadow: 0 0 10px var(--success-glow); }}
        .glow-warning {{ color: var(--warning); text-shadow: 0 0 10px var(--warning-glow); }}
        .glow-accent {{ color: var(--accent); text-shadow: 0 0 10px var(--accent-glow); }}
        .glow-info {{ color: var(--info); text-shadow: 0 0 10px var(--info-glow); }}
        
        /* Pulse animation for important elements */
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.7; }}
        }}
        
        .pulse {{ animation: pulse 2s ease-in-out infinite; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>ASTRO RUN QUALITY CONTROLLER</h1>
            <p class="meta">
                <strong style="font-size:1.3em; color:var(--text-primary);">{self.analyzer.target_name or 'Unknown Target'}</strong><br>
                <span style="color:var(--info);">{session_date}</span> &bull; 
                <span style="color:var(--success);">{session_duration}</span> &bull; 
                <span style="color:var(--warning);">{len(self.analyzer.images)} exposures</span>
            </p>
        </header>
        
        <div class="grid">
            <!-- Overall Score with Ring Gauge -->
            <div class="card score-display">
                <div class="score-ring">
                    <svg width="180" height="180" viewBox="0 0 180 180">
                        <circle class="score-ring-bg" cx="90" cy="90" r="78"/>
                        <circle class="score-ring-fill" cx="90" cy="90" r="78" 
                            stroke="{self._score_color(overall_score)}"
                            stroke-dasharray="{overall_score * 4.9} 490"
                            stroke-dashoffset="0"/>
                    </svg>
                    <div class="score-value">
                        <div class="overall-score">{overall_score:.0f}</div>
                        <div class="score-label">QUALITY</div>
                    </div>
                </div>
                {self._generate_weather_display()}
            </div>
            
            <!-- Session Stats -->
            <div class="card">
                <h2>Session Overview</h2>
                <div class="stat-grid">
                    <div class="stat-item">
                        <div class="stat-value">{len(self.analyzer.images)}</div>
                        <div class="stat-label">Light Frames</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{sum(img.exposure_time for img in self.analyzer.images)/3600:.1f}h</div>
                        <div class="stat-label">Integration</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{len(self.analyzer.filter_stats)}</div>
                        <div class="stat-label">Filters</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-value">{len(self.analyzer.autofocus_events)}</div>
                        <div class="stat-label">AF Runs</div>
                    </div>
                </div>
                <div style="margin-top:12px; text-align:center;">
                    {self._generate_filter_badges()}
                </div>
                {self._generate_guiding_summary()}
                {self._generate_moon_summary()}
            </div>
        </div>
        
        <!-- Quality Scores Breakdown -->
        <div class="card card-full">
            <h2>Quality Score Breakdown</h2>
            <table class="score-table">
                <thead>
                    <tr>
                        <th>Metric</th>
                        <th>Score</th>
                        <th style="width:25%">Visual</th>
                        <th>Weight</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
                    {self._generate_score_rows(scores)}
                </tbody>
            </table>
        </div>
        
        <!-- Time Series Charts with Filter Zones -->
        <div class="card card-full">
            <h2>Altitude & Azimuth Timeline</h2>
            <div class="legend">
                {self._generate_filter_legend()}
            </div>
            <div class="chart-container chart-container-tall">
                <canvas id="altazChart"></canvas>
            </div>
        </div>
        
        <!-- Guiding Error Chart -->
        {self._generate_guiding_chart_section()}
        
        <!-- Sky Background & Temperature -->
        <div class="grid">
            <div class="card">
                <h2>Sky Background (ADU)</h2>
                <div class="chart-container">
                    <canvas id="skyBgChart"></canvas>
                </div>
            </div>
            <div class="card">
                <h2>Focuser Temperature</h2>
                <div class="chart-container">
                    <canvas id="tempChart"></canvas>
                </div>
            </div>
        </div>
        
        <!-- Focus Position & HFR -->
        <div class="grid">
            <div class="card">
                <h2>Focus Position by Filter</h2>
                <div class="chart-container">
                    <canvas id="focusChart"></canvas>
                </div>
            </div>
            <div class="card">
                <h2>HFR by Filter</h2>
                <div class="chart-container">
                    <canvas id="hfrChart"></canvas>
                </div>
            </div>
        </div>
        
        <!-- Per-Filter Statistics -->
        <div class="card card-full">
            <h2>Per-Filter Statistics</h2>
            <table class="score-table">
                <thead>
                    <tr>
                        <th>Filter</th>
                        <th>Frames</th>
                        <th>Total Time</th>
                        <th>Avg Focus</th>
                        <th>Focus Std</th>
                        <th>Avg HFR</th>
                        <th>HFR Std</th>
                        <th>Sky BG (ADU)</th>
                    </tr>
                </thead>
                <tbody>
                    {self._generate_filter_table()}
                </tbody>
            </table>
        </div>
        
        <!-- Autofocus Events -->
        <div class="card card-full">
            <h2>Autofocus Events</h2>
            <table class="score-table">
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Filter</th>
                        <th>Trigger</th>
                        <th>Temp</th>
                        <th>Best HFR</th>
                        <th>Position</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {self._generate_af_table()}
                </tbody>
            </table>
        </div>
        
        {self._generate_star_analysis_section()}
        
        <footer>
            <p style="margin-bottom:8px;">ASTRO RUN QUALITY CONTROLLER <span class="glow-info">v1.3.0</span></p>
            <p style="font-size:0.75em; opacity:0.6;">{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} UTC</p>
            <p style="font-size:0.7em; margin-top:10px; opacity:0.4;">Ad Astra Per Aspera</p>
        </footer>
    </div>
    
    <script>
        // Filter zone annotations for charts
        const filterZones = {json.dumps(filter_zones)};
        
        // Alt/Az Chart with filter zones
        const altazCtx = document.getElementById('altazChart').getContext('2d');
        new Chart(altazCtx, {{
            type: 'line',
            data: {{
                datasets: [
                    {{
                        label: 'Altitude',
                        data: {json.dumps(timeline_data['alt'])},
                        borderColor: '#4ecca3',
                        backgroundColor: 'rgba(78, 204, 163, 0.1)',
                        yAxisID: 'y',
                        tension: 0.3,
                        pointRadius: 3,
                        pointBackgroundColor: {json.dumps(timeline_data['filter_colors'])}
                    }},
                    {{
                        label: 'Azimuth',
                        data: {json.dumps(timeline_data['az'])},
                        borderColor: '#e94560',
                        backgroundColor: 'rgba(233, 69, 96, 0.1)',
                        yAxisID: 'y1',
                        tension: 0.3,
                        pointRadius: 3
                    }},
                    {{
                        label: 'Moon Alt',
                        data: {json.dumps(timeline_data['moon_alt'])},
                        borderColor: '#ffd700',
                        backgroundColor: 'rgba(255, 215, 0, 0.1)',
                        yAxisID: 'y',
                        tension: 0.3,
                        pointRadius: 2,
                        borderDash: [5, 5]
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                interaction: {{ mode: 'index', intersect: false }},
                plugins: {{
                    legend: {{ labels: {{ color: '#eaeaea' }} }},
                    annotation: {{
                        annotations: filterZones
                    }}
                }},
                scales: {{
                    x: {{
                        type: 'time',
                        time: {{ unit: 'hour', displayFormats: {{ hour: 'HH:mm' }} }},
                        grid: {{ color: 'rgba(255,255,255,0.1)' }},
                        ticks: {{ color: '#a0a0a0' }}
                    }},
                    y: {{
                        type: 'linear',
                        position: 'left',
                        min: 0,
                        max: 90,
                        grid: {{ color: 'rgba(255,255,255,0.1)' }},
                        ticks: {{ color: '#4ecca3' }},
                        title: {{ display: true, text: 'Altitude (deg)', color: '#4ecca3' }}
                    }},
                    y1: {{
                        type: 'linear',
                        position: 'right',
                        min: 0,
                        max: 360,
                        grid: {{ drawOnChartArea: false }},
                        ticks: {{ color: '#e94560' }},
                        title: {{ display: true, text: 'Azimuth (deg)', color: '#e94560' }}
                    }}
                }}
            }}
        }});
        
        // Sky Background Chart (ADU)
        const skyBgCtx = document.getElementById('skyBgChart').getContext('2d');
        new Chart(skyBgCtx, {{
            type: 'line',
            data: {{
                datasets: [{{
                    label: 'Sky Background',
                    data: {json.dumps(timeline_data['sky_bg'])},
                    borderColor: '#17a2b8',
                    backgroundColor: 'rgba(23, 162, 184, 0.1)',
                    tension: 0.3,
                    fill: true,
                    pointRadius: 3,
                    pointBackgroundColor: {json.dumps(timeline_data['sky_filter_colors'])}
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ labels: {{ color: '#eaeaea' }} }},
                    annotation: {{ annotations: filterZones }}
                }},
                scales: {{
                    x: {{
                        type: 'time',
                        time: {{ unit: 'hour', displayFormats: {{ hour: 'HH:mm' }} }},
                        grid: {{ color: 'rgba(255,255,255,0.1)' }},
                        ticks: {{ color: '#a0a0a0' }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255,255,255,0.1)' }},
                        ticks: {{ color: '#17a2b8' }},
                        title: {{ display: true, text: 'ADU', color: '#17a2b8' }}
                    }}
                }}
            }}
        }});
        
        // Temperature Chart
        const tempCtx = document.getElementById('tempChart').getContext('2d');
        new Chart(tempCtx, {{
            type: 'line',
            data: {{
                datasets: [{{
                    label: 'Focuser Temp',
                    data: {json.dumps(timeline_data['temp'])},
                    borderColor: '#ffc107',
                    backgroundColor: 'rgba(255, 193, 7, 0.1)',
                    tension: 0.3,
                    fill: true
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ labels: {{ color: '#eaeaea' }} }} }},
                scales: {{
                    x: {{
                        type: 'time',
                        time: {{ unit: 'hour', displayFormats: {{ hour: 'HH:mm' }} }},
                        grid: {{ color: 'rgba(255,255,255,0.1)' }},
                        ticks: {{ color: '#a0a0a0' }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255,255,255,0.1)' }},
                        ticks: {{ color: '#ffc107' }},
                        title: {{ display: true, text: 'Temp (C)', color: '#ffc107' }}
                    }}
                }}
            }}
        }});
        
        // Error bar plugin
        const errorBarPlugin = {{
            id: 'errorBars',
            afterDatasetsDraw: (chart) => {{
                const ctx = chart.ctx;
                chart.data.datasets.forEach((dataset, datasetIndex) => {{
                    if (!dataset.errorBars) return;
                    const meta = chart.getDatasetMeta(datasetIndex);
                    meta.data.forEach((bar, index) => {{
                        const error = dataset.errorBars[index] || 0;
                        if (error === 0) return;
                        const x = bar.x;
                        const y = bar.y;
                        const yScale = chart.scales.y;
                        const errorPixels = Math.abs(yScale.getPixelForValue(dataset.data[index] + error) - y);
                        
                        ctx.save();
                        ctx.strokeStyle = '#ffffff';
                        ctx.lineWidth = 2;
                        ctx.beginPath();
                        // Vertical line
                        ctx.moveTo(x, y - errorPixels);
                        ctx.lineTo(x, y + errorPixels);
                        // Top cap
                        ctx.moveTo(x - 5, y - errorPixels);
                        ctx.lineTo(x + 5, y - errorPixels);
                        // Bottom cap
                        ctx.moveTo(x - 5, y + errorPixels);
                        ctx.lineTo(x + 5, y + errorPixels);
                        ctx.stroke();
                        ctx.restore();
                    }});
                }});
            }}
        }};
        
        // Focus Position Chart with error bars
        const focusData = {json.dumps([s.avg_focus_position for s in self.analyzer.filter_stats.values()])};
        const focusErrors = {json.dumps([s.std_focus_position for s in self.analyzer.filter_stats.values()])};
        const focusMin = Math.min(...focusData) - Math.max(...focusErrors) * 2;
        const focusMax = Math.max(...focusData) + Math.max(...focusErrors) * 2;
        const focusPadding = (focusMax - focusMin) * 0.2;
        
        const focusCtx = document.getElementById('focusChart').getContext('2d');
        new Chart(focusCtx, {{
            type: 'bar',
            plugins: [errorBarPlugin],
            data: {{
                labels: {json.dumps(list(self.analyzer.filter_stats.keys()))},
                datasets: [{{
                    label: 'Avg Focus Position',
                    data: focusData,
                    errorBars: focusErrors,
                    backgroundColor: {json.dumps([FILTER_COLORS.get(f, 'rgba(150,150,150,0.5)').replace('0.15', '0.6') for f in self.analyzer.filter_stats.keys()])},
                    borderColor: {json.dumps([FILTER_BORDER_COLORS.get(f, '#969696') for f in self.analyzer.filter_stats.keys()])},
                    borderWidth: 2
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{
                        min: focusMin - focusPadding,
                        max: focusMax + focusPadding,
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ color: '#8b949e' }},
                        title: {{ display: true, text: 'Position (steps)', color: '#8b949e' }}
                    }},
                    x: {{
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ color: '#8b949e' }}
                    }}
                }}
            }}
        }});
        
        // HFR Chart with error bars
        const hfrData = {json.dumps([s.avg_hfr for s in self.analyzer.filter_stats.values()])};
        const hfrErrors = {json.dumps([s.std_hfr for s in self.analyzer.filter_stats.values()])};
        const hfrMax = Math.max(...hfrData.map((v, i) => v + (hfrErrors[i] || 0)));
        const hfrMin = Math.max(0, Math.min(...hfrData.map((v, i) => v - (hfrErrors[i] || 0))) * 0.8);
        
        const hfrCtx = document.getElementById('hfrChart').getContext('2d');
        new Chart(hfrCtx, {{
            type: 'bar',
            plugins: [errorBarPlugin],
            data: {{
                labels: {json.dumps(list(self.analyzer.filter_stats.keys()))},
                datasets: [{{
                    label: 'Avg HFR',
                    data: hfrData,
                    errorBars: hfrErrors,
                    backgroundColor: {json.dumps([FILTER_COLORS.get(f, 'rgba(150,150,150,0.5)').replace('0.15', '0.6') for f in self.analyzer.filter_stats.keys()])},
                    borderColor: {json.dumps([FILTER_BORDER_COLORS.get(f, '#969696') for f in self.analyzer.filter_stats.keys()])},
                    borderWidth: 2
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    y: {{
                        min: hfrMin,
                        max: hfrMax * 1.2,
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ color: '#8b949e' }},
                        title: {{ display: true, text: 'HFR (pixels)', color: '#8b949e' }}
                    }},
                    x: {{
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{ color: '#8b949e' }}
                    }}
                }}
            }}
        }});
        
        {self._generate_guiding_chart_script(guiding_data)}
    </script>
</body>
</html>'''
        return html
    
    def _score_color(self, score: float) -> str:
        """Return color based on score value."""
        if score >= 80:
            return "#4ecca3"
        elif score >= 60:
            return "#ffc107"
        elif score >= 40:
            return "#fd7e14"
        else:
            return "#e94560"
    
    def _prepare_timeline_data(self) -> Dict:
        """Prepare time series data for charts."""
        alt_points = []
        az_points = []
        moon_alt_points = []
        sky_bg_points = []
        sky_filter_colors = []  # Separate colors array aligned with sky_bg_points
        guide_rms_points = []
        filter_colors = []
        
        for img in sorted(self.analyzer.images, key=lambda x: x.timestamp):
            ts = img.timestamp.isoformat()
            alt_points.append({'x': ts, 'y': round(img.alt_deg, 1)})
            az_points.append({'x': ts, 'y': round(img.az_deg, 1)})
            moon_alt_points.append({'x': ts, 'y': round(max(0, img.moon_alt_deg), 1)})
            
            # Use raw ADU for sky background
            if img.sky_background > 0:
                sky_bg_points.append({'x': ts, 'y': round(img.sky_background, 0)})
                sky_filter_colors.append(FILTER_BORDER_COLORS.get(img.filter_name, '#969696'))
            # Per-image guiding RMS
            if img.guide_rms_total > 0:
                guide_rms_points.append({'x': ts, 'y': round(img.guide_rms_total, 2)})
            filter_colors.append(FILTER_BORDER_COLORS.get(img.filter_name, '#969696'))

        
        # Temperature data from AF events
        temp_points = []
        for af in sorted(self.analyzer.autofocus_events, key=lambda x: x.timestamp):
            if af.temperature > 0:
                temp_points.append({
                    'x': af.timestamp.isoformat(),
                    'y': round(af.temperature, 1)
                })
        
        # Get session time bounds for consistent X-axis across charts
        session_start = None
        session_end = None
        if self.analyzer.session_start:
            session_start = self.analyzer.session_start.isoformat()
        if self.analyzer.session_end:
            session_end = self.analyzer.session_end.isoformat()
        
        return {
            'alt': alt_points,
            'az': az_points,
            'moon_alt': moon_alt_points,
            'sky_bg': sky_bg_points,
            'sky_filter_colors': sky_filter_colors,
            'guide_rms': guide_rms_points,
            'temp': temp_points,
            'filter_colors': filter_colors,
            'session_start': session_start,
            'session_end': session_end
        }
    
    def _prepare_guiding_data(self) -> Dict:
        """Prepare guiding error data for charts with outlier removal, proper time ordering,
        and per-image RMS overlays. Uses absolute local time for x-axis."""
        if not self.analyzer.guiding_frames:
            return {'ra': [], 'dec': [], 'per_image_rms': [], 'y_min': -5, 'y_max': 5, 'outliers_removed': 0}
        
        frames = self.analyzer.guiding_frames
        
        # Filter frames with valid absolute timestamps and sort by absolute time
        frames_with_abs = [f for f in frames if f.timestamp_abs is not None]
        frames_sorted = sorted(frames_with_abs, key=lambda f: f.timestamp_abs)
        
        if not frames_sorted:
            return {'ra': [], 'dec': [], 'per_image_rms': [], 'y_min': -5, 'y_max': 5, 'outliers_removed': 0}
        
        # Calculate all errors with absolute timestamps (ISO format for Chart.js)
        timestamps = [f.timestamp_abs.isoformat() for f in frames_sorted]
        all_ra = np.array([f.ra_raw * self.analyzer.pixel_scale for f in frames_sorted])
        all_dec = np.array([f.dec_raw * self.analyzer.pixel_scale for f in frames_sorted])
        
        # Sigma clipping: remove points > 4 sigma from median
        def sigma_clip_mask(data, sigma=4):
            median = np.median(data)
            mad = np.median(np.abs(data - median))
            std_est = mad * 1.4826
            if std_est == 0:
                return np.ones(len(data), dtype=bool)
            return np.abs(data - median) < (sigma * std_est)
        
        # Create mask for valid points
        ra_mask = sigma_clip_mask(all_ra)
        dec_mask = sigma_clip_mask(all_dec)
        valid_mask = ra_mask & dec_mask
        
        # Get valid data
        valid_indices = np.where(valid_mask)[0]
        
        # Subsample AFTER filtering to maintain time ordering
        n_valid = len(valid_indices)
        if n_valid > 2000:
            sample_indices = np.linspace(0, n_valid - 1, 2000, dtype=int)
            valid_indices = valid_indices[sample_indices]
        
        # Build chart data in time order
        ra_points = []
        dec_points = []
        for idx in valid_indices:
            ra_points.append({'x': timestamps[idx], 'y': round(all_ra[idx], 3)})
            dec_points.append({'x': timestamps[idx], 'y': round(all_dec[idx], 3)})
        
        # Per-image data with mean and std for error bars
        per_image_data = []
        for img in sorted(self.analyzer.images, key=lambda x: x.timestamp):
            if img.guide_frame_count > 0 and len(img.guide_frames_ra) > 0:
                ra_vals = np.array(img.guide_frames_ra)
                dec_vals = np.array(img.guide_frames_dec)
                
                # Get filter color for this image
                filter_color = FILTER_BORDER_COLORS.get(img.filter_name, '#ffd700')
                
                per_image_data.append({
                    'x': img.timestamp.isoformat(),
                    'ra_mean': round(float(np.mean(ra_vals)), 3),
                    'ra_std': round(float(np.std(ra_vals)), 3),
                    'dec_mean': round(float(np.mean(dec_vals)), 3),
                    'dec_std': round(float(np.std(dec_vals)), 3),
                    'rms_total': round(img.guide_rms_total, 3),
                    'exposure': img.exposure_time,
                    'filter': img.filter_name,
                    'filter_color': filter_color,
                    'frame_count': img.guide_frame_count
                })
        
        # Calculate date range for x-axis label
        date_label = ""
        if frames_sorted:
            start_date = frames_sorted[0].timestamp_abs.strftime("%Y-%m-%d")
            end_date = frames_sorted[-1].timestamp_abs.strftime("%Y-%m-%d")
            if start_date == end_date:
                date_label = start_date
            else:
                date_label = f"{start_date} to {end_date}"
        
        # Calculate axis bounds with 20% padding
        if len(valid_indices) > 0:
            valid_ra = all_ra[valid_mask]
            valid_dec = all_dec[valid_mask]
            all_valid = np.concatenate([valid_ra, valid_dec])
            y_min = float(np.min(all_valid))
            y_max = float(np.max(all_valid))
            y_range = max(y_max - y_min, 0.1)
            y_padding = y_range * 0.2
        else:
            y_min, y_max, y_padding = -5, 5, 1
        
        return {
            'ra': ra_points, 
            'dec': dec_points, 
            'per_image_data': per_image_data,
            'date_label': date_label,
            'y_min': round(y_min - y_padding, 2),
            'y_max': round(y_max + y_padding, 2),
            'outliers_removed': int(np.sum(~valid_mask))
        }
    
    def _prepare_filter_zones(self) -> List[Dict]:
        """Create annotation zones for filter changes."""
        if not self.analyzer.images:
            return []
        
        zones = []
        sorted_images = sorted(self.analyzer.images, key=lambda x: x.timestamp)
        
        current_filter = None
        zone_start = None
        
        for i, img in enumerate(sorted_images):
            if img.filter_name != current_filter:
                # Close previous zone
                if current_filter and zone_start:
                    zones.append({
                        'type': 'box',
                        'xMin': zone_start,
                        'xMax': img.timestamp.isoformat(),
                        'backgroundColor': FILTER_COLORS.get(current_filter, 'rgba(150,150,150,0.1)'),
                        'borderWidth': 0
                    })
                
                # Start new zone
                current_filter = img.filter_name
                zone_start = img.timestamp.isoformat()
        
        # Close last zone
        if current_filter and zone_start:
            zones.append({
                'type': 'box',
                'xMin': zone_start,
                'xMax': sorted_images[-1].timestamp.isoformat(),
                'backgroundColor': FILTER_COLORS.get(current_filter, 'rgba(150,150,150,0.1)'),
                'borderWidth': 0
            })
        
        return zones
    
    def _generate_filter_legend(self) -> str:
        """Generate legend for filter colors."""
        items = []
        for filter_name in self.analyzer.filter_stats.keys():
            color = FILTER_BORDER_COLORS.get(filter_name, '#969696')
            items.append(f'''
                <div class="legend-item">
                    <div class="legend-color" style="background:{color}"></div>
                    <span>{filter_name}</span>
                </div>
            ''')
        return ''.join(items)
    
    def _generate_weather_display(self) -> str:
        """Generate weather info display."""
        if not self.analyzer.weather:
            return '<div class="weather-card"><span style="color:var(--text-secondary);">Weather data unavailable</span></div>'
        
        w = self.analyzer.weather
        # Convert to mph for wind
        wind_mph = w.wind_speed_kmh / 1.60934
        gust_mph = w.wind_gust_kmh / 1.60934
        
        wind_class = "glow-warning" if wind_mph > 12 else "glow-success"
        gust_class = "glow-accent" if gust_mph > 22 else ("glow-warning" if gust_mph > 15 else "glow-success")
        cloud_class = "glow-warning" if w.cloud_cover_pct > 30 else "glow-success"
        
        return f'''
            <div class="weather-card">
                <div class="weather-item">
                    <div class="weather-value glow-info">{w.temperature_c:.0f}°C</div>
                    <div class="stat-label">TEMP</div>
                </div>
                <div class="weather-item">
                    <div class="weather-value {wind_class}">{wind_mph:.0f}</div>
                    <div class="stat-label">WIND mph</div>
                </div>
                <div class="weather-item">
                    <div class="weather-value {gust_class}">{gust_mph:.0f}</div>
                    <div class="stat-label">GUSTS mph</div>
                </div>
                <div class="weather-item">
                    <div class="weather-value {cloud_class}">{w.cloud_cover_pct:.0f}%</div>
                    <div class="stat-label">CLOUDS</div>
                </div>
            </div>
        '''
    
    def _generate_guiding_summary(self) -> str:
        """Generate guiding stats summary."""
        if not self.analyzer.guiding_stats:
            return '<div style="margin-top:12px; text-align:center; color:var(--text-secondary);">No PHD2 guiding data</div>'
        
        gs = self.analyzer.guiding_stats
        rms_class = "glow-success" if gs.rms_total < 1.5 else ("glow-warning" if gs.rms_total < 2.5 else "glow-accent")
        
        return f'''
            <h2 style="font-size:0.9em; margin-top:15px;">GUIDING PERFORMANCE</h2>
            <div class="stat-grid">
                <div class="stat-item">
                    <div class="stat-value {rms_class}">{gs.rms_total:.2f}"</div>
                    <div class="stat-label">RMS TOTAL</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value glow-info">{gs.rms_ra:.2f}"</div>
                    <div class="stat-label">RMS RA</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value glow-info">{gs.rms_dec:.2f}"</div>
                    <div class="stat-label">RMS DEC</div>
                </div>
                <div class="stat-item">
                    <div class="stat-value glow-success">{gs.avg_snr:.0f}</div>
                    <div class="stat-label">AVG SNR</div>
                </div>
            </div>
        '''
    
    def _generate_moon_summary(self) -> str:
        """Generate moon info summary."""
        if not self.analyzer.images:
            return ''
        
        avg_phase = np.mean([img.moon_phase for img in self.analyzer.images]) * 100
        avg_dist = np.mean([img.moon_distance_deg for img in self.analyzer.images])
        avg_alt = np.mean([img.moon_alt_deg for img in self.analyzer.images])
        
        moon_status = "Below horizon" if avg_alt < 0 else f"{avg_alt:.0f} deg alt"
        
        # Select moon emoji based on phase
        if avg_phase < 5:
            moon_emoji = "&#127761;"  # New moon
        elif avg_phase < 35:
            moon_emoji = "&#127764;"  # Waxing crescent
        elif avg_phase < 65:
            moon_emoji = "&#127763;"  # First quarter
        elif avg_phase < 85:
            moon_emoji = "&#127765;"  # Waxing gibbous
        else:
            moon_emoji = "&#127765;"  # Full moon
        
        return f'''
            <div class="moon-info">
                <span class="moon-icon">{moon_emoji}</span>
                <span>{avg_phase:.0f}% illuminated &bull; {avg_dist:.0f}° from target &bull; {moon_status}</span>
            </div>
        '''
    
    def _generate_guiding_chart_section(self) -> str:
        """Generate the guiding error chart section."""
        if not self.analyzer.guiding_frames:
            return ''
        
        return '''
        <div class="card card-full">
            <h2>Guiding Error (arcsec)</h2>
            <div class="chart-container chart-container-tall">
                <canvas id="guidingChart"></canvas>
            </div>
        </div>
        '''
    
    def _generate_guiding_chart_script(self, guiding_data: Dict) -> str:
        """Generate JavaScript for guiding chart with per-image error bars."""
        if not guiding_data['ra']:
            return ''
        
        y_min = guiding_data.get('y_min', -5)
        y_max = guiding_data.get('y_max', 5)
        outliers = guiding_data.get('outliers_removed', 0)
        per_image_data = guiding_data.get('per_image_data', [])
        date_label = guiding_data.get('date_label', '')
        outlier_note = f" ({outliers} outliers removed)" if outliers > 0 else ""
        
        # Build per-image data with error bars
        per_image_json = json.dumps(per_image_data)
        
        return f'''
        // Custom error bar plugin for per-image data
        const perImageErrorBarPlugin = {{
            id: 'perImageErrorBars',
            afterDatasetsDraw: (chart) => {{
                const ctx = chart.ctx;
                chart.data.datasets.forEach((dataset, datasetIndex) => {{
                    if (dataset.label !== 'Per-Image Mean') return;
                    const meta = chart.getDatasetMeta(datasetIndex);
                    const perImageData = dataset.perImageData || [];
                    
                    meta.data.forEach((point, index) => {{
                        if (index >= perImageData.length) return;
                        const d = perImageData[index];
                        const x = point.x;
                        const yScale = chart.scales.y;
                        
                        // Draw error bar for combined std - use filter color
                        const combinedStd = Math.sqrt(d.ra_std * d.ra_std + d.dec_std * d.dec_std);
                        const yTop = yScale.getPixelForValue(d.ra_mean + combinedStd);
                        const yBottom = yScale.getPixelForValue(d.ra_mean - combinedStd);
                        const filterColor = d.filter_color || '#ffd700';
                        
                        ctx.save();
                        ctx.strokeStyle = filterColor;
                        ctx.lineWidth = 2;
                        ctx.beginPath();
                        // Vertical line
                        ctx.moveTo(x, yTop);
                        ctx.lineTo(x, yBottom);
                        // Top cap
                        ctx.moveTo(x - 4, yTop);
                        ctx.lineTo(x + 4, yTop);
                        // Bottom cap
                        ctx.moveTo(x - 4, yBottom);
                        ctx.lineTo(x + 4, yBottom);
                        ctx.stroke();
                        ctx.restore();
                    }});
                }});
            }}
        }};
        
        // Guiding Error Chart with Per-Image Error Bars
        const guidingCtx = document.getElementById('guidingChart');
        if (guidingCtx) {{
            const perImageData = {per_image_json};
            
            // Create mean data points for per-image overlay
            const perImageMeanData = perImageData.map(d => ({{ x: d.x, y: d.ra_mean }}));
            
            new Chart(guidingCtx.getContext('2d'), {{
                type: 'line',
                plugins: [perImageErrorBarPlugin],
                data: {{
                    datasets: [
                        {{
                            label: 'RA Error',
                            data: {json.dumps(guiding_data['ra'])},
                            borderColor: 'rgba(78, 204, 163, 0.4)',
                            backgroundColor: 'rgba(78, 204, 163, 0.05)',
                            pointRadius: 0,
                            borderWidth: 1,
                            order: 3
                        }},
                        {{
                            label: 'DEC Error',
                            data: {json.dumps(guiding_data['dec'])},
                            borderColor: 'rgba(233, 69, 96, 0.4)',
                            backgroundColor: 'rgba(233, 69, 96, 0.05)',
                            pointRadius: 0,
                            borderWidth: 1,
                            order: 3
                        }},
                        {{
                            label: 'Per-Image Mean',
                            data: perImageMeanData,
                            perImageData: perImageData,
                            borderColor: perImageData.map(d => d.filter_color || '#ffd700'),
                            backgroundColor: perImageData.map(d => d.filter_color || '#ffd700'),
                            pointRadius: 5,
                            pointStyle: 'circle',
                            showLine: false,
                            order: 1
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {{ mode: 'index', intersect: false }},
                    plugins: {{
                        legend: {{ labels: {{ color: '#e6edf3' }} }},
                        title: {{
                            display: true,
                            text: 'Guiding Error vs Local Time ({date_label}){outlier_note}',
                            color: '#8b949e',
                            font: {{ size: 12 }}
                        }},
                        tooltip: {{
                            callbacks: {{
                                label: function(context) {{
                                    if (context.dataset.label === 'Per-Image Mean') {{
                                        const idx = context.dataIndex;
                                        const d = perImageData[idx];
                                        const combinedStd = Math.sqrt(d.ra_std * d.ra_std + d.dec_std * d.dec_std);
                                        return [
                                            `Mean: ${{d.ra_mean.toFixed(2)}}" ± ${{combinedStd.toFixed(2)}}"`,
                                            `RMS Total: ${{d.rms_total.toFixed(2)}}"`,
                                            `${{d.frame_count}} frames, ${{d.filter}} filter`
                                        ];
                                    }}
                                    return `${{context.dataset.label}}: ${{context.parsed.y.toFixed(2)}}"`;
                                }}
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{
                            type: 'time',
                            time: {{ 
                                unit: 'hour',
                                displayFormats: {{ hour: 'HH:mm' }},
                                tooltipFormat: 'HH:mm:ss'
                            }},
                            title: {{ display: true, text: 'Local Time ({date_label})', color: '#8b949e' }},
                            grid: {{ color: 'rgba(255,255,255,0.05)' }},
                            ticks: {{ color: '#8b949e' }}
                        }},
                        y: {{
                            min: {y_min},
                            max: {y_max},
                            grid: {{ color: 'rgba(255,255,255,0.05)' }},
                            ticks: {{ color: '#8b949e' }},
                            title: {{ display: true, text: 'Error (arcsec)', color: '#8b949e' }}
                        }}
                    }}
                }}
            }});
        }}
        '''
    
    def _generate_filter_badges(self) -> str:
        """Generate HTML for filter badges."""
        badges = []
        for name, stats in self.analyzer.filter_stats.items():
            badges.append(
                f'<span class="filter-badge filter-{name}">'
                f'{name}: {stats.image_count} ({stats.total_exposure_time/60:.0f}m)</span>'
            )
        return ' '.join(badges)
    
    def _generate_score_rows(self, scores: List[QualityScore]) -> str:
        """Generate table rows for quality scores."""
        rows = []
        for s in scores:
            color = self._score_color(s.score)
            rows.append(f'''
                <tr>
                    <td><strong>{s.name}</strong><br><small style="color:#a0a0a0">{s.description}</small></td>
                    <td style="color:{color}; font-weight:bold;">{s.score:.0f}</td>
                    <td>
                        <div class="score-bar">
                            <div class="score-bar-fill" style="width:{s.score}%; background:{color};"></div>
                        </div>
                    </td>
                    <td>{s.weight*100:.0f}%</td>
                    <td><small>{s.details}</small></td>
                </tr>
            ''')
        return '\n'.join(rows)
    
    def _generate_filter_table(self) -> str:
        """Generate table rows for filter statistics."""
        rows = []
        for name, stats in self.analyzer.filter_stats.items():
            rows.append(f'''
                <tr>
                    <td><span class="filter-badge filter-{name}">{name}</span></td>
                    <td>{stats.image_count}</td>
                    <td>{stats.total_exposure_time/60:.1f} min</td>
                    <td>{stats.avg_focus_position:.0f}</td>
                    <td>+/-{stats.std_focus_position:.1f}</td>
                    <td>{stats.avg_hfr:.2f}</td>
                    <td>+/-{stats.std_hfr:.2f}</td>
                    <td>{stats.avg_sky_background:.0f} +/-{stats.std_sky_background:.0f}</td>
                </tr>
            ''')
        return '\n'.join(rows)
    
    def _generate_af_table(self) -> str:
        """Generate table rows for autofocus events."""
        rows = []
        for af in self.analyzer.autofocus_events:
            status_color = "#4ecca3" if af.success else "#e94560"
            status_text = "Success" if af.success else "Failed"
            rows.append(f'''
                <tr>
                    <td>{af.timestamp.strftime("%H:%M:%S")}</td>
                    <td><span class="filter-badge filter-{af.filter_name}">{af.filter_name}</span></td>
                    <td>{af.trigger}</td>
                    <td>{af.temperature:.1f}C</td>
                    <td>{af.best_hfr:.2f}</td>
                    <td>{af.final_position}</td>
                    <td style="color:{status_color}">{status_text}</td>
                </tr>
            ''')
        return '\n'.join(rows)
    
    def _generate_star_analysis_section(self) -> str:
        """Generate HTML for per-frame star analysis with SPC charts."""
        if not self.analyzer.frame_star_stats:
            return ""  # No star analysis data
        
        valid_frames = [f for f in self.analyzer.frame_star_stats if f.num_stars > 0]
        if not valid_frames:
            return ""
        
        # Sort frames by timestamp
        valid_frames = sorted(valid_frames, key=lambda x: x.timestamp or datetime.min)
        
        # Get local timezone for conversion
        local_tz = self.analyzer.timezone
        
        # Compute per-filter baselines for more accurate flagging
        filter_baselines = compute_filter_baselines(valid_frames)
        
        # Flag each frame using filter-specific baselines
        frame_flags = {}
        for f in valid_frames:
            baseline = filter_baselines.get(f.filter_name)
            if baseline:
                frame_flags[f.filename] = flag_frame(f, baseline)
            else:
                frame_flags[f.filename] = FrameFlags()
        
        # Calculate session-wide SPC statistics (for charts)
        hfr_values = [f.median_hfr for f in valid_frames if f.median_hfr > 0]
        fwhm_values = [f.median_fwhm for f in valid_frames if f.median_fwhm > 0]
        ecc_values = [f.median_eccentricity for f in valid_frames if f.median_eccentricity > 0]
        hfr_std_values = [f.std_hfr for f in valid_frames if f.std_hfr > 0]
        
        # HFR statistics
        hfr_mean = float(np.mean(hfr_values)) if hfr_values else 0
        hfr_sigma = float(np.std(hfr_values)) if len(hfr_values) > 1 else 0
        hfr_ucl = hfr_mean + 3 * hfr_sigma  # Upper Control Limit
        
        # FWHM statistics
        fwhm_mean = float(np.mean(fwhm_values)) if fwhm_values else 0
        fwhm_sigma = float(np.std(fwhm_values)) if len(fwhm_values) > 1 else 0
        fwhm_ucl = fwhm_mean + 3 * fwhm_sigma
        
        # Eccentricity statistics
        ecc_mean = float(np.mean(ecc_values)) if ecc_values else 1.0
        ecc_sigma = float(np.std(ecc_values)) if len(ecc_values) > 1 else 0
        ecc_ucl = ecc_mean + 3 * ecc_sigma
        
        # HFR Std Dev statistics (for detecting non-uniformity)
        hfr_std_mean = float(np.mean(hfr_std_values)) if hfr_std_values else 0
        hfr_std_sigma = float(np.std(hfr_std_values)) if len(hfr_std_values) > 1 else 0
        hfr_std_ucl = hfr_std_mean + 3 * hfr_std_sigma
        
        # Prepare time series data with local time conversion and flagging
        hfr_time_data = []
        fwhm_time_data = []
        ecc_time_data = []
        hfr_std_time_data = []
        
        for f in valid_frames:
            if not f.timestamp:
                continue
            
            # Convert to local time
            if f.timestamp.tzinfo is None:
                local_ts = f.timestamp.replace(tzinfo=zoneinfo.ZoneInfo('UTC')).astimezone(local_tz)
            else:
                local_ts = f.timestamp.astimezone(local_tz)
            
            ts_iso = local_ts.isoformat()
            filter_color = FILTER_BORDER_COLORS.get(f.filter_name, '#17a2b8')
            
            # HFR data with flagging
            if f.median_hfr > 0:
                flagged = f.median_hfr > hfr_ucl
                hfr_time_data.append({
                    'x': ts_iso,
                    'y': round(f.median_hfr, 2),
                    'std': round(f.std_hfr, 2),
                    'filter': f.filter_name,
                    'filter_color': filter_color,
                    'stars': f.num_stars,
                    'filename': f.filename,
                    'flagged': flagged
                })
            
            # FWHM data with flagging
            if f.median_fwhm > 0:
                flagged = f.median_fwhm > fwhm_ucl
                fwhm_time_data.append({
                    'x': ts_iso,
                    'y': round(f.median_fwhm, 2),
                    'std': round(f.std_fwhm, 2),
                    'filter': f.filter_name,
                    'filter_color': filter_color,
                    'stars': f.num_stars,
                    'filename': f.filename,
                    'flagged': flagged
                })
            
            # Eccentricity data with flagging
            if f.median_eccentricity > 0:
                flagged = f.median_eccentricity > ecc_ucl
                ecc_time_data.append({
                    'x': ts_iso,
                    'y': round(f.median_eccentricity, 3),
                    'std': round(f.std_eccentricity, 3),
                    'filter': f.filter_name,
                    'filter_color': filter_color,
                    'stars': f.num_stars,
                    'filename': f.filename,
                    'flagged': flagged
                })
            
            # HFR Std Dev data (for non-uniformity detection)
            if f.std_hfr > 0:
                flagged = f.std_hfr > hfr_std_ucl
                hfr_std_time_data.append({
                    'x': ts_iso,
                    'y': round(f.std_hfr, 2),
                    'filter': f.filter_name,
                    'filter_color': filter_color,
                    'stars': f.num_stars,
                    'filename': f.filename,
                    'flagged': flagged
                })
        
        # Calculate Y-axis ranges including error bars
        def calc_y_range(data, include_std=True):
            if not data:
                return 0, 1
            if include_std:
                y_max = max(d['y'] + d.get('std', 0) for d in data) * 1.15
                y_min = max(0, min(d['y'] - d.get('std', 0) for d in data) * 0.85)
            else:
                y_max = max(d['y'] for d in data) * 1.15
                y_min = max(0, min(d['y'] for d in data) * 0.85)
            return round(y_min, 2), round(y_max, 2)
        
        hfr_y_min, hfr_y_max = calc_y_range(hfr_time_data)
        fwhm_y_min, fwhm_y_max = calc_y_range(fwhm_time_data)
        ecc_y_min, ecc_y_max = calc_y_range(ecc_time_data)
        hfr_std_y_min, hfr_std_y_max = calc_y_range(hfr_std_time_data, include_std=False)
        
        # Ensure UCL is visible
        hfr_y_max = max(hfr_y_max, hfr_ucl * 1.1)
        fwhm_y_max = max(fwhm_y_max, fwhm_ucl * 1.1)
        ecc_y_max = max(ecc_y_max, ecc_ucl * 1.05)
        hfr_std_y_max = max(hfr_std_y_max, hfr_std_ucl * 1.1)
        
        # Count flagged frames (using filter-specific flags)
        hfr_flagged = sum(1 for f in valid_frames if frame_flags.get(f.filename, FrameFlags()).hfr_flag)
        ecc_flagged = sum(1 for f in valid_frames if frame_flags.get(f.filename, FrameFlags()).ecc_flag)
        flux_flagged = sum(1 for f in valid_frames if frame_flags.get(f.filename, FrameFlags()).flux_flag)
        total_bad = sum(1 for f in valid_frames if frame_flags.get(f.filename, FrameFlags()).is_bad)
        
        # For charts, keep the session-wide flagging  
        fwhm_flagged = sum(1 for d in fwhm_time_data if d['flagged'])
        hfr_std_flagged = sum(1 for d in hfr_std_time_data if d['flagged'])
        
        # Generate table rows with filter-specific flagging
        table_rows = []
        for f in valid_frames:
            if not f.timestamp:
                continue
            # Convert to local time for display
            if f.timestamp.tzinfo is None:
                local_ts = f.timestamp.replace(tzinfo=zoneinfo.ZoneInfo('UTC')).astimezone(local_tz)
            else:
                local_ts = f.timestamp.astimezone(local_tz)
            
            filter_class = f"filter-{f.filter_name}" if f.filter_name else ""
            
            # Get frame flags (filter-specific)
            flags = frame_flags.get(f.filename, FrameFlags())
            
            # Flag row if any critical flag is set
            row_style = 'background: rgba(233, 69, 96, 0.2);' if flags.is_bad else ''
            hfr_flag_icon = ' &#9888;' if flags.hfr_flag else ''
            ecc_flag_icon = ' &#9888;' if flags.ecc_flag else ''
            flux_flag_icon = ' &#9729;' if flags.flux_flag else ''  # Cloud icon
            
            # Tracking type badge
            tracking_color = {
                'good': '#28a745',
                'wind': '#ffc107', 
                'mechanical': '#17a2b8'
            }.get(f.tracking_type, '#6c757d')
            tracking_badge = f'<span style="color:{tracking_color};">{f.tracking_type}</span>'
            
            # Relative flux with warning
            flux_str = f'{f.relative_flux:.2f}'
            if f.relative_flux < 0.8:
                flux_str = f'<span style="color:#e94560;">{flux_str}{flux_flag_icon}</span>'
            
            table_rows.append(f'''
                <tr style="{row_style}">
                    <td>{local_ts.strftime("%H:%M:%S")}</td>
                    <td><span class="filter-badge {filter_class}">{f.filter_name or "-"}</span></td>
                    <td>{f.num_stars}</td>
                    <td>{f.median_hfr:.2f}{hfr_flag_icon}</td>
                    <td>{f.median_fwhm:.2f}</td>
                    <td>{f.median_eccentricity:.2f}{ecc_flag_icon}</td>
                    <td>{tracking_badge}</td>
                    <td>{flux_str}</td>
                    <td>{f.guide_rms:.2f}"</td>
                </tr>
            ''')
        
        return f'''
        <!-- Per-Frame Star Analysis Section -->
        <div class="card card-full">
            <h2>Per-Frame Star Analysis: HFR Over Time</h2>
            <p style="color: #8b949e; margin-bottom: 10px;">
                Session: x&#772; = {hfr_mean:.2f} px | 
                <span style="color: #e94560;">{hfr_flagged} HFR flags</span> |
                <span style="color: #ffc107;">{ecc_flagged} Ecc flags</span> |
                <span style="color: #17a2b8;">{flux_flagged} cloud flags</span> |
                <strong>{total_bad} bad frames</strong>
            </p>
            <div class="chart-container" style="height: 280px;">
                <canvas id="hfrTimeChart"></canvas>
            </div>
        </div>
        
        <div class="card card-full">
            <h2>HFR Std Dev Over Time (Non-Uniformity Detection)</h2>
            <p style="color: #8b949e; margin-bottom: 10px;">
                Spike = cloud/obstruction (uneven star sizes) | Stable = focus issue (uniform blur)<br>
                x&#772; = {hfr_std_mean:.2f} px | UCL (3&sigma;) = {hfr_std_ucl:.2f} px |
                <span style="color: #e94560;">{hfr_std_flagged} frames flagged</span>
            </p>
            <div class="chart-container" style="height: 250px;">
                <canvas id="hfrStdChart"></canvas>
            </div>
        </div>
        
        <div class="card card-full">
            <h2>FWHM Over Time</h2>
            <p style="color: #8b949e; margin-bottom: 10px;">
                x&#772; = {fwhm_mean:.2f} px | UCL (3&sigma;) = {fwhm_ucl:.2f} px |
                <span style="color: #e94560;">{fwhm_flagged} frames flagged</span>
            </p>
            <div class="chart-container" style="height: 280px;">
                <canvas id="fwhmTimeChart"></canvas>
            </div>
        </div>
        
        <div class="card card-full">
            <h2>Eccentricity Over Time</h2>
            <p style="color: #8b949e; margin-bottom: 10px;">
                1.0 = perfectly round | Higher = elongated stars (tracking/wind issues)<br>
                x&#772; = {ecc_mean:.2f} | UCL (3&sigma;) = {ecc_ucl:.2f} |
                <span style="color: #e94560;">{ecc_flagged} frames flagged</span>
            </p>
            <div class="chart-container" style="height: 280px;">
                <canvas id="eccTimeChart"></canvas>
            </div>
        </div>
        
        <div class="card card-full">
            <h2>Per-Filter Quality Baselines</h2>
            <p style="color: #8b949e; margin-bottom: 10px;">
                Reference baselines computed with sigma-clipping. UCL = baseline + 3&sigma;
            </p>
            <table class="score-table" style="margin-bottom: 20px;">
                <thead>
                    <tr>
                        <th>Filter</th>
                        <th>Frames</th>
                        <th>HFR Baseline</th>
                        <th>HFR UCL</th>
                        <th>Ecc Baseline</th>
                        <th>Ecc UCL</th>
                        <th>Flux Baseline</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(f"""
                    <tr>
                        <td><span class="filter-badge filter-{b.filter_name}">{b.filter_name}</span></td>
                        <td>{b.num_frames}</td>
                        <td>{b.hfr_baseline:.2f} px</td>
                        <td>{(b.hfr_baseline + 3 * b.hfr_std):.2f} px</td>
                        <td>{b.ecc_baseline:.2f}</td>
                        <td>{(b.ecc_baseline + 3 * b.ecc_std):.2f}</td>
                        <td>{b.flux_baseline:.0f} ADU</td>
                    </tr>
                    """ for b in filter_baselines.values())}
                </tbody>
            </table>
        </div>
        
        <div class="card card-full">
            <h2>Per-Frame Star Statistics</h2>
            <p style="color: #8b949e; margin-bottom: 10px;">
                Rows highlighted in red have flags set. &#9888; = exceeds UCL, &#9729; = cloud (flux &lt;0.8)
            </p>
            <div style="max-height: 400px; overflow-y: auto;">
                <table class="score-table">
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Filter</th>
                            <th>Stars</th>
                            <th>HFR (px)</th>
                            <th>FWHM (px)</th>
                            <th>Eccentricity</th>
                            <th>Tracking</th>
                            <th>Rel. Flux</th>
                            <th>Guide RMS</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join(table_rows)}
                    </tbody>
                </table>
            </div>
        </div>
        
        <script>
            // Per-frame star analysis data
            const hfrTimeData = {json.dumps(hfr_time_data)};
            const hfrStdTimeData = {json.dumps(hfr_std_time_data)};
            const fwhmTimeData = {json.dumps(fwhm_time_data)};
            const eccTimeData = {json.dumps(ecc_time_data)};
            
            // SPC Statistics
            const hfrMean = {hfr_mean};
            const hfrUcl = {hfr_ucl};
            const hfrStdMean = {hfr_std_mean};
            const hfrStdUcl = {hfr_std_ucl};
            const fwhmMean = {fwhm_mean};
            const fwhmUcl = {fwhm_ucl};
            const eccMean = {ecc_mean};
            const eccUcl = {ecc_ucl};
            
            // Generic error bar plugin factory
            function createErrorBarPlugin(dataArray, pluginId) {{
                return {{
                    id: pluginId,
                    afterDatasetsDraw: (chart) => {{
                        const ctx = chart.ctx;
                        const meta = chart.getDatasetMeta(0);
                        
                        meta.data.forEach((point, index) => {{
                            const d = dataArray[index];
                            if (!d || !d.std) return;
                            
                            const x = point.x;
                            const yScale = chart.scales.y;
                            const yTop = yScale.getPixelForValue(d.y + d.std);
                            const yBottom = yScale.getPixelForValue(Math.max(0, d.y - d.std));
                            const color = d.flagged ? '#e94560' : (d.filter_color || '#17a2b8');
                            
                            ctx.save();
                            ctx.strokeStyle = color;
                            ctx.lineWidth = 2;
                            ctx.beginPath();
                            ctx.moveTo(x, yTop);
                            ctx.lineTo(x, yBottom);
                            ctx.moveTo(x - 3, yTop);
                            ctx.lineTo(x + 3, yTop);
                            ctx.moveTo(x - 3, yBottom);
                            ctx.lineTo(x + 3, yBottom);
                            ctx.stroke();
                            ctx.restore();
                        }});
                    }}
                }};
            }}
            
            // Generic SPC chart factory
            function createSpcChart(canvasId, data, mean, ucl, yMin, yMax, yLabel, dataLabel) {{
                const ctx = document.getElementById(canvasId).getContext('2d');
                const errorPlugin = createErrorBarPlugin(data, canvasId + 'ErrorBars');
                
                new Chart(ctx, {{
                    type: 'line',
                    plugins: [errorPlugin],
                    data: {{
                        datasets: [
                            {{
                                label: dataLabel,
                                data: data.map(d => ({{ x: d.x, y: d.y }})),
                                borderColor: '#17a2b8',
                                backgroundColor: data.map(d => d.flagged ? '#e94560' : (d.filter_color || '#17a2b8')),
                                pointBackgroundColor: data.map(d => d.flagged ? '#e94560' : (d.filter_color || '#17a2b8')),
                                pointBorderColor: data.map(d => d.flagged ? '#e94560' : (d.filter_color || '#17a2b8')),
                                pointRadius: data.map(d => d.flagged ? 8 : 5),
                                pointStyle: data.map(d => d.flagged ? 'rectRot' : 'circle'),
                                pointHoverRadius: 10,
                                tension: 0.1,
                                fill: false,
                                order: 2
                            }},
                            {{
                                label: 'x\u0304 (mean)',
                                data: [{{ x: data[0]?.x, y: mean }}, {{ x: data[data.length-1]?.x, y: mean }}],
                                borderColor: 'rgba(255, 215, 0, 0.8)',
                                borderWidth: 2,
                                pointRadius: 0,
                                fill: false,
                                order: 3
                            }},
                            {{
                                label: 'UCL (3\u03C3)',
                                data: [{{ x: data[0]?.x, y: ucl }}, {{ x: data[data.length-1]?.x, y: ucl }}],
                                borderColor: 'rgba(233, 69, 96, 0.8)',
                                borderWidth: 2,
                                borderDash: [5, 5],
                                pointRadius: 0,
                                fill: false,
                                order: 3
                            }}
                        ]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{
                            legend: {{ 
                                labels: {{ color: '#eaeaea' }},
                                position: 'top'
                            }},
                            tooltip: {{
                                callbacks: {{
                                    label: function(context) {{
                                        if (context.datasetIndex > 0) return context.dataset.label + ': ' + context.parsed.y.toFixed(2);
                                        const idx = context.dataIndex;
                                        const d = data[idx];
                                        const flag = d.flagged ? ' [FLAGGED]' : '';
                                        return [
                                            dataLabel + ': ' + d.y.toFixed(2) + (d.std ? ' \u00B1 ' + d.std.toFixed(2) : '') + flag,
                                            'Filter: ' + d.filter,
                                            'Stars: ' + d.stars,
                                            'File: ' + d.filename
                                        ];
                                    }}
                                }}
                            }}
                        }},
                        scales: {{
                            x: {{
                                type: 'time',
                                time: {{ unit: 'hour', displayFormats: {{ hour: 'HH:mm' }} }},
                                grid: {{ color: 'rgba(255,255,255,0.1)' }},
                                ticks: {{ color: '#8b949e' }},
                                title: {{ display: true, text: 'Local Time', color: '#8b949e' }}
                            }},
                            y: {{
                                min: yMin,
                                max: yMax,
                                grid: {{ color: 'rgba(255,255,255,0.1)' }},
                                ticks: {{ color: '#17a2b8' }},
                                title: {{ display: true, text: yLabel, color: '#17a2b8' }}
                            }}
                        }}
                    }}
                }});
            }}
            
            // Create all SPC charts
            if (hfrTimeData.length > 0) {{
                createSpcChart('hfrTimeChart', hfrTimeData, hfrMean, hfrUcl, {hfr_y_min}, {hfr_y_max}, 'HFR (pixels)', 'Median HFR');
            }}
            
            if (hfrStdTimeData.length > 0) {{
                // HFR Std chart (no error bars on this one)
                const hfrStdCtx = document.getElementById('hfrStdChart').getContext('2d');
                new Chart(hfrStdCtx, {{
                    type: 'line',
                    data: {{
                        datasets: [
                            {{
                                label: 'HFR Std Dev',
                                data: hfrStdTimeData.map(d => ({{ x: d.x, y: d.y }})),
                                borderColor: '#ffc107',
                                backgroundColor: hfrStdTimeData.map(d => d.flagged ? '#e94560' : (d.filter_color || '#ffc107')),
                                pointBackgroundColor: hfrStdTimeData.map(d => d.flagged ? '#e94560' : (d.filter_color || '#ffc107')),
                                pointBorderColor: hfrStdTimeData.map(d => d.flagged ? '#e94560' : (d.filter_color || '#ffc107')),
                                pointRadius: hfrStdTimeData.map(d => d.flagged ? 8 : 5),
                                pointStyle: hfrStdTimeData.map(d => d.flagged ? 'rectRot' : 'circle'),
                                pointHoverRadius: 10,
                                tension: 0.1,
                                fill: false,
                                order: 2
                            }},
                            {{
                                label: 'x\u0304 (mean)',
                                data: [{{ x: hfrStdTimeData[0]?.x, y: hfrStdMean }}, {{ x: hfrStdTimeData[hfrStdTimeData.length-1]?.x, y: hfrStdMean }}],
                                borderColor: 'rgba(255, 215, 0, 0.8)',
                                borderWidth: 2,
                                pointRadius: 0,
                                fill: false,
                                order: 3
                            }},
                            {{
                                label: 'UCL (3\u03C3)',
                                data: [{{ x: hfrStdTimeData[0]?.x, y: hfrStdUcl }}, {{ x: hfrStdTimeData[hfrStdTimeData.length-1]?.x, y: hfrStdUcl }}],
                                borderColor: 'rgba(233, 69, 96, 0.8)',
                                borderWidth: 2,
                                borderDash: [5, 5],
                                pointRadius: 0,
                                fill: false,
                                order: 3
                            }}
                        ]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{
                            legend: {{ labels: {{ color: '#eaeaea' }}, position: 'top' }},
                            tooltip: {{
                                callbacks: {{
                                    label: function(context) {{
                                        if (context.datasetIndex > 0) return context.dataset.label + ': ' + context.parsed.y.toFixed(2);
                                        const idx = context.dataIndex;
                                        const d = hfrStdTimeData[idx];
                                        const flag = d.flagged ? ' [HIGH VARIANCE - possible cloud]' : '';
                                        return ['Std Dev: ' + d.y.toFixed(2) + ' px' + flag, 'Filter: ' + d.filter, 'File: ' + d.filename];
                                    }}
                                }}
                            }}
                        }},
                        scales: {{
                            x: {{
                                type: 'time',
                                time: {{ unit: 'hour', displayFormats: {{ hour: 'HH:mm' }} }},
                                grid: {{ color: 'rgba(255,255,255,0.1)' }},
                                ticks: {{ color: '#8b949e' }},
                                title: {{ display: true, text: 'Local Time', color: '#8b949e' }}
                            }},
                            y: {{
                                min: {hfr_std_y_min},
                                max: {hfr_std_y_max},
                                grid: {{ color: 'rgba(255,255,255,0.1)' }},
                                ticks: {{ color: '#ffc107' }},
                                title: {{ display: true, text: 'HFR Std Dev (pixels)', color: '#ffc107' }}
                            }}
                        }}
                    }}
                }});
            }}
            
            if (fwhmTimeData.length > 0) {{
                createSpcChart('fwhmTimeChart', fwhmTimeData, fwhmMean, fwhmUcl, {fwhm_y_min}, {fwhm_y_max}, 'FWHM (pixels)', 'Median FWHM');
            }}
            
            if (eccTimeData.length > 0) {{
                createSpcChart('eccTimeChart', eccTimeData, eccMean, eccUcl, {ecc_y_min}, {ecc_y_max}, 'Eccentricity', 'Eccentricity');
            }}
        </script>
        '''
