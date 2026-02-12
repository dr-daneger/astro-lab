#!/usr/bin/env python3
"""
User-friendly wrapper script for running autofocus analysis.
This script provides a simple interface to the AutofocusAnalysis class.
"""

import sys
from pathlib import Path
import argparse

# Add the parent directory to the path to ensure astro_utils can be imported
sys.path.insert(0, str(Path(__file__).parent))

from astro_utils.config import Config
from astro_utils.autofocus_analysis import AutofocusAnalysis
from astro_utils.astro_logger import Logger

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Analyze autofocus events with a user-friendly interface."
    )
    
    parser.add_argument(
        "-d", "--log-dir",
        type=Path,
        required=True,
        help="Directory containing Autorun logs"
    )
    
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to YAML configuration file"
    )
    
    parser.add_argument(
        "-p", "--plot-dir",
        type=Path,
        help="Directory to save performance plots (optional)"
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
    logger = Logger("AutofocusAnalysis")
    if args.debug:
        logger.set_level(10)  # DEBUG level
    
    logger.info("Starting autofocus analysis")
    
    try:
        # Load configuration
        config = Config(args.config if args.config else None)
        logger.info("Configuration loaded")
        
        # Create analyzer
        analyzer = AutofocusAnalysis(config, args.log_dir)
        logger.info("Autofocus Analyzer initialized")
        
        # Run analysis
        analyzer.analyze_session()
        
        # Generate plots if requested
        if args.plot_dir:
            logger.info(f"Generating plots in {args.plot_dir}")
            analyzer.plot_temperature_vs_duration(args.plot_dir)
        
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