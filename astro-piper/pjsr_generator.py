#!/usr/bin/env python3
"""
pjsr_generator.py -- Sprint 2: Dynamic PJSR script generation

Generates complete, valid PixInsight JavaScript (PJSR) scripts as Python
strings. Scripts are self-contained: they open the necessary image windows,
execute the process, save outputs, and close windows. Ready to pass directly
to pi_runner.run_pjsr_inline().

Phases covered:
    Phase 0  -- Master calibration frame creation (bias, dark, flat)
    Phase 1  -- ImageCalibration, StarAlignment, ImageIntegration,
                 DrizzleIntegration, LocalNormalization
    Phase 2  -- BlurXTerminator, NoiseXTerminator, StarXTerminator,
                 ChannelExtraction, LinearFit, PixelMath (combine/split)
    Phase 3  -- GeneralizedHyperbolicStretch, HistogramTransformation,
                 Foraxx palette (PixelMath)
    Phase 4  -- SCNR, CurvesTransformation (hue/saturation/contrast),
                 HDRMultiscaleTransform, LocalHistogramEqualization
    Phase 5  -- ChannelCombination, SpectrophotometricColorCalibration,
                 PixelMath (screen blend)

CRITICAL: All paths embedded in PJSR scripts MUST use forward slashes.
Use pjsr_path() on every path before embedding in a script string. The
Windows backslash path separator causes silent parse failures in PI's JS
engine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


# =============================================================================
# Path and value utilities
# =============================================================================


def pjsr_path(p: "str | Path") -> str:
    """Convert any path to forward-slash format required by PJSR on Windows."""
    return Path(p).as_posix()


def js_bool(b: bool) -> str:
    """Python bool -> JavaScript boolean literal."""
    return "true" if b else "false"


def js_float(f: float, decimals: int = 3) -> str:
    """Format a float for JavaScript (always includes decimal point)."""
    return f"{f:.{decimals}f}"


def js_path_array(paths: list[str]) -> str:
    """
    Emit a JavaScript array of forward-slash path strings.
    e.g. ["/a/b.xisf", "/c/d.xisf"]
    """
    items = ", ".join(f'"{pjsr_path(p)}"' for p in paths)
    return f"[{items}]"


def js_enabled_path_array(paths: list[str], enabled: bool = True) -> str:
    """
    Emit [[enabled, path], ...] used by ImageCalibration.targetFrames,
    StarAlignment.targets (2-element form), etc.
    """
    en = js_bool(enabled)
    rows = ",\n      ".join(f'[{en}, "{pjsr_path(p)}"]' for p in paths)
    return f"[\n      {rows}\n   ]"


def js_integration_images(paths: list[str], drizzle_paths: Optional[list[str]] = None) -> str:
    """
    Emit [[enabled, imagePath, drizzlePath], ...] for ImageIntegration.images.
    drizzle_paths defaults to empty strings (Drizzle not used in integration step).
    """
    if drizzle_paths is None:
        drizzle_paths = [""] * len(paths)
    rows = []
    for img, dzl in zip(paths, drizzle_paths):
        rows.append(f'[true, "{pjsr_path(img)}", "{pjsr_path(dzl) if dzl else ""}"]')
    joined = ",\n      ".join(rows)
    return f"[\n      {joined}\n   ]"


def js_drizzle_input_array(image_paths: list[str], drizzle_paths: list[str]) -> str:
    """
    Emit [[enabled, imagePath, drizzlePath], ...] for DrizzleIntegration.inputData.
    """
    rows = []
    for img, dzl in zip(image_paths, drizzle_paths):
        rows.append(f'[true, "{pjsr_path(img)}", "{pjsr_path(dzl)}"]')
    joined = ",\n      ".join(rows)
    return f"[\n      {joined}\n   ]"


# =============================================================================
# Common script fragments
# =============================================================================


def _header(comment: str = "") -> str:
    lines = ['#include <pjsr/DataType.jsh>', ""]
    if comment:
        lines += [f"// {comment}", ""]
    return "\n".join(lines)


def _open_image(var_name: str, path: str, view_id: Optional[str] = None) -> str:
    """
    Emit JavaScript to open an XISF/FITS file and optionally assign a view ID.

    The view ID assignment is needed when subsequent PixelMath expressions
    reference images by name (e.g. "Ha", "Oiii", "Sii").
    """
    src = pjsr_path(path)
    lines = [f'var {var_name} = ImageWindow.open("{src}")[0];']
    if view_id:
        lines.append(f'{var_name}.currentView.id = "{view_id}";')
    return "\n".join(lines)


def _save_image(var_name: str, output_path: str) -> str:
    """Emit saveAs call. The five false args suppress overwrite/format dialogs."""
    dst = pjsr_path(output_path)
    return f'{var_name}.saveAs("{dst}", false, false, false, false);'


def _close_image(var_name: str) -> str:
    return f"{var_name}.forceClose();"


def _find_window_by_literal_id(var_name: str, view_id: str) -> str:
    """
    Generate JS to find an ImageWindow by a *literal string* view ID.

    ImageWindow.windowById() does not exist in PI 1.8.x -- iterate instead.
    ``view_id`` is a Python string that becomes a JS string literal.
    """
    return (
        f'var {var_name} = null;\n'
        f'{{ var _ws = ImageWindow.windows; '
        f'for (var _wi = 0; _wi < _ws.length; _wi++) '
        f'{{ if (_ws[_wi].currentView.id == "{view_id}") '
        f'{{ {var_name} = _ws[_wi]; break; }} }} }}\n'
        f'if ({var_name} == null || {var_name}.isNull) '
        f'throw new Error("Window not found: {view_id}");'
    )


def _find_window_by_js_expr(var_name: str, id_js_expr: str, label: str = "") -> str:
    """
    Generate JS to find an ImageWindow by a *JS expression* that evaluates to the ID.

    Use this when the ID is only known at JS runtime (e.g. ``P.integrationImageId``).
    ``label`` is used in the error message.
    """
    err_label = label or id_js_expr
    return (
        f'var {var_name} = null;\n'
        f'{{ var _ws = ImageWindow.windows; var _id = {id_js_expr}; '
        f'for (var _wi = 0; _wi < _ws.length; _wi++) '
        f'{{ if (_ws[_wi].currentView.id == _id) '
        f'{{ {var_name} = _ws[_wi]; break; }} }} }}\n'
        f'if ({var_name} == null || {var_name}.isNull) '
        f'throw new Error("Window not found: {err_label} (id=" + {id_js_expr} + ")");'
    )


def _save_by_id(view_id: str, output_path: str) -> str:
    """Emit save for a window looked up by its view ID (used after ChannelExtraction)."""
    dst = pjsr_path(output_path)
    tmp = f"_wById_{view_id.replace('-', '_')}"
    return (
        f'{_find_window_by_literal_id(tmp, view_id)}\n'
        f'{tmp}.saveAs("{dst}", false, false, false, false);'
    )


def _close_by_id(view_id: str) -> str:
    tmp = f"_wById_{view_id.replace('-', '_')}"
    return (
        f'{_find_window_by_literal_id(tmp, view_id)}\n'
        f'{tmp}.forceClose();'
    )


# =============================================================================
# ImageIntegration numeric enum constants
#
# ImageIntegration.prototype.* values are NOT reliably available in all PI
# versions (confirmed undefined in PI 1.8.x). Use these numeric constants
# directly in generated PJSR scripts to ensure compatibility.
# =============================================================================

_II_COMBINATION = {
    "Average": 0,
}
_II_WEIGHT_MODE = {
    "DontCare":            0,
    "NoiseEvaluation":     2,
    "KeywordValue":        5,
    "MeanKeyword":         5,
}
_II_NORMALIZATION = {
    "NoNormalization":          0,
    "None":                     0,
    "Additive":                 1,
    "Multiplicative":           2,
    "AdditiveWithScaling":      3,
    "MultiplicativeWithScaling": 4,
}
_II_REJECTION = {
    "NoRejection":              0,
    "Rejection_None":           0,
    "MinMax":                   1,
    "PercentileClip":           2,
    "SigmaClip":                3,
    "SigmaClipping":            3,
    "WinsorisedSigmaClipping":  4,
    "WinsorizedSigmaClip":      4,
    "AveragedSigmaClipping":    5,
    "LinearFitClip":            6,
    "LinearClipping":           6,
    "CCDClip":                  7,
    "ESD":                      8,   # Extreme Studentized Deviate
}
_II_REJECTION_NORM = {
    "NoNormalization": 0,
    "Scale":           1,
    "Flux":            2,
}


# =============================================================================
# Breakpoint review script
# =============================================================================

# Shared JavaScript helper — computes auto-STF parameters for a view using
# the standard PI formula: clip at median - 2.8*MAD, stretch to target bg=0.25.
_AUTO_STF_JS = """\
function _MTF(m, x) {
   if (x <= 0) return 0;
   if (x >= 1) return 1;
   if (Math.abs(x - m) < 1.0e-7) return 0.5;
   return (m - 1) * x / ((2*m - 1) * x - m);
}

function applyAutoSTF(view, shadowsClip, targetBg) {
   if (shadowsClip === undefined) shadowsClip = -2.8;
   if (targetBg    === undefined) targetBg    =  0.25;
   var n = view.image.numberOfChannels;
   var c0 = 0.0, med = 0.0;
   for (var c = 0; c < n; c++) {
      view.image.selectedChannel = c;
      var m   = view.computeOrFetchProperty("Median").at(0);
      var mad = view.computeOrFetchProperty("MAD").at(0) * 1.4826;
      c0  += m + shadowsClip * mad;
      med += m;
   }
   view.image.resetSelections();
   c0  /= n;  med /= n;
   if (c0 < 0) c0 = 0;
   var midtone = _MTF(targetBg, med - c0);
   var stf = new ScreenTransferFunction;
   stf.STF = [
      [c0, 1.0, midtone, 0.0, 1.0],
      [c0, 1.0, midtone, 0.0, 1.0],
      [c0, 1.0, midtone, 0.0, 1.0],
      [c0, 1.0, midtone, 0.0, 1.0]
   ];
   stf.executeOn(view, false);
}
"""


def generate_review_script(
    file_paths: list[str],
    label: str = "",
) -> str:
    """
    Generate a PJSR script that opens image files with auto-STF applied and
    tiles all windows for quick visual evaluation at pipeline breakpoints.

    The script is launched in normal (non-automation) PI mode so the application
    stays open for interactive review. All images receive linked auto-STF with
    standard astrophotography settings (-2.8σ shadows clip, 0.25 target bg).

    Args:
        file_paths: Paths to the image files to open.
        label:      Human-readable breakpoint label for the PI console.

    Returns:
        Complete PJSR script string.
    """
    files_js  = js_path_array(file_paths)
    label_esc = label.replace("\\", "\\\\").replace('"', '\\"')

    return f"""{_header("Breakpoint review -- auto-STF + tile")}
{_AUTO_STF_JS}
var label = "{label_esc}";
var files = {files_js};

Console.writeln("\\n=== BREAKPOINT REVIEW: " + label + " ===");
Console.writeln("Opening " + files.length + " file(s) with auto-STF...");

for (var i = 0; i < files.length; i++) {{
   var wins = ImageWindow.open(files[i]);
   if (wins.length > 0 && !wins[0].isNull) {{
      applyAutoSTF(wins[0].currentView, -2.8, 0.25);
      wins[0].show();
      Console.writeln("  [" + (i+1) + "/" + files.length + "] " + files[i]);
   }} else {{
      Console.warningln("  Could not open: " + files[i]);
   }}
}}

