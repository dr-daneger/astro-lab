import logging
import os
from datetime import datetime
from pathlib import Path
import pypandoc # Import pypandoc

log = logging.getLogger(__name__)

# --- Configuration ---
REPORT_DIR = Path("reports") # Directory to save reports

def ensure_report_dir():
    """Creates the report directory if it doesn't exist."""
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        log.info(f"Report directory checked/created: {REPORT_DIR.resolve()}")
    except Exception as e:
        log.error(f"Could not create report directory {REPORT_DIR}: {e}")
        raise # Re-raise the exception to signal failure

def generate_report_filename(base_name="astronomy_report") -> str:
    """Generates a filename with a timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{base_name}_{timestamp}"

def save_markdown_report(markdown_content: str, filename_base: str) -> Path | None:
    """Saves the Markdown content to a file in the report directory."""
    ensure_report_dir() # Make sure directory exists
    filepath = REPORT_DIR / f"{filename_base}.md"
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        log.info(f"Markdown report saved successfully to: {filepath}")
        return filepath
    except Exception as e:
        log.error(f"Failed to save Markdown report to {filepath}: {e}")
        return None

def convert_md_to_pdf(md_filepath: Path, pdf_filename_base: str) -> Path | None:
    """Converts a Markdown file to PDF using pandoc."""
    ensure_report_dir() # Make sure directory exists
    pdf_filepath = REPORT_DIR / f"{pdf_filename_base}.pdf"
    try:
        # Check if pandoc is available
        try:
            pypandoc.get_pandoc_path()
            log.info("Pandoc installation found.")
        except OSError:
            log.error("Pandoc is not installed or not found in PATH. Cannot convert to PDF.")
            log.error("Please install pandoc from https://pandoc.org/installing.html")
            return None
            
        log.info(f"Attempting to convert {md_filepath} to {pdf_filepath} using pandoc...")
        output = pypandoc.convert_file(
            str(md_filepath),
            'pdf', 
            outputfile=str(pdf_filepath),
            extra_args=['--pdf-engine=pdflatex'] # Specify engine, adjust if needed (e.g., weasyprint, wkhtmltopdf)
        )
        if output == "": # pypandoc returns empty string on success
             log.info(f"PDF report generated successfully: {pdf_filepath}")
             return pdf_filepath
        else:
            # Output might contain errors if conversion failed
            log.error(f"Pandoc conversion failed. Output/Error: {output}")
            return None
            
    except FileNotFoundError:
        log.error(f"Markdown source file not found for PDF conversion: {md_filepath}")
        return None
    except Exception as e:
        log.error(f"Error during Markdown to PDF conversion: {e}", exc_info=True)
        return None

def generate_and_save_reports(llm_markdown_output: str):
    """Main function to save MD and generate PDF reports."""
    if not llm_markdown_output:
        log.error("Cannot generate report: LLM output is empty.")
        return None, None

    try:
        filename_base = generate_report_filename()
        
        # Save Markdown
        md_path = save_markdown_report(llm_markdown_output, filename_base)
        if not md_path:
            # Error already logged in save_markdown_report
            return None, None # Indicate MD saving failed
            
        # Convert to PDF
        pdf_path = convert_md_to_pdf(md_path, filename_base)
        if not pdf_path:
            # Error already logged in convert_md_to_pdf
            log.warning(f"PDF conversion failed, but Markdown report is available at {md_path}")
            # Return MD path even if PDF fails
            return md_path, None 
            
        return md_path, pdf_path
        
    except Exception as e:
        # Catch errors from ensure_report_dir or filename generation
        log.error(f"Critical error during report generation setup: {e}")
        return None, None 