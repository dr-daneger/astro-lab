"""Configuration module for astro_utils package."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any
import yaml

@dataclass
class PHD2Config:
    """Configuration for PHD2 analysis."""
    pixel_scale_arcsec: float = 6.45  # arcsec per pixel
    pixel_size_um: float = 3.8  # microns per pixel
    autorun_log_prefix: str = "Autorun_Log"
    phd2_log_prefix: str = "PHD2_GuideLog"
    log_extension: str = ".txt"

@dataclass
class AutofocusConfig:
    """Configuration for autofocus analysis."""
    min_stars: int = 10
    max_hfr: float = 5.0  # Maximum Half Flux Radius in pixels
    curve_fit_points: int = 7

@dataclass
class AltAzConfig:
    """Configuration for Alt/Az statistics and observer location."""
    # Observer location (default: Beaverton, OR)
    latitude: float = 45.5145       # degrees North
    longitude: float = -122.848     # degrees East (negative for West)
    elevation: float = 60.0         # meters above sea level
    timezone: str = "America/Los_Angeles"
    
    # Analysis settings
    min_altitude: float = 30.0      # Minimum altitude in degrees
    time_resolution: float = 1.0    # Time resolution in minutes

class Config:
    """Main configuration class."""
    def __init__(self, config_file: Path = None):
        self.phd2 = PHD2Config()
        self.autofocus = AutofocusConfig()
        self.altaz = AltAzConfig()
        
        if config_file and config_file.exists():
            self._load_from_file(config_file)
    
    def _load_from_file(self, config_file: Path) -> None:
        """Load configuration from YAML file."""
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
            
        if 'phd2' in config_data:
            for key, value in config_data['phd2'].items():
                setattr(self.phd2, key, value)
                
        if 'autofocus' in config_data:
            for key, value in config_data['autofocus'].items():
                setattr(self.autofocus, key, value)
                
        if 'altaz' in config_data:
            for key, value in config_data['altaz'].items():
                setattr(self.altaz, key, value)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            'phd2': {
                'pixel_scale_arcsec': self.phd2.pixel_scale_arcsec,
                'pixel_size_um': self.phd2.pixel_size_um,
                'autorun_log_prefix': self.phd2.autorun_log_prefix,
                'phd2_log_prefix': self.phd2.phd2_log_prefix,
                'log_extension': self.phd2.log_extension
            },
            'autofocus': {
                'min_stars': self.autofocus.min_stars,
                'max_hfr': self.autofocus.max_hfr,
                'curve_fit_points': self.autofocus.curve_fit_points
            },
            'altaz': {
                'latitude': self.altaz.latitude,
                'longitude': self.altaz.longitude,
                'elevation': self.altaz.elevation,
                'timezone': self.altaz.timezone,
                'min_altitude': self.altaz.min_altitude,
                'time_resolution': self.altaz.time_resolution
            }
        }
    
    def save(self, config_file: Path) -> None:
        """Save configuration to YAML file."""
        with open(config_file, 'w') as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False) 