ImageWindow.tile();
Console.writeln("Review ready -- " + files.length + " file(s) tiled with auto-STF.");
"""


# =============================================================================
# Phase 0 -- Calibration Master Creation
# =============================================================================


def generate_master_bias(
    frame_paths: list[str],
    output_path: str,
    sigma_low: float = 4.0,
    sigma_high: float = 3.0,
) -> str:
    """
    Generate an ImageIntegration script that combines raw bias frames into a
    master bias.

    Bias frames need no prior calibration. WinsorisedSigmaClipping with
    NoNormalization rejects hot/cold pixels without altering the bias level.
    DontCare weight mode treats all frames equally (no SNR weighting for
    calibration masters).

    Args:
        frame_paths: Raw bias frame paths.
        output_path: Destination .xisf path for the master bias.
        sigma_low:   Lower sigma threshold for WinsorisedSigmaClipping.
        sigma_high:  Upper sigma threshold.

    Returns:
        Complete PJSR script string.
    """
    images_js = js_integration_images(frame_paths)
    out_dir   = pjsr_path(str(Path(output_path).parent))

    rej  = _II_REJECTION["WinsorisedSigmaClipping"]
    norm = _II_NORMALIZATION["NoNormalization"]
    wt   = _II_WEIGHT_MODE["DontCare"]

    return f"""{_header("ImageIntegration -- Master Bias creation")}
var P = new ImageIntegration;

P.images = {images_js};

P.combination             = 0;    // Average
P.weightMode              = {wt};  // DontCare
P.normalization           = {norm}; // NoNormalization

P.rejection               = {rej}; // WinsorisedSigmaClipping
P.rejectionNormalization  = 0;    // NoNormalization
P.sigmaLow                = {js_float(sigma_low)};
P.sigmaHigh               = {js_float(sigma_high)};

P.generateDrizzleData     = false;
P.generateIntegratedImage = true;
P.outputDirectory         = "{out_dir}";

P.executeGlobal();

{_find_window_by_js_expr("wInt", "P.integrationImageId", "master bias")}
wInt.saveAs("{pjsr_path(output_path)}", false, false, false, false);
wInt.forceClose();
Console.writeln("Master bias saved to: {pjsr_path(output_path)}");
"""


def generate_integrate_calibrated_frames(
    frame_paths: list[str],
    output_path: str,
    normalization: str = "NoNormalization",
    sigma_low: float = 4.0,
    sigma_high: float = 3.0,
) -> str:
    """
    Generate an ImageIntegration script for pre-calibrated dark or flat frames.

    Used as the second step in master dark/flat creation, after the raw frames
    have been bias-subtracted (and optionally dark-subtracted) via
    generate_image_calibration().

    Normalization modes:
        "NoNormalization"  -- for master darks (preserve absolute ADU levels)
        "Multiplicative"   -- for master flats (normalize response to ~1.0)

    Args:
        frame_paths:   Pre-calibrated frame paths (output of ImageCalibration).
        output_path:   Destination .xisf path for the master.
        normalization: "NoNormalization" or "Multiplicative".
        sigma_low:     Lower sigma for WinsorisedSigmaClipping.
        sigma_high:    Upper sigma.

    Returns:
        Complete PJSR script string.
    """
    norm_int  = _II_NORMALIZATION.get(normalization, _II_NORMALIZATION["NoNormalization"])
    rej_int   = _II_REJECTION["WinsorisedSigmaClipping"]
    wt_int    = _II_WEIGHT_MODE["DontCare"]
    images_js = js_integration_images(frame_paths)
    out_dir   = pjsr_path(str(Path(output_path).parent))

    return f"""{_header("ImageIntegration -- Master calibration frame integration")}
var P = new ImageIntegration;

P.images = {images_js};

P.combination             = 0;        // Average
P.weightMode              = {wt_int};  // DontCare
P.normalization           = {norm_int}; // {normalization}

P.rejection               = {rej_int}; // WinsorisedSigmaClipping
P.rejectionNormalization  = 0;        // NoNormalization
P.sigmaLow                = {js_float(sigma_low)};
P.sigmaHigh               = {js_float(sigma_high)};

P.generateDrizzleData     = false;
P.generateIntegratedImage = true;
P.outputDirectory         = "{out_dir}";

P.executeGlobal();

{_find_window_by_js_expr("wInt", "P.integrationImageId", "calibration master")}
wInt.saveAs("{pjsr_path(output_path)}", false, false, false, false);
wInt.forceClose();
Console.writeln("Calibration master saved to: {pjsr_path(output_path)}");
"""


# =============================================================================
# Phase 1 -- Preprocessing
# =============================================================================


def generate_image_calibration(
    light_paths: list[str],
    output_dir: str,
    master_dark_path: Optional[str] = None,
    master_flat_path: Optional[str] = None,
    master_bias_path: Optional[str] = None,
    pedestal: int = 150,
    output_postfix: str = "_c",
    output_extension: str = ".xisf",
) -> str:
    """
    Generate an ImageCalibration script for one channel's light frames.

    Call once per channel (Ha, OIII, SII or R, G, B). Output files are
    written to output_dir with `output_postfix` appended to the stem.

    Args:
        light_paths:      List of raw light frame paths for this channel.
        output_dir:       Directory to write calibrated frames.
        master_dark_path: Pre-integrated master dark. If None, dark step disabled.
        master_flat_path: Pre-integrated master flat. If None, flat step disabled.
        master_bias_path: Pre-integrated master bias. Usually None for CMOS
                          (use pedestal instead).
        pedestal:         Output pedestal in DN. 150 prevents black-clip on
                          CMOS narrowband after dark subtraction.
        output_postfix:   Suffix appended to calibrated frame filenames.
        output_extension: File extension for calibrated output frames.

    Returns:
        Complete PJSR script string.
    """
    dark_enabled = master_dark_path is not None
    flat_enabled = master_flat_path is not None
    bias_enabled = master_bias_path is not None

    dark_path_js = pjsr_path(master_dark_path) if master_dark_path else ""
    flat_path_js = pjsr_path(master_flat_path) if master_flat_path else ""
    bias_path_js = pjsr_path(master_bias_path) if master_bias_path else ""

    targets = js_enabled_path_array(light_paths)

    return f"""{_header("ImageCalibration -- Phase 1 calibration")}
var P = new ImageCalibration;

P.targetFrames = {targets};

P.masterBiasEnabled   = {js_bool(bias_enabled)};
P.masterBiasPath      = "{bias_path_js}";

P.masterDarkEnabled   = {js_bool(dark_enabled)};
P.masterDarkPath      = "{dark_path_js}";
P.masterDarkOptimizationLow    = 3.0;
P.masterDarkOptimizationWindow = 1024;

P.masterFlatEnabled   = {js_bool(flat_enabled)};
P.masterFlatPath      = "{flat_path_js}";

P.outputDirectory       = "{pjsr_path(output_dir)}";
P.outputExtension       = "{output_extension}";
P.outputPostfix         = "{output_postfix}";
P.outputSampleFormat    = 4;  // f32 (32-bit float)
P.pedestal              = {pedestal};
P.enableCFA             = false;
P.noiseEvaluation       = true;
P.overwriteExistingFiles = true;

P.executeGlobal();
Console.writeln("ImageCalibration complete -- " + P.targetFrames.length + " frames calibrated.");
"""


def generate_star_alignment(
    reference_path: str,
    target_paths: list[str],
    output_dir: str,
    distortion_correction: bool = True,
    generate_drizzle_data: bool = True,
    output_postfix: str = "_r",
) -> str:
    """
    Generate a StarAlignment script to register target frames to a reference.

    Used in two contexts:
        Phase 1 NB:  Register NB channel frames to a per-channel reference
        Phase 1b RGB: Register RGB masters to the Ha master reference frame

    Args:
        reference_path:        Reference image path. For RGB->NB registration,
                               use Ha_master.xisf.
        target_paths:          List of paths to register.
        output_dir:            Directory for registered output frames.
        distortion_correction: Enable polynomial distortion correction (True
                               recommended for refractors with field curvature).
        generate_drizzle_data: Write .xdrz sidecar files for DrizzleIntegration.
        output_postfix:        Suffix for registered output filenames.

    Returns:
        Complete PJSR script string.
    """
    targets_js = js_path_array(target_paths)

    return f"""{_header("StarAlignment -- Phase 1 registration (executeOn loop)")}
// Open reference as a window so we can pass its view ID to executeOn().
// P.targets setter is broken in this PI version -- executeOn() is the workaround.
var _refWins = ImageWindow.open("{pjsr_path(reference_path)}");
if (_refWins.length == 0 || _refWins[0].isNull)
    throw new Error("StarAlignment: could not open reference: {pjsr_path(reference_path)}");
var _refWin = _refWins[0];

var P = new StarAlignment;
P.referenceImage       = _refWin.currentView.id;
P.referenceIsFile      = false;
P.distortionCorrection  = {js_bool(distortion_correction)};
P.generateDrizzleData   = {js_bool(generate_drizzle_data)};

var _targets = {targets_js};
var _outDir  = "{pjsr_path(output_dir)}";
var _postfix = "{output_postfix}";
var _registered = 0;

for (var _i = 0; _i < _targets.length; _i++) {{
   var _tpath = _targets[_i];
   var _tWins = ImageWindow.open(_tpath);
   if (_tWins.length == 0 || _tWins[0].isNull) {{
      Console.writeln("WARNING: could not open: " + _tpath);
      continue;
   }}
   var _tWin = _tWins[0];
   try {{
      P.executeOn(_tWin.currentView);
      var _base = _tpath.substring(_tpath.lastIndexOf("/") + 1);
      var _stem = _base.substring(0, _base.lastIndexOf("."));
      var _outPath = _outDir + "/" + _stem + _postfix + ".xisf";
      _tWin.saveAs(_outPath, false, false, false, false);
      _registered++;
      Console.writeln("Registered [" + (_i+1) + "/" + _targets.length + "]: " + _stem + _postfix + ".xisf");
   }} catch(e) {{
      Console.writeln("ERROR registering " + _tpath + ": " + e.toString());
   }} finally {{
      _tWin.forceClose();
   }}
}}

_refWin.forceClose();
Console.writeln("StarAlignment complete -- " + _registered + "/" + _targets.length + " frames registered.");
"""


def generate_star_alignment_global(
    reference_path: str,
    target_paths: list[str],
    output_dir: str,
    distortion_correction: bool = True,
    generate_drizzle_data: bool = True,
    output_postfix: str = "_r",
) -> str:
    """
    Generate a StarAlignment script using executeGlobal() with P.targets.

    This variant is required for Drizzle data generation. When `generate_drizzle_data`
    is True, StarAlignment writes .xdrz sidecar files alongside each registered frame
    in output_dir — but only when using executeGlobal(), NOT executeOn(). The
    executeOn() loop used in generate_star_alignment() does not produce .xdrz files.

    Use this function in place of generate_star_alignment() when DrizzleIntegration
    is planned. The executeOn() variant remains available as a fallback for
    PI versions where P.targets is broken.

    Args:
        reference_path:        Reference image path.
        target_paths:          List of input frame paths to register.
        output_dir:            Directory for registered output frames and .xdrz files.
        distortion_correction: Enable polynomial distortion correction.
        generate_drizzle_data: Write .xdrz sidecar files for DrizzleIntegration.
        output_postfix:        Suffix appended to registered output filenames.

    Returns:
        Complete PJSR script string.

    Notes:
        .xdrz files are written to output_dir alongside the registered .xisf frames.
        The filename pattern is <stem><postfix>.xdrz (same stem as the registered frame).
    """
    # SA targets: [[enabled, isFile, path], ...]  (3-element form for executeGlobal)
    rows = ",\n      ".join(
        f'[true, true, "{pjsr_path(p)}"]' for p in target_paths
    )
    targets_js = f"[\n      {rows}\n   ]"

    return f"""{_header("StarAlignment -- Phase 1 registration (executeGlobal with drizzle data)")}
