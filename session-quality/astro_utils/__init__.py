"""
astro_utils - A package for astronomical data analysis and visualization.

This package provides tools for:
- PHD2 guiding analysis
- Autofocus analysis
- Alt/Az statistics calculation
- HTML dashboard generation with quality scoring

Part of the Astro Run Quality Controller suite.
"""

__version__ = "1.4.0"

from .config import Config
from .astro_logger import Logger
from . import utils
from .phd2_analysis import PHD2Analysis
from .autofocus_analysis import AutofocusAnalysis
from .altaz_analysis import AltAzAnalysis
from .dashboard import SessionAnalyzer, QualityScorer, DashboardGenerator

__all__ = [
    'Config',
    'Logger',
    'utils',
    'PHD2Analysis',
    'AutofocusAnalysis',
    'AltAzAnalysis',
    'SessionAnalyzer',
    'QualityScorer',
    'DashboardGenerator',
]