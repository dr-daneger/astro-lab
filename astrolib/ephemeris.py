import logging
from astropy.coordinates import EarthLocation, AltAz, get_sun, get_moon, get_body
from astropy.time import Time, TimeDelta
import astropy.units as u
from astroplan import Observer, FixedTarget, AltitudeConstraint, AirmassConstraint, AtNightConstraint
from astroplan.constraints import TimeConstraint
from astroplan.scheduling import Transitioner, Schedule, ObservingBlock
from astroplan.utils import time_grid_from_range
import numpy as np
from astroquery.simbad import Simbad

log = logging.getLogger(__name__)

# Define a default list of interesting targets (can be expanded/loaded later)
DEFAULT_TARGETS = [
    "M31", "M42", "M45", "M13", "M51", "M81", "M101", # Messier DSOs
    "NGC 2244", "NGC 7000", # Other NGC DSOs
    "Jupiter", "Saturn", "Mars", "Venus" # Planets
]

# Mapping for planet lookups if needed (astroplan usually handles this)
PLANET_MAPPING = {
    "jupiter": "jupiter barycenter",
    "saturn": "saturn barycenter",
    "mars": "mars barycenter",
    "venus": "venus barycenter"
}

MIN_ALTITUDE = 30 * u.deg

# Configure Simbad query fields
Simbad.add_votable_fields('dim')

def get_targets(target_list=DEFAULT_TARGETS):
    """Creates FixedTarget objects from a list of names."""
    targets = []
    for name in target_list:
        try:
            # Use mapping for planets if direct lookup fails, otherwise try name directly
            lookup_name = PLANET_MAPPING.get(name.lower(), name)
            target = FixedTarget.from_name(lookup_name)
            # Store the common name for easier reference later
            target.common_name = name
            targets.append(target)
            log.debug(f"Successfully created FixedTarget for {name} ({lookup_name})")
        except Exception as e:
            log.warning(f"Could not resolve target '{name}' using astroplan/astroquery: {e}")
    return targets