var P = new StarAlignment;

P.referenceImage        = "{pjsr_path(reference_path)}";
P.referenceIsFile       = true;
P.distortionCorrection  = {js_bool(distortion_correction)};
P.generateDrizzleData   = {js_bool(generate_drizzle_data)};
P.outputDirectory       = "{pjsr_path(output_dir)}";
P.outputPostfix         = "{output_postfix}";
P.overwriteExistingFiles = false;

P.targets = {targets_js};

var _beforeCount = ImageWindow.windows.length;
P.executeGlobal();

Console.writeln("StarAlignment (global) complete. outputDirectory=" + P.outputDirectory);
"""


def generate_local_normalization(
    reference_path: str,
    target_paths: list[str],
    output_dir: str,
    scale: int = 128,
    output_postfix: str = "_n",
) -> str:
    """
    Generate a LocalNormalization script.

    Applied after registration, before integration. Compensates for
    per-frame sky background and throughput variations.
    """
    targets_js = js_path_array(target_paths)

    return f"""{_header("LocalNormalization -- Phase 1 pre-integration normalization (executeOn loop)")}
// Open reference as a window (same workaround as StarAlignment -- executeOn only).
var _refWins = ImageWindow.open("{pjsr_path(reference_path)}");
if (_refWins.length == 0 || _refWins[0].isNull)
    throw new Error("LocalNormalization: could not open reference: {pjsr_path(reference_path)}");
var _refWin = _refWins[0];

var P = new LocalNormalization;
P.referencePathOrViewId        = _refWin.currentView.id;
P.referenceIsView              = true;
P.scale                        = {scale};
P.noScale                      = false;
P.globalLocationNormalization  = true;
P.generateNormalizedImages     = 2;  // ViewExecutionOnly
P.outputDirectory              = "{pjsr_path(output_dir)}";
P.outputExtension              = ".xisf";
P.outputPostfix                = "{output_postfix}";

var _targets = {targets_js};
var _outDir  = "{pjsr_path(output_dir)}";
var _postfix = "{output_postfix}";
var _normalized = 0;

for (var _i = 0; _i < _targets.length; _i++) {{
   var _tpath = _targets[_i];
   var _tWins = ImageWindow.open(_tpath);
   if (_tWins.length == 0 || _tWins[0].isNull) {{
      Console.writeln("WARNING: could not open: " + _tpath);
      continue;
   }}
   var _tWin = _tWins[0];
   try {{
      P.executeOn(_tWin.currentView);
      var _base = _tpath.substring(_tpath.lastIndexOf("/") + 1);
      var _stem = _base.substring(0, _base.lastIndexOf("."));
      var _outPath = _outDir + "/" + _stem + _postfix + ".xisf";
      _tWin.saveAs(_outPath, false, false, false, false);
      _normalized++;
      Console.writeln("Normalized [" + (_i+1) + "/" + _targets.length + "]: " + _stem + _postfix + ".xisf");
   }} catch(e) {{
      Console.writeln("ERROR normalizing " + _tpath + ": " + e.toString());
   }} finally {{
      _tWin.forceClose();
   }}
}}

_refWin.forceClose();
Console.writeln("LocalNormalization complete -- " + _normalized + "/" + _targets.length + " frames normalized.");
"""


def generate_image_integration(
    image_paths: list[str],
    output_path: str,
    rejection_algorithm: str = "ESD",
    sigma_low: float = 4.0,
    sigma_high: float = 3.0,
    esd_significance: float = 0.05,
    esd_outliers_fraction: float = 0.30,
    esd_low_relaxation: float = 2.0,
    normalization: str = "AdditiveWithScaling",
    weight_mode: str = "NoiseEvaluation",
    drizzle_paths: Optional[list[str]] = None,
    generate_drizzle_output: bool = False,
) -> str:
    """
    Generate an ImageIntegration script.

    Rejection algorithm mapping (from design_doc.md):
        3-6 subs:    "PercentileClip"
        5-10 subs:   "SigmaClip"
        10-20 subs:  "WinsorizedSigmaClip"
        20-50 subs:  "LinearFitClip" or "ESD"
        50+ subs:    "ESD"  (recommended, with esd_low_relaxation=2.0 for NB)

    Large-scale rejection (satellite trails) is enabled via the
    large_scale_rejection parameter.

    Args:
        image_paths:             Registered light frame paths.
        output_path:             Output master integrated image path.
        rejection_algorithm:     One of: ESD, SigmaClip, WinsorizedSigmaClip,
                                 LinearFitClip, PercentileClip, Rejection_None.
        sigma_low/high:          Rejection thresholds for sigma-based methods.
        esd_significance:        ESD significance level (0.01-0.10; 0.05 standard).
        esd_outliers_fraction:   Max fraction of rejected pixels (0.30 = 30%).
        esd_low_relaxation:      Multiplier to relax low-side rejection (2.0 for NB
                                 to protect faint genuine signal from clipping).
        normalization:           "AdditiveWithScaling" | "Multiplicative" | "None"
        weight_mode:             "NoiseEvaluation" | "KeywordValue" | "DontCare"
        drizzle_paths:           Optional list of .xdrz sidecar paths. Required if
                                 generate_drizzle_output=True (DrizzleIntegration
                                 input).
        generate_drizzle_output: Emit .xdrz data for DrizzleIntegration.

    Returns:
        Complete PJSR script string.
    """
    # Use numeric enum values — ImageIntegration.prototype.* is not available
    # in all PI versions (returns undefined in PI 1.8.x, causing silent failures)
    rej_int    = _II_REJECTION.get(rejection_algorithm, _II_REJECTION["ESD"])
    norm_int   = _II_NORMALIZATION.get(normalization, _II_NORMALIZATION["AdditiveWithScaling"])
    weight_int = _II_WEIGHT_MODE.get(weight_mode, _II_WEIGHT_MODE["NoiseEvaluation"])

    images_js = js_integration_images(image_paths, drizzle_paths)
    out_dir   = pjsr_path(str(Path(output_path).parent))

    rej_norm_int = _II_REJECTION_NORM["Scale"]

    return f"""{_header("ImageIntegration -- Phase 1 stacking")}
var P = new ImageIntegration;

P.images = {images_js};

P.combination             = 0;           // Average
P.weightMode              = {weight_int}; // {weight_mode}
P.weightKeyword           = "SSWEIGHT";
P.normalization           = {norm_int};   // {normalization}

P.rejection               = {rej_int};   // {rejection_algorithm}
P.rejectionNormalization  = {rej_norm_int}; // Scale
P.sigmaLow                = {js_float(sigma_low)};
P.sigmaHigh               = {js_float(sigma_high)};
P.linearFitLow            = 5.000;
P.linearFitHigh           = 2.500;
P.esdOutliersFraction     = {js_float(esd_outliers_fraction)};
P.esdSignificance         = {js_float(esd_significance)};
P.esdLowRelaxation        = {js_float(esd_low_relaxation)};

P.pcClipLow               = 0.200;
P.pcClipHigh              = 0.100;
P.minMaxLow               = 1;
P.minMaxHigh              = 1;

P.generateIntegratedImage = true;
P.generateDrizzleData     = {js_bool(generate_drizzle_output)};

P.outputDirectory         = "{out_dir}";

P.executeGlobal();

// Find the integration window by ID and save it to the desired path.
// ImageWindow.windowById() does not exist in all PI versions -- iterate windows instead.
var _intId   = P.integrationImageId;  // typically "integration"
var _intWin  = null;
var _allWins = ImageWindow.windows;
for (var _j = 0; _j < _allWins.length; _j++) {{
   if (_allWins[_j].currentView.id == _intId) {{
      _intWin = _allWins[_j];
      break;
   }}
}}
if (_intWin == null || _intWin.isNull)
   throw new Error("ImageIntegration: could not find integration window (id=" + _intId + ")");
_intWin.saveAs("{pjsr_path(output_path)}", false, false, false, false);
// Close all II output windows (integration + rejection maps etc.)
for (var _k = 0; _k < _allWins.length; _k++) _allWins[_k].forceClose();
Console.writeln("ImageIntegration complete -- master saved to: {pjsr_path(output_path)}");
"""


def generate_drizzle_integration(
    image_paths: list[str],
    drizzle_paths: list[str],
    output_path: str,
    scale: float = 2.0,
    drop_shrink: float = 0.9,
    kernel: str = "Square",
) -> str:
    """
    Generate a DrizzleIntegration script for 2x upsampled integration.

    Requires dithered data with >=15 subs. Quadruples output file size
    (~800MB per channel at 2x for the ASI2600MM Pro).

    Args:
        image_paths:   Registered frame paths (same order as drizzle_paths).
        drizzle_paths: Corresponding .xdrz sidecar files from StarAlignment
                       (generate_drizzle_data must have been True).
        output_path:   Output drizzle master path.
        scale:         Drizzle scale factor. 2 is standard for mildly
                       undersampled data (0.957 arcsec/px after drizzle).
        drop_shrink:   Drop size relative to output pixel (0.9 recommended
                       for 2x; reduces aliasing vs. 1.0).
        kernel:        Kernel type: "Square" | "Circular" | "Gaussian"
                       Square is PI's default and works well for most data.

    Returns:
        Complete PJSR script string.
    """
    kernel_map = {
        "Square":   "DrizzleIntegration.prototype.Square",
        "Circular": "DrizzleIntegration.prototype.Circular",
        "Gaussian": "DrizzleIntegration.prototype.Gaussian",
    }
    kernel_proto = kernel_map.get(kernel, kernel_map["Square"])
    input_data = js_drizzle_input_array(image_paths, drizzle_paths)

    return f"""{_header("DrizzleIntegration -- Phase 1 2x drizzle stacking")}
var P = new DrizzleIntegration;

P.inputData = {input_data};

P.scale       = {js_float(scale, 1)};
P.dropShrink  = {js_float(drop_shrink)};
P.kernel      = {kernel_proto};

P.enableCFA   = false;
P.enableRejection = false;     // Rejection handled in ImageIntegration pass

P.executeGlobal();

