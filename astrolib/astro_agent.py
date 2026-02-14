import google.generativeai as genai
import os
import logging
# import argparse # Remove argparse, no longer needed for generic prompts
import datetime
import time
from abc import ABC, abstractmethod
import requests # Add requests for weather API
import json # Add json for loading equipment specs
from dotenv import load_dotenv
from pathlib import Path

# Add rich library for beautiful terminal output
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn

# Import our new modules
from equipment import load_equipment_specs
from ephemeris import get_targets, calculate_ephemeris
from astropy.coordinates import EarthLocation
from astropy.time import Time
import astropy.units as u
from report import generate_and_save_reports, generate_report_filename, ensure_report_dir, REPORT_DIR # <-- Import more from report
# from report import ... (will add later)

# Initialize rich console
console = Console()

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - [%(name)s] - %(message)s')

# --- Configuration ---
# Load environment variables from .env file
# Try to find .env in the current directory or in the parent directory
env_path = Path('.env')
if not env_path.exists():
    # Use the new naming convention for the parent directory check
    env_path = Path('astro-agent/.env') 
    
load_dotenv(dotenv_path=env_path)

# Get API keys from environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

# Location Configuration (Read from environment or use defaults/raise error)
LOCATION_NAME = os.getenv("LOCATION_NAME", "Default Location (Not Set)") # Provide a default name
LATITUDE_STR = os.getenv("LATITUDE")
LONGITUDE_STR = os.getenv("LONGITUDE")

# Validate and convert Latitude/Longitude
try:
    if LATITUDE_STR is None or LONGITUDE_STR is None:
         raise ValueError("LATITUDE or LONGITUDE environment variables not set in .env file.")
    LATITUDE = float(LATITUDE_STR)
    LONGITUDE = float(LONGITUDE_STR)
    if not (-90 <= LATITUDE <= 90 and -180 <= LONGITUDE <= 180):
        raise ValueError(f"Invalid LATITUDE ({LATITUDE}) or LONGITUDE ({LONGITUDE}) values in .env file.")
except ValueError as e:
    logging.error(f"Error processing location from .env file: {e}")
    # Decide how to handle: Exit? Use a default? For now, let's exit.
    raise SystemExit(f"Exiting due to location configuration error: {e}")

# --- LLM Provider Interface ---
class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    def __init__(self, api_key: str):
        self.api_key = api_key

    @abstractmethod
    def generate_response(self, prompt: str) -> str:
        """Generates a response from the LLM."""
        pass

    @abstractmethod
    def get_usage_info(self) -> dict:
        """Returns usage information for the last call."""
        pass

