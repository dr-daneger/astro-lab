# Transit Photometry Pipeline

Exoplanet transit observation and analysis workflow: from target selection through data reduction and light curve modeling.

## Workflow Stages

### 1. **target-selector/** — Pre-Observation Planning
Identify and rank exoplanet transit candidates from the ExoClock catalog. Useful for:
- Finding which targets are observable during your clear-sky windows
- Filtering by signal-to-noise potential based on target properties and your equipment

See `target-selector/README.md` and `target-selector/pick_targets.py` for usage.

### 2. **scripts/** — Post-Observation Reduction
Dataset-driven AstroImageJ (AIJ) photometry with reproducible analysis. 

**Key scripts:**
- `generate_skews.py`: writes AIJ job macros for a grid of aperture/annulus radii (RA/Dec aware) and can optionally drive AIJ headlessly.
- `batch_reduce.py`: ingests AIJ Multi-Aperture CSVs, detrends, bins, models the transit, and writes summary figures.
- `windows_pipeline_helpers.ps1`: optional PowerShell helpers for Windows users.

### 3. **aij/** — Reusable AIJ Macros
- `Probe_Environment.ijm`, `Reflect_MultiAperture_API.ijm`: diagnostic probes that enumerate the commands/classes exported by your AIJ build.
- `Dir_FITS_Glob.ijm`, `Run_Radec_Photometry_Template.ijm`: reusable macros for opening FITS stacks, loading RA/Dec catalogues, and running Multi-Aperture.

`aij/jobs/` receives the generated per-skew job files.

## External Dataset Requirements

The scripts never require science data inside the repo — you point them at an external dataset directory with `--dataset PATH`.

Expected dataset structure:
```
<dataset>/
+- raw/ (or reduced/ or another folder you name)   # FITS files to analyse; select with --fits-root
+- csv/                                            # AIJ Multi-Aperture CSV outputs (created if missing)
+- outputs/                                        # Plots + summary tables (created if missing)
+- apertures.csv or apertures.radec                # RA/Dec catalogue for target + comparison stars
+- ephemeris.yaml (optional)                       # Ingress/mid/egress override (UTC)
```

Only the apertures catalogue is required up front. `csv/` and `outputs/` are created automatically. The current pipeline searches for `*.fits` files in the FITS root because ASIAir outputs that extension.

### Supported Aperture Catalogues

**CSV (decimal degrees):**
```
label,type,ra_deg,dec_deg,mag,include
T1,target,304.814000,65.162000,12.7,true
C1,comparison,304.805100,65.168900,12.9,true
C2,comparison,304.829500,65.155000,13.1,true
```

**RA/Dec list saved by AIJ (`.radec`):**
```
#RA in decimal or sexagesimal HOURS
#Dec in decimal or sexagesimal DEGREES
#Ref Star=0,1,missing (0=target star, 1=ref star, missing->first ap=target, others=ref)
#Centroid=0,1,missing (0=do not centroid, 1=centroid, missing=centroid)
#Apparent Magnitude or missing
#RA, Dec, Ref Star, Centroid, Magnitude
20:13:31.529, +65:09:46.47, 0, 0, 99.999
20:14:48.915, +65:10:53.44, 1, 0, 99.999
...
```
If a `.radec` file is provided, `generate_skews.py` converts it to decimal degrees automatically and stores `apertures_converted.csv` in the dataset for the macros to consume.

### ephemeris.yaml override (optional)
```
ingress_utc: "2025-09-24 08:49"
mid_utc:     "2025-09-24 09:38"
egress_utc:  "2025-09-24 10:28"
```
If this file is missing, `batch_reduce.py` falls back to `config/target.yaml`.

## Example: External Dataset on Windows
Assume your FITS live at:
```
C:\Users\Dane\Pictures\DSOs\04_exoplanets\Qatar-1 b\no_filter\DATE_09-23-2025\debayered_green
```
Treat `C:\Users\Dane\Pictures\DSOs\04_exoplanets\Qatar-1 b\no_filter\DATE_09-23-2025` as the dataset root. Inside it:
- Keep the FITS under `debayered_green/` and run with `--fits-root debayered_green`. This reflects the OSC workflow where AIJ debayers and uses the green channel as a Johnson V proxy. Future monochrome + filter runs can rename the folder (for example `raw/` or `V_band`) and pass that via `--fits-root`.
- Place `apertures.radec` (or `apertures.csv`) in the dataset root.
- Optionally include `ephemeris.yaml` with ingress/mid/egress overrides.

Generate macros (and optionally run AIJ headless) with:
```
python scripts/generate_skews.py ^
    --dataset "C:\Users\Dane\Pictures\DSOs\04_exoplanets\Qatar-1 b\no_filter\DATE_09-23-2025" ^
    --fits-root debayered_green ^
    --run-aij ^
    --aij-exec "C:\Program Files\AstroImageJ\AstroImageJ.exe"
```
Outputs land in `<dataset>/csv/` and `<dataset>/outputs/`, keeping science products outside the repo.