{_find_window_by_js_expr("wOut", "P.integrationImageId", "DrizzleIntegration output")}
wOut.saveAs("{pjsr_path(output_path)}", false, false, false, false);
wOut.forceClose();
Console.writeln("DrizzleIntegration complete -- saved to: {pjsr_path(output_path)}");
"""


def generate_subframe_selector(
    frame_paths: list[str],
    output_csv: str,
    approval_expression: str = "Approved",
    weighting_expression: str = (
        "5*(1-(FWHM-FWHMMin)/(FWHMMax-FWHMMin))"
        " + 5*(1-(Eccentricity-EccentricityMin)/(EccentricityMax-EccentricityMin))"
        " + 10*(SNRWeight-SNRWeightMin)/(SNRWeightMax-SNRWeightMin)"
    ),
) -> str:
    """
    Generate a SubframeSelector script to evaluate frame quality and output a CSV.

    Runs SubframeSelector in measurement mode — no frames are rejected or moved.
    The output CSV is informational: operator reviews FWHM/eccentricity/SNR
    metrics and removes outlier frames from raw_nb/ before running NB integration.

    P.measures row format (PI 1.8.x):
        [0]  Index               — frame index into P.subframes
        [1]  Approved            — 0 or 1 per approval_expression
        [2]  FWHM                — full-width at half-maximum (pixels)
        [3]  FWHMMin             — channel minimum FWHM
        [4]  FWHMMax             — channel maximum FWHM
        [5]  Eccentricity        — star eccentricity (0=round, 1=line)
        [6]  EccentricityMin
        [7]  EccentricityMax
        [8]  PSFSignalWeight     — PSF-based signal weight
        [9]  PSFSignalWeightMin
        [10] PSFSignalWeightMax
        [11] SNRWeight           — signal-to-noise ratio weight
        [12] SNRWeightMin
        [13] SNRWeightMax
        [14] Median              — background median
        ...

    Args:
        frame_paths:           List of raw/calibrated frame paths to evaluate.
        output_csv:            Path to write the measurements CSV.
        approval_expression:   SubframeSelector approval JS expression.
        weighting_expression:  SubframeSelector weighting JS expression.

    Returns:
        Complete PJSR script string.
    """
    subframes_js = js_enabled_path_array(frame_paths)
    csv_out = pjsr_path(output_csv)

    return f"""{_header("SubframeSelector -- Phase 1 quality evaluation")}
var P = new SubframeSelector;

P.subframes = {subframes_js};

P.fileCache                  = false;
P.structureLayers            = 5;
P.noiseLayers                = 0;
P.hotPixelFilterRadius       = 1;
P.noiseReductionFilterRadius = 0;
P.sensitivity                = 0.10;
P.peakResponse               = 0.80;
P.maxDistortion              = 0.50;
P.upperLimit                 = 1.00;
P.tilingEnabled              = false;

P.approval  = "{approval_expression}";
P.weighting = "{weighting_expression}";

P.executeGlobal();
Console.writeln("SubframeSelector measurements complete. Saving CSV...");

// P.measures row: [idx, approved, FWHM, FWHMMin, FWHMMax,
//   Eccentricity, EccMin, EccMax, PSFSigWeight, PSFMin, PSFMax,
//   SNRWeight, SNRMin, SNRMax, Median, ...]
var f = new File;
f.createForWriting("{csv_out}");
f.outTextLn("Index,Path,Approved,FWHM,Eccentricity,SNRWeight");
for (var i = 0; i < P.measures.length; i++) {{
   var m = P.measures[i];
   var idx       = m[0];
   var approved  = m[1] ? "1" : "0";
   var fwhm      = m[2].toFixed(3);
   var ecc       = m[5].toFixed(4);
   var snrWeight = m[11].toFixed(4);
   var framePath = P.subframes[idx][1];
   f.outTextLn([idx, '"' + framePath + '"', approved, fwhm, ecc, snrWeight].join(","));
}}
f.close();
Console.writeln("SubframeSelector CSV saved to: {csv_out}");
"""


# =============================================================================
# Phase 2 -- Linear Processing
# =============================================================================


def generate_crop(
    input_path: str,
    output_path: str,
    crop_pixels: int = 200,
) -> str:
    """
    Generate a Crop script to remove stacking-edge artifacts.

    Uses PI's scriptable Crop process (not DynamicCrop which is GUI-only).
    Removes crop_pixels from each of the four edges uniformly. This eliminates
    coverage-gradient artifacts produced by DrizzleIntegration and StarAlignment
    at frame borders where fewer frames overlap.

    Called once per channel at BREAKPOINT 1. All NB drizzle and RGB registered
    channels receive the same crop margin so they remain pixel-aligned for all
    subsequent processing.

    Args:
        input_path:   Path to the input image.
        output_path:  Path to the cropped output image.
        crop_pixels:  Pixels to remove from each edge (applied to all 4 sides).
                      Default 200; increase if stacking artifacts remain visible
                      after reviewing BREAKPOINT 1 in PixInsight.

    Returns:
        Complete PJSR script string.
    """
    return f"""{_header("Crop -- Phase 2 stacking-edge removal")}
{_open_image("w", input_path)}
var view = w.currentView;

var P = new Crop;
P.cropMode     = Crop.prototype.AbsolutePixels;
P.leftMargin   = {crop_pixels};
P.topMargin    = {crop_pixels};
P.rightMargin  = {crop_pixels};
P.bottomMargin = {crop_pixels};
P.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("Crop complete. Removed {crop_pixels}px from each edge.");
"""


def generate_blur_xterminator(
    input_path: str,
    output_path: str,
    correct_only: bool = False,
    sharpen_stars: float = 0.25,
    sharpen_nonstellar: float = 0.40,
    adjust_halos: float = 0.05,
    automatic_psf: bool = True,
) -> str:
    """
    Generate a BlurXTerminator script.

    Call twice per the two-pass pipeline:
        Pass 1: correct_only=True  -- aberration correction only (no sharpening)
        Pass 2: correct_only=False -- full deconvolution + sharpening [BREAKPOINT 2]

    Args:
        correct_only:       True = correct PSF aberrations only, no sharpening.
                            False = full deconvolution.
        sharpen_stars:      Star sharpening amount (0-1). 0.25 is conservative
                            for 1.9 arcsec/px undersampled data.
        sharpen_nonstellar: Nebula/background sharpening (0-1). Start at 0.40,
                            increase for high-SNR data.
        adjust_halos:       Halo reduction (0-1). Keep low; >0.2 risks artifacts.
        automatic_psf:      True = BXT auto-measures the PSF. Set False only if
                            you want to supply a manual PSF diameter.

    Returns:
        Complete PJSR script string.
    """
    mode_comment = "correct-only (pass 1)" if correct_only else "sharpen (pass 2)"
    return f"""{_header(f"BlurXTerminator -- Phase 2 deconvolution [{mode_comment}]")}
{_open_image("w", input_path)}
var view = w.currentView;

var BXT = new BlurXTerminator;
BXT.correct_only        = {js_bool(correct_only)};
BXT.automatic_psf       = {js_bool(automatic_psf)};
BXT.sharpen_stars       = {js_float(sharpen_stars)};
BXT.sharpen_nonstellar  = {js_float(sharpen_nonstellar)};
BXT.adjust_halos        = {js_float(adjust_halos)};
BXT.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("BlurXTerminator [{mode_comment}] complete.");
"""


def generate_noise_xterminator(
    input_path: str,
    output_path: str,
    denoise: float = 0.80,
    detail: float = 0.15,
) -> str:
    """
    Generate a NoiseXTerminator script.

    NXT must always be applied AFTER BXT -- never before deconvolution.
    Deconvolution algorithms require intact noise statistics for regularization.

    Used in:
        Linear stage (Phase 2): denoise=0.80, detail=0.15 -- aggressive NB cleaning
        Phase 4 nonlinear uses GraXpert denoising instead (generate_graxpert_denoise).

    Args:
        denoise: Noise reduction strength (0-1). Higher values remove more noise.
        detail:  Detail preservation (0-1). Higher values preserve small-scale
                 structure at the cost of residual noise.

    Returns:
        Complete PJSR script string.
    """
    return f"""{_header("NoiseXTerminator -- Phase 2/4 noise reduction")}
{_open_image("w", input_path)}
var view = w.currentView;

var NXT = new NoiseXTerminator;
NXT.denoise  = {js_float(denoise)};
NXT.detail   = {js_float(detail)};
NXT.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("NoiseXTerminator complete. denoise={denoise}, detail={detail}");
"""


def generate_star_xterminator(
    input_path: str,
    starless_output_path: str,
    stars_output_path: Optional[str] = None,
    unscreen: bool = False,
) -> str:
    """
    Generate a StarXTerminator script.

    Called for each channel in Phase 2 (linear). The NB stars-only images
    are diagnostics only -- final star data comes from the RGB track.

    Also called in Phase 5 on the RGB composite to extract the RGB stars
    layer for screen-blend recombination.

    Args:
        input_path:            Path to the input image (channel to process).
        starless_output_path:  Output path for the starless image.
        stars_output_path:     Optional output path for the stars-only image.
                               Pass None to discard the stars image.
        unscreen:              False for linear data and clean subtraction.
                               True for nonlinear data where stars were added via
                               screen blend (inverse operation).

    Returns:
        Complete PJSR script string.
    """
    stars_enabled = stars_output_path is not None
    save_stars_block = ""
    if stars_enabled:
        save_stars_block = f"""
// Save and close the stars-only image created by SXT.
// SXT names the stars window <source_view_id>_stars. We captured the source
// view ID before closing the starless window so we can find it here.
var _starsId = _srcViewId + "_stars";
var wStars = null;
{{ var _ws = ImageWindow.windows; for (var _wi = 0; _wi < _ws.length; _wi++) {{ if (_ws[_wi].currentView.id == _starsId) {{ wStars = _ws[_wi]; break; }} }} }}
if (wStars != null && !wStars.isNull) {{
   wStars.saveAs("{pjsr_path(stars_output_path)}", false, false, false, false);
   wStars.forceClose();
}} else {{
   Console.warningln("SXT: stars-only window not found (expected ID=" + _starsId + ").");
}}"""

    return f"""{_header("StarXTerminator -- Phase 2/5 star removal")}
{_open_image("w", input_path)}
var view = w.currentView;
// Capture view ID before SXT/close so we can find the stars window afterwards.
var _srcViewId = view.id;

var SXT = new StarXTerminator;
SXT.stars    = {js_bool(stars_enabled)};   // generate stars-only output image
SXT.unscreen = {js_bool(unscreen)};
SXT.executeOn(view);

// The view now contains the starless image; save it
{_save_image("w", starless_output_path)}
{_close_image("w")}
{save_stars_block}
Console.writeln("StarXTerminator complete.");
"""


def generate_channel_extraction(
    input_path: str,
    output_paths: dict[str, str],
    view_ids: Optional[dict[str, str]] = None,
) -> str:
    """
    Generate a ChannelExtraction script that splits an RGB image into channels.

    In Phase 2, this splits the SHO combined image (R=SII, G=Ha, B=OIII)
    back into separate channels after BXT+NXT processing.

    Args:
        input_path:   Path to the combined RGB image to extract from.
        output_paths: Dict mapping channel key to output path.
                      Expected keys: "R", "G", "B".
        view_ids:     Optional dict mapping channel key to the view ID assigned
                      to the extracted channel window. Defaults to
                      {"R": "<stem>_R", "G": "<stem>_G", "B": "<stem>_B"}.

    Returns:
        Complete PJSR script string.
    """
    stem = Path(input_path).stem
    default_ids = {"R": f"{stem}_R", "G": f"{stem}_G", "B": f"{stem}_B"}
    ids = view_ids or default_ids

    r_id = ids.get("R", default_ids["R"])
    g_id = ids.get("G", default_ids["G"])
    b_id = ids.get("B", default_ids["B"])

    r_out = output_paths.get("R", "")
    g_out = output_paths.get("G", "")
    b_out = output_paths.get("B", "")

    save_r = _save_by_id(r_id, r_out) if r_out else "// R not saved"
    save_g = _save_by_id(g_id, g_out) if g_out else "// G not saved"
    save_b = _save_by_id(b_id, b_out) if b_out else "// B not saved"

    return f"""{_header("ChannelExtraction -- Phase 2 channel split")}
{_open_image("w", input_path)}
var view = w.currentView;