def calculate_ephemeris(location: EarthLocation, targets: list, calculation_time: Time):
    """Calculates ephemeris data including observability windows (manual checks)."""
    observer = Observer(location=location)
    ephemeris_data = []

    # --- Determine Observability Window (Tonight's Astro Dusk to Tomorrow's Astro Dawn) ---
    try:
        time_range_start = observer.twilight_evening_astronomical(calculation_time, which='next')
        time_range_end = observer.twilight_morning_astronomical(time_range_start, which='next')
        log.info(f"Using Astronomical Twilight Window: {time_range_start.iso} to {time_range_end.iso}")
    except Exception as e:
        log.error(f"Error calculating twilight times: {e}. Using next 12 hours from now.")
        time_range_start = calculation_time
        time_range_end = calculation_time + TimeDelta(12 * u.hour)
    # ----------------------------------------------------------------------------------
    
    time_range = Time([time_range_start, time_range_end])
    time_grid = time_grid_from_range(time_range, time_resolution=5*u.minute)

    # Define constraints (to get parameters like horizon angle)
    min_altitude_constraint = AltitudeConstraint(min=MIN_ALTITUDE)
    night_constraint = AtNightConstraint.twilight_astronomical()
    
    log.info(f"Calculating ephemeris for {len(targets)} targets between {time_range_start.iso} and {time_range_end.iso}")

    # Calculate Sun and Moon positions for context using get_body
    try:
        sun_body = get_body("sun", calculation_time, location)
        moon_body = get_body("moon", calculation_time, location)
        sun_altaz_now = sun_body.transform_to(AltAz(obstime=calculation_time, location=location))
        moon_altaz_now = moon_body.transform_to(AltAz(obstime=calculation_time, location=location))
    except Exception as e:
        log.warning(f"Could not calculate Sun/Moon position at current time: {e}")
        sun_altaz_now, moon_altaz_now = None, None

    base_astro_info = {
        "calculation_time_iso": calculation_time.iso,
        "observability_window_start_iso": time_range_start.iso,
        "observability_window_end_iso": time_range_end.iso,
        "sun_altitude_now": sun_altaz_now.alt.deg if sun_altaz_now else None,
        "moon_altitude_now": moon_altaz_now.alt.deg if moon_altaz_now else None,
        # TODO: Add moon phase calculation later
    }

    for target in targets:
        target_name = getattr(target, 'common_name', target.name)
        target_info = {"name": target_name}
        try:
            # Current Alt/Az (calculated at the initial `calculation_time`)
            altaz_frame_now = AltAz(obstime=calculation_time, location=location)
            target_altaz_now = target.coord.transform_to(altaz_frame_now)
            target_info['altitude_now'] = round(target_altaz_now.alt.deg, 2)
            target_info['azimuth_now'] = round(target_altaz_now.az.deg, 2)
            target_info['is_up_now'] = target_altaz_now.alt > 0 * u.deg

            # Simplified Rise/Transit/Set times relative to initial calculation_time
            try:
                rise_time = observer.target_rise_time(calculation_time, target, which='next', horizon=0*u.deg)
                target_info['rise_time_iso'] = rise_time.iso if rise_time else None
            except Exception: target_info['rise_time_iso'] = "Circumpolar/Never Rises"
            try:
                transit_time = observer.target_meridian_transit_time(calculation_time, target, which='nearest')
                transit_altaz = observer.altaz(transit_time, target)
                target_info['transit_time_iso'] = transit_time.iso if transit_time else None
                target_info['transit_altitude'] = round(transit_altaz.alt.deg, 2) if transit_altaz else None
            except Exception: target_info['transit_time_iso'], target_info['transit_altitude'] = "N/A", None
            try:
                set_time = observer.target_set_time(calculation_time, target, which='next', horizon=0*u.deg)
                target_info['set_time_iso'] = set_time.iso if set_time else None
            except Exception: target_info['set_time_iso'] = "Circumpolar/Never Sets"

            # --- Query Simbad for Angular Size --- 
            target_info['angular_size_maj'] = None
            target_info['angular_size_min'] = None
            target_info['angular_size_unit'] = None
            if target_name.lower() not in PLANET_MAPPING: # Don't query planets
                try:
                    log.debug(f"Querying Simbad for dimensions of {target_name}...")
                    simbad_result = Simbad.query_object(target_name)
                    # --- Remove TEMP DEBUG Prints ---
                    # if simbad_result:
                    #      log.debug(f"Simbad result table for {target_name}:\n{simbad_result}")
                    #      log.debug(f"Simbad result columns: {simbad_result.colnames}")
                    # else:
                    #      log.debug(f"Simbad query returned None for {target_name}.")
                    # --------------------------------
                    
                    # --- Use Correct Column Names --- 
                    if simbad_result and 'galdim_majaxis' in simbad_result.colnames and 'galdim_minaxis' in simbad_result.colnames:
                        maj_axis = simbad_result['galdim_majaxis'][0]
                        min_axis = simbad_result['galdim_minaxis'][0]
                        # --------------------------------
                        if np.isfinite(maj_axis) and maj_axis > 0: # Check for valid numeric dimension
                             target_info['angular_size_maj'] = round(maj_axis, 2)
                             target_info['angular_size_min'] = round(min_axis if np.isfinite(min_axis) else maj_axis, 2)
                             target_info['angular_size_unit'] = "arcmin"
                             log.debug(f"Found dimensions for {target_name}: {target_info['angular_size_maj']}' x {target_info['angular_size_min']}'")
                        else:
                            log.debug(f"Simbad query for {target_name} returned zero/invalid/masked dimensions in galdim columns.") # Updated log
                    else:
                        log.debug(f"Simbad query for {target_name} did not return galdim_majaxis/galdim_minaxis fields.") # Updated log
                except Exception as simbad_e:
                     log.warning(f"Simbad query failed for {target_name}: {simbad_e}")
            # ------------------------------------
            
            # --- Manual Observability Calculation for astroplan v0.10 --- 
            target_altaz_grid = observer.altaz(time_grid, target)
            altitudes = target_altaz_grid.alt
            altitude_mask = altitudes >= MIN_ALTITUDE
            night_horizon = -18*u.deg
            if hasattr(night_constraint, 'horizon'):
                 night_horizon = night_constraint.horizon
            night_mask = observer.is_night(time_grid, horizon=night_horizon)
            observable_mask = altitude_mask & night_mask
            # -----------------------------------------------------------
            
            observable_indices = np.where(observable_mask)[0]

            if len(observable_indices) > 0:
                # Find contiguous blocks of observability
                # For simplicity, we'll take the first major block or longest block
                # A more robust approach might find all blocks
                diff = np.diff(observable_indices)
                split_indices = np.where(diff > 1)[0]
                blocks = np.split(observable_indices, split_indices + 1)
                
                # Find the longest block
                longest_block_indices = max(blocks, key=len)
                
                start_index = longest_block_indices[0]
                end_index = longest_block_indices[-1]
                
                observable_start_time = time_grid[start_index]
                observable_end_time = time_grid[end_index]
                # Duration calculation needs care with indices vs time steps
                duration = (observable_end_time - observable_start_time) + (time_grid[1] - time_grid[0]) # Add one time step duration

                target_info['observable_start_iso'] = observable_start_time.iso
                target_info['observable_end_iso'] = observable_end_time.iso
                target_info['observable_duration_hours'] = round(duration.to(u.hour).value, 2)
                target_info['is_observable'] = True
                
                # Max altitude during the *observable* window
                observable_times = time_grid[observable_mask]
                observable_altitudes = altitudes[observable_mask]
                target_info['max_observable_altitude'] = round(np.max(observable_altitudes).to(u.deg).value, 2)
                
            else:
                target_info['is_observable'] = False
                target_info['observable_duration_hours'] = 0.0
                target_info['observable_start_iso'] = None
                target_info['observable_end_iso'] = None
                target_info['max_observable_altitude'] = None

            ephemeris_data.append(target_info)
            log.debug(f"Processed ephemeris for {target_info['name']}. Observable: {target_info['is_observable']}")

        except Exception as e:
            log.error(f"Failed to calculate ephemeris for target {target_name}: {e}", exc_info=True)
            ephemeris_data.append({"name": target_name, "error": str(e), "is_observable": False})

    return {"base_info": base_astro_info, "targets": ephemeris_data} 