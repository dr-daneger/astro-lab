import json
import logging

# Configure logging (if you want module-specific logging)
log = logging.getLogger(__name__)

# Calculated values from the specific equipment
# Apertura 75Q + ZWO ASI585MC Pro
# Pixel Scale = (2.9 / 405) * 206.265 = 1.476 arcsec/pixel
# FOV Width = (3840 * 1.476) / 60 = 94.5 arcmin
# FOV Height = (2160 * 1.476) / 60 = 53.1 arcmin

DEFAULT_PIXEL_SCALE = 1.48  # arcsec/pixel
DEFAULT_FOV_WIDTH = 94.5    # arcmin
DEFAULT_FOV_HEIGHT = 53.1   # arcmin

def calculate_equipment_params(specs: dict) -> dict:
    """Calculates derived parameters like pixel scale and FOV from specs."""
    calculated_params = {
        'pixel_scale': None,
        'fov_width': None,
        'fov_height': None
    }

    try:
        if 'imaging_telescope' in specs and 'imaging_camera' in specs:
            telescope = specs['imaging_telescope']['specs']
            camera = specs['imaging_camera']['specs']

            focal_length = telescope.get('focal_length_mm')
            pixel_size = camera.get('pixel_size_microns')
            res_width = camera.get('resolution_width_px')
            res_height = camera.get('resolution_height_px')

            if focal_length and pixel_size:
                scale = (pixel_size / focal_length) * 206.265
                calculated_params['pixel_scale'] = round(scale, 2) # arcsec/pixel

                if res_width and res_height and scale:
                    fov_w = (res_width * scale) / 60
                    fov_h = (res_height * scale) / 60
                    calculated_params['fov_width'] = round(fov_w, 1) # arcmin
                    calculated_params['fov_height'] = round(fov_h, 1) # arcmin
    except Exception as e:
        log.error(f"Error calculating equipment parameters: {e}", exc_info=True)
        # Fallback to defaults if calculation fails

    # Use defaults if calculation wasn't possible or failed
    if calculated_params['pixel_scale'] is None:
        calculated_params['pixel_scale'] = DEFAULT_PIXEL_SCALE
        log.warning(f"Could not calculate pixel scale, using default: {DEFAULT_PIXEL_SCALE} arcsec/pixel")
    if calculated_params['fov_width'] is None or calculated_params['fov_height'] is None:
        calculated_params['fov_width'] = DEFAULT_FOV_WIDTH
        calculated_params['fov_height'] = DEFAULT_FOV_HEIGHT
        log.warning(f"Could not calculate FOV, using defaults: {DEFAULT_FOV_WIDTH}' x {DEFAULT_FOV_HEIGHT}'")

    return calculated_params

def load_equipment_specs(filepath="equipment_specs.json") -> dict:
    """Loads equipment specifications from a JSON file and adds calculated params."""
    specs = {}
    try:
        # Adjust the filepath if needed relative to the project root
        with open(filepath, 'r') as file:
            specs = json.load(file)
        log.info(f"Equipment specifications loaded successfully from {filepath}.")
        
        # Calculate and add derived parameters
        calculated_params = calculate_equipment_params(specs)
        specs['calculated'] = calculated_params
        log.info(f"Calculated Equipment Params: Pixel Scale={calculated_params['pixel_scale']}, FOV={calculated_params['fov_width']}x{calculated_params['fov_height']}")

    except FileNotFoundError:
        log.error(f"Equipment specifications file not found at {filepath}. Returning empty specs with default calculations.")
        specs['calculated'] = {
            'pixel_scale': DEFAULT_PIXEL_SCALE,
            'fov_width': DEFAULT_FOV_WIDTH,
            'fov_height': DEFAULT_FOV_HEIGHT
        }
    except json.JSONDecodeError as e:
        log.error(f"Error parsing equipment JSON file {filepath}: {e}. Returning empty specs with default calculations.")
        specs['calculated'] = {
            'pixel_scale': DEFAULT_PIXEL_SCALE,
            'fov_width': DEFAULT_FOV_WIDTH,
            'fov_height': DEFAULT_FOV_HEIGHT
        }
    except Exception as e:
        log.error(f"An unexpected error occurred loading {filepath}: {e}. Returning empty specs with default calculations.")
        specs['calculated'] = {
            'pixel_scale': DEFAULT_PIXEL_SCALE,
            'fov_width': DEFAULT_FOV_WIDTH,
            'fov_height': DEFAULT_FOV_HEIGHT
        }

    return specs 