# --- Gemini Implementation ---
class GeminiProvider(LLMProvider):
    """Implementation for the Google Gemini provider."""
    def __init__(self, api_key: str):
        super().__init__(api_key)
        if not api_key:
             logging.error("GeminiProvider requires a valid API key (received empty or None).")
             raise ValueError("Invalid or missing Gemini API key provided to GeminiProvider.")
        try:
            genai.configure(api_key=self.api_key)
            # Use a specific, reliable model version
            self.model = genai.GenerativeModel('gemini-1.5-flash') # Or gemini-1.5-pro if preferred
            self._last_usage = {}
            logging.info("Gemini API configured successfully with model: gemini-1.5-flash")
        except Exception as e:
            logging.error(f"Failed to configure Gemini API: {e}")
            raise ConnectionError(f"Failed to configure Gemini API: {e}")

    def generate_response(self, prompt: str) -> str:
        """Sends the prompt to Gemini and returns the response."""
        self._last_usage = {} # Reset usage info
        try:
            start_time = time.time()
            response = self.model.generate_content(prompt)
            end_time = time.time()

            # Basic usage info
            response_text = ""
            # Handle cases where response might be structured differently
            if response.parts:
                response_text = response.text # Preferred method
            elif response.candidates and response.candidates[0].content.parts:
                 response_text = " ".join(part.text for part in response.candidates[0].content.parts)

            self._last_usage = {
                "timestamp": datetime.datetime.now().isoformat(),
                "provider": "gemini",
                "model": "gemini-1.5-flash", # Match model used
                "prompt_length": len(prompt),
                "response_length": len(response_text),
                "latency_seconds": round(end_time - start_time, 2),
                # Attempt to get token counts if available in usage_metadata
                "prompt_tokens": getattr(response.usage_metadata, 'prompt_token_count', None),
                "candidates_tokens": getattr(response.usage_metadata, 'candidates_token_count', None),
                "total_tokens": getattr(response.usage_metadata, 'total_token_count', None),
            }
            logging.info(f"Gemini response received. Latency: {self._last_usage['latency_seconds']}s")

            # Check for blocked content
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
                block_reason = response.prompt_feedback.block_reason
                logging.error(f"Prompt blocked due to: {block_reason}")
                self._last_usage["error"] = f"Blocked: {block_reason}"
                # Raise a specific error for blocked prompts
                raise ValueError(f"Prompt blocked by API: {block_reason}")
            
            # Check for potentially empty but not blocked responses
            if not response_text and not (hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason):
                logging.warning("Gemini response appears empty.")
                finish_reason = "Unknown"
                if response.candidates:
                    finish_reason = str(response.candidates[0].finish_reason)
                warning_msg = f"Warning: Received empty response from Gemini. Finish Reason: {finish_reason}."
                self._last_usage["warning"] = warning_msg
                # Return warning but don't raise error unless blocked
                return warning_msg

            return response_text
        except (genai.types.generation_types.StopCandidateException,
                genai.types.generation_types.BlockedPromptException,
                genai.types.generation_types.InvalidArgumentException) as gen_ex:
             # Catch specific Gemini SDK errors
             logging.error(f"Gemini generation error: {gen_ex}")
             self._last_usage["error"] = str(gen_ex)
             # Re-raise the specific Gemini exception for upstream handling
             raise ValueError(f"Gemini API Error: {gen_ex}") from gen_ex
        except Exception as e:
            # Catch unexpected errors during API call
            logging.error(f"Unexpected error calling Gemini API: {e}", exc_info=True)
            if "rate limit" in str(e).lower():
                logging.warning("Potential rate limit hit. Consider adding delays or backoff.")
            self._last_usage["error"] = str(e)
            # Raise a ConnectionError for consistency on call failures
            raise ConnectionError(f"Failed to get response from Gemini: {e}") from e

    def get_usage_info(self) -> dict:
        """Returns usage information for the last Gemini call."""
        return self._last_usage

# --- Weather Fetching ---
def get_weather_data(api_key: str, lat: float, lon: float) -> dict:
    """
    Fetches weather data for the specified location using OpenWeatherMap API.
    Requires 'requests' library: pip install requests
    Uses the provided API key.
    """
    logging.info(f"Attempting to fetch weather for Lat: {lat}, Lon: {lon}")
    if not api_key:
        logging.warning("Weather API key not provided to get_weather_data function.")
        # Return error data if API key is missing
        return {
            "description": "Weather data unavailable (API key missing in function call)",
            "cloud_cover_percent": -1,
            "seeing_conditions": "Unknown",
            "temperature_c": -999,
            "humidity_percent": -1,
            "error": "API key missing"
        }

    # Use the OpenWeatherMap Current Weather endpoint
    base_url = "http://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": api_key,
        "units": "metric" # Get temperature in Celsius
    }

    try:
        response = requests.get(base_url, params=params, timeout=10) # 10 second timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()

        # Extract relevant information
        description = data.get('weather', [{}])[0].get('description', 'N/A')
        cloud_cover = data.get('clouds', {}).get('all', -1) # Cloudiness percentage
        temperature = data.get('main', {}).get('temp', -999)
        humidity = data.get('main', {}).get('humidity', -1)

        # Basic seeing condition inference (very simplified)
        seeing = "Unknown"
        if cloud_cover != -1:
            if cloud_cover <= 20:
                seeing = "Good"
            elif cloud_cover <= 60:
                seeing = "Average"
            else:
                seeing = "Poor"

        weather_info = {
            "description": description,
            "cloud_cover_percent": cloud_cover,
            "seeing_conditions": seeing,
            "temperature_c": temperature,
            "humidity_percent": humidity
        }
        logging.info(f"Weather data fetched successfully: {description}, Clouds: {cloud_cover}%" )
        return weather_info

    except requests.exceptions.Timeout:
        logging.error("Weather API request timed out.")
        return {"description": "Error: Weather API timeout", "error": "Timeout", "cloud_cover_percent": -1, "seeing_conditions": "Unknown"}
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error occurred: {http_err} - Status Code: {response.status_code}")
        # Handle specific errors like 401 Unauthorized (invalid key) or 429 Too Many Requests
        error_detail = f"HTTP Error: {response.status_code}"
        if response.status_code == 401:
            error_detail = "HTTP Error: 401 Unauthorized (Invalid API Key?)"
        elif response.status_code == 429:
            error_detail = "HTTP Error: 429 Too Many Requests (Rate Limit?)"
        return {"description": error_detail, "error": str(http_err), "cloud_cover_percent": -1, "seeing_conditions": "Unknown"}
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Error fetching weather data: {req_err}")
        return {"description": f"Error fetching weather: {req_err}", "error": str(req_err), "cloud_cover_percent": -1, "seeing_conditions": "Unknown"}
    except Exception as e:
        # Catch other potential errors (e.g., JSON parsing)
        logging.error(f"Error processing weather data: {e}", exc_info=True)
        return {"description": f"Error processing weather data: {e}", "error": str(e), "cloud_cover_percent": -1, "seeing_conditions": "Unknown"}

