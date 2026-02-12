#!/usr/bin/env python3
"""
User-friendly wrapper script for running Alt/Az analysis.
This script provides a simple interface to the AltAzAnalysis class.
"""

import sys
from pathlib import Path
import argparse

# Add the parent directory to the path to ensure astro_utils can be imported
sys.path.insert(0, str(Path(__file__).parent))

from astro_utils.config import Config
from astro_utils.altaz_analysis import AltAzAnalysis
from astro_utils.astro_logger import Logger


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Analyze Alt/Az coordinates for FITS files in an imaging session."
    )
    
    parser.add_argument(
        "-d", "--fits-dir",
        type=Path,
        required=True,
        help="Directory containing FITS files"
    )
    
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to YAML configuration file"
    )
    
    parser.add_argument(
        "-p", "--plot-dir",
        type=Path,
        help="Directory to save plots (optional)"
    )
    
    parser.add_argument(
        "-o", "--output-csv",
        type=Path,
        help="Path for output CSV file (optional)"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    
    # Create logger with appropriate level
    logger = Logger("AltAzAnalysis")
    if args.debug:
        logger.set_level(10)  # DEBUG level
    
    logger.info("Starting Alt/Az analysis")
    
    try:
        # Load configuration
        config = Config(args.config if args.config else None)
        logger.info("Configuration loaded")
        
        # Create analyzer
        analyzer = AltAzAnalysis(config, args.fits_dir)
        logger.info("Alt/Az Analyzer initialized")
        
        # Run analysis
        analyzer.analyze_session()
        
        # Save CSV
        analyzer.save_csv(args.output_csv)
        
        # Generate plots if requested
        if args.plot_dir:
            logger.info(f"Generating plots in {args.plot_dir}")
            analyzer.plot_altitude_timeline(args.plot_dir)
        
        logger.success("Analysis completed successfully!")
        return 0
        
    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        return 1
    except ValueError as e:
        logger.error(f"Invalid value: {e}")
        return 1
    except Exception as e:
        logger.error(f"Error during analysis: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
