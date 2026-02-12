#!/usr/bin/env python3
"""
Astro Run Quality Controller - Combined Runner with HTML Dashboard

This script analyzes an imaging session and generates a comprehensive HTML 
dashboard with quality scoring.

Features:
- Time-based plots (Alt/Az, temperature, focus position)
- Per-filter statistics (focus position, HFR)
- Guiding quality analysis
- Quality scoring with weighted breakdown
- Interactive HTML dashboard output

Usage:
    python run_night_quality.py -d /path/to/session/folder
    python run_night_quality.py -d /path/to/session -o report.html

The session folder should contain:
- Autorun_Log_*.txt files
- FITS image files (*.fits or *.fit)
- Optionally: PHD2_GuideLog_*.txt files
"""

import sys
from pathlib import Path
import argparse
from datetime import datetime

# Add the parent directory to the path to ensure astro_utils can be imported
sys.path.insert(0, str(Path(__file__).parent))

from astro_utils.config import Config
from astro_utils.astro_logger import Logger
from astro_utils.dashboard import SessionAnalyzer, DashboardGenerator
from astro_utils.utils import ensure_directory


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate a comprehensive astro run quality report with HTML dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python run_night_quality.py -d "G:\\Astrophotography\\2026-01-23"
    python run_night_quality.py -d /path/to/session -o my_report.html
    python run_night_quality.py -d /path/to/session -c custom_config.yaml --debug
        """
    )
    
    parser.add_argument(
        "-d", "--session-dir",
        type=Path,
        required=True,
        help="Directory containing the imaging session data (logs and FITS files)"
    )
    
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output path for HTML report (default: session_dir/astro_qc_report.html)"
    )
    
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to YAML configuration file"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    
    parser.add_argument(
        "--star-analysis",
        action="store_true",
        help="Run per-frame star detection for HFR/eccentricity analysis (adds 2-5 min)"
    )
    
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    
    # Create logger
    logger = Logger("NightQuality")
    if args.debug:
        logger.set_level(10)  # DEBUG level
    
    # Print header
    logger.panel(
        f"Session: {args.session_dir}\n"
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        title="Astro Run Quality Controller v1.3.0",
        style="info"
    )
    
    # Validate session directory
    if not args.session_dir.exists():
        logger.error(f"Session directory does not exist: {args.session_dir}")
        return 1
    
    try:
        # Load configuration
        config_path = args.config if args.config else Path(__file__).parent / "config.yaml"
        if config_path.exists():
            config = Config(config_path)
            logger.info(f"Configuration loaded from {config_path}")
        else:
            config = Config()
            logger.info("Using default configuration")
        
        # Create session analyzer
        logger.info("Analyzing session data...")
        status_msg = "Processing FITS files and logs..."
        if args.star_analysis:
            status_msg += " (with star analysis)"
        with logger.status(status_msg, spinner="dots"):
            analyzer = SessionAnalyzer(config, args.session_dir)
            analyzer.analyze(star_analysis=args.star_analysis)
        
        # Display summary
        logger.panel(
            f"Target: {analyzer.target_name or 'Unknown'}\n"
            f"Images: {len(analyzer.images)}\n"
            f"Filters: {', '.join(analyzer.filter_stats.keys())}\n"
            f"AF Events: {len(analyzer.autofocus_events)}\n"
            f"Guide Events: {len(analyzer.guide_events)}",
            title="Session Summary",
            style="info"
        )
        
        # Determine output path
        if args.output:
            output_path = args.output
        else:
            # Default: put in session directory with date-based name
            date_str = ""
            if analyzer.session_start:
                date_str = analyzer.session_start.strftime("%Y-%m-%d")
            else:
                date_str = datetime.now().strftime("%Y-%m-%d")
            output_path = args.session_dir / f"astro_qc_report_{date_str}.html"
        
        # Generate dashboard
        logger.info("Generating HTML dashboard...")
        dashboard = DashboardGenerator(analyzer)
        report_path = dashboard.generate(output_path)
        
        logger.success(f"Dashboard generated: {report_path}")
        
        # Calculate and display overall score
        from astro_utils.dashboard import QualityScorer
        scorer = QualityScorer(analyzer)
        scores = scorer.calculate_scores()
        overall = scorer.calculate_overall_score(scores)
        
        # Display score summary
        logger.panel(
            f"Overall Quality Score: {overall:.0f}/100",
            title="Quality Rating",
            style="success" if overall >= 70 else "warning" if overall >= 50 else "error"
        )
        
        # Show score breakdown
        headers = ["Metric", "Score", "Weight"]
        rows = [[s.name, f"{s.score:.0f}", f"{s.weight*100:.0f}%"] for s in scores]
        logger.table(title="Score Breakdown", columns=headers, rows=rows)
        
        logger.success("Astro run quality analysis completed!")
        logger.info(f"Open the report in your browser: {report_path}")
        
        return 0
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        if args.debug:
            logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
