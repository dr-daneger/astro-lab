"""PHD2 guiding error analysis module."""

import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from astropy.io import fits

from .config import Config, PHD2Config
from .astro_logger import Logger
from .utils import (
    find_files_with_prefix,
    parse_datetime,
    compute_rms,
    read_fits_header,
    ensure_directory
)

@dataclass
class GuidingFrame:
    """Class representing a single guiding frame."""
    timestamp: datetime
    ra_error: float  # pixels
    dec_error: float  # pixels

@dataclass
class ImageExposure:
    """Class representing a single image exposure."""
    number: int
    filename: str
    start_time: datetime
    end_time: datetime
    exposure_time: float
    guide_frames: List[GuidingFrame]
    star_lost_events: int

class PHD2Analysis:
    """Class for analyzing PHD2 guiding logs."""
    
    def __init__(self, config: Config, log_dir: Path):
        self.config = config.phd2
        self.log_dir = Path(log_dir)
        self.logger = Logger("PHD2Analysis")
        
        # Data storage
        self.exposures: List[ImageExposure] = []
        self.all_frames: List[GuidingFrame] = []
        self.star_lost_times: List[datetime] = []
        
        # Ensure log directory exists
        if not self.log_dir.exists():
            raise ValueError(f"Log directory does not exist: {self.log_dir}")
    
    def analyze_session(self) -> None:
        """Analyze an entire guiding session."""
        self.logger.info("Starting PHD2 analysis session...")
        
        # Find log files
        autorun_log = self._find_autorun_log()
        phd2_log = self._find_phd2_log()
        
        if not autorun_log or not phd2_log:
            raise FileNotFoundError("Could not find required log files")
        
        # Parse logs
        self._parse_autorun_log(autorun_log)
        self._parse_phd2_log(phd2_log)
        
        # Match guide frames to exposures
        self._match_frames_to_exposures()
        
        # Compute statistics
        self._compute_statistics()
        
        self.logger.info("Analysis complete!")
    
    def _find_autorun_log(self) -> Optional[Path]:
        """Find the Autorun log file."""
        logs = find_files_with_prefix(
            self.log_dir,
            self.config.autorun_log_prefix,
            self.config.log_extension
        )
        return logs[0] if logs else None
    
    def _find_phd2_log(self) -> Optional[Path]:
        """Find the PHD2 log file."""
        logs = find_files_with_prefix(
            self.log_dir,
            self.config.phd2_log_prefix,
            self.config.log_extension
        )
        return logs[0] if logs else None
    
    def _parse_autorun_log(self, log_path: Path) -> None:
        """Parse the Autorun log file."""
        self.logger.info(f"Parsing Autorun log: {log_path.name}")
        
        exposure_pattern = re.compile(
            r'^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+Exposure\s+([\d.]+)s\s+image\s+(\d+)#'
        )
        fits_pattern = re.compile(r'^(Light_\S+\.fits)$', re.IGNORECASE)
        
        current_exposure = None
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                
                # Check for exposure start
                m_exp = exposure_pattern.match(line)
                if m_exp:
                    start_time = datetime.strptime(m_exp.group(1), "%Y/%m/%d %H:%M:%S")
                    exposure_time = float(m_exp.group(2))
                    image_num = int(m_exp.group(3))
                    
                    current_exposure = ImageExposure(
                        number=image_num,
                        filename=None,
                        start_time=start_time,
                        end_time=start_time + timedelta(seconds=exposure_time),
                        exposure_time=exposure_time,
                        guide_frames=[],
                        star_lost_events=0
                    )
                    self.exposures.append(current_exposure)
                    continue
                
                # Check for FITS filename
                if current_exposure:
                    m_fits = fits_pattern.match(line)
                    if m_fits:
                        current_exposure.filename = m_fits.group(1)
    
    def _parse_phd2_log(self, log_path: Path) -> None:
        """Parse the PHD2 log file."""
        self.logger.info(f"Parsing PHD2 log: {log_path.name}")
        
        guide_pattern = re.compile(
            r'^(\d+),([\d.]+),"Mount",([-+\d.]+),([-+\d.]+),([-+\d.]+),([-+\d.]+),'
        )
        star_lost_pattern = re.compile(
            r'^(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}).*Guide star lost'
        )
        session_start_pattern = re.compile(
            r'Guiding Begins at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'
        )
        
        session_start = None
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                
                # Check for session start
                m_start = session_start_pattern.match(line)
                if m_start:
                    session_start = datetime.strptime(
                        m_start.group(1),
                        "%Y-%m-%d %H:%M:%S"
                    )
                    continue
                
                # Check for guide frame
                m_guide = guide_pattern.match(line)
                if m_guide and session_start:
                    rel_time = float(m_guide.group(2))
                    ra_error = float(m_guide.group(5))
                    dec_error = float(m_guide.group(6))
                    
                    frame_time = session_start + timedelta(seconds=rel_time)
                    frame = GuidingFrame(frame_time, ra_error, dec_error)
                    self.all_frames.append(frame)
                    continue
                
                # Check for star lost event
                m_lost = star_lost_pattern.match(line)
                if m_lost:
                    lost_time = datetime.strptime(
                        m_lost.group(1),
                        "%Y/%m/%d %H:%M:%S"
                    )
                    self.star_lost_times.append(lost_time)
    
    def _match_frames_to_exposures(self) -> None:
        """Match guide frames to their corresponding exposures."""
        self.logger.info("Matching guide frames to exposures...")
        
        for exposure in self.exposures:
            # Find frames within exposure time window
            exposure.guide_frames = [
                frame for frame in self.all_frames
                if exposure.start_time <= frame.timestamp <= exposure.end_time
            ]
            
            # Count star lost events during exposure
            exposure.star_lost_events = sum(
                1 for t in self.star_lost_times
                if exposure.start_time <= t <= exposure.end_time
            )
    
    def _compute_statistics(self) -> None:
        """Compute RMS statistics for each exposure."""
        self.logger.info("Computing guiding statistics...")
        
        # Prepare results DataFrame
        results = []
        
        # Use the enhanced status indicator
        with self.logger.status("Processing exposures...", spinner="dots"):
            for exp in self.exposures:
                if not exp.guide_frames:
                    continue
                    
                # Extract RA/DEC errors
                ra_errors = np.array([f.ra_error for f in exp.guide_frames])
                dec_errors = np.array([f.dec_error for f in exp.guide_frames])
                
                # Compute RMS in arcsec and microns
                rms_ra_px = compute_rms(ra_errors)
                rms_dec_px = compute_rms(dec_errors)
                rms_total_px = compute_rms(np.sqrt(ra_errors**2 + dec_errors**2))
                
                results.append({
                    'Image': exp.number,
                    'Filename': exp.filename or 'N/A',
                    'Start': exp.start_time,
                    'End': exp.end_time,
                    'Star Lost Events': exp.star_lost_events,
                    'RMS RA (arcsec)': rms_ra_px * self.config.pixel_scale_arcsec,
                    'RMS DEC (arcsec)': rms_dec_px * self.config.pixel_scale_arcsec,
                    'RMS Total (arcsec)': rms_total_px * self.config.pixel_scale_arcsec,
                    'RMS RA (µm)': rms_ra_px * self.config.pixel_size_um,
                    'RMS DEC (µm)': rms_dec_px * self.config.pixel_size_um,
                    'RMS Total (µm)': rms_total_px * self.config.pixel_size_um,
                    'Frames Used': len(exp.guide_frames)
                })
        
        # Create DataFrame and save to CSV
        df = pd.DataFrame(results)
        csv_path = self.log_dir / f"phd2_analysis_{datetime.now():%Y%m%d-%H%M%S}.csv"
        df.to_csv(csv_path, index=False)
        self.logger.success(f"Results saved to: {csv_path}")
        
        # Print summary using rich table
        self.logger.panel("Analysis Results Summary", style="success")
        
        # Display results in a table if any
        if results:
            # Convert to list format for table
            headers = [
                "Img#", "Filename", "Start", "End", "Lost",
                "RMS RA (as)", "RMS DEC (as)", "RMS TOT (as)",
                "Frames"
            ]
            
            rows = []
            for r in results:
                rows.append([
                    r['Image'],
                    r['Filename'],
                    r['Start'].strftime("%H:%M:%S"),
                    r['End'].strftime("%H:%M:%S"),
                    r['Star Lost Events'],
                    f"{r['RMS RA (arcsec)']:.2f}",
                    f"{r['RMS DEC (arcsec)']:.2f}",
                    f"{r['RMS Total (arcsec)']:.2f}",
                    r['Frames Used']
                ])
            
            self.logger.table(
                title="Per-Image Results",
                columns=headers,
                rows=rows
            )
        else:
            self.logger.warning("No results to display")
        
        # Compute and print overall statistics
        if self.all_frames:
            all_ra = np.array([f.ra_error for f in self.all_frames])
            all_dec = np.array([f.dec_error for f in self.all_frames])
            
            overall_rms_ra_px = compute_rms(all_ra)
            overall_rms_dec_px = compute_rms(all_dec)
            overall_rms_total_px = compute_rms(np.sqrt(all_ra**2 + all_dec**2))
            
            # Display overall stats as a dictionary
            self.logger.display_dict(
                {
                    "RA (arcsec)": f"{overall_rms_ra_px * self.config.pixel_scale_arcsec:.2f}",
                    "DEC (arcsec)": f"{overall_rms_dec_px * self.config.pixel_scale_arcsec:.2f}",
                    "Total (arcsec)": f"{overall_rms_total_px * self.config.pixel_scale_arcsec:.2f}",
                    "RA (µm)": f"{overall_rms_ra_px * self.config.pixel_size_um:.2f}",
                    "DEC (µm)": f"{overall_rms_dec_px * self.config.pixel_size_um:.2f}",
                    "Total (µm)": f"{overall_rms_total_px * self.config.pixel_size_um:.2f}",
                    "Frames": f"{len(self.all_frames)}",
                    "Star Lost Events": f"{len(self.star_lost_times)}"
                },
                title="Overall Session RMS"
            )
        else:
            self.logger.warning("No frames for overall RMS calculation")
    
    def plot_guiding_performance(self, save_dir: Optional[Path] = None) -> None:
        """Generate plots of guiding performance."""
        self.logger.info("Generating performance plots...")
        
        if not self.all_frames:
            self.logger.warning("No frames to plot!")
            return
        
        # Ensure save directory exists
        if save_dir:
            ensure_directory(save_dir)
        
        with self.logger.status("Creating plots...", spinner="dots"):
            # Extract timestamps and errors
            times = [f.timestamp for f in self.all_frames]
            ra_errors = [f.ra_error * self.config.pixel_scale_arcsec for f in self.all_frames]
            dec_errors = [f.dec_error * self.config.pixel_scale_arcsec for f in self.all_frames]
            
            # Create figure with subplots
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
            
            # Plot RA errors
            ax1.plot(times, ra_errors, 'b-', label='RA', alpha=0.6)
            ax1.set_ylabel('RA Error (arcsec)')
            ax1.grid(True)
            ax1.legend()
            
            # Plot DEC errors
            ax2.plot(times, dec_errors, 'r-', label='DEC', alpha=0.6)
            ax2.set_xlabel('Time')
            ax2.set_ylabel('DEC Error (arcsec)')
            ax2.grid(True)
            ax2.legend()
            
            # Add star lost events if any
            if self.star_lost_times:
                for t in self.star_lost_times:
                    ax1.axvline(t, color='gray', linestyle='--', alpha=0.5)
                    ax2.axvline(t, color='gray', linestyle='--', alpha=0.5)
            
            # Adjust layout and save
            plt.tight_layout()
            
            if save_dir:
                plot_path = save_dir / f"guiding_performance_{datetime.now():%Y%m%d-%H%M%S}.png"
                plt.savefig(plot_path, dpi=300, bbox_inches='tight')
                self.logger.success(f"Plot saved to: {plot_path}")
            else:
                plt.show()
            
            plt.close()
            
            # Create additional histogram plots
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6))
            
            # RA error histogram
            ax1.hist(ra_errors, bins=30, alpha=0.7, color='blue')
            ax1.set_title('RA Error Distribution')
            ax1.set_xlabel('RA Error (arcsec)')
            ax1.set_ylabel('Frequency')
            ax1.grid(True, alpha=0.3)
            
            # DEC error histogram
            ax2.hist(dec_errors, bins=30, alpha=0.7, color='red')
            ax2.set_title('DEC Error Distribution')
            ax2.set_xlabel('DEC Error (arcsec)')
            ax2.set_ylabel('Frequency')
            ax2.grid(True, alpha=0.3)
            
            plt.tight_layout()
            
            if save_dir:
                hist_path = save_dir / f"error_histograms_{datetime.now():%Y%m%d-%H%M%S}.png"
                plt.savefig(hist_path, dpi=300, bbox_inches='tight')
                self.logger.success(f"Histogram saved to: {hist_path}")
            else:
                plt.show()
                
            plt.close()
        
        # Display plot statistics
        if self.all_frames and save_dir:
            self.logger.panel(
                f"Total frames plotted: {len(self.all_frames)}\n"
                f"Star lost events: {len(self.star_lost_times)}\n"
                f"Plot files saved to: {save_dir}",
                title="Plot Summary",
                style="success"
            ) 