var CE = new ChannelExtraction;
CE.colorSpace   = ChannelExtraction.prototype.RGB;
CE.channels     = [
   [true, "{r_id}"],   // R channel
   [true, "{g_id}"],   // G channel
   [true, "{b_id}"]    // B channel
];
CE.sampleFormat = ChannelExtraction.prototype.SameAsSource;
CE.executeOn(view);

// Input window is now the first extracted channel (PI behavior).
// The other two channels are new windows; all are saved below.
{_close_image("w")}

// Save extracted channels
{save_r}
{save_g}
{save_b}

// Close extracted channel windows
{_close_by_id(r_id)}
{_close_by_id(g_id)}
{_close_by_id(b_id)}
Console.writeln("ChannelExtraction complete.");
"""


def generate_channel_combination(
    r_path: str,
    g_path: str,
    b_path: str,
    output_path: str,
    output_id: str = "RGB_combined",
) -> str:
    """
    Generate a ChannelCombination script to create an RGB composite.

    Used in Phase 5 to combine the registered R, G, B masters into
    a single RGB image before SPCC.

    Returns:
        Complete PJSR script string.
    """
    return f"""{_header("ChannelCombination (via PixelMath) -- Phase 5 RGB composite")}
// ChannelCombination.channels setter is broken in PI 1.8.x automation mode.
// Use PixelMath with explicit dimensions instead -- proven to work.
{_open_image("wR", r_path)}
{_open_image("wG", g_path)}
{_open_image("wB", b_path)}

var _srcImg = wR.currentView.image;
var _W = _srcImg.width;
var _H = _srcImg.height;

var PM = new PixelMath;
PM.expression  = wR.currentView.id;   // R channel
PM.expression1 = wG.currentView.id;   // G channel
PM.expression2 = wB.currentView.id;   // B channel
PM.useSingleExpression  = false;
PM.createNewImage       = true;
PM.newImageId           = "{output_id}";
PM.newImageWidth        = _W;
PM.newImageHeight       = _H;
PM.newImageColorSpace   = PixelMath.prototype.RGB;
PM.newImageSampleFormat = PixelMath.prototype.f32;
PM.executeGlobal();

{_find_window_by_literal_id("wOut", output_id)}
{_save_image("wOut", output_path)}
{_close_image("wOut")}

{_close_image("wR")}
{_close_image("wG")}
{_close_image("wB")}
Console.writeln("RGB ChannelCombination complete -- saved to: {pjsr_path(output_path)}");
"""


def generate_linear_fit(
    target_paths: list[str],
    output_paths: list[str],
    reference_path: str,
    reject_low: float = 0.0,
    reject_high: float = 0.92,
) -> str:
    """
    Generate a LinearFit script to normalize channel brightnesses.

    In Phase 3 before Foraxx combination, LinearFit normalizes Ha and SII
    to the OIII reference (weakest channel). This prevents Ha from dominating
    the combination and ensures OIII structure is visible.

    Per design_doc.md Section 12: reference = OIII (weakest channel).

    Args:
        target_paths:   List of channel paths to normalize (Ha, SII).
        output_paths:   Output paths for normalized channels (same order).
        reference_path: Path to the reference channel (OIII_starless_stretched).
        reject_low:     Low-end rejection fraction for statistics calculation.
        reject_high:    High-end rejection fraction (0.92 avoids bright star bias).

    Returns:
        Complete PJSR script string.
    """
    ref_id = Path(reference_path).stem

    blocks = []
    for i, (tgt, out) in enumerate(zip(target_paths, output_paths)):
        var = f"wTarget{i}"
        blocks.append(f"""\
// LinearFit target {i+1}: {Path(tgt).name}
{_open_image(var, tgt)}
var refWindow = ImageWindow.open("{pjsr_path(reference_path)}")[0];
refWindow.currentView.id = "{ref_id}";

var LF = new LinearFit;
LF.referenceViewId = "{ref_id}";
LF.rejectLow       = {js_float(reject_low)};
LF.rejectHigh      = {js_float(reject_high)};
LF.executeOn({var}.currentView);

{_save_image(var, out)}
{_close_image(var)}
refWindow.forceClose();
""")

    return f"""{_header("LinearFit -- Phase 3 channel normalization to OIII reference")}
{"".join(blocks)}
Console.writeln("LinearFit normalization complete.");
"""


# =============================================================================
# Phase 3 -- Stretching and Palette Combination
# =============================================================================


def generate_histogram_stats(
    input_path: str,
    output_json_path: str,
    channel_id: str = "unknown",
) -> str:
    """
    Generate a PJSR script that measures image statistics and writes them to JSON.

    Measures background median and mean of a linear monochrome image using
    PI's built-in image statistics. Results are written as a JSON file that
    Python can read to determine per-channel GHS SP (symmetry point).

    For linear narrowband data after background extraction, the median is a
    good proxy for the histogram peak (background pedestal level). Setting
    GHS SP to this value anchors the stretch transition at the correct
    brightness level for each channel independently.

    Args:
        input_path:       Absolute path to the input image (XISF/FITS).
        output_json_path: Path for the JSON statistics output file.
        channel_id:       Label for the channel (e.g. "Ha", "OIII", "SII").

    Returns:
        Complete PJSR script string.

    JSON output format:
        {
            "channel": "Ha",
            "median": 0.000287,
            "mean": 0.000312,
            "stddev": 0.000094,
            "min": 0.0,
            "max": 0.87
        }
    """
    return f"""{_header("Histogram statistics measurement for GHS SP calibration")}
{_open_image("w", input_path)}
var view = w.currentView;
var img  = view.image;

// Compute per-image statistics (monochrome NB channel -- single channel)
var med    = img.median();
var mean   = img.mean();
var stddev = img.stdDev();
var minVal = img.minimum();
var maxVal = img.maximum();

// Write JSON to disk
var jsonStr = "{{\\n"
    + "  \\"channel\\": \\"{channel_id}\\",\\n"
    + "  \\"median\\": " + med.toFixed(8) + ",\\n"
    + "  \\"mean\\": " + mean.toFixed(8) + ",\\n"
    + "  \\"stddev\\": " + stddev.toFixed(8) + ",\\n"
    + "  \\"min\\": " + minVal.toFixed(8) + ",\\n"
    + "  \\"max\\": " + maxVal.toFixed(8) + "\\n"
    + "}}\\n";

var f = new File;
f.createForWriting("{pjsr_path(output_json_path)}");
f.outTextLn(jsonStr);
f.close();

{_close_image("w")}
Console.writeln("Stats for {channel_id}: median=" + med.toFixed(6) + ", mean=" + mean.toFixed(6));
"""


def generate_ghs_stretch(
    input_path: str,
    output_path: str,
    D: float = 5.0,
    b: float = 2.0,
    SP: float = 0.0001,
) -> str:
    """
    Generate a GeneralizedHyperbolicStretch script.

    GHS is the recommended stretch method for the Foraxx pipeline because it
    can stretch faint OIII without blowing out the bright Ha core. Parameters
    should be tuned per-channel at BREAKPOINT 3.

    Args:
        D:  Stretch factor (>=1). Higher = more stretch. Start ~5 for NB;
            reduce to ~3 for RGB stars (already bright).
        b:  Shape parameter. Controls the transition between linear and
            hyperbolic regions. b=2 is a reasonable starting point.
        SP: Symmetry point -- set to the histogram peak of the input image
            (typically ~0.0001 for dark linear NB data).

    Returns:
        Complete PJSR script string.
    """
    return f"""{_header("GeneralizedHyperbolicStretch -- Phase 3/5 stretch")}
{_open_image("w", input_path)}
var view = w.currentView;

var GHS = new GeneralizedHyperbolicStretch;
GHS.stretchType          = GeneralizedHyperbolicStretch.prototype.ST_GeneralisedHyperbolic;
GHS.stretchFactor        = {js_float(D, 4)};   // Stretch factor (local gradient at SP)
GHS.localIntensity       = {js_float(b, 4)};   // Shape / hardness parameter
GHS.symmetryPoint        = {js_float(SP, 6)};  // Symmetry point (histogram peak)
GHS.shadowProtection     = 0.000000;           // Lower protection boundary
GHS.highlightProtection  = 1.000000;           // Upper protection boundary
GHS.blackPoint           = 0.000000;           // Black point
GHS.whitePoint           = 1.000000;           // White point
GHS.inverse              = false;
GHS.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("GHS stretch complete. D={D}, b={b}, SP={SP}");
"""


def generate_histogram_stretch(
    input_path: str,
    output_path: str,
    shadows_clip: float = 0.0,
    midtones: float = 0.10,
    highlights_clip: float = 1.0,
) -> str:
    """
    Generate a HistogramTransformation stretch script.

    Simpler fallback if GHS is unavailable. Set shadows_clip to just below
    the histogram peak (background value), then adjust midtones left.

    Args:
        shadows_clip:    Black point. Set to background median.
        midtones:        Midtone balance (0-1; lower = brighter stretch).
        highlights_clip: White point. Usually 1.0 unless stars are blown.

    Returns:
        Complete PJSR script string.
    """
    return f"""{_header("HistogramTransformation -- Phase 3 stretch (fallback)")}
{_open_image("w", input_path)}
var view = w.currentView;

var HT = new HistogramTransformation;
// channels: [[m, clip_low, clip_high, low, high], ...] for R, G, B, L, A
HT.H = [
   [{js_float(midtones, 6)}, {js_float(shadows_clip, 6)}, {js_float(highlights_clip, 6)}, 0.000000, 1.000000],
   [{js_float(midtones, 6)}, {js_float(shadows_clip, 6)}, {js_float(highlights_clip, 6)}, 0.000000, 1.000000],
   [{js_float(midtones, 6)}, {js_float(shadows_clip, 6)}, {js_float(highlights_clip, 6)}, 0.000000, 1.000000],
   [0.500000, 0.000000, 1.000000, 0.000000, 1.000000],  // Luminance (all channels)
   [0.500000, 0.000000, 1.000000, 0.000000, 1.000000]   // Alpha
];
HT.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("HistogramTransformation stretch complete.");
"""


def generate_foraxx_palette(
    ha_path: str,
    sii_path: str,
    oiii_path: str,
    output_path: str,
    output_id: str = "SHO_Foraxx",
) -> str:
    """
    Generate the Foraxx dynamic SHO palette combination script.

    The Foraxx palette uses Power of Inverted Pixels (PIP) weighting to
    dynamically blend SII/Ha in the red channel based on local OIII intensity.
    This produces gold/cyan Hubble-like colors without the overwhelming green
    cast of standard SHO.

    PixelMath expressions (from design_doc.md):
        R = (Oiii ^ ~Oiii) * Sii + ~(Oiii ^ ~Oiii) * Ha
        G = ((Oiii*Ha) ^ ~(Oiii*Ha)) * Ha + ~((Oiii*Ha) ^ ~(Oiii*Ha)) * Oiii
        B = Oiii

    Where ~ is (1-x) and ^ is the power operator.

    IMPORTANT: All three input images must be stretched (nonlinear) before
    running this script. The Foraxx formula does not work on linear data.

    Args:
        ha_path:    Stretched, starless Ha channel path.
        sii_path:   Stretched, starless SII channel path.
        oiii_path:  Stretched, starless OIII channel path.
        output_path: Output SHO Foraxx image path.
        output_id:  View ID for the newly created PixelMath output window.

    Returns:
        Complete PJSR script string.
    """
    return f"""{_header("Foraxx dynamic SHO palette -- Phase 3 palette combination")}