## Before You Run the Jobs: interrogate your AIJ build
AIJ's macro APIs vary between releases. Use the supplied probes once per workstation�no internet required:

1. **Enumerate commands and helper functions**
   Open `Plugins > Macros > Run�` and choose `aij/Probe_Environment.ijm`. The log reports whether helpers such as `endsWith`/`Array.sort` exist and reveals the Java class bound to �Multi-Aperture�.

2. **Inspect Multi-Aperture methods**
   Open `Plugins > Macros > Run�` again and choose `aij/Reflect_MultiAperture_API.ijm`. Pass the class name from step 1 (or accept the default). The macro lists exported setter methods so you know the exact `call("�")` signatures available on your build.

3. **Record the UI workflow once**
   With the Macro Recorder active (`Plugins > Macros > Record�`), manually open the FITS stack, load the RA/Dec list, set radii, run Multi-Aperture, and save the �Aperture Photometry� table. Copy the recorded `run("�", "�")` lines and paste them into the placeholders inside `aij/Run_Radec_Photometry_Template.ijm` (look for the `NOTE: insert your recorded �` comments). This gives the template rock-solid commands tailored to your installation.

Once the template is patched, `generate_skews.py` can safely reuse it for every dataset/skew.

## Step-by-Step Workflow
1. **Prepare apertures:** place `apertures.csv` or `apertures.radec` in the dataset directory.
2. **Optional ephemeris:** add `ephemeris.yaml` to override ingress/mid/egress.
3. **Probe & patch AIJ macros (first-time only):** run the probes above and paste your recorded RA/Dec + Multi-Aperture commands into `Run_Radec_Photometry_Template.ijm`.
4. **Generate per-skew macros:**
   ```
   python scripts/generate_skews.py --dataset <dataset> --fits-root <fits-folder>
   ```
   Job files appear under `aij/jobs/`.
5. **Run a job manually (optional test):**
   ```
   "C:\Program Files\AstroImageJ\AstroImageJ.exe" -macro "C:\Users\Dane\Documents\Local Repo\astronomy\transit_process_pipeline\aij\jobs\job_debayered_green_Ap2-5_In9-0_Out19-0.ijm"
   ```
6. **Batch reduction & modelling:**
   ```
   python scripts/batch_reduce.py --dataset <dataset>
   ```
7. **Inspect results:** open `<dataset>/outputs/summary.csv`, `heatmap_WRMS.png`, `composite_best.png`, `residuals_best.png`, and the per-skew figures.

## Optional: PowerShell Helpers for Windows
Source the helpers from the repo root:
```
PS> . .\scripts\windows_pipeline_helpers.ps1
```
Defaults target the path above, use `debayered_green` as the FITS folder, and point to `C:\Program Files\AstroImageJ\AstroImageJ.exe`. Available functions:
- `Invoke-TransitGenerateSkews [-RunAIJ] [-Dataset <path>] [-FitsRoot <name>] [-Apertures <path>] [-AijExec <path>]`
- `Invoke-TransitBatchReduce [-Dataset <path>]`
- `Invoke-TransitMacro [-JobName <stem>] [-AijExec <path>]`

Example session:
```
PS> . .\scripts\windows_pipeline_helpers.ps1
PS> Invoke-TransitGenerateSkews -RunAIJ
PS> Invoke-TransitBatchReduce
PS> Invoke-TransitMacro -JobName "job_debayered_green_Ap2-5_In9-0_Out19-0"
```

## Testing Individual Components
- **Macro generation only:** run `generate_skews.py` without `--run-aij` and inspect the `.ijm` files.
- **Single macro validation:** execute one macro manually (command above) and confirm a CSV appears in `<dataset>/csv/`.
- **Analysis dry run:** drop an existing `MA_*.csv` into `<dataset>/csv/` and run `batch_reduce.py` to confirm the analysis stage without reprocessing FITS.

## Frequently Asked Questions
- **Do macros have to live in AIJ's macros directory?** No. Pass the macro path on the command line or via `Plugins > Macros > Run�` and AIJ will execute it in place.
- **Can I keep multiple datasets on different drives?** Yes. Point `--dataset` at the dataset you want to process. The repo never copies FITS internally unless you choose to.
- **What happens if `csv/` or `outputs/` are missing?** Both scripts create them automatically inside the dataset.
- **Is sample data provided?** `example_datasets/` contains illustrative content (including a `.radec` list and a few FITS frames). Use it for tests or remove it once you have real data elsewhere.

## Next Steps / Open Questions
- Do you want additional helper presets for other dataset roots (e.g., per-night archives)?
- When you move to monochrome sensors + dedicated filters, should the README include guidance for those workflows?
- Are there other automation hooks (Task Scheduler, batch files) you would like documented?

Let me know and we can refine further.

