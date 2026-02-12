#!/usr/bin/env python3
"""
Camera Noise Characterization Tool

A standalone script to characterize the thermal dependence of camera noise for 
TEC CMOS sensors used in astronomy. Analyzes FITS files to create histograms 
of pixel values, convert to electron counts, and fit Gaussian distributions 
to understand read noise characteristics at different temperatures and gain settings.

This script is fully self-contained with no external dependencies beyond:
- numpy, scipy, matplotlib, astropy (standard astronomy stack)

Features:
- Automatic grouping of FITS files by exposure time, gain, and temperature
- Calculation of electron counts from ADU values using EGAIN header
- Gaussian fitting to pixel/electron count distributions
- KDE analysis for non-parametric noise characterization
- Statistical analysis of noise characteristics
- Visualization of results with detailed plots

Usage:
    python camera_noise_characterization.py -d /path/to/fits/files -e 0.0001 -p /path/to/plots

Author: Dane
"""

import numpy as np
from astropy.io import fits
import csv
import os
import logging
from pathlib import Path
import time
from scipy.optimize import curve_fit
from scipy import stats
from scipy.signal import find_peaks
import matplotlib.pyplot as plt
import argparse
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Any
from contextlib import contextmanager


# =============================================================================
# Simple Logger Class (standalone, no external dependencies)
# =============================================================================

class SimpleLogger:
    """A simple logger with optional colored output for console."""
    
    def __init__(self, name: str, level: int = logging.INFO):
        self.name = name
        self.level = level
        
        # Set up standard Python logging
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self._logger = logging.getLogger(name)
    
    def set_level(self, level: int) -> None:
        self.level = level
        self._logger.setLevel(level)
    
    def info(self, msg: str) -> None:
        self._logger.info(msg)
    
    def warning(self, msg: str) -> None:
        self._logger.warning(msg)
    
    def error(self, msg: str) -> None:
        self._logger.error(msg)
    
    def debug(self, msg: str) -> None:
        self._logger.debug(msg)
    
    def success(self, msg: str) -> None:
        self._logger.info(f"SUCCESS: {msg}")
    
    @contextmanager
    def status(self, description: str = "Processing", spinner: str = "dots"):
        """Simple status context manager."""
        print(f"{description}...")
        yield
        print("Done.")
    
    def table(self, title: str = None, columns: List[str] = None, 
              rows: List[List[Any]] = None, show_header: bool = True) -> None:
        """Display data as a simple text table."""
        if title:
            print(f"\n{'='*60}")
            print(f" {title}")
            print(f"{'='*60}")
        
        if columns and show_header:
            # Calculate column widths
            col_widths = [len(str(c)) for c in columns]
            if rows:
                for row in rows:
                    for i, cell in enumerate(row):
                        if i < len(col_widths):
                            col_widths[i] = max(col_widths[i], len(str(cell)))
            
            # Print header
            header = " | ".join(f"{str(c):<{col_widths[i]}}" for i, c in enumerate(columns))
            print(header)
            print("-" * len(header))
        
        if rows:
            for row in rows:
                print(" | ".join(f"{str(cell):<{col_widths[i] if i < len(col_widths) else 10}}" 
                               for i, cell in enumerate(row)))


# Initialize the logger
logger = SimpleLogger("CameraNoise")

@dataclass
class FITSGroup:
    """Class representing a group of FITS files with the same parameters."""
    gain: float
    temperature: float
    exposure_time: float
    file_paths: List[str]
    egain: Optional[float] = None
    pixel_counts: Optional[np.ndarray] = None
    fit_params: Optional[Dict[str, Any]] = None
    bin_centers: Optional[np.ndarray] = None
    stats: Optional[Dict[str, float]] = None

@dataclass
class GaussianFitResult:
    """Class representing the results of a Gaussian fit."""
    amplitude: float
    amplitude_error: float
    mean: float
    mean_error: float
    sigma: float
    sigma_error: float
    r_squared: float
    degrees_of_freedom: int
    electron_counts: np.ndarray
    frequencies: np.ndarray
    fitted_frequencies: np.ndarray