// Open all three channels with view IDs matching the PixelMath expressions
{_open_image("wHa",   ha_path,   view_id="Ha")}
{_open_image("wSii",  sii_path,  view_id="Sii")}
{_open_image("wOiii", oiii_path, view_id="Oiii")}

// Read source dimensions -- required for PixelMath.executeGlobal() with createNewImage.
var _srcImg = wHa.currentView.image;
var _W = _srcImg.width;
var _H = _srcImg.height;

var PM = new PixelMath;

// Foraxx dynamic SHO expressions:
//   ~ = (1-x)  [Inverted Pixels]
//   ^ = power operator
//   R: PIP(Oiii) * Sii + (1-PIP(Oiii)) * Ha
//   G: PIP(Oiii*Ha) * Ha + (1-PIP(Oiii*Ha)) * Oiii
//   B: Oiii
PM.expression  = "(Oiii^~Oiii)*Sii + ~(Oiii^~Oiii)*Ha";
PM.expression1 = "((Oiii*Ha)^~(Oiii*Ha))*Ha + ~((Oiii*Ha)^~(Oiii*Ha))*Oiii";
PM.expression2 = "Oiii";
PM.expression3 = "";

PM.useSingleExpression  = false;
PM.createNewImage       = true;
PM.newImageId           = "{output_id}";
PM.newImageWidth        = _W;
PM.newImageHeight       = _H;
PM.newImageColorSpace   = PixelMath.prototype.RGB;
PM.newImageSampleFormat = PixelMath.prototype.f32;

PM.executeGlobal();

// Save and close the result
{_find_window_by_literal_id("wOut", output_id)}
{_save_image("wOut", output_path)}
{_close_image("wOut")}

// Close input images
{_close_image("wHa")}
{_close_image("wSii")}
{_close_image("wOiii")}
Console.writeln("Foraxx palette combination complete.");
"""


def generate_sho_linear_combine(
    ha_path: str,
    sii_path: str,
    oiii_path: str,
    output_path: str,
    output_id: str = "SHO_linear",
) -> str:
    """
    Generate the equal-weight SHO combination script for BXT input.

    Combines three NB channels into a temporary RGB image for BXT processing.
    BXT's AI model was trained on color images and produces better results on
    combined data than individual channels.

    Channel mapping:  R = SII,  G = Ha,  B = OIII  (Hubble palette order)
    """
    return f"""{_header("Equal-weight SHO combination for BXT -- Phase 2")}
// Channel mapping: R = SII,  G = Ha,  B = OIII  (Hubble palette / BXT input)
// Open source images to read dimensions and get their view IDs for PixelMath.
{_open_image("wSii",  sii_path)}
{_open_image("wHa",   ha_path)}
{_open_image("wOiii", oiii_path)}

// Read source dimensions -- required for PixelMath.executeGlobal() with createNewImage.
var _srcImg = wSii.currentView.image;
var _W = _srcImg.width;
var _H = _srcImg.height;

var PM = new PixelMath;
// Expressions reference the view IDs of the opened images.
PM.expression  = wSii.currentView.id;   // R = SII
PM.expression1 = wHa.currentView.id;    // G = Ha
PM.expression2 = wOiii.currentView.id;  // B = OIII
PM.useSingleExpression  = false;
PM.createNewImage       = true;
PM.newImageId           = "{output_id}";
PM.newImageWidth        = _W;
PM.newImageHeight       = _H;
PM.newImageColorSpace   = PixelMath.prototype.RGB;
PM.newImageSampleFormat = PixelMath.prototype.f32;
PM.executeGlobal();

// Find the new window and save it.
{_find_window_by_literal_id("wOut", output_id)}
wOut.saveAs("{pjsr_path(output_path)}", false, false, false, false);
wOut.forceClose();

{_close_image("wSii")}
{_close_image("wHa")}
{_close_image("wOiii")}
Console.writeln("SHO linear combination complete -- saved to: {pjsr_path(output_path)}");
"""


# =============================================================================
# Phase 4 -- Nonlinear Processing
# =============================================================================


def generate_scnr(
    input_path: str,
    output_path: str,
    amount: float = 0.65,
    protection_method: str = "MaximumMask",
) -> str:
    """
    Generate a SCNR (Subtractive Chromatic Noise Reduction) script.

    Ha is mapped to the green channel in SHO, creating a dominant green cast.
    SCNR removes this. Apply after stretching (nonlinear). The MaximumMask
    protection method prevents desaturating non-green hues.

    Args:
        amount:            Removal strength (0-1). 0.65 is a common starting point.
        protection_method: "MaximumMask" | "AverageNeutral" | "MaximumNeutral"

    Returns:
        Complete PJSR script string.
    """
    method_map = {
        "MaximumMask":    "SCNR.prototype.MaximumMask",
        "AverageNeutral": "SCNR.prototype.AverageNeutral",
        "MaximumNeutral": "SCNR.prototype.MaximumNeutral",
    }
    method_proto = method_map.get(protection_method, method_map["MaximumMask"])

    return f"""{_header("SCNR green removal -- Phase 4")}
{_open_image("w", input_path)}
var view = w.currentView;

var P = new SCNR;
P.amount              = {js_float(amount)};
P.protectionMethod    = {method_proto};
P.colorToRemove       = SCNR.prototype.Green;
P.preserveLuminance   = true;
P.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("SCNR complete. amount={amount}");
"""


def generate_curves_saturation_contrast(
    input_path: str,
    output_path: str,
    saturation_points: Optional[list[tuple]] = None,
    luminance_points: Optional[list[tuple]] = None,
) -> str:
    """
    Generate a CurvesTransformation script for saturation and contrast.

    Default curves provide a mild S-curve contrast boost with saturation
    increase. Tune at BREAKPOINT 4 (color_grading).

    Args:
        saturation_points: List of (x, y) control points for saturation curve.
                           Defaults to moderate boost [(0,0), (0.5,0.65), (1,1)].
        luminance_points:  List of (x, y) control points for luminance/contrast.
                           Defaults to mild S-curve.

    Returns:
        Complete PJSR script string.
    """
    if saturation_points is None:
        saturation_points = [(0.0, 0.0), (0.50, 0.65), (1.0, 1.0)]
    if luminance_points is None:
        luminance_points = [(0.0, 0.0), (0.20, 0.17), (0.50, 0.50), (0.80, 0.85), (1.0, 1.0)]

    def fmt_points(pts: list) -> str:
        return "[" + ", ".join(f"[{x}, {y}]" for x, y in pts) + "]"

    return f"""{_header("CurvesTransformation saturation/contrast -- Phase 4")}
{_open_image("w", input_path)}
var view = w.currentView;

var CT = new CurvesTransformation;

// S = saturation curve
CT.S = {fmt_points(saturation_points)};

// K = luminance/contrast curve (all channels combined)
CT.K = {fmt_points(luminance_points)};

// Identity curves for individual RGB channels (adjust for color grading)
CT.R = [[0, 0], [1, 1]];
CT.G = [[0, 0], [1, 1]];
CT.B = [[0, 0], [1, 1]];

CT.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("CurvesTransformation saturation/contrast complete.");
"""


def generate_curves_hue_shift(
    input_path: str,
    output_path: str,
    hue_points: Optional[list[tuple]] = None,
) -> str:
    """
    Generate a CurvesTransformation script for hue adjustment.

    Shifts residual green hues toward gold/orange. The exact curve depends
    on the image -- this is a BREAKPOINT 4 manual adjustment. The default
    provides a starting point that shifts the green range toward yellow.

    Args:
        hue_points: List of (input_hue, output_hue) control points in [0,1]
                    where 0/1 = 0/360 degrees.
                    Default shifts green (0.33) slightly toward yellow (0.28).

    Returns:
        Complete PJSR script string.
    """
    if hue_points is None:
        # Shift green-yellow range toward gold/amber
        hue_points = [
            (0.00, 0.00),   # Red: unchanged
            (0.15, 0.14),   # Orange-yellow: slight shift
            (0.33, 0.28),   # Green: shift toward yellow-orange
            (0.50, 0.50),   # Cyan: unchanged
            (0.67, 0.67),   # Blue: unchanged
            (0.83, 0.83),   # Magenta: unchanged
            (1.00, 1.00),   # Red (wrap): unchanged
        ]

    def fmt_points(pts: list) -> str:
        return "[" + ", ".join(f"[{x}, {y}]" for x, y in pts) + "]"

    return f"""{_header("CurvesTransformation hue shift -- Phase 4 [BREAKPOINT 4]")}
// NOTE: This is a starting point. Tune the H curve at BREAKPOINT 4 to taste.
// Hue values are in [0,1] where 0/1 = red, 0.33 = green, 0.67 = blue.
{_open_image("w", input_path)}
var view = w.currentView;

var CT = new CurvesTransformation;

// H = hue vs. hue curve
CT.H = {fmt_points(hue_points)};

// Leave all other curves at identity
CT.R = [[0, 0], [1, 1]];
CT.G = [[0, 0], [1, 1]];
CT.B = [[0, 0], [1, 1]];
CT.K = [[0, 0], [1, 1]];
CT.S = [[0, 0], [1, 1]];

CT.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("CurvesTransformation hue shift complete.");
"""


def generate_hdr_multiscale(
    input_path: str,
    output_path: str,
    number_of_layers: int = 6,
    number_of_iterations: int = 1,
) -> str:
    """
    Generate an HDRMultiscaleTransform script.

    Compresses dynamic range in NGC 1499's bright central Ha ridge while
    preserving faint OIII structure in the outer envelope. Apply with a
    luminance mask protecting the background (mask application is done in
    the calling stage -- scripts receive pre-masked views or use MaskEditor).

    Args:
        number_of_layers:     Wavelet layers (1-8). 6 is recommended for
                              large-scale dynamic range compression.
        number_of_iterations: Iterations per pass. 1 is usually sufficient.

    Returns:
        Complete PJSR script string.
    """
    return f"""{_header("HDRMultiscaleTransform -- Phase 4 dynamic range compression")}
{_open_image("w", input_path)}
var view = w.currentView;

var HDRMT = new HDRMultiscaleTransform;
HDRMT.numberOfLayers     = {number_of_layers};
HDRMT.numberOfIterations = {number_of_iterations};
HDRMT.invertedIterations = true;
HDRMT.overdrive          = 0.000;
HDRMT.medianTransform    = false;
HDRMT.scalingFunctionData = [];
HDRMT.smallScaleFunction = HDRMultiscaleTransform.prototype.B3Spline5x5;
HDRMT.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("HDRMultiscaleTransform complete. layers={number_of_layers}");
"""


def generate_local_histogram_equalization(
    input_path: str,
    output_path: str,
    kernel_radius: int = 96,
    contrast_limit: float = 2.0,
    amount: float = 0.35,
) -> str:
    """
    Generate a LocalHistogramEqualization script.

    Boosts local contrast in nebula structure. Apply with a luminance mask
    that protects dark background regions (prevents noise amplification).

    Args:
        kernel_radius:  Pixel radius of the local equalization kernel.
                        96 px covers ~3 arcmin at 1.9 arcsec/px, appropriate
                        for NGC 1499's large-scale filamentary structure.
        contrast_limit: CLAHE contrast limit. 2.0 avoids over-processing.
        amount:         Blend amount (0-1). 0.35 = 35% LHE mixed with original.

    Returns:
        Complete PJSR script string.
    """
    return f"""{_header("LocalHistogramEqualization -- Phase 4 local contrast")}
{_open_image("w", input_path)}
var view = w.currentView;

