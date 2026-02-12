"""Alt/Az statistics analysis module for astro_utils package.

This module calculates altitude and azimuth coordinates for FITS images
based on their timestamps and the observer's location, providing statistics
on observing conditions throughout a session.
"""

import re
import os
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import numpy as np
import pandas as pd
from datetime import datetime
from astropy.io import fits
from astropy.coordinates import SkyCoord, EarthLocation, AltAz
from astropy.time import Time
import astropy.units as u

try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo

from .config import Config, AltAzConfig
from .astro_logger import Logger
from .utils import ensure_directory


@dataclass
class FITSImageStats:
    """Class representing statistics for a single FITS image."""
    filename: str
    filepath: str
    local_time: datetime
    ra_deg: float
    dec_deg: float
    alt_deg: float
    az_deg: float
    mean_adu: float
    std_adu: float


class AltAzAnalysis:
    """Class for analyzing Alt/Az coordinates from FITS files."""
    
    def __init__(self, config: Config, fits_dir: Path):
        """
        Initialize the Alt/Az analyzer.
        
        Args:
            config: Configuration object with observer location settings
            fits_dir: Directory containing FITS files to analyze
        """
        self.config = config.altaz
        self.fits_dir = Path(fits_dir)
        self.logger = Logger("AltAzAnalysis")
        
        # Set up observer location from config
        self.observer_location = EarthLocation(
            lat=self.config.latitude * u.deg,
            lon=self.config.longitude * u.deg,
            height=self.config.elevation * u.m
        )
        
        # Set up timezone
        try:
            self.local_zone = zoneinfo.ZoneInfo(self.config.timezone)
        except Exception:
            self.logger.warning(f"Could not load timezone {self.config.timezone}, using UTC")
            self.local_zone = zoneinfo.ZoneInfo("UTC")
        
        # Data storage
        self.image_stats: List[FITSImageStats] = []
        
        # Ensure directory exists
        if not self.fits_dir.exists():
            raise ValueError(f"FITS directory does not exist: {self.fits_dir}")
    
    def analyze_session(self) -> None:
        """Analyze Alt/Az for all FITS files in the directory."""
        self.logger.info(f"Starting Alt/Az analysis for: {self.fits_dir}")
        self.logger.info(f"Observer location: {self.config.latitude:.4f}°, {self.config.longitude:.4f}°")
        
        # Find FITS files
        fits_files = self._find_fits_files()
        
        if not fits_files:
            self.logger.warning("No FITS files found in directory")
            return
        
        self.logger.info(f"Found {len(fits_files)} FITS files")
        
        # Process each file
        with self.logger.status("Processing FITS files...", spinner="dots"):
            for fpath in fits_files:
                self._process_fits_file(fpath)
        
        # Compute and display statistics
        self._compute_statistics()
        
        self.logger.info("Alt/Az analysis complete!")
    
    def _find_fits_files(self) -> List[Path]:
        """Find all FITS files in the directory recursively."""
        fits_files = list(self.fits_dir.rglob("*.fits"))
        fits_files += list(self.fits_dir.rglob("*.fit"))
        fits_files.sort()
        return fits_files
    
    def _parse_local_time_from_filename(self, fname: str) -> Optional[datetime]:
        """
        Parse timestamp from filename in format YYYYMMDD-HHMMSS.
        
        Args:
            fname: Filename to parse
            
        Returns:
            Localized datetime or None if parsing fails
        """
        # Pattern to match YYYYMMDD-HHMMSS in filename
        pattern = r".*_(\d{8})-(\d{6})_.*(?:\.fits?)?$"
        match = re.match(pattern, fname, re.IGNORECASE)
        
        if not match:
            # Try alternative pattern without underscore prefix
            pattern = r"(\d{8})-(\d{6})"
            match = re.search(pattern, fname)
            if not match:
                return None
        
        date_part = match.group(1)
        time_part = match.group(2)
        dt_str = date_part + time_part
        
        try:
            dt_naive = datetime.strptime(dt_str, "%Y%m%d%H%M%S")
            return dt_naive.replace(tzinfo=self.local_zone)
        except ValueError:
            return None
    
    def _get_radec_from_header(self, header) -> tuple:
        """
        Extract RA/DEC from FITS header.
        
        Args:
            header: FITS header object
            
        Returns:
            Tuple of (ra_deg, dec_deg)
        """
        # Try standard RA/DEC keywords
        if "RA" in header and "DEC" in header:
            try:
                return float(header["RA"]), float(header["DEC"])
            except (ValueError, TypeError):
                pass
        
        # Try CRVAL keywords (WCS)
        if "CRVAL1" in header and "CRVAL2" in header:
            try:
                return float(header["CRVAL1"]), float(header["CRVAL2"])
            except (ValueError, TypeError):
                pass
        
        # Try OBJCTRA/OBJCTDEC
        if "OBJCTRA" in header and "OBJCTDEC" in header:
            try:
                # These might be in sexagesimal format
                ra_str = header["OBJCTRA"]
                dec_str = header["OBJCTDEC"]
                coord = SkyCoord(ra_str, dec_str, unit=(u.hourangle, u.deg))
                return coord.ra.deg, coord.dec.deg
            except Exception:
                pass
        
        return 0.0, 0.0
    
    def _calc_altaz(self, local_dt: datetime, ra_deg: float, dec_deg: float) -> tuple:
        """
        Calculate Alt/Az coordinates for given RA/DEC at given time.
        
        Args:
            local_dt: Local datetime with timezone
            ra_deg: Right Ascension in degrees
            dec_deg: Declination in degrees
            
        Returns:
            Tuple of (altitude_deg, azimuth_deg)
        """
        # Convert to UTC
        dt_utc = local_dt.astimezone(zoneinfo.ZoneInfo("UTC"))
        obs_time = Time(dt_utc)
        
        # Create sky coordinate
        coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
        
        # Transform to Alt/Az
        altaz_frame = AltAz(obstime=obs_time, location=self.observer_location)
        altaz = coord.transform_to(altaz_frame)
        
        return float(altaz.alt.deg), float(altaz.az.deg)
    
    def _process_fits_file(self, fpath: Path) -> None:
        """Process a single FITS file."""
        fname = fpath.name
        
        try:
            # Parse timestamp from filename
            local_dt = self._parse_local_time_from_filename(fname)
            if local_dt is None:
                self.logger.debug(f"Could not parse timestamp from {fname}")
                return
            
            # Read FITS header and data
            with fits.open(fpath) as hdul:
                header = hdul[0].header
                data = hdul[0].data
                
                # Get RA/DEC
                ra_deg, dec_deg = self._get_radec_from_header(header)
                
                # Calculate image statistics
                if data is not None:
                    mean_adu = float(np.mean(data))
                    std_adu = float(np.std(data))
                else:
                    mean_adu = 0.0
                    std_adu = 0.0
            
            # Calculate Alt/Az
            alt_deg, az_deg = self._calc_altaz(local_dt, ra_deg, dec_deg)
            
            # Store results
            stats = FITSImageStats(
                filename=fname,
                filepath=str(fpath),
                local_time=local_dt,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                alt_deg=alt_deg,
                az_deg=az_deg,
                mean_adu=mean_adu,
                std_adu=std_adu
            )
            self.image_stats.append(stats)
            
            # Log progress
            time_str = local_dt.strftime("%H:%M:%S")
            self.logger.debug(f"{fname}: Alt={alt_deg:.1f}°, Az={az_deg:.1f}° at {time_str}")
            
        except Exception as e:
            self.logger.error(f"Error processing {fname}: {e}")
    
    def _compute_statistics(self) -> None:
        """Compute and display summary statistics."""
        if not self.image_stats:
            self.logger.warning("No images processed")
            return
        
        # Extract arrays
        altitudes = np.array([s.alt_deg for s in self.image_stats])
        azimuths = np.array([s.az_deg for s in self.image_stats])
        
        # Filter images above minimum altitude
        above_min = altitudes >= self.config.min_altitude
        
        # Compute statistics
        stats_dict = {
            "Total Images": len(self.image_stats),
            "Above Min Alt": int(np.sum(above_min)),
            "Below Min Alt": int(np.sum(~above_min)),
            "Min Altitude": f"{np.min(altitudes):.1f}°",
            "Max Altitude": f"{np.max(altitudes):.1f}°",
            "Mean Altitude": f"{np.mean(altitudes):.1f}°",
            "Azimuth Range": f"{np.min(azimuths):.1f}° - {np.max(azimuths):.1f}°"
        }
        
        self.logger.display_dict(stats_dict, title="Alt/Az Session Summary")
        
        # Display table of results
        if len(self.image_stats) <= 20:
            # Show all if not too many
            self._display_results_table(self.image_stats)
        else:
            # Show first and last few
            self.logger.info(f"\nShowing first 5 and last 5 of {len(self.image_stats)} images:")
            self._display_results_table(self.image_stats[:5] + self.image_stats[-5:])
    
    def _display_results_table(self, stats_list: List[FITSImageStats]) -> None:
        """Display a table of image statistics."""
        headers = ["Filename", "Time", "Alt (°)", "Az (°)", "Mean ADU", "Std ADU"]
        rows = []
        
        for s in stats_list:
            rows.append([
                s.filename[:40] + "..." if len(s.filename) > 40 else s.filename,
                s.local_time.strftime("%H:%M:%S"),
                f"{s.alt_deg:.1f}",
                f"{s.az_deg:.1f}",
                f"{s.mean_adu:.0f}",
                f"{s.std_adu:.0f}"
            ])
        
        self.logger.table(title="Image Alt/Az Statistics", columns=headers, rows=rows)
    
    def save_csv(self, output_path: Optional[Path] = None) -> Path:
        """
        Save results to CSV file.
        
        Args:
            output_path: Optional path for CSV file
            
        Returns:
            Path to saved CSV file
        """
        if not self.image_stats:
            self.logger.warning("No data to save")
            return None
        
        # Default output path
        if output_path is None:
            # Use imaging date from first file
            if self.image_stats:
                date_str = self.image_stats[0].local_time.strftime("%Y%m%d")
            else:
                date_str = datetime.now().strftime("%Y%m%d")
            output_path = self.fits_dir / f"altaz_stats_{date_str}.csv"
        
        # Create DataFrame
        data = []
        for s in self.image_stats:
            data.append({
                'filename': s.filename,
                'local_time': s.local_time.strftime("%Y-%m-%d %H:%M:%S"),
                'ra_deg': s.ra_deg,
                'dec_deg': s.dec_deg,
                'alt_deg': s.alt_deg,
                'az_deg': s.az_deg,
                'mean_adu': s.mean_adu,
                'std_adu': s.std_adu
            })
        
        df = pd.DataFrame(data)
        df.to_csv(output_path, index=False)
        
        self.logger.success(f"Results saved to: {output_path}")
        return output_path
    
    def plot_altitude_timeline(self, save_dir: Optional[Path] = None) -> None:
        """
        Generate a plot of altitude over time.
        
        Args:
            save_dir: Optional directory to save the plot
        """
        if not self.image_stats:
            self.logger.warning("No data to plot")
            return
        
        if save_dir:
            ensure_directory(save_dir)
        
        times = [s.local_time for s in self.image_stats]
        altitudes = [s.alt_deg for s in self.image_stats]
        azimuths = [s.az_deg for s in self.image_stats]
        
        with self.logger.status("Creating altitude timeline plot...", spinner="dots"):
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
            
            # Altitude plot
            ax1.plot(times, altitudes, 'b-o', alpha=0.7, markersize=3)
            ax1.axhline(y=self.config.min_altitude, color='r', linestyle='--', 
                       label=f'Min Alt ({self.config.min_altitude}°)')
            ax1.set_ylabel('Altitude (degrees)')
            ax1.set_title('Altitude Throughout Session')
            ax1.grid(True, alpha=0.3)
            ax1.legend()
            
            # Azimuth plot
            ax2.plot(times, azimuths, 'g-o', alpha=0.7, markersize=3)
            ax2.set_ylabel('Azimuth (degrees)')
            ax2.set_xlabel('Time')
            ax2.set_title('Azimuth Throughout Session')
            ax2.grid(True, alpha=0.3)
            
            fig.autofmt_xdate()
            plt.tight_layout()
            
            if save_dir:
                plot_path = save_dir / f"altaz_timeline_{datetime.now():%Y%m%d-%H%M%S}.png"
                plt.savefig(plot_path, dpi=300, bbox_inches='tight')
                self.logger.success(f"Plot saved to: {plot_path}")
            else:
                plt.show()
            
            plt.close()