class FITSHistogramAnalyzer:
    """Analyzes histograms of pixel values in FITS files and performs Gaussian fits."""
    
    def __init__(self):
        """Initialize the analyzer with default configuration."""
        self.logger = logger
        
        # Default configuration values
        self.default_exptime = 0.0001  # Default exposure time to filter by (in seconds)
        self.bin_width = 1.0  # Histogram bin width
        self.plot_dpi = 300   # Plot resolution
        
    def get_header_value(self, header: fits.Header, keys: List[str]) -> Optional[Any]:
        """
        Retrieves the value from a FITS header given a list of possible keys.

        Args:
            header: FITS file header
            keys: Possible keys to search for in the header

        Returns:
            The value associated with the first found key, or None if not found
        """
        for key in keys:
            value = header.get(key)
            if value is not None:
                return value
        return None
        
    def group_fits_files(self, directory_path: str, exptime_value: Optional[float] = None) -> Dict[Tuple[float, float], FITSGroup]:
        """
        Groups FITS files based on GAIN and SET-TEMP parameters for a given EXPTIME.

        Args:
            directory_path: Path to the directory containing FITS files
            exptime_value: The exposure time value to filter FITS files (optional)

        Returns:
            A dictionary where keys are (GAIN, SET-TEMP) tuples and values are FITSGroup objects
        """
        self.logger.info(f"Searching for FITS files in {directory_path}...")
        
        groups = {}
        directory = Path(directory_path)
        fits_files = list(directory.glob("*.fit")) + list(directory.glob("*.fits"))
        
        if not fits_files:
            self.logger.warning(f"No FITS files found in {directory_path}")
            return groups
            
        self.logger.info(f"Found {len(fits_files)} FITS files")

        for file_path in fits_files:
            try:
                with fits.open(file_path) as hdul:
                    header = hdul[0].header

                    # Extract header values
                    exptime = self.get_header_value(header, ['EXPTIME', 'EXPTIME ', 'exptime'])
                    gain = self.get_header_value(header, ['GAIN', 'GAIN ', 'gain'])
                    set_temp = self.get_header_value(header, ['SET-TEMP', 'SET-TEMP ', 'set-temp', 'CCD-TEMP', 'CCDTEMP'])

                    # Skip if not matching the specified exptime (if provided)
                    if exptime_value is not None and (exptime is None or float(exptime) != float(exptime_value)):
                        continue

                    # Skip if missing required parameters
                    if gain is None or set_temp is None:
                        self.logger.debug(f"Skipping {file_path.name} - missing required parameters")
                        continue

                    # Convert to appropriate types
                    try:
                        gain = float(gain)
                        set_temp = float(set_temp)
                        exptime = float(exptime) if exptime is not None else None
                    except (ValueError, TypeError) as e:
                        self.logger.debug(f"Skipping {file_path.name} - parameter conversion error: {e}")
                        continue

                    # Create a key for the group
                    group_key = (gain, set_temp)

                    # Add to the appropriate group
                    if group_key in groups:
                        groups[group_key].file_paths.append(str(file_path))
                    else:
                        groups[group_key] = FITSGroup(
                            gain=gain,
                            temperature=set_temp,
                            exposure_time=exptime,
                            file_paths=[str(file_path)]
                        )

            except Exception as e:
                self.logger.error(f"Error processing {file_path.name}: {e}")

        # Sort groups by gain and temperature for easier comparison
        sorted_groups = {k: groups[k] for k in sorted(groups.keys())}
        
        # Log the groups
        for (gain, set_temp), group in sorted_groups.items():
            self.logger.info(f"Group: GAIN={gain}, TEMP={set_temp}°C, EXPTIME={group.exposure_time}, Files: {len(group.file_paths)}")
        
        return sorted_groups

    def process_group(self, group: FITSGroup) -> None:
        """
        Processes a group of FITS files to compute cumulative pixel counts and average EGAIN.

        Args:
            group: FITSGroup object containing file paths and metadata
        """
        self.logger.info(f"Processing group: GAIN={group.gain}, TEMP={group.temperature}°C, Files: {len(group.file_paths)}")
        
        # For proper integer ADU binning
        all_pixel_values = []
        total_egain = 0.0
        valid_egain_count = 0
        total_pixels_processed = 0
        
        # For direct statistical analysis
        image_means = []
        image_variances = []

        with self.logger.status("Processing FITS files...", spinner="dots"):
            for file_path in group.file_paths:
                try:
                    with fits.open(file_path) as hdul:
                        # Get the image data
                        image_data = hdul[0].data
                        
                        # Store dimensions for logging
                        height, width = image_data.shape
                        total_pixels_processed += height * width
                        
                        # Calculate statistics for this image
                        image_mean = np.mean(image_data)
                        image_variance = np.var(image_data)
                        image_means.append(image_mean)
                        image_variances.append(image_variance)
                        
                        # Flatten the 2D array to 1D and add to collection
                        all_pixel_values.append(image_data.flatten())
                        
                        # Get EGAIN if available (used to convert ADU to electrons)
                        header = hdul[0].header
                        egain = self.get_header_value(header, ['EGAIN', 'EGAIN ', 'egain'])
                        
                        if egain is not None:
                            try:
                                egain_value = float(egain)
                                total_egain += egain_value
                                valid_egain_count += 1
                            except (ValueError, TypeError):
                                self.logger.debug(f"Non-numeric EGAIN value in file {file_path}")
                except Exception as e:
                    self.logger.error(f"Error processing {file_path}: {e}")

        # Combine all pixel values
        if not all_pixel_values:
            self.logger.error("No valid pixel data found.")
            return
            
        all_pixels = np.concatenate(all_pixel_values)
        
        # Direct statistical measurement of read noise
        # The variance in zero-exposure frames (bias frames) is primarily due to read noise
        mean_variance_adu = np.mean(image_variances)
        std_variance_adu = np.std(image_variances)
        
        # Calculate mean and variance for all pixels
        overall_mean_adu = np.mean(all_pixels)
        overall_variance_adu = np.var(all_pixels)
        overall_std_adu = np.std(all_pixels)
        
        self.logger.info(f"Statistical analysis:")
        self.logger.info(f"  Mean ADU: {overall_mean_adu:.2f}")
        self.logger.info(f"  Standard deviation (ADU): {overall_std_adu:.2f}")
        self.logger.info(f"  Mean per-frame variance (ADU²): {mean_variance_adu:.2f} ± {std_variance_adu:.2f}")
        
        # For integer binning, find min and max, then create integer bins
        min_adu = int(np.floor(np.min(all_pixels)))
        max_adu = int(np.ceil(np.max(all_pixels)))
        self.logger.info(f"ADU values range from {min_adu} to {max_adu}")
        
        # Create bins with edges aligned to integer ADU values
        # For 12/16-bit data, every integer should have its own bin
        bin_edges = np.arange(min_adu, max_adu + 2) - 0.5  # +2 to include max_adu and edge offset
        
        # Compute histogram using integer-aligned bins
        hist, bin_edges = np.histogram(all_pixels, bins=bin_edges)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
        
        # Log the histogram properties
        self.logger.info(f"Created histogram with {len(hist)} bins ({min_adu} to {max_adu} ADU)")
        self.logger.debug(f"First 10 bin centers: {bin_centers[:10]}")
        self.logger.debug(f"First 10 counts: {hist[:10]}")

        # Calculate average EGAIN
        average_egain = total_egain / valid_egain_count if valid_egain_count > 0 else None
        
        # Handle missing EGAIN values based on gain setting
        # Typical EGAIN relationships for astronomical cameras:
        # - Higher gain (numerically) means LOWER e-/ADU (more sensitive)
        # - Lower gain (numerically) means HIGHER e-/ADU (less sensitive)
        if average_egain is None:
            self.logger.warning(f"No valid EGAIN values found in any files for gain={group.gain}.")
            
            # Estimate EGAIN based on gain
            if group.gain == 0:
                # Unity gain or minimum gain typically has highest e-/ADU
                estimated_egain = 10.0
            elif group.gain > 200:  # High gain
                estimated_egain = 0.5
            else:  # Medium gain
                estimated_egain = 3.0
                
            self.logger.warning(f"Using estimated EGAIN of {estimated_egain:.1f} e-/ADU based on gain={group.gain}")
            average_egain = estimated_egain
        
        self.logger.info(f"Average EGAIN value: {average_egain:.3f} e-/ADU")
        self.logger.info(f"Total pixels processed: {total_pixels_processed:,} from {len(group.file_paths)} files")
        
        # Convert ADU statistics to electron statistics
        overall_mean_e = overall_mean_adu * average_egain
        overall_std_e = overall_std_adu * average_egain
        mean_variance_e = mean_variance_adu * (average_egain ** 2)
        std_variance_e = std_variance_adu * (average_egain ** 2)
        
        # In bias frames, read noise equals the standard deviation in electrons
        statistical_read_noise = np.sqrt(mean_variance_e)
        statistical_read_noise_error = 0.5 * std_variance_e / statistical_read_noise if statistical_read_noise > 0 else 0

        self.logger.info(f"Statistical results (e-):")
        self.logger.info(f"  Mean: {overall_mean_e:.2f} e-")
        self.logger.info(f"  Standard deviation: {overall_std_e:.2f} e-")
        self.logger.info(f"  Read noise (from variance): {statistical_read_noise:.2f} ± {statistical_read_noise_error:.2f} e- RMS")
        
        # Update the group with processed data
        group.egain = average_egain
        group.pixel_counts = hist
        group.bin_centers = bin_centers
        group.stats = {
            'Mean ADU': overall_mean_adu,
            'Std ADU': overall_std_adu,
            'Mean Variance ADU': mean_variance_adu,
            'Std Variance ADU': std_variance_adu,
            'Mean e-': overall_mean_e,
            'Std e-': overall_std_e, 
            'Mean Variance e-': mean_variance_e,
            'Std Variance e-': std_variance_e,
            'Statistical Read Noise': statistical_read_noise,
            'Statistical Read Noise Error': statistical_read_noise_error
        }

    def fit_gaussian(self, group: FITSGroup) -> None:
        """
        Fits a Gaussian function to the pixel count data and collects fit parameters.
        Now also includes KDE analysis for a non-parametric approach.

        Args:
            group: FITSGroup object with pixel_counts and egain data
        """
        if group.pixel_counts is None or np.sum(group.pixel_counts) == 0 or group.egain is None:
            self.logger.warning("No valid pixel counts or EGAIN value to process.")
            return
        
        self.logger.info("Performing distribution analysis...")
        
        # We now work with ADU values first, then convert to electrons later
        pixel_values_adu = group.bin_centers
        frequencies = group.pixel_counts

        # Find the peak region - FITS histograms often have a strong peak
        # but can also have long tails or outliers
        peak_idx = np.argmax(frequencies)
        peak_frequency = frequencies[peak_idx]
        peak_adu = pixel_values_adu[peak_idx]
        
        # Filter out very low frequencies for cleaner analysis
        # But use a relatively low threshold to keep more of the distribution
        threshold = np.max(frequencies) * 0.0001  # 0.01% of peak
        mask = frequencies > threshold
        
        # Make sure we keep some minimum number of points around the peak
        peak_region = (pixel_values_adu >= peak_adu - 20) & (pixel_values_adu <= peak_adu + 20)
        mask = mask | peak_region
        
        pixel_values_adu = pixel_values_adu[mask]
        frequencies = frequencies[mask]
        
        self.logger.info(f"Peak found at {peak_adu:.1f} ADU with {peak_frequency} counts")
        self.logger.info(f"Using {len(pixel_values_adu)} of {len(group.bin_centers)} ADU bins for analysis")

        # Check if data is sufficient for analysis
        if len(pixel_values_adu) < 10:
            self.logger.warning("Not enough data points for analysis.")
            return

        # Create weighted samples for KDE
        # We need to expand the data points according to their frequencies
        # For computational efficiency, we'll sample from the distribution
        max_samples = 100000  # Cap to prevent memory issues
        total_points = np.sum(frequencies)
        
        if total_points > 0:
            # Calculate probability for each bin
            probs = frequencies / total_points
            
            # Sample from the distribution
            samples = np.random.choice(
                pixel_values_adu, 
                size=min(int(total_points), max_samples), 
                p=probs
            )
            
            self.logger.info(f"Generated {len(samples)} representative samples for KDE")
            
            # Perform KDE on the samples
            try:
                # Use scipy's gaussian_kde with automatic bandwidth selection
                kde = stats.gaussian_kde(samples, bw_method='scott')
                
                # Generate a smooth evaluation of the KDE
                x_kde = np.linspace(np.min(samples), np.max(samples), 1000)
                y_kde = kde(x_kde)
                
                # Find peaks in the KDE
                peaks, _ = find_peaks(y_kde, height=np.max(y_kde)*0.5)
                
                if len(peaks) > 0:
                    # Get the highest peak
                    main_peak_idx = np.argmax(y_kde[peaks])
                    main_peak = peaks[main_peak_idx]
                    kde_peak_x = x_kde[main_peak]
                    kde_peak_y = y_kde[main_peak]
                    
                    self.logger.info(f"KDE analysis found peak at {kde_peak_x:.2f} ADU")
                    
                    # Estimate distribution width by finding points at half max height
                    half_max = kde_peak_y / 2
                    
                    # Find indices where KDE crosses half-max height
                    above_half_max = y_kde >= half_max
                    regions = np.where(np.diff(above_half_max.astype(int)))[0]
                    
                    if len(regions) >= 2:
                        left_idx = regions[0]
                        right_idx = regions[-1]
                        kde_fwhm = x_kde[right_idx] - x_kde[left_idx]
                        # Convert FWHM to standard deviation (for a Gaussian, sigma = FWHM / 2.355)
                        kde_sigma = kde_fwhm / 2.355
                    else:
                        # Fallback: use standard deviation of the samples
                        kde_sigma = np.std(samples)
                        
                    self.logger.info(f"KDE width (sigma): {kde_sigma:.2f} ADU")
                    
                    # Now use KDE's peak and width as initial guesses for Gaussian fit
                    initial_amplitude = np.max(frequencies)
                    initial_mean = kde_peak_x
                    initial_sigma = kde_sigma
                    
                else:
                    # Fallback to traditional statistics if no peak found
                    self.logger.warning("No clear peak found in KDE. Using traditional statistics.")
                    initial_mean = np.average(pixel_values_adu, weights=frequencies)
                    # Calculate weighted standard deviation
                    weights = frequencies / np.sum(frequencies)
                    initial_sigma = np.sqrt(np.sum(weights * (pixel_values_adu - initial_mean)**2))
                    initial_amplitude = np.max(frequencies)
            
            except Exception as e:
                self.logger.warning(f"KDE analysis failed: {e}. Using traditional statistics.")
                # Calculate weighted mean and standard deviation
                initial_mean = np.average(pixel_values_adu, weights=frequencies)
                weights = frequencies / np.sum(frequencies)
                initial_sigma = np.sqrt(np.sum(weights * (pixel_values_adu - initial_mean)**2))
                initial_amplitude = np.max(frequencies)
        
        else:
            self.logger.warning("No valid data for KDE analysis.")
            return
            
        self.logger.debug(f"Initial fit parameters: A={initial_amplitude:.1f}, μ={initial_mean:.1f} ADU, σ={initial_sigma:.1f} ADU")
        
        # Define Gaussian function
        def gaussian(x, A, mu, sigma):
            return A * np.exp(-((x - mu) ** 2) / (2 * sigma ** 2))
        
        # Use a more targeted approach for Gaussian fitting:
        # Focus on the region around the identified peak (±3 sigma)
        fit_min = initial_mean - 3 * initial_sigma
        fit_max = initial_mean + 3 * initial_sigma
        
        fit_mask = (pixel_values_adu >= fit_min) & (pixel_values_adu <= fit_max)
        if np.sum(fit_mask) < 5:
            self.logger.warning("Not enough points in peak region for Gaussian fitting.")
            # Use the full range
            fit_mask = np.ones_like(pixel_values_adu, dtype=bool)
            
        fit_x = pixel_values_adu[fit_mask]
        fit_y = frequencies[fit_mask]
        
        self.logger.debug(f"Fitting Gaussian to {np.sum(fit_mask)} points in peak region")
        
        p0 = [initial_amplitude, initial_mean, initial_sigma]
        
        # Define looser bounds for the fit
        lower_bounds = [initial_amplitude * 0.1,     # Amplitude > 10% of initial guess
                       initial_mean - 3*initial_sigma,  # Mean within reasonable range
                       initial_sigma * 0.2]          # Sigma > 20% of initial guess
                      
        upper_bounds = [initial_amplitude * 5,     # Amplitude < 5x initial
                       initial_mean + 3*initial_sigma,  # Mean within reasonable range
                       initial_sigma * 5]          # Sigma < 5x initial guess
        
        bounds = (lower_bounds, upper_bounds)

        # Fit the Gaussian to the peak region
        try:
            popt, pcov = curve_fit(gaussian, fit_x, fit_y, 
                                  p0=p0, bounds=bounds, 
                                  method='trf', 
                                  max_nfev=2000)
                                  
            A_fit, mu_fit_adu, sigma_fit_adu = popt
            
            # Calculate standard errors
            try:
                perr = np.sqrt(np.diag(pcov))
                amplitude_err, mean_err_adu, sigma_err_adu = perr
            except:
                amplitude_err = A_fit * 0.1
                mean_err_adu = sigma_fit_adu * 0.1
                sigma_err_adu = sigma_fit_adu * 0.1

            # Convert to electrons
            mu_fit_electrons = mu_fit_adu * group.egain
            mean_err_electrons = mean_err_adu * group.egain
            sigma_fit_electrons = sigma_fit_adu * group.egain
            sigma_err_electrons = sigma_err_adu * group.egain
            
            # Calculate fit quality - but only for the peak region
            fitted_y = gaussian(fit_x, *popt)
            residuals = fit_y - fitted_y
            ss_res = np.sum(residuals ** 2)
            ss_tot = np.sum((fit_y - np.mean(fit_y)) ** 2)
            
            # Avoid division by zero
            if ss_tot > 0:
                r_squared = 1 - (ss_res / ss_tot)
            else:
                r_squared = 0
                
            # Calculate RMSE relative to peak
            if np.max(fit_y) > 0:
                rmse = np.sqrt(np.mean(residuals**2))
                rmse_percent = (rmse / np.max(fit_y)) * 100
            else:
                rmse_percent = 0
                
            # Calculate for full range (for visualization)
            fitted_frequencies = gaussian(pixel_values_adu, *popt)
            
            # Store both Gaussian fit and KDE results
            group.fit_params = {
                'Amplitude': A_fit,
                'Amplitude Error': amplitude_err,
                'Mean ADU': mu_fit_adu,
                'Mean ADU Error': mean_err_adu,
                'Sigma ADU': sigma_fit_adu,
                'Sigma ADU Error': sigma_err_adu,
                'Mean': mu_fit_electrons,
                'Mean Error': mean_err_electrons,
                'Sigma': sigma_fit_electrons,
                'Sigma Error': sigma_err_electrons,
                'R-squared': r_squared,
                'RMSE Percent': rmse_percent,
                'ADU Values': pixel_values_adu,
                'Frequencies': frequencies,
                'Fitted Frequencies': fitted_frequencies,
                'KDE X': x_kde,
                'KDE Y': y_kde,
                'KDE Peak X': kde_peak_x if 'kde_peak_x' in locals() else initial_mean,
                'KDE Sigma': kde_sigma if 'kde_sigma' in locals() else initial_sigma,
                'Samples': samples
            }
            
            # Log results with both Gaussian and KDE estimates
            self.logger.info(f"Gaussian fit results (ADU): Mean={mu_fit_adu:.3f}±{mean_err_adu:.3f}, "
                           f"Sigma={sigma_fit_adu:.3f}±{sigma_err_adu:.3f}, "
                           f"R²={r_squared:.4f}")
            
            if 'kde_peak_x' in locals() and 'kde_sigma' in locals():
                self.logger.info(f"KDE analysis (ADU): Peak={kde_peak_x:.3f}, "
                               f"Sigma={kde_sigma:.3f}")
            
            self.logger.info(f"Results (e-): Mean={mu_fit_electrons:.3f}±{mean_err_electrons:.3f} e-, "
                           f"Sigma={sigma_fit_electrons:.3f}±{sigma_err_electrons:.3f} e-")
            
            read_noise = sigma_fit_electrons
            self.logger.info(f"Calculated read noise: {read_noise:.3f} e- RMS")
            self.logger.info(f"Gain setting: {group.gain}, EGAIN: {group.egain:.3f} e-/ADU")
            
            # Evaluate fit quality
            if r_squared < 0.9:
                self.logger.warning(f"Note: Gaussian fit R²={r_squared:.4f} indicates non-Gaussian distribution.")
                
        except Exception as e:
            self.logger.error(f"Gaussian fitting failed: {e}")
            # Use KDE results directly if available
            if 'kde_peak_x' in locals() and 'kde_sigma' in locals():
                self.logger.info("Using KDE results directly since Gaussian fit failed")
                
                # Store KDE results without Gaussian fit
                kde_mean_electrons = kde_peak_x * group.egain
                kde_sigma_electrons = kde_sigma * group.egain
                
                group.fit_params = {
                    'Amplitude': np.max(frequencies),
                    'Amplitude Error': 0,
                    'Mean ADU': kde_peak_x,
                    'Mean ADU Error': kde_sigma / 10,  # Estimate error as 10% of width
                    'Sigma ADU': kde_sigma,
                    'Sigma ADU Error': kde_sigma / 10,
                    'Mean': kde_mean_electrons,
                    'Mean Error': kde_sigma_electrons / 10,
                    'Sigma': kde_sigma_electrons,
                    'Sigma Error': kde_sigma_electrons / 10,
                    'R-squared': 0,  # No R² for KDE
                    'RMSE Percent': 0,
                    'ADU Values': pixel_values_adu,
                    'Frequencies': frequencies,
                    'Fitted Frequencies': np.zeros_like(frequencies),  # No Gaussian fit
                    'KDE X': x_kde,
                    'KDE Y': y_kde,
                    'KDE Peak X': kde_peak_x,
                    'KDE Sigma': kde_sigma,
                    'Samples': samples,
                    'Using KDE Only': True
                }
                
                self.logger.info(f"KDE analysis (ADU): Peak={kde_peak_x:.3f}, Sigma={kde_sigma:.3f}")
                self.logger.info(f"KDE results (e-): Mean={kde_mean_electrons:.3f} e-, Sigma={kde_sigma_electrons:.3f} e-")
                self.logger.info(f"Estimated read noise from KDE: {kde_sigma_electrons:.3f} e- RMS")
            else:
                self.logger.error("Both Gaussian fit and KDE analysis failed.")

    def generate_individual_plot(self, group: FITSGroup, plots_directory: str) -> None:
        """
        Generates an individual plot for a group showing histogram, KDE, and Gaussian fit.
        
        Args:
            group: FITSGroup object with fit parameters
            plots_directory: Directory to save the plot
        """
        if group.fit_params is None or not group.pixel_counts.any() or group.bin_centers is None:
            self.logger.warning(f"Not enough data to generate plot for GAIN={group.gain}, TEMP={group.temperature}°C")
            return
            
        with self.logger.status(f"Generating plot for GAIN={group.gain}, TEMP={group.temperature}°C...", spinner="dots"):
            # Create figure with three subplots
            fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 15))
            
            # Define Gaussian function for plotting
            def gaussian(x, A, mu, sigma):
                return A * np.exp(-((x - mu) ** 2) / (2 * sigma ** 2))
            
            # Get data from fit parameters
            adu_values = group.fit_params['ADU Values']
            frequencies = group.fit_params['Frequencies']
            
            # Plot 1: Histogram and Both Fits (ADU)
            # Create histogram using actual frequencies
            bar_width = (np.max(adu_values) - np.min(adu_values)) / len(adu_values)
            ax1.bar(adu_values, frequencies, width=bar_width, alpha=0.6, color='blue', label='Pixel Distribution')
            
            # Plot Gaussian fit if available (not using KDE-only mode)
            using_kde_only = group.fit_params.get('Using KDE Only', False)
            
            if not using_kde_only:
                # Get Gaussian parameters
                A_fit = group.fit_params['Amplitude']
                mu_fit = group.fit_params['Mean ADU']
                sigma_fit = group.fit_params['Sigma ADU']
                
                # Create smooth x-axis for fit visualization
                x_range = max(6 * sigma_fit, np.max(adu_values) - np.min(adu_values))
                smooth_x = np.linspace(mu_fit - x_range/2, mu_fit + x_range/2, 1000)
                smooth_gaussian = gaussian(smooth_x, A_fit, mu_fit, sigma_fit)
                
                # Plot Gaussian fit
                ax1.plot(smooth_x, smooth_gaussian, color='red', linestyle='-', 
                         linewidth=2, label='Gaussian Fit')
            
            # Plot KDE if available
            if 'KDE X' in group.fit_params and 'KDE Y' in group.fit_params:
                kde_x = group.fit_params['KDE X']
                kde_y = group.fit_params['KDE Y']
                
                # Scale KDE to same height as histogram for comparison
                scale_factor = np.max(frequencies) / np.max(kde_y) if np.max(kde_y) > 0 else 1
                scaled_kde_y = kde_y * scale_factor
                
                ax1.plot(kde_x, scaled_kde_y, color='green', linestyle='-', 
                         linewidth=2, label='KDE (scaled)')
                
                # Mark KDE peak
                kde_peak_x = group.fit_params.get('KDE Peak X')
                if kde_peak_x is not None:
                    peak_idx = np.abs(kde_x - kde_peak_x).argmin()
                    ax1.axvline(x=kde_peak_x, color='green', linestyle='--', alpha=0.7)
                    ax1.plot(kde_peak_x, scaled_kde_y[peak_idx], 'go', markersize=8)
            
            # Add fit parameters as text
            if using_kde_only:
                info_text = (
                    f"KDE Analysis (ADU):\n"
                    f"Peak = {group.fit_params['KDE Peak X']:.2f} ADU\n"
                    f"Width (σ) = {group.fit_params['KDE Sigma']:.2f} ADU\n"
                    f"GAIN = {group.gain}\n"
                    f"Note: Using KDE only (Gaussian fit failed)"
                )
            else:
                r_squared = group.fit_params.get('R-squared', 0)
                info_text = (
                    f"Gaussian Fit (ADU):\n"
                    f"μ = {group.fit_params['Mean ADU']:.2f} ± {group.fit_params['Mean ADU Error']:.2f}\n"
                    f"σ = {group.fit_params['Sigma ADU']:.2f} ± {group.fit_params['Sigma ADU Error']:.2f}\n"
                    f"GAIN = {group.gain}\n"
                    f"R² = {r_squared:.4f}"
                )
            
            # Place text box
            props = dict(boxstyle='round', facecolor='wheat', alpha=0.4)
            ax1.text(0.98, 0.98, info_text, transform=ax1.transAxes, fontsize=10,
                    verticalalignment='top', horizontalalignment='right', bbox=props)
            
            # Set axis limits and labels
            ax1.set_title(f"ADU Distribution with Fits\nGAIN={group.gain}, Temperature={group.temperature}°C",
                      fontweight='bold')
            ax1.set_xlabel('ADU Value', fontweight='bold')
            ax1.set_ylabel('Frequency', fontweight='bold')
            ax1.grid(True, linestyle='--', alpha=0.7)
            ax1.legend(loc='upper left')
            
            # Plot 2: Log scale to better see tails
            ax2.bar(adu_values, frequencies, width=bar_width, alpha=0.6, color='blue', label='Pixel Distribution')
            
            if not using_kde_only:
                ax2.plot(smooth_x, smooth_gaussian, color='red', linestyle='-', 
                         linewidth=2, label='Gaussian Fit')
            
            # Plot KDE on log scale too
            if 'KDE X' in group.fit_params and 'KDE Y' in group.fit_params:
                ax2.plot(kde_x, scaled_kde_y, color='green', linestyle='-', 
                         linewidth=2, label='KDE (scaled)')
            
            ax2.set_yscale('log')
            ax2.set_title("Log Scale View (ADU)", fontweight='bold')
            ax2.set_xlabel('ADU Value', fontweight='bold')
            ax2.set_ylabel('Frequency (log scale)', fontweight='bold')
            ax2.grid(True, linestyle='--', alpha=0.7)
            ax2.legend(loc='upper left')
            
            # Plot 3: Electron Values
            # Convert to electron values
            electron_values = adu_values * group.egain
            
            bar_width_e = (np.max(electron_values) - np.min(electron_values)) / len(electron_values)
            ax3.bar(electron_values, frequencies, width=bar_width_e, alpha=0.6, color='green', 
                    label='Pixel Distribution')
            
            # Convert fits to electron space
            if not using_kde_only:
                smooth_electron_x = smooth_x * group.egain
                ax3.plot(smooth_electron_x, smooth_gaussian, color='red', linestyle='-', 
                         linewidth=2, label='Gaussian Fit')
            
            if 'KDE X' in group.fit_params and 'KDE Y' in group.fit_params:
                kde_electron_x = kde_x * group.egain
                ax3.plot(kde_electron_x, scaled_kde_y, color='green', linestyle='-', 
                         linewidth=2, label='KDE (scaled)')
            
            # Add electron space parameters as text
            if using_kde_only:
                e_info_text = (
                    f"KDE Analysis (e-):\n"
                    f"Peak = {group.fit_params['KDE Peak X'] * group.egain:.2f} e-\n"
                    f"Width (σ) = {group.fit_params['KDE Sigma'] * group.egain:.2f} e-\n"
                    f"Read Noise = {group.fit_params['KDE Sigma'] * group.egain:.2f} e- RMS\n"
                    f"EGAIN = {group.egain:.3f} e-/ADU"
                )
            else:
                e_info_text = (
                    f"Fit Parameters (e-):\n"
                    f"μ = {group.fit_params['Mean']:.2f} ± {group.fit_params['Mean Error']:.2f} e-\n"
                    f"σ = {group.fit_params['Sigma']:.2f} ± {group.fit_params['Sigma Error']:.2f} e-\n"
                    f"Read Noise = {group.fit_params['Sigma']:.2f} e- RMS\n"
                    f"EGAIN = {group.egain:.3f} e-/ADU"
                )
            
            ax3.text(0.98, 0.98, e_info_text, transform=ax3.transAxes, fontsize=10,
                    verticalalignment='top', horizontalalignment='right', bbox=props)
            
            ax3.set_title("Electron Count Distribution", fontweight='bold')
            ax3.set_xlabel('Electron Count (e-)', fontweight='bold')
            ax3.set_ylabel('Frequency', fontweight='bold')
            ax3.grid(True, linestyle='--', alpha=0.7)
            ax3.legend(loc='upper left')
            
            # Adjust spacing
            plt.tight_layout()
            
            # Save plot
            os.makedirs(plots_directory, exist_ok=True)
            plot_filename = f"GaussianFit_GAIN_{group.gain}_TEMP_{group.temperature}.png"
            plot_path = os.path.join(plots_directory, plot_filename)
            plt.savefig(plot_path, dpi=self.plot_dpi)
            plt.close()
            
            self.logger.info(f"Plot saved to {plot_path}")

    def generate_overlay_plot(self, groups: List[FITSGroup], plots_directory: str) -> None:
        """
        Generates an overlay plot comparing all groups.
        
        Args:
            groups: List of FITSGroup objects with fit parameters
            plots_directory: Directory to save the plot
        """
        # Filter groups with fit parameters
        valid_groups = [g for g in groups if g.fit_params]
        
        if not valid_groups:
            self.logger.warning("No valid groups with fit parameters for overlay plot")
            return
            
        with self.logger.status("Generating overlay plot...", spinner="dots"):
            # Create a figure with subplots: main plot and a small table-like subplot for parameters
            fig = plt.figure(figsize=(12, 10))
            
            # Create two main plots: one for ADU, one for electrons
            ax_adu = plt.subplot2grid((6, 1), (0, 0), rowspan=2)
            ax_electrons = plt.subplot2grid((6, 1), (2, 0), rowspan=2)
            
            # Color palette
            colors = plt.cm.tab10(np.linspace(0, 1, len(valid_groups)))
            
            # For smooth Gaussian curves
            def gaussian(x, A, mu, sigma):
                return A * np.exp(-((x - mu) ** 2) / (2 * sigma ** 2))
            
            # Plot each group - ADU VALUES
            for idx, group in enumerate(valid_groups):
                label = f"G={group.gain}, T={group.temperature}°C"
                color = colors[idx]
                
                # Get data from fit params
                adu_values = group.fit_params['ADU Values']
                frequencies = group.fit_params['Frequencies']
                
                # Create properly dense x-axis for smooth Gaussian curve
                adu_min, adu_max = np.min(adu_values), np.max(adu_values)
                smooth_x = np.linspace(adu_min, adu_max, 1000)
                
                # Get fitted parameters
                A_fit = group.fit_params['Amplitude']
                mu_fit_adu = group.fit_params['Mean ADU']
                sigma_fit_adu = group.fit_params['Sigma ADU']
                
                # Generate smooth Gaussian curve
                smooth_gaussian = gaussian(smooth_x, A_fit, mu_fit_adu, sigma_fit_adu)
                
                # Normalize frequencies to maximum of 1.0 for comparison
                max_freq = np.max(frequencies)
                norm_frequencies = frequencies / max_freq
                norm_gaussian = smooth_gaussian / max_freq
                
                # Plot histogram data and fit line
                # Use step plot for better visualization in overlay
                ax_adu.step(adu_values, norm_frequencies, where='mid', color=color, alpha=0.5, linewidth=1, label=f"Data: {label}")
                ax_adu.plot(smooth_x, smooth_gaussian, color=color, linestyle='--', linewidth=2, alpha=0.8)
            
            # Set ADU plot properties
            ax_adu.set_xlabel('ADU Value', fontweight='bold')
            ax_adu.set_ylabel('Normalized Frequency', fontweight='bold')
            ax_adu.set_title('Comparison of ADU Distributions', fontweight='bold')
            ax_adu.grid(True, linestyle='--', alpha=0.5)
            ax_adu.legend(loc='upper right', fontsize=8)
            
            # Plot each group - ELECTRON VALUES
            for idx, group in enumerate(valid_groups):
                label = f"G={group.gain}, T={group.temperature}°C"
                color = colors[idx]
                
                # Get data from fit params
                adu_values = group.fit_params['ADU Values']
                frequencies = group.fit_params['Frequencies']
                
                # Create electron values
                electron_values = adu_values * group.egain
                
                # Create properly dense x-axis for smooth Gaussian curve
                adu_min, adu_max = np.min(adu_values), np.max(adu_values)
                smooth_x = np.linspace(adu_min, adu_max, 1000)
                smooth_electron_x = smooth_x * group.egain
                
                # Get fitted parameters
                A_fit = group.fit_params['Amplitude']
                mu_fit_adu = group.fit_params['Mean ADU']
                sigma_fit_adu = group.fit_params['Sigma ADU']
                
                # Generate smooth Gaussian curve
                smooth_gaussian = gaussian(smooth_x, A_fit, mu_fit_adu, sigma_fit_adu)
                
                # Normalize frequencies to maximum of 1.0 for comparison
                max_freq = np.max(frequencies)
                norm_frequencies = frequencies / max_freq
                norm_gaussian = smooth_gaussian / max_freq
                
                # Plot histogram data and fit line
                ax_electrons.step(electron_values, norm_frequencies, where='mid', color=color, alpha=0.5, linewidth=1, label=f"Data: {label}")
                ax_electrons.plot(smooth_electron_x, smooth_gaussian, color=color, linestyle='--', linewidth=2, alpha=0.8)
            
            # Set electron plot properties
            ax_electrons.set_xlabel('Electron Count (e-)', fontweight='bold')
            ax_electrons.set_ylabel('Normalized Frequency', fontweight='bold')
            ax_electrons.set_title('Comparison of Electron Count Distributions', fontweight='bold')
            ax_electrons.grid(True, linestyle='--', alpha=0.5)
            ax_electrons.legend(loc='upper right', fontsize=8)
            
            # Create a table at the bottom for parameters
            ax_table = plt.subplot2grid((6, 1), (4, 0), rowspan=2)
            ax_table.axis('off')
            
            # Prepare table data (separate tables for ADU and electrons)
            table_data = []
            column_labels = ['GAIN', 'TEMP (°C)', 'EGAIN (e-/ADU)', 'Mean ADU', 'Sigma ADU', 'Mean (e-)', 'Sigma (e-)', 'Read Noise (e-)', 'R²']
            
            for group in valid_groups:
                fit = group.fit_params
                table_data.append([
                    f"{group.gain:.1f}",
                    f"{group.temperature:.1f}",
                    f"{group.egain:.3f}",
                    f"{fit['Mean ADU']:.3f}±{fit['Mean ADU Error']:.3f}",
                    f"{fit['Sigma ADU']:.3f}±{fit['Sigma ADU Error']:.3f}",
                    f"{fit['Mean']:.3f}±{fit['Mean Error']:.3f}",
                    f"{fit['Sigma']:.3f}±{fit['Sigma Error']:.3f}",
                    f"{fit['Sigma']:.3f}",
                    f"{fit['R-squared']:.4f}"
                ])
            
            # Create the table
            table = ax_table.table(
                cellText=table_data,
                colLabels=column_labels,
                cellLoc='center',
                loc='center',
                bbox=[0, 0, 1, 1]
            )
            
            # Style the table
            table.auto_set_font_size(False)
            table.set_fontsize(9)
            table.scale(1, 1.5)
            
            for key, cell in table.get_celld().items():
                if key[0] == 0:  # Header row
                    cell.set_text_props(weight='bold')
            
            # Adjust spacing
            plt.tight_layout()
            plt.subplots_adjust(hspace=0.4)
            
            # Save the plot
            os.makedirs(plots_directory, exist_ok=True)
            plot_filename = "Overlay_Gaussian_Fits_Comparison.png"
            plot_path = os.path.join(plots_directory, plot_filename)
            plt.savefig(plot_path, dpi=self.plot_dpi)
            plt.close()
            
            self.logger.info(f"Overlay plot saved to {plot_path}")

    def save_summary_csv(self, groups: List[FITSGroup], csv_path: str) -> None:
        """
        Saves a summary of all group fit parameters to a CSV file.
        
        Args:
            groups: List of FITSGroup objects with fit parameters
            csv_path: Path to save the CSV file
        """
        # Filter groups with fit parameters
        valid_groups = [g for g in groups if g.fit_params]
        
        if not valid_groups:
            self.logger.warning("No valid groups with fit parameters for CSV summary")
            return
            
        self.logger.info("Saving summary to CSV...")
        
        try:
            with open(csv_path, mode='w', newline='') as file:
                writer = csv.writer(file)
                
                # Write header row with both ADU and electron values
                headers = [
                    'Gain', 'Temperature (°C)', 'Exposure (s)', 'EGAIN (e-/ADU)',
                    'Mean ADU', 'Mean ADU Error', 'Sigma ADU', 'Sigma ADU Error',
                    'Mean (e-)', 'Mean Error (e-)', 'Sigma (e-)', 'Sigma Error (e-)',
                    'Read Noise (e- RMS)', 'R-squared', 
                    'Statistical Mean ADU', 'Statistical Std ADU', 
                    'Statistical Mean e-', 'Statistical Std e-',
                    'Statistical Read Noise', 'Statistical Read Noise Error',
                    'Number of Files'
                ]
                writer.writerow(headers)
                
                # Write data rows
                for group in valid_groups:
                    fit = group.fit_params
                    stats = getattr(group, 'stats', {})
                    
                    row = [
                        group.gain,
                        group.temperature,
                        group.exposure_time,
                        group.egain,
                        fit['Mean ADU'],
                        fit['Mean ADU Error'],
                        fit['Sigma ADU'],
                        fit['Sigma ADU Error'],
                        fit['Mean'],
                        fit['Mean Error'],
                        fit['Sigma'],
                        fit['Sigma Error'],
                        fit['Sigma'],  # Read noise = sigma
                        fit['R-squared'],
                        stats.get('Mean ADU', 0),
                        stats.get('Std ADU', 0),
                        stats.get('Mean e-', 0),
                        stats.get('Std e-', 0),
                        stats.get('Statistical Read Noise', 0),
                        stats.get('Statistical Read Noise Error', 0),
                        len(group.file_paths)
                    ]
                    writer.writerow(row)
                    
            self.logger.success(f"Summary saved to {csv_path}")
            
        except Exception as e:
            self.logger.error(f"Error saving CSV: {e}")

    def analyze(self, 
                directory_path: str, 
                exptime_value: Optional[float] = None,
                plots_directory: Optional[str] = None, 
                summary_csv_path: Optional[str] = None) -> None:
        """
        Main method to process FITS files, perform Gaussian fitting, and generate outputs.
        
        Args:
            directory_path: Path to the directory containing FITS files
            exptime_value: The exposure time value to filter FITS files (optional)
            plots_directory: Directory to save the plots (optional)
            summary_csv_path: Path to save the summary CSV file (optional)
        """
        start_time = time.time()
        
        # Default paths if not provided
        if plots_directory is None:
            plots_directory = os.path.join(os.path.dirname(directory_path), "Plots")
        
        if summary_csv_path is None:
            summary_csv_path = os.path.join(os.path.dirname(directory_path), "gaussian_fit_summary.csv")
        
        # Ensure directories exist
        Path(plots_directory).mkdir(parents=True, exist_ok=True)
        
        self.logger.info("=== FITS Histogram Analysis ===")
        self.logger.info(f"Directory: {directory_path}")
        self.logger.info(f"Exposure filter: {exptime_value if exptime_value is not None else 'None (using all)'}")
        
        try:
            # Group FITS files by parameters
            group_dict = self.group_fits_files(directory_path, exptime_value)
            
            if not group_dict:
                self.logger.warning("No suitable FITS files found for analysis.")
                return
                
            # Process each group and collect results
            groups = list(group_dict.values())
            for group in groups:
                self.process_group(group)
                self.fit_gaussian(group)
                if group.fit_params:
                    self.generate_individual_plot(group, plots_directory)
            
            # Generate overlay plot
            self.generate_overlay_plot(groups, plots_directory)
            
            # Save summary CSV
            self.save_summary_csv(groups, summary_csv_path)
            
            # Overall summary
            valid_groups = [g for g in groups if g.fit_params]
            
            if valid_groups:
                self.logger.info("\n=== Analysis Summary ===")
                self.logger.info(f"Processed {len(groups)} groups, {sum(len(g.file_paths) for g in groups)} files total")
                self.logger.info(f"Successfully fit {len(valid_groups)} groups")
                
                # Display table of results
                headers = ["Gain", "Temp (°C)", "Mean (e-)", "Sigma (e-)", "Read Noise (e-)", "Statistical Read Noise (e-)"]
                
                rows = []
                for group in valid_groups:
                    fit = group.fit_params
                    stats = getattr(group, 'stats', {})
                    stat_read_noise = stats.get('Statistical Read Noise', 0)
                    
                    rows.append([
                        f"{group.gain:.1f}",
                        f"{group.temperature:.1f}",
                        f"{fit['Mean']:.3f}",
                        f"{fit['Sigma']:.3f}",
                        f"{fit['Sigma']:.3f}",
                        f"{stat_read_noise:.3f}"
                    ])
                
                self.logger.table(title="Results Summary", columns=headers, rows=rows)
            
        except Exception as e:
            self.logger.error(f"Error during analysis: {e}")
            import traceback
            self.logger.debug(traceback.format_exc())
        
        end_time = time.time()
        self.logger.success(f"Analysis completed in {end_time - start_time:.2f} seconds")


def main():
    """
    Main function for command-line execution.
    """
    parser = argparse.ArgumentParser(
        description="Camera Noise Characterization - Analyze FITS files to characterize "
                    "thermal dependence of read noise for TEC CMOS sensors"
    )
    
    parser.add_argument(
        "-d", "--directory",
        type=str,
        required=True,
        help="Directory containing FITS files"
    )
    
    parser.add_argument(
        "-e", "--exptime",
        type=float,
        help="Filter files by exposure time (in seconds)"
    )
    
    parser.add_argument(
        "-p", "--plots-dir",
        type=str,
        help="Directory to save plots (defaults to 'Plots' in parent directory)"
    )
    
    parser.add_argument(
        "-c", "--csv-path",
        type=str,
        help="Path to save the summary CSV (defaults to 'gaussian_fit_summary.csv' in parent directory)"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    # Set up logger
    if args.debug:
        logger.set_level(10)  # DEBUG level
    
    # Create and run analyzer
    analyzer = FITSHistogramAnalyzer()
    analyzer.analyze(
        directory_path=args.directory,
        exptime_value=args.exptime,
        plots_directory=args.plots_dir,
        summary_csv_path=args.csv_path
    )

if __name__ == "__main__":
    main() 