var LHE = new LocalHistogramEqualization;
LHE.radius        = {kernel_radius};
LHE.histogramBins = LocalHistogramEqualization.prototype.Bit8;
LHE.slopeLimit    = {js_float(contrast_limit)};
LHE.amount        = {js_float(amount)};
LHE.circular      = true;
LHE.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("LocalHistogramEqualization complete. radius={kernel_radius}, limit={contrast_limit}");
"""


# =============================================================================
# Quality Analysis
# =============================================================================


def generate_quality_report(
    input_path: str,
    output_json_path: str,
    stage_label: str = "unknown",
) -> str:
    """
    Generate a PJSR script that measures channel balance metrics on an RGB image.

    Extracts per-channel statistics (mean, median, std) and computes channel
    balance ratios that correlate with human aesthetic judgments about color
    balance. The dominant failure mode for SHO images (blue/purple cast) maps
    to B/G > 1.3 or B/R > 1.5.

    Args:
        input_path:       Path to an RGB XISF/FITS image to analyze.
        output_json_path: Path for JSON metrics output.
        stage_label:      Label identifying when in the pipeline this runs.

    Returns:
        Complete PJSR script string.

    JSON output format:
        {{
            "stage": "after_foraxx",
            "r_mean": 0.312,  "r_median": 0.289,
            "g_mean": 0.298,  "g_median": 0.271,
            "b_mean": 0.345,  "b_median": 0.318,
            "ratio_rg": 1.047, "ratio_bg": 1.158,
            "color_cast_score": 0.072,
            "background_clip_fraction": 0.001,
            "highlight_clip_fraction": 0.0002,
            "quality_warnings": ["B/G ratio 1.158 exceeds threshold 1.30"]
        }}
    """
    return f"""{_header("Quality Report -- channel balance and color metrics")}
{_open_image("w", input_path)}
var view = w.currentView;
var img  = view.image;
var nCh  = img.numberOfChannels;

// ── Extract per-channel stats via ChannelExtraction ──────────────────────────
// Create temporary grayscale views for each channel so we can call scalar stats
var CE = new ChannelExtraction;
CE.colorSpace = ChannelExtraction.prototype.RGB;
CE.channels = [
   [true, "qr_ch_R"],
   [true, "qr_ch_G"],
   [true, "qr_ch_B"]
];
CE.executeOn(view);

// ── Helper: find window by ID ─────────────────────────────────────────────────
function findWin(id) {{
   var all = ImageWindow.windows;
   for (var i = 0; i < all.length; i++) if (all[i].currentView.id == id) return all[i];
   return null;
}}

var wR = findWin("qr_ch_R");
var wG = findWin("qr_ch_G");
var wB = findWin("qr_ch_B");

if (!wR || !wG || !wB) throw new Error("QualityReport: channel extraction failed");

var rMean   = wR.currentView.image.mean();
var rMed    = wR.currentView.image.median();
var rStd    = wR.currentView.image.stdDev();
var gMean   = wG.currentView.image.mean();
var gMed    = wG.currentView.image.median();
var gStd    = wG.currentView.image.stdDev();
var bMean   = wB.currentView.image.mean();
var bMed    = wB.currentView.image.median();
var bStd    = wB.currentView.image.stdDev();

wR.forceClose(); wG.forceClose(); wB.forceClose();

// ── Channel balance ratios ────────────────────────────────────────────────────
var ratioRG = (gMean > 0) ? rMean / gMean : 0;
var ratioBG = (gMean > 0) ? bMean / gMean : 0;
var chanAvg = (rMean + gMean + bMean) / 3.0;
var chanVar = ((rMean-chanAvg)*(rMean-chanAvg) + (gMean-chanAvg)*(gMean-chanAvg) + (bMean-chanAvg)*(bMean-chanAvg)) / 3.0;
var colorCastScore = (chanAvg > 0) ? Math.sqrt(chanVar) / chanAvg : 0;

// ── Quality warnings ──────────────────────────────────────────────────────────
var warnings = [];
if (ratioBG > 1.30) warnings.push("B/G=" + ratioBG.toFixed(3) + " >1.30 (blue cast)");
if (ratioBG < 0.70) warnings.push("B/G=" + ratioBG.toFixed(3) + " <0.70 (red/orange cast)");
if (ratioRG < 0.75) warnings.push("R/G=" + ratioRG.toFixed(3) + " <0.75 (Ha suppressed)");
if (ratioRG > 1.30) warnings.push("R/G=" + ratioRG.toFixed(3) + " >1.30 (Ha dominant)");
if (colorCastScore > 0.30) warnings.push("color_cast_score=" + colorCastScore.toFixed(3) + " >0.30 (strong cast)");

// ── Build JSON ────────────────────────────────────────────────────────────────
var warnJsonArr = "[";
for (var _wi = 0; _wi < warnings.length; _wi++) {{
   warnJsonArr += (_wi > 0 ? ", " : "") + "\\"" + warnings[_wi] + "\\"";
}}
warnJsonArr += "]";

var jsonStr = "{{\\n"
    + "  \\"stage\\": \\"{stage_label}\\",\\n"
    + "  \\"r_mean\\": " + rMean.toFixed(6) + ",\\n"
    + "  \\"r_median\\": " + rMed.toFixed(6) + ",\\n"
    + "  \\"r_std\\": " + rStd.toFixed(6) + ",\\n"
    + "  \\"g_mean\\": " + gMean.toFixed(6) + ",\\n"
    + "  \\"g_median\\": " + gMed.toFixed(6) + ",\\n"
    + "  \\"g_std\\": " + gStd.toFixed(6) + ",\\n"
    + "  \\"b_mean\\": " + bMean.toFixed(6) + ",\\n"
    + "  \\"b_median\\": " + bMed.toFixed(6) + ",\\n"
    + "  \\"b_std\\": " + bStd.toFixed(6) + ",\\n"
    + "  \\"ratio_rg\\": " + ratioRG.toFixed(6) + ",\\n"
    + "  \\"ratio_bg\\": " + ratioBG.toFixed(6) + ",\\n"
    + "  \\"color_cast_score\\": " + colorCastScore.toFixed(6) + ",\\n"
    + "  \\"quality_warnings\\": " + warnJsonArr + "\\n"
    + "}}\\n";

var f = new File;
f.createForWriting("{pjsr_path(output_json_path)}");
f.outTextLn(jsonStr);
f.close();

{_close_image("w")}

// Print summary to PI console
Console.writeln("Quality[{stage_label}]: R/G=" + ratioRG.toFixed(3) + " B/G=" + ratioBG.toFixed(3) + " cast=" + colorCastScore.toFixed(3));
if (warnings.length > 0) {{
   Console.writeln("  QUALITY_WARN: " + warnings.join("; "));
}} else {{
   Console.writeln("  QUALITY_OK: channel balance within thresholds");
}}
"""


def generate_hue_analysis(
    input_path: str,
    output_json_path: str,
    stage_label: str = "unknown",
    sample_fraction: float = 0.05,
) -> str:
    """
    Generate a PJSR script that analyzes the hue distribution of a stretched RGB image.

    Converts the image to HSV color space and measures the fraction of pixels in
    specific hue ranges that correspond to aesthetically important color zones for
    SHO palette images:

        Gold/amber   0–50°    Ha-dominant nebula filaments (target: largest peak)
        Green-yellow 50–110°  Transition zone (SCNR should have suppressed this)
        Cyan/teal    150–210° OIII-dominant regions
        Blue/purple  220–280° Pathological zone (target: near zero)
        Red          310–360° Minority hue from SII residuals

    The hue histogram is estimated via random pixel sampling to avoid processing
    the full multi-hundred-MB image in a slow PJSR loop.

    Args:
        input_path:      Path to a stretched RGB SHO image.
        output_json_path: JSON output path.
        stage_label:     Label for the pipeline stage.
        sample_fraction: Fraction of pixels to sample (0.05 = 5%, ~1M px for 2x drizzle).

    Returns:
        Complete PJSR script string.

    JSON output:
        {{
            "stage": "after_foraxx",
            "hue_gold_amber_pct":    45.2,   // 0–50° target largest
            "hue_green_yellow_pct":   8.1,   // 50–110° should be low post-SCNR
            "hue_cyan_teal_pct":     18.7,   // 150–210° good OIII contribution
            "hue_blue_purple_pct":   12.4,   // 220–280° should be near zero
            "dominant_hue_zone":     "gold_amber",
            "pathological_blue_purple": false,
            "quality_warnings": ["blue/purple 12.4% exceeds 5% threshold"]
        }}
    """
    return f"""{_header("Hue distribution analysis for SHO aesthetic quality")}
{_open_image("w", input_path)}
var view = w.currentView;
var img  = view.image;
var nPx  = img.width * img.height;

// Sample a random subset of pixels for hue analysis (avoid full scan on 800MB images)
var sampleN = Math.floor(nPx * {js_float(sample_fraction, 4)});
sampleN = Math.min(sampleN, 500000);  // cap at 500k samples

// Hue zone accumulators (degrees: 0-360)
var cntGold   = 0;  // 0-50: gold/amber (Ha)
var cntGreen  = 0;  // 50-110: green-yellow (should be SCNR'd)
var cntCyan   = 0;  // 150-210: cyan/teal (OIII)
var cntBlue   = 0;  // 220-280: blue/purple (pathological)
var cntRed    = 0;  // 310-360 or 0-10: red (SII residual)
var cntOther  = 0;  // everything else

// Sample random pixel positions
var rng = Math.random;
for (var _i = 0; _i < sampleN; _i++) {{
   var px = Math.floor(rng() * img.width);
   var py = Math.floor(rng() * img.height);

   var r = img.sample(px, py, 0);
   var g = img.sample(px, py, 1);
   var b = img.sample(px, py, 2);

   // Skip near-black background pixels (not informative for color distribution)
   var luma = 0.2126*r + 0.7152*g + 0.0722*b;
   if (luma < 0.05) continue;

   // RGB to HSV hue calculation
   var maxC = Math.max(r, g, b);
   var minC = Math.min(r, g, b);
   var delta = maxC - minC;
   if (delta < 1e-6) {{ cntOther++; continue; }}  // achromatic

   var h;
   if (maxC == r) {{
      h = 60.0 * (((g - b) / delta) % 6);
   }} else if (maxC == g) {{
      h = 60.0 * ((b - r) / delta + 2.0);
   }} else {{
      h = 60.0 * ((r - g) / delta + 4.0);
   }}
   if (h < 0) h += 360.0;

   // Classify into hue zones
   if      (h <  50) cntGold++;
   else if (h < 110) cntGreen++;
   else if (h < 150) cntOther++;
   else if (h < 210) cntCyan++;
   else if (h < 220) cntOther++;
   else if (h < 280) cntBlue++;
   else if (h < 310) cntOther++;
   else              cntRed++;
}}

var sampled = cntGold + cntGreen + cntCyan + cntBlue + cntRed + cntOther;
var safeN   = Math.max(sampled, 1);

var pctGold  = 100.0 * cntGold  / safeN;
var pctGreen = 100.0 * cntGreen / safeN;
var pctCyan  = 100.0 * cntCyan  / safeN;
var pctBlue  = 100.0 * cntBlue  / safeN;
var pctRed   = 100.0 * cntRed   / safeN;