# --- Prompt Engineering (New Function) ---
def create_prompt_with_data(location_name: str, base_astro_info: dict, weather: dict, equipment_specs: dict, observable_targets: list) -> str:
    """Creates the LLM prompt using pre-calculated astronomical data."""
    
    # --- Weather Summary ---
    weather_str = f"Cloud Cover: {weather.get('cloud_cover_percent', 'N/A')}%, Seeing: {weather.get('seeing_conditions', 'N/A')}, Temp: {weather.get('temperature_c', 'N/A')}°C, Humidity: {weather.get('humidity_percent', 'N/A')}%, Description: {weather.get('description', 'N/A')}"
    if weather.get("error"):
        weather_str = f"Weather data retrieval failed: {weather.get('description', 'Unknown error')}"
    
    # --- Equipment Summary ---
    equip_summary = "No equipment specified."
    calculated_params = equipment_specs.get('calculated', {})
    fov_width = calculated_params.get('fov_width', 'N/A')
    fov_height = calculated_params.get('fov_height', 'N/A')
    pixel_scale = calculated_params.get('pixel_scale', 'N/A')
    
    if equipment_specs and 'calculated' in equipment_specs:
        telescope_model = equipment_specs.get('imaging_telescope', {}).get('model', 'N/A')
        camera_model = equipment_specs.get('imaging_camera', {}).get('model', 'N/A')
        equip_summary = (
            f"Telescope: {telescope_model}\n"
            f"Camera: {camera_model}\n"
            f"Calculated Pixel Scale: {pixel_scale} arcsec/pixel\n"
            f"Calculated FOV: {fov_width} x {fov_height} arcminutes"
        )
        if 'filter' in equipment_specs:
            equip_summary += f"\nFilter Available: {equipment_specs['filter'].get('model', 'N/A')} (Dual Narrowband - Ha/OIII)"
        else:
            equip_summary += "\nFilter Available: None (Can shoot RGB/Luminance)"
    
    # --- Observable Targets Summary ---
    targets_summary = "No targets found to be observable meeting criteria.\n"
    if observable_targets:
        targets_summary = "Based on calculations, the following targets are observable tonight (above 30 degrees altitude during astronomical night), sorted by maximum altitude:\n\n"
        targets_summary += "| Target Name | Max Alt (deg) | Duration (hr) | Size (arcmin)   | Transit (Local) | Transit Alt (deg) |\n"
        targets_summary += "|-------------|---------------|---------------|-----------------|-----------------|-------------------|\n"
        for t in observable_targets:
            # Format Transit Time to Local (Approximate based on longitude)
            transit_time_local_str = "N/A"
            transit_time_iso = t.get('transit_time_iso')
            if transit_time_iso and isinstance(transit_time_iso, str) and transit_time_iso not in ["N/A", "Circumpolar/Never Rises", "Circumpolar/Never Sets"]:
                 try:
                     transit_time_utc = Time(transit_time_iso, format='iso')
                     # Approximate local offset (longitude / 15 deg/hr) - rough!
                     # Beaverton LONGITUDE = -122.8
                     # Offset = -122.8 / 15 = -8.2 hrs (PST)
                     # Need to handle DST if applicable - let's assume PDT (UTC-7) for now for simplicity
                     # TODO: Implement proper timezone handling using pytz or zoneinfo later
                     local_offset = -7 * u.hour 
                     transit_time_local = transit_time_utc + local_offset
                     transit_time_local_str = transit_time_local.to_datetime().strftime("%H:%M %Z(approx)")
                 except Exception as time_e:
                     logging.log.warning(f"Could not convert transit time {transit_time_iso} to local: {time_e}")
                     transit_time_local_str = transit_time_iso + " (UTC)" # Fallback to UTC display
            
            # Format Size
            size_str = "N/A"
            if t.get('angular_size_maj') and t.get('angular_size_unit'):
                # Explicitly format to 2 decimal places
                size_str = f"{t['angular_size_maj']:.2f}'x{t['angular_size_min']:.2f}'"
            
            targets_summary += (
                f"| {t.get('name', 'N/A'):<11} | "
                f"{t.get('max_observable_altitude', 'N/A'):<13} | "
                f"{t.get('observable_duration_hours', 'N/A'):<13} | "
                f"{size_str:<15} | "
                f"{transit_time_local_str:<15} | "
                f"{t.get('transit_altitude', 'N/A'):<17} |\n"
            )
        targets_summary += "\n"

    # --- Prompt Construction ---
    prompt = f"""
You are an expert astronomy assistant generating an observation plan in Markdown format.

**Context:**
*   Location: {location_name} (Lat: {LATITUDE:.4f}, Lon: {LONGITUDE:.4f})
*   Sky Conditions: Bortle 8, significant light dome strongest in the Southern half of the sky.
*   Calculation Time: {base_astro_info.get('calculation_time_iso', 'N/A')} (UTC)
*   Astronomical Night Window: {base_astro_info.get('observability_window_start_iso', 'N/A')} to {base_astro_info.get('observability_window_end_iso', 'N/A')} (UTC)
*   Current Conditions: Sun Alt={base_astro_info.get('sun_altitude_now', 'N/A'):.1f}°, Moon Alt={base_astro_info.get('moon_altitude_now', 'N/A'):.1f}°
*   Weather Forecast: {weather_str}

**User's Equipment Summary:**
{equip_summary}
*   Filter Options: ONLY the listed Dual Narrowband filter (Ha+OIII) OR No Filter (RGB/Luminance).

**Pre-Calculated Observable Targets (>30° Alt during Astro Night):**
{targets_summary}
**Instructions:**

Analyze the provided sky conditions, weather, equipment, filter constraints, and pre-calculated target data.
Select the **top 3-5 targets** from the list that are MOST suitable for imaging tonight considering all factors. Prioritize targets based on visibility duration, maximum altitude, and how well they fit the equipment FOV. Also consider the Bortle 8 conditions and southern light dome.

Generate a report in **Markdown format** with the following sections:

1.  **Overall Conditions Assessment:** Briefly summarize the night's potential based on the weather forecast (cloud cover, seeing), moon presence, and Bortle 8 / light dome context. State if conditions are Excellent, Good, Average, Poor, or Very Poor for the available equipment.

2.  **Top Recommended Targets:** List the 3-5 selected targets. For EACH target, provide:
    *   **Target:** Common Name (e.g., M31 - Andromeda Galaxy)
    *   **Observability:** Mention its peak altitude ({t.get('max_observable_altitude')} deg) and duration ({t.get('observable_duration_hours')} hr).
    *   **Framing Analysis:** Compare the 'Object Size' ({t.get('angular_size_maj')} x {t.get('angular_size_min')} arcmin) with the 'Equipment FOV' ({fov_width}' x {fov_height}'). State if the framing is 'Good Fit', 'Tight Fit', 'Widefield', or 'Requires Mosaic'. If Mosaic is needed, estimate a grid size (e.g., 2x1, 2x2). 
    *   **Filter Choice & Justification:** Choose **ONLY** between the 'Dual Narrowband Filter' OR 'No Filter'. Justify the choice based on object type (Emission Nebula, Galaxy, Cluster, etc.), Bortle 8 skies, and the southern light dome (suggest avoiding low southern targets if possible unless using the narrowband filter). **Guidance:** Dual Narrowband is best for emission nebulae. 'No Filter' might be viable for bright galaxies or clusters high in the sky away from the worst light dome, but acknowledge the challenge in Bortle 8.
    *   **Imaging Tips:** Provide a concise beginner tip AND an advanced insight relevant to the target, chosen filter, equipment ({pixel_scale} arcsec/px), and sky conditions.

**Output Format:** Strictly Markdown.
"""
    return prompt.strip()

