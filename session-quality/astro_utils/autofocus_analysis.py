"""Autofocus analysis module for astro_utils package."""

import re
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import os
import csv

from .config import Config, AutofocusConfig
from .astro_logger import Logger
from .utils import (
    find_files_with_prefix,
    parse_datetime,
    ensure_directory
)

@dataclass
class AutofocusEvent:
    """Class representing a single autofocus event."""
    timestamp: datetime
    description: str
    position: Optional[int] = None
    status: str = "Unknown"
    duration_sec: Optional[float] = None
    temperature: Optional[float] = None

class AutofocusAnalysis:
    """Class for analyzing autofocus events in astronomy session logs."""
    
    def __init__(self, config: Config, log_dir: Path):
        self.config = config.autofocus
        self.log_dir = Path(log_dir)
        self.logger = Logger("AutofocusAnalysis")
        
        # Data storage
        self.events: List[AutofocusEvent] = []
        
        # Ensure log directory exists
        if not self.log_dir.exists():
            raise ValueError(f"Log directory does not exist: {self.log_dir}")
    
    def analyze_session(self) -> None:
        """Analyze autofocus events in a session."""
        self.logger.info("Starting autofocus analysis session...")
        
        # Find log files (usually in the Autorun log)
        autorun_log = self._find_autorun_log()
        
        if not autorun_log:
            raise FileNotFoundError("Could not find required Autorun log file")
        
        # Parse logs
        self._parse_autorun_log(autorun_log)
        
        # Compute statistics
        self._compute_statistics()
        
        self.logger.info("Analysis complete!")
    
    def _find_autorun_log(self) -> Optional[Path]:
        """Find the Autorun log file."""
        logs = find_files_with_prefix(
            self.log_dir,
            "Autorun_Log",
            ".txt"
        )
        return logs[0] if logs else None
    
    def _parse_autorun_log(self, log_path: Path) -> None:
        """Parse the Autorun log file."""
        self.logger.info(f"Parsing Autorun log: {log_path.name}")
        
        # Regex patterns for autofocus events
        begin_pattern = re.compile(
            r'(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[AutoFocus\|Begin\] (.+)'
        )
        success_pattern = re.compile(
            r'(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[AutoFocus\|End\] Auto focus succeeded'
        )
        failure_pattern = re.compile(
            r'(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[AutoFocus\|End\] Auto focus failed'
        )
        
        # Extract temperature from begin description
        temp_pattern = re.compile(r'temperature ([+-]?\d+\.\d+)℃')
        
        current_event = None
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                
                # Check for autofocus begin
                m_begin = begin_pattern.search(line)
                if m_begin:
                    timestamp = datetime.strptime(m_begin.group(1), "%Y/%m/%d %H:%M:%S")
                    description = m_begin.group(2)
                    
                    # Extract temperature if available
                    temp_match = temp_pattern.search(description)
                    temperature = float(temp_match.group(1)) if temp_match else None
                    
                    current_event = AutofocusEvent(
                        timestamp=timestamp,
                        description=description,
                        temperature=temperature
                    )
                    continue
                
                # Check for autofocus success
                m_success = success_pattern.search(line)
                if m_success and current_event:
                    end_time = datetime.strptime(m_success.group(1), "%Y/%m/%d %H:%M:%S")
                    current_event.status = "Success"
                    current_event.duration_sec = (end_time - current_event.timestamp).total_seconds()
                    self.events.append(current_event)
                    current_event = None
                    continue
                
                # Check for autofocus failure
                m_failure = failure_pattern.search(line)
                if m_failure and current_event:
                    end_time = datetime.strptime(m_failure.group(1), "%Y/%m/%d %H:%M:%S")
                    current_event.status = "Failed"
                    current_event.duration_sec = (end_time - current_event.timestamp).total_seconds()
                    self.events.append(current_event)
                    current_event = None
                    continue
    
    def _compute_statistics(self) -> None:
        """Compute statistics for autofocus events."""
        self.logger.info("Computing autofocus statistics...")
        
        # Prepare results DataFrame
        results = []
        
        # Use the enhanced status indicator
        with self.logger.status("Processing autofocus events...", spinner="dots"):
            for evt in self.events:
                results.append({
                    'Timestamp': evt.timestamp,
                    'Description': evt.description,
                    'Status': evt.status,
                    'Duration (sec)': evt.duration_sec,
                    'Temperature (°C)': evt.temperature
                })
        
        # Create DataFrame and save to CSV
        if results:
            df = pd.DataFrame(results)
            csv_path = self.log_dir / f"autofocus_analysis_{datetime.now():%Y%m%d-%H%M%S}.csv"
            df.to_csv(csv_path, index=False)
            self.logger.success(f"Results saved to: {csv_path}")
            
            # Display results in a table
            headers = ["Timestamp", "Status", "Duration (sec)", "Temperature (°C)"]
            
            rows = []
            for r in results:
                rows.append([
                    r['Timestamp'].strftime("%Y-%m-%d %H:%M:%S"),
                    r['Status'],
                    f"{r['Duration (sec)']:.1f}" if r['Duration (sec)'] else "N/A",
                    f"{r['Temperature (°C)']:.1f}" if r['Temperature (°C)'] else "N/A"
                ])
            
            self.logger.table(
                title="Autofocus Events",
                columns=headers,
                rows=rows
            )
        else:
            self.logger.warning("No autofocus events found")
        
        # Compute and display overall statistics
        if self.events:
            success_events = [e for e in self.events if e.status == "Success"]
            failed_events = [e for e in self.events if e.status == "Failed"]
            
            success_durations = [e.duration_sec for e in success_events if e.duration_sec is not None]
            
            avg_duration = np.mean(success_durations) if success_durations else 0
            
            # Display overall stats as a dictionary
            self.logger.display_dict(
                {
                    "Total Events": f"{len(self.events)}",
                    "Successful Events": f"{len(success_events)}",
                    "Failed Events": f"{len(failed_events)}",
                    "Success Rate": f"{len(success_events)/len(self.events)*100:.1f}%" if self.events else "N/A",
                    "Average Duration (sec)": f"{avg_duration:.1f}" if success_durations else "N/A"
                },
                title="Autofocus Summary"
            )
    
    def plot_temperature_vs_duration(self, save_dir: Optional[Path] = None) -> None:
        """Generate a plot showing temperature vs. autofocus duration."""
        if not self.events:
            self.logger.warning("No events to plot!")
            return
        
        success_events = [e for e in self.events if e.status == "Success" 
                          and e.temperature is not None and e.duration_sec is not None]
        
        if not success_events:
            self.logger.warning("No successful events with temperature data to plot!")
            return
        
        # Ensure save directory exists
        if save_dir:
            ensure_directory(save_dir)
        
        # Extract data
        temperatures = [e.temperature for e in success_events]
        durations = [e.duration_sec for e in success_events]
        timestamps = [e.timestamp for e in success_events]
        
        with self.logger.status("Creating temperature vs. duration plot...", spinner="dots"):
            fig, ax = plt.subplots(figsize=(10, 6))
            
            # Create a scatter plot with points colored by timestamp
            sc = ax.scatter(temperatures, durations, c=range(len(timestamps)), cmap='viridis', 
                           alpha=0.8, s=80)
            
            # Add labels and title
            ax.set_xlabel('Temperature (°C)')
            ax.set_ylabel('Autofocus Duration (seconds)')
            ax.set_title('Autofocus Duration vs. Temperature')
            ax.grid(True, alpha=0.3)
            
            # Add a colorbar to represent time progression
            cbar = plt.colorbar(sc)
            cbar.set_label('Event Sequence')
            
            # Add a best fit line if there are enough points
            if len(temperatures) > 1:
                z = np.polyfit(temperatures, durations, 1)
                p = np.poly1d(z)
                ax.plot(temperatures, p(temperatures), "r--", alpha=0.8, 
                       label=f"Trend: {z[0]:.2f}x + {z[1]:.2f}")
                ax.legend()
            
            plt.tight_layout()
            
            if save_dir:
                plot_path = save_dir / f"autofocus_temp_duration_{datetime.now():%Y%m%d-%H%M%S}.png"
                plt.savefig(plot_path, dpi=300, bbox_inches='tight')
                self.logger.success(f"Plot saved to: {plot_path}")
            else:
                plt.show()
                
            plt.close()
            
        # Create a timeline plot
        with self.logger.status("Creating autofocus timeline plot...", spinner="dots"):
            fig, ax1 = plt.subplots(figsize=(12, 6))
            
            # Plot durations
            ax1.set_xlabel('Time')
            ax1.set_ylabel('Duration (seconds)', color='tab:blue')
            ax1.plot(timestamps, durations, 'o-', color='tab:blue', alpha=0.7, label='Duration')
            ax1.tick_params(axis='y', labelcolor='tab:blue')
            
            # Create a second y-axis for temperature
            ax2 = ax1.twinx()
            ax2.set_ylabel('Temperature (°C)', color='tab:red')
            ax2.plot(timestamps, temperatures, 's-', color='tab:red', alpha=0.7, label='Temperature')
            ax2.tick_params(axis='y', labelcolor='tab:red')
            
            # Format x-axis to show times nicely
            fig.autofmt_xdate()
            
            # Add a title
            plt.title('Autofocus Duration and Temperature Timeline')
            
            # Add legend
            lines1, labels1 = ax1.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left')
            
            plt.tight_layout()
            
            if save_dir:
                plot_path = save_dir / f"autofocus_timeline_{datetime.now():%Y%m%d-%H%M%S}.png"
                plt.savefig(plot_path, dpi=300, bbox_inches='tight')
                self.logger.success(f"Plot saved to: {plot_path}")
            else:
                plt.show()
                
            plt.close()
        
        # Display plot statistics
        if save_dir:
            self.logger.panel(
                f"Total events plotted: {len(success_events)}\n"
                f"Temperature range: {min(temperatures):.1f}°C to {max(temperatures):.1f}°C\n"
                f"Duration range: {min(durations):.1f}s to {max(durations):.1f}s\n"
                f"Plot files saved to: {save_dir}",
                title="Plot Summary",
                style="success"
            ) 