// Determine dominant zone
var zones = [["gold_amber", pctGold], ["green_yellow", pctGreen], ["cyan_teal", pctCyan], ["blue_purple", pctBlue], ["red", pctRed]];
var domZone = "other";
var domPct  = 0;
for (var _z = 0; _z < zones.length; _z++) {{
   if (zones[_z][1] > domPct) {{ domPct = zones[_z][1]; domZone = zones[_z][0]; }}
}}

// Quality warnings
var hueWarnings = [];
if (pctBlue > 5.0)  hueWarnings.push("blue/purple " + pctBlue.toFixed(1) + "% >5% (OIII over-stretch)");
if (pctGreen > 15.0) hueWarnings.push("green " + pctGreen.toFixed(1) + "% >15% (SCNR insufficient)");
if (pctGold < 20.0) hueWarnings.push("gold/amber " + pctGold.toFixed(1) + "% <20% (Ha suppressed)");

var warnArr = "[";
for (var _wi = 0; _wi < hueWarnings.length; _wi++) {{
   warnArr += (_wi > 0 ? ", " : "") + "\\"" + hueWarnings[_wi] + "\\"";
}}
warnArr += "]";

var jsonStr = "{{\\n"
    + "  \\"stage\\": \\"{stage_label}\\",\\n"
    + "  \\"pixels_sampled\\": " + sampled + ",\\n"
    + "  \\"hue_gold_amber_pct\\": "  + pctGold.toFixed(2)  + ",\\n"
    + "  \\"hue_green_yellow_pct\\": " + pctGreen.toFixed(2) + ",\\n"
    + "  \\"hue_cyan_teal_pct\\": "   + pctCyan.toFixed(2)  + ",\\n"
    + "  \\"hue_blue_purple_pct\\": " + pctBlue.toFixed(2)  + ",\\n"
    + "  \\"hue_red_pct\\": "         + pctRed.toFixed(2)   + ",\\n"
    + "  \\"dominant_hue_zone\\": \\"" + domZone + "\\",\\n"
    + "  \\"pathological_blue_purple\\": " + (pctBlue > 5.0 ? "true" : "false") + ",\\n"
    + "  \\"quality_warnings\\": " + warnArr + "\\n"
    + "}}\\n";

var f = new File;
f.createForWriting("{pjsr_path(output_json_path)}");
f.outTextLn(jsonStr);
f.close();

{_close_image("w")}
Console.writeln("HueAnalysis[{stage_label}]: gold=" + pctGold.toFixed(1) + "% green=" + pctGreen.toFixed(1) + "% cyan=" + pctCyan.toFixed(1) + "% blue=" + pctBlue.toFixed(1) + "% dominant=" + domZone);
if (hueWarnings.length > 0) Console.writeln("  HUE_WARN: " + hueWarnings.join("; "));
"""


# =============================================================================
# Phase 5 -- RGB Star Processing and Final Combination
# =============================================================================


def generate_spcc(
    input_path: str,
    output_path: str,
    catalog: str = "GaiaDR3SP",
) -> str:
    """
    Generate a SpectrophotometricColorCalibration (SPCC) script.

    SPCC calibrates star colors against the Gaia DR3 spectrophotometric catalog
    (BP/RP spectra) for physically accurate stellar chromaticity. Requires:
        1. The image to have valid WCS (plate solve via ImageSolver first)
        2. Gaia catalog data downloaded via PI's online catalog system

    Args:
        input_path:  Path to the RGB composite image (must be plate-solved).
        output_path: Output path for the SPCC-calibrated image.
        catalog:     Catalog identifier. "GaiaDR3SP" is the default (Gaia DR3
                     spectrophotometric catalog).

    Returns:
        Complete PJSR script string.

    Note:
        SPCC may fail if the Gaia catalog server is unreachable or if the
        image lacks a valid WCS solution. The orchestrator stage should
        catch non-zero exit codes and surface the PI log output for debugging.
    """
    return f"""{_header("SpectrophotometricColorCalibration -- Phase 5 star color calibration")}
{_open_image("w", input_path)}
var view = w.currentView;

// SPCC requires a valid WCS solution (plate solve with ImageSolver first).
// The catalog data download is handled automatically by PI if internet is available.
var SPCC = new SpectrophotometricColorCalibration;

SPCC.applyCalibration     = true;
SPCC.narrowbandMode       = false;    // false = broadband RGB
SPCC.whiteReferenceId     = "";       // empty = use catalog photometry
SPCC.generateGraphs       = false;
SPCC.generateStarMaps     = false;

// The catalog and solver settings are read from PI's global preferences.
// Ensure the Gaia DR3 catalog is configured under Resources -> Catalog.

SPCC.executeOn(view);

{_save_image("w", output_path)}
{_close_image("w")}
Console.writeln("SPCC complete.");
"""


def generate_screen_blend(
    starless_path: str,
    stars_path: str,
    output_path: str,
    star_brightness: float = 1.0,
    output_id: str = "SHO_final",
) -> str:
    """
    Generate the screen blend star recombination script.

    Merges the starless SHO nebula with the RGB stars-only layer using the
    screen blend formula: ~(~starless * ~stars).

    Screen blend prevents pixel clipping where stars overlap bright nebulosity
    (which additive blending would clip to white at star positions atop bright
    Ha regions). The result is a natural-looking composite.

    Args:
        starless_path:    Path to the final starless SHO image.
        stars_path:       Path to the RGB stars-only image (from Phase 5 SXT).
        output_path:      Output path for the final composite.
        star_brightness:  Multiplier for star layer before blending (0.5-1.0).
                          Reduce to 0.7 if stars look too bright against the
                          nebula. This is BREAKPOINT 5.
        output_id:        View ID for the output PixelMath window.

    Returns:
        Complete PJSR script string.
    """
    if star_brightness == 1.0:
        blend_expr = "~(~SHO_starless * ~RGB_stars)"
    else:
        b = js_float(star_brightness)
        blend_expr = f"~(~SHO_starless * ~(RGB_stars * {b}))"

    return f"""{_header("Screen blend star recombination -- Phase 5 [BREAKPOINT 5]")}
// Screen blend formula: ~(~starless * ~stars)
// Adjusting star_brightness={star_brightness} to scale star layer before blend.
{_open_image("wStarless", starless_path, view_id="SHO_starless")}
{_open_image("wStars",    stars_path,    view_id="RGB_stars")}

// Read source dimensions -- required for PixelMath.executeGlobal() with createNewImage.
var _srcImg = wStarless.currentView.image;
var _W = _srcImg.width;
var _H = _srcImg.height;

var PM = new PixelMath;
PM.expression  = "{blend_expr}";
PM.useSingleExpression = true;
PM.createNewImage      = true;
PM.newImageId          = "{output_id}";
PM.newImageWidth       = _W;
PM.newImageHeight      = _H;
PM.newImageColorSpace  = PixelMath.prototype.RGB;
PM.newImageSampleFormat = PixelMath.prototype.f32;
PM.executeGlobal();

{_find_window_by_literal_id("wOut", output_id)}
{_save_image("wOut", output_path)}
{_close_image("wOut")}

{_close_image("wStarless")}
{_close_image("wStars")}
Console.writeln("Screen blend recombination complete. star_brightness={star_brightness}");
"""


# =============================================================================
# Template file writer (documentation / debugging utility)
# =============================================================================


def write_reference_templates(
    output_dir: "str | Path",
    config: Optional[dict] = None,
) -> dict[str, Path]:
    """
    Write reference .js.tmpl files to the templates/ directory.

    These files are NOT used at pipeline runtime -- they serve as readable
    documentation of what each generated script looks like with representative
    parameter values.

    Args:
        output_dir: Directory to write template files (typically templates/).
        config:     Optional pipeline config dict to use actual parameter values.
                    If None, uses documentation-friendly placeholder values.

    Returns:
        Dict mapping template name to the Path of the written file.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    c = config or {}
    proc = c.get("processing", {})
    pre  = c.get("preprocessing", {})

    # Representative placeholder paths
    W  = "E:/AstroPipeline/NGC1499/working"
    NB = ["Ha", "OIII", "SII"]

    templates = {
        "calibration.js.tmpl": generate_image_calibration(
            light_paths=[f"{W}/raw/NGC1499_Ha_001.xisf", f"{W}/raw/NGC1499_Ha_002.xisf"],
            output_dir=f"{W}/calibrated",
            master_dark_path=f"{W}/masters/master_dark_300s.xisf",
            master_flat_path=f"{W}/masters/master_flat_Ha.xisf",
            pedestal=pre.get("pedestal", 150),
        ),
        "registration.js.tmpl": generate_star_alignment(
            reference_path=f"{W}/NGC1499_Ha_master.xisf",
            target_paths=[f"{W}/raw/NGC1499_OIII_001_c.xisf"],
            output_dir=f"{W}/registered",
            generate_drizzle_data=True,
        ),
        "integration.js.tmpl": generate_image_integration(
            image_paths=[f"{W}/registered/NGC1499_Ha_{i:03d}_c_r.xisf" for i in range(1, 6)],
            output_path=f"{W}/NGC1499_Ha_master.xisf",
            rejection_algorithm=pre.get("rejection_algorithm", "ESD"),
            esd_low_relaxation=pre.get("esd_low_relaxation", 2.0),
        ),
        "bxt.js.tmpl": generate_blur_xterminator(
            input_path=f"{W}/NGC1499_SHO_linear.xisf",
            output_path=f"{W}/NGC1499_SHO_bxt.xisf",
            correct_only=False,
            sharpen_stars=proc.get("bxt_sharpen_stars", 0.25),
            sharpen_nonstellar=proc.get("bxt_sharpen_nonstellar", 0.40),
            adjust_halos=proc.get("bxt_adjust_halos", 0.05),
        ),
        "nxt.js.tmpl": generate_noise_xterminator(
            input_path=f"{W}/NGC1499_SHO_bxt.xisf",
            output_path=f"{W}/NGC1499_SHO_nxt.xisf",
            denoise=proc.get("nxt_denoise_linear", 0.80),
            detail=proc.get("nxt_detail_linear", 0.15),
        ),
        "sxt.js.tmpl": generate_star_xterminator(
            input_path=f"{W}/NGC1499_Ha_processed.xisf",
            starless_output_path=f"{W}/NGC1499_Ha_starless.xisf",
            stars_output_path=f"{W}/NGC1499_Ha_stars.xisf",
        ),
        "stretch.js.tmpl": generate_ghs_stretch(
            input_path=f"{W}/NGC1499_Ha_starless.xisf",
            output_path=f"{W}/NGC1499_Ha_starless_stretched.xisf",
            D=proc.get("ghs_stretch_factor", 5.0),
            b=proc.get("ghs_shape_param", 2.0),
        ),
        "pixelmath.js.tmpl": generate_foraxx_palette(
            ha_path=f"{W}/NGC1499_Ha_starless_stretched.xisf",
            sii_path=f"{W}/NGC1499_SII_starless_stretched.xisf",
            oiii_path=f"{W}/NGC1499_OIII_starless_stretched.xisf",
            output_path=f"{W}/NGC1499_SHO_foraxx.xisf",
        ),
        "spcc.js.tmpl": generate_spcc(
            input_path=f"{W}/NGC1499_RGB_composite.xisf",
            output_path=f"{W}/NGC1499_RGB_spcc.xisf",
        ),
    }

    written = {}
    for filename, content in templates.items():
        path = out_dir / filename
        path.write_text(content, encoding="utf-8")
        written[filename] = path

    return written