# --- Main Execution ---
def run_astro_assistant():
    """Main execution function for the astro assistant."""
    
    console.print(Panel.fit("[bold blue]🔭 Astro Agent[/bold blue]", title="Welcome", subtitle="Powered by Gemini AI"))
    
    # Check for API keys (critical)
    if not GEMINI_API_KEY:
        console.print("[bold red]❌ Error: Gemini API key not found![/bold red]")
        console.print("[yellow]Please set the GEMINI_API_KEY environment variable or configure it in the script.[/yellow]")
        return
    
    if not WEATHER_API_KEY:
        console.print("[bold yellow]⚠️ Warning: Weather API key not found![/bold yellow]")
        console.print("[yellow]Weather data will not be available. Please set the WEATHER_API_KEY environment variable.[/yellow]")
    
    # Display configuration
    console.print("\n[bold cyan]📍 Location:[/bold cyan] " + LOCATION_NAME)
    console.print(f"[cyan]   Coordinates: {LATITUDE}, {LONGITUDE}[/cyan]")
    
    # Initialize LLM provider
    try:
        with console.status("[bold green]🧠 Initializing Gemini AI...[/bold green]"):
            llm = GeminiProvider(GEMINI_API_KEY)
        console.print("[bold green]✅ Gemini AI initialized successfully[/bold green]")
    except Exception as e:
        console.print(f"[bold red]❌ Error initializing Gemini AI: {e}[/bold red]")
        return
    
    # Get current date/time using astropy Time.now() for consistency
    calculation_time = Time.now() # Gets current time as timezone-aware UTC Time object
    # Convert to datetime object if needed for display or other non-astro parts
    current_time_dt = calculation_time.to_datetime() 
    current_date_str = current_time_dt.strftime("%Y-%m-%d %H:%M:%S Local") # Indicate it's derived from UTC
    console.print(f"\n[bold cyan]📅 Current Time (UTC):[/bold cyan] {calculation_time.iso}")
    # console.print(f"\n[bold cyan]📅 Current Date/Time:[/bold cyan] {current_date_str}") # Optional local display
    
    # Load equipment specifications using the new function
    with console.status("[bold green]📷 Loading equipment specifications...[/bold green]"):
        # Ensure the path is correct relative to the main script
        equipment_specs = load_equipment_specs(filepath="equipment_specs.json") 
    
    if equipment_specs and 'imaging_telescope' in equipment_specs: # Check if specs loaded
        console.print("[bold green]✅ Equipment specifications loaded successfully[/bold green]")
        # Display summary of equipment
        console.print(f"[cyan]   🔭 Telescope: {equipment_specs['imaging_telescope']['model']}[/cyan]")
        console.print(f"[cyan]   📷 Camera: {equipment_specs['imaging_camera']['model']}[/cyan]")
        if 'mount' in equipment_specs:
            console.print(f"[cyan]   🛠️ Mount: {equipment_specs['mount']['model']}[/cyan]")
        # Display calculated params
        if 'calculated' in equipment_specs:
            calc = equipment_specs['calculated']
            console.print(f"[cyan]   📏 Pixel Scale: {calc['pixel_scale']} arcsec/pixel[/cyan]")
            console.print(f"[cyan]   🖼️ FOV: {calc['fov_width']}' x {calc['fov_height']}'[/cyan]")
    else:
        console.print("[bold yellow]⚠️ Warning: Could not load equipment specifications or main components missing![/bold yellow]")
        console.print("[yellow]   Proceeding with default calculation values.[/yellow]")
        # Ensure 'calculated' exists even if loading failed
        if 'calculated' not in equipment_specs:
            equipment_specs['calculated'] = { 
                'pixel_scale': 1.48, # Using defaults from equipment.py
                'fov_width': 94.5,
                'fov_height': 53.1
            }
    
    # Get weather data
    if WEATHER_API_KEY:
        with console.status("[bold green]☁️ Fetching weather data...[/bold green]"):
            weather = get_weather_data(WEATHER_API_KEY, LATITUDE, LONGITUDE)
        
        if "error" not in weather:
            console.print("[bold green]✅ Weather data retrieved successfully[/bold green]")
            console.print(f"[cyan]   ☁️ Cloud Cover: {weather['cloud_cover_percent']}%[/cyan]")
            console.print(f"[cyan]   🌡️ Temperature: {weather['temperature_c']}°C[/cyan]")
            console.print(f"[cyan]   💧 Humidity: {weather['humidity_percent']}%[/cyan]")
            console.print(f"[cyan]   👁️ Seeing Conditions: {weather['seeing_conditions']}[/cyan]")
            console.print(f"[cyan]   📝 Description: {weather['description']}[/cyan]")
        else:
            console.print(f"[bold yellow]⚠️ Warning: {weather['description']}[/bold yellow]")
            weather = {
                "description": "Weather data unavailable",
                "cloud_cover_percent": -1,
                "seeing_conditions": "Unknown",
                "temperature_c": -999,
                "humidity_percent": -1
            }
    else:
        console.print("[bold yellow]⚠️ Weather data not available (missing API key)[/bold yellow]")
        weather = {
            "description": "Weather data unavailable (no API key)",
            "cloud_cover_percent": -1,
            "seeing_conditions": "Unknown",
            "temperature_c": -999,
            "humidity_percent": -1
        }
    
    # --- Phase 1: Ephemeris Calculation ---
    console.print("\n[bold magenta]🌌 Calculating Ephemeris Data...[/bold magenta]")
    ephemeris_results = None # Initialize
    try:
        observer_location = EarthLocation(lat=LATITUDE*u.deg, lon=LONGITUDE*u.deg)
        targets = get_targets()
        if not targets:
            console.print("[bold red]❌ Error: Could not resolve any targets.[/bold red]")
            return
        console.print(f"[magenta]   Identified {len(targets)} potential targets.[/magenta]")
        # calculation_time is already defined correctly using Time.now()
        
        with console.status("[bold green]🛰️ Calculating positions and times...[/bold green]"):
            # Pass the correct calculation_time object
            ephemeris_results = calculate_ephemeris(observer_location, targets, calculation_time)
        
        # --- DEBUG PRINT --- 
        console.print("[bold yellow]--- DEBUG: Raw Ephemeris Results ---[/bold yellow]")
        console.print(ephemeris_results)
        console.print("[bold yellow]-------------------------------------[/bold yellow]")
        # --- END DEBUG PRINT ---
        
        if ephemeris_results and ephemeris_results["targets"]:
            console.print("[bold green]✅ Ephemeris calculation complete.[/bold green]")
        else:
             console.print("[bold red]❌ Error during ephemeris calculation or empty results.[/bold red]") # Modified error message
             return # Stop if calculations failed or returned no targets

    except ImportError as imp_err:
         console.print(f"[bold red]❌ Missing Library Error: {imp_err}. Please install required astronomy libraries (astropy, astroplan).[/bold red]")
         return
    except Exception as e:
        console.print(f"[bold red]❌ Critical Error during ephemeris setup or calculation: {e}[/bold red]")
        logging.error("Ephemeris calculation phase failed:", exc_info=True)
        return
    # ---------------------------------------

    # --- Phase 2: Data Aggregation & Filtering ---
    console.print("\n[bold blue]📊 Aggregating and Filtering Data...[/bold blue]")
    observable_targets = []
    if ephemeris_results and "targets" in ephemeris_results and ephemeris_results["targets"]:
        # Filter based on observability flag from ephemeris.py
        observable_targets = [t for t in ephemeris_results["targets"] if t.get('is_observable') and not t.get("error")] # Also check for errors
        
        # Sort observable targets (e.g., by max observable altitude descending)
        if observable_targets:
             observable_targets.sort(key=lambda x: x.get('max_observable_altitude', 0), reverse=True)
             console.print(f"[blue]   Found {len(observable_targets)} observable targets meeting criteria.[/blue]")
             # Display top few sorted targets (using CORRECTED keys)
             for t in observable_targets[:5]:
                  console.print(f"[dim]     - {t['name']} (AltNow: {t.get('altitude_now','N/A')}°, Max Alt: {t.get('max_observable_altitude', 'N/A')}°, Duration: {t.get('observable_duration_hours', 'N/A')} hr)[/dim]")
        else:
             console.print("[yellow]   No targets meet the observability criteria (e.g., >30° altitude during astronomical night).[/yellow]")
    else:
        console.print("[red]   Error: No ephemeris target results available for filtering.[/red]")

    # --- Phase 2: Prompt Generation ---
    console.print("\n[bold green]📝 Generating Prompt for LLM...[/bold green]")
    prompt = ""
    if ephemeris_results and ephemeris_results.get("base_info"):
         prompt = create_prompt_with_data(
             location_name=LOCATION_NAME,
             base_astro_info=ephemeris_results["base_info"],
             weather=weather,
             equipment_specs=equipment_specs,
             observable_targets=observable_targets # Pass the filtered & sorted list
         )
         console.print("[green]   Prompt created successfully.[/green]")
         
         # --- Save the generated prompt to a file ---
         try:
             ensure_report_dir() # Ensure reports directory exists
             # Use the new base name for the prompt file
             prompt_filename_base = generate_report_filename(base_name="astro_agent_prompt") 
             prompt_filepath = REPORT_DIR / f"{prompt_filename_base}.md"
             with open(prompt_filepath, 'w', encoding='utf-8') as f_prompt:
                 f_prompt.write(prompt)
             console.print(f"[blue]   💾 Intermediate prompt saved to: {prompt_filepath}[/blue]")
         except Exception as save_e:
             console.print(f"[yellow]   ⚠️ Could not save intermediate prompt file: {save_e}[/yellow]")
         # -------------------------------------------
             
    else:
         console.print("[red]   Error: Cannot create prompt without base ephemeris info.[/red]")
         return

    # --- Phase 3: LLM Call & Report Generation --- 
    raw_recommendations = None
    if prompt and llm:
        console.print("\n[bold purple]🔮 Calling Gemini with generated prompt...[/bold purple]")
        with console.status("[bold green]Generating analysis...[/bold green]"):
            try:
                # ---- LLM Call is now ACTIVE ----
                raw_recommendations = llm.generate_response(prompt)
                console.print("[bold green]✅ LLM analysis received.[/bold green]\n") 
                # ---- Simulation code removed ----
                
            except Exception as e:
                console.print(f"[bold red]❌ Error during LLM call: {e}[/bold red]") # Use original error message
                # Log full usage info if available, even on error
                if llm:
                     usage = llm.get_usage_info()
                     if usage:
                         logging.warning(f"LLM Usage Info (error state): {usage}")
                return # Stop if LLM fails
    else:
        console.print("[red]   Skipping LLM call (no prompt or LLM unavailable).[/red]")
        return
        
    # --- Generate and Save Reports --- 
    console.print("\n[bold blue]📄 Generating Reports...[/bold blue]")
    md_path, pdf_path = generate_and_save_reports(raw_recommendations)
    
    if md_path:
        console.print(f"[green]   ✅ Markdown report saved to: {md_path}[/green]")
    else:
        console.print("[red]   ❌ Failed to save Markdown report.[/red]")
        
    if pdf_path:
        console.print(f"[green]   ✅ PDF report generated: {pdf_path}[/green]")
    elif md_path: # If MD saved but PDF failed
         console.print("[yellow]   ⚠️ PDF generation failed. Check logs and pandoc installation.[/yellow]")
    else: # If both failed
         console.print("[red]   ❌ Failed to generate PDF report.[/red]")
    # ---------------------------------------------------------------------
    
    console.print("\n[bold blue]Thanks for using Astro Agent! Clear skies! ✨[/bold blue]")

# --- Execution ---
if __name__ == "__main__":
    run_astro_assistant() 