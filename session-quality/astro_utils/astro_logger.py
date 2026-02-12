"""
Astro logger module for astro_utils package.

This module provides enhanced logging capabilities, avoiding conflict
with Python's built-in logging module.
"""

import logging as python_logging
import sys
from pathlib import Path
from typing import Optional, Any, Dict, Tuple, List, Union

# Try to import Rich components, fall back to standard logging if not available
try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.progress import Progress, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
    from rich.table import Table
    from rich.panel import Panel
    from rich.theme import Theme
    from rich import print as rich_print
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Warning: Rich library not found. Using standard logging instead.")

# Custom theme (only used if Rich is available)
CUSTOM_THEME = None
if RICH_AVAILABLE:
    CUSTOM_THEME = Theme({
        "info": "dim cyan",
        "warning": "yellow",
        "error": "bold red",
        "critical": "bold white on red",
        "success": "bold green",
        "progress": "blue",
        "highlight": "bold magenta"
    })

class Logger:
    """
    Enhanced logger with rich console output and visualization features.
    
    Features:
    - Rich formatted console output with colors
    - File logging with standard format
    - Progress bar support
    - Status spinner support
    - Table output support
    - Panel output support
    """
    
    def __init__(
        self, 
        name: str, 
        log_file: Optional[Path] = None,
        log_level: int = python_logging.INFO,
        theme: Optional[Theme] = None
    ):
        """
        Initialize the logger.
        
        Args:
            name: Logger name
            log_file: Optional file to log to
            log_level: Initial logging level
            theme: Optional custom theme for console output
        """
        self.name = name
        self.log_level = log_level
        
        # Set up console if Rich is available
        if RICH_AVAILABLE:
            self.console = Console(theme=theme or CUSTOM_THEME)
            
            # Set up rich logging
            python_logging.basicConfig(
                level=log_level,
                format="%(message)s",
                datefmt="[%X]",
                handlers=[RichHandler(console=self.console, rich_tracebacks=True)]
            )
        else:
            # Fall back to standard logging
            self.console = None
            python_logging.basicConfig(
                level=log_level,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        
        self.logger = python_logging.getLogger(name)
        
        # Add file handler if log_file is provided
        if log_file:
            file_handler = python_logging.FileHandler(log_file)
            file_handler.setFormatter(
                python_logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            )
            self.logger.addHandler(file_handler)
    
    def set_level(self, level: int) -> None:
        """
        Set logging level.
        
        Args:
            level: Logging level (e.g., logging.DEBUG, logging.INFO)
        """
        self.log_level = level
        self.logger.setLevel(level)
    
    def info(self, msg: str) -> None:
        """Log info message."""
        self.logger.info(msg)
    
    def warning(self, msg: str) -> None:
        """Log warning message."""
        self.logger.warning(msg)
    
    def error(self, msg: str) -> None:
        """Log error message."""
        self.logger.error(msg)
    
    def debug(self, msg: str) -> None:
        """Log debug message."""
        self.logger.debug(msg)
    
    def critical(self, msg: str) -> None:
        """Log critical message."""
        self.logger.critical(msg)
    
    def success(self, msg: str) -> None:
        """Log success message with green color."""
        if RICH_AVAILABLE:
            self.console.print(f"[success]{msg}[/success]")
        else:
            self.logger.info(f"SUCCESS: {msg}")
    
    def status(self, description: str = "Processing", spinner: str = "dots") -> Any:
        """
        Create a status indicator with spinner.
        
        Args:
            description: Status description
            spinner: Spinner style ("dots", "line", "clock", etc.)
            
        Returns:
            Status context manager
        """
        if RICH_AVAILABLE:
            return self.console.status(description, spinner=spinner)
        else:
            # If rich is not available, return a dummy context manager
            from contextlib import contextmanager
            
            @contextmanager
            def dummy_status():
                print(f"{description}...")
                yield
                print("Done!")
            
            return dummy_status()
        
    def progress_bar(
        self, 
        total: int, 
        description: str = "Processing",
        transient: bool = False
    ) -> Tuple[Any, int]:
        """
        Create a proper progress bar.
        
        Args:
            total: Total steps for the progress bar
            description: Progress bar description
            transient: Whether to remove the progress bar after completion
            
        Returns:
            Tuple of (Progress object, task_id)
        """
        if RICH_AVAILABLE:
            progress = Progress(
                TextColumn("[progress]{task.description}[/progress]"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=self.console,
                transient=transient
            )
            task_id = progress.add_task(description, total=total)
            return progress, task_id
        else:
            # If rich is not available, return a simple progress tracker
            from contextlib import contextmanager
            
            class SimpleProgress:
                def __init__(self, total, description):
                    self.total = total
                    self.description = description
                    self.current = 0
                
                def update(self, task_id=None, advance=1):
                    self.current += advance
                    percent = (self.current / self.total) * 100
                    print(f"\r{self.description}: {percent:.1f}% ({self.current}/{self.total})", end="")
                    if self.current >= self.total:
                        print()  # newline at the end
                
                @contextmanager
                def __enter__(self):
                    print(f"{self.description} (0/{self.total})")
                    yield self
                
                def __exit__(self, exc_type, exc_val, exc_tb):
                    pass
                
                def add_task(self, description, total):
                    # For compatibility, but not really supported
                    print(f"  Subtask: {description}")
                    return 0
            
            progress = SimpleProgress(total, description)
            return progress, 0
    
    def table(
        self, 
        title: str = None, 
        columns: List[str] = None,
        rows: List[List[Any]] = None,
        show_header: bool = True
    ) -> None:
        """
        Display data as a table.
        
        Args:
            title: Optional table title
            columns: List of column headers
            rows: List of rows (each row is a list of values)
            show_header: Whether to show column headers
        """
        if RICH_AVAILABLE:
            table = Table(title=title, show_header=show_header)
            
            if columns:
                for column in columns:
                    table.add_column(column)
            
            if rows:
                for row in rows:
                    table.add_row(*[str(cell) for cell in row])
            
            self.console.print(table)
        else:
            # Simple table display if rich is not available
            if title:
                print(f"\n=== {title} ===")
            
            if columns and show_header:
                print(" | ".join(str(col) for col in columns))
                print("-" * (sum(len(str(col)) for col in columns) + 3 * (len(columns) - 1)))
            
            if rows:
                for row in rows:
                    print(" | ".join(str(cell) for cell in row))
        
    def panel(self, content: str, title: str = None, style: str = "info") -> None:
        """
        Display content in a panel.
        
        Args:
            content: Panel content
            title: Optional panel title
            style: Panel style
        """
        if RICH_AVAILABLE:
            self.console.print(Panel(content, title=title, style=style))
        else:
            # Simple panel if rich is not available
            if title:
                print(f"\n=== {title} ===")
            
            print(content)
            print("="*40)
    
    def highlight(self, msg: str) -> None:
        """
        Print highlighted message.
        
        Args:
            msg: Message to highlight
        """
        if RICH_AVAILABLE:
            self.console.print(f"[highlight]{msg}[/highlight]")
        else:
            print(f"** {msg} **")
    
    def display_dict(self, data: Dict[str, Any], title: str = None) -> None:
        """
        Display a dictionary as a formatted table.
        
        Args:
            data: Dictionary to display
            title: Optional title
        """
        if RICH_AVAILABLE:
            table = Table(title=title, show_header=True)
            table.add_column("Key")
            table.add_column("Value")
            
            for key, value in data.items():
                table.add_row(str(key), str(value))
            
            self.console.print(table)
        else:
            # Simple dictionary display if rich is not available
            if title:
                print(f"\n=== {title} ===")
            
            max_key_length = max(len(str(key)) for key in data.keys())
            for key, value in data.items():
                print(f"{str(key):{max_key_length}} : {value}") 