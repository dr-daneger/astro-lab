// ============================================================================
// CascadiaPhotoelectric Calibration Diagnostic v1.3.0
// ============================================================================
//
// PixInsight Script — appears under Script > CascadiaPhotoelectric > Calibration Diagnostic
//
// PURPOSE: Runs ImageCalibration on a selectable subset of light frames with
// togglable dark/flat/bias masters, then tiles the results for A/B comparison.
// Optionally runs all 3 combinations (dark+flat, dark-only, flat-only) to
// isolate calibration problems.
//
// FILTER-AWARE: Scans light directories recursively, extracts filter from
// ASIAIR/NINA filenames (e.g. _H_, _Ha_, _OIII_, _B_), and only calibrates
// frames matching the selected filter. Automatically finds the matching
// flat master from a masters directory.
//
// OBSERVATION CONTEXT: Auto-extracts FITS headers (target, RA/Dec, equipment,
// gain, temp, airmass) and computes moon phase. User adds target type, Bortle
// class, and seeing. All confounding variables are logged to diagnostic_log.json
// so future analysis can group results by conditions and learn optimal settings.
//
// This script calls the EXACT SAME ImageCalibration process configuration as
// astro-piper's automated pipeline (pjsr_generator.generate_image_calibration),
// ensuring what you see in the GUI is what the pipeline produces.
//
// INSTALL: Copy this file to:
//   [PixInsight]/src/scripts/CascadiaPhotoelectric/CascadiaPhotoelectric-CalibrationDiagnostic.js
//   Then: Script > Feature Scripts > Add > select the CascadiaPhotoelectric folder
//   It will appear under: Script > CascadiaPhotoelectric > Calibration Diagnostic
//
// ============================================================================

#feature-id   CascadiaPhotoelectric > Calibration Diagnostic
#feature-info Filter-aware calibration diagnostic with A/B testing and observation context logging.

#include <pjsr/DataType.jsh>
#include <pjsr/Sizer.jsh>
#include <pjsr/FrameStyle.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/NumericControl.jsh>

#define VERSION "1.3.0"
#define TITLE   "CascadiaPhotoelectric Calibration Diagnostic"

// ============================================================================
// Auto-STF helper (matches astro-piper pjsr_generator._AUTO_STF_JS)
// ============================================================================

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

// ============================================================================
// Filter extraction from ASIAIR / NINA filenames
// ============================================================================

// ASIAIR convention: ..._Bin1_H_gain100_...  (single letter after _Bin1_)
// NINA convention:   ..._Ha_... or ..._OIII_... (full filter name)
// Map all variants to canonical names used in master flat filenames

var FILTER_MAP = {
   "H":    "Ha",
   "Ha":   "Ha",
   "O":    "OIII",
   "OIII": "OIII",
   "S":    "SII",
   "SII":  "SII",
   "R":    "R",
   "G":    "G",
   "B":    "B"
};

// All known filter tokens for display in dropdown
// Index 6 = "All (one each)" — runs each filter found
var FILTER_CHOICES = ["Ha", "OIII", "SII", "R", "G", "B", "All (one each)"];
var ALL_FILTERS_INDEX = 6;

function extractFilter(filename) {
   // Try ASIAIR pattern: _Bin1_X_gain  (single letter filter)
   var m = filename.match(/_Bin\d+_([A-Za-z]+)_gain/);
   if (m) {
      var token = m[1];
      if (FILTER_MAP[token] !== undefined)
         return FILTER_MAP[token];
   }
   // Try NINA pattern: _FilterName_ anywhere in filename
   var tokens = ["OIII", "SII", "Ha", "R", "G", "B"];
   for (var i = 0; i < tokens.length; i++) {
      // Match _Token_ with word boundaries via underscores
      var re = new RegExp("_" + tokens[i] + "_", "i");
      if (re.test(filename))
         return FILTER_MAP[tokens[i]] || tokens[i];
   }
   return null;  // unknown filter
}

// ============================================================================
// FITS header context extraction
// ============================================================================

// Target type choices for user classification
var TARGET_TYPES = [
   "(not set)",
   "Extended Nebula",
   "Compact Nebula",
   "Galaxy",
   "Star Cluster",
   "Planetary Nebula",
   "Comet",
   "Other"
];

function readFITSKeyword(keywords, name) {
   // Search FITS keyword array for a named keyword, return trimmed value string
   for (var i = 0; i < keywords.length; i++) {
      if (keywords[i].name.trim() == name)
         return keywords[i].value.trim().replace(/^'|'$/g, "").trim();
   }
   return "";
}

function extractObservationContext(lightFiles) {
   // Read FITS/XISF headers from the first light frame to capture session metadata
   var ctx = {
      object: "",
      ra: "",
      dec: "",
      dateObs: "",
      exposure: 0,
      gain: 0,
      ccdTemp: 0,
      binning: 1,
      camera: "",
      telescope: "",
      focalLength: 0,
      aperture: 0,
      airmass: 0,
      siteLatitude: 0,
      siteLongitude: 0,
      filter: "",
      altitude: 0,
      moonIllumination: 0,
      moonPhase: "",
      isNarrowband: false
   };

   if (lightFiles.length == 0) return ctx;

   try {
      var wins = ImageWindow.open(lightFiles[0]);
      if (wins.length == 0 || wins[0].isNull) return ctx;

      var keywords = wins[0].keywords;
      if (!keywords || keywords.length == 0) {
         wins[0].forceClose();
         return ctx;
      }

      // Standard FITS keywords (covers ASIAIR, NINA, SGP, Voyager, etc.)
      ctx.object     = readFITSKeyword(keywords, "OBJECT");
      ctx.ra         = readFITSKeyword(keywords, "OBJCTRA");
      if (ctx.ra.length == 0) ctx.ra = readFITSKeyword(keywords, "RA");
      ctx.dec        = readFITSKeyword(keywords, "OBJCTDEC");
      if (ctx.dec.length == 0) ctx.dec = readFITSKeyword(keywords, "DEC");
      ctx.dateObs    = readFITSKeyword(keywords, "DATE-OBS");
      ctx.filter     = readFITSKeyword(keywords, "FILTER");
      ctx.camera     = readFITSKeyword(keywords, "INSTRUME");
      ctx.telescope  = readFITSKeyword(keywords, "TELESCOP");

      var expStr = readFITSKeyword(keywords, "EXPTIME");
      if (expStr.length == 0) expStr = readFITSKeyword(keywords, "EXPOSURE");
      if (expStr.length > 0) ctx.exposure = parseFloat(expStr);

      var gainStr = readFITSKeyword(keywords, "GAIN");
      if (gainStr.length == 0) gainStr = readFITSKeyword(keywords, "EGAIN");
      if (gainStr.length > 0) ctx.gain = parseFloat(gainStr);

      var tempStr = readFITSKeyword(keywords, "CCD-TEMP");
      if (tempStr.length == 0) tempStr = readFITSKeyword(keywords, "SET-TEMP");
      if (tempStr.length > 0) ctx.ccdTemp = parseFloat(tempStr);

      var binStr = readFITSKeyword(keywords, "XBINNING");
      if (binStr.length > 0) ctx.binning = parseInt(binStr);

      var flStr = readFITSKeyword(keywords, "FOCALLEN");
      if (flStr.length > 0) ctx.focalLength = parseFloat(flStr);

      var apStr = readFITSKeyword(keywords, "APTDIA");
      if (apStr.length > 0) ctx.aperture = parseFloat(apStr);

      var amStr = readFITSKeyword(keywords, "AIRMASS");
      if (amStr.length > 0) {
         ctx.airmass = parseFloat(amStr);
         if (ctx.airmass > 0)
            ctx.altitude = Math.round(Math.asin(1.0 / ctx.airmass) * 180.0 / Math.PI);
      }

      var latStr = readFITSKeyword(keywords, "SITELAT");
      if (latStr.length == 0) latStr = readFITSKeyword(keywords, "LAT-OBS");
      if (latStr.length > 0) ctx.siteLatitude = parseFloat(latStr);

      var lonStr = readFITSKeyword(keywords, "SITELONG");
      if (lonStr.length == 0) lonStr = readFITSKeyword(keywords, "LONG-OBS");
      if (lonStr.length > 0) ctx.siteLongitude = parseFloat(lonStr);

      // Determine if narrowband from FITS FILTER keyword
      var fitsFilter = ctx.filter.toUpperCase();
      ctx.isNarrowband = (fitsFilter.indexOf("HA") >= 0 ||
                          fitsFilter.indexOf("OIII") >= 0 ||
                          fitsFilter.indexOf("SII") >= 0 ||
                          fitsFilter.indexOf("NII") >= 0);

      // Moon phase from DATE-OBS
      if (ctx.dateObs.length > 0) {
         var moonInfo = computeMoonPhase(ctx.dateObs);
         ctx.moonIllumination = moonInfo.illumination;
         ctx.moonPhase = moonInfo.phase;
      }

      wins[0].forceClose();
   } catch(e) {
      Console.warningln("  Could not read FITS context: " + e.message);
   }

   return ctx;
}

// ============================================================================
// Moon phase calculator (simplified synodic method, ~1% accuracy)
// ============================================================================

function computeMoonPhase(dateObsStr) {
   // Parse ISO date string to Julian Date, then compute moon illumination
   // Known new moon epoch: 2000-01-06 18:14 UTC (JD 2451550.26)
   // Synodic period: 29.530588853 days

   var result = { illumination: 0, phase: "Unknown" };

   try {
      // Parse DATE-OBS (ISO format: 2026-01-21T21:29:12 or 2026-01-21)
      var parts = dateObsStr.split("T");
      var dateParts = parts[0].split("-");
      var y = parseInt(dateParts[0]);
      var m = parseInt(dateParts[1]);
      var d = parseInt(dateParts[2]);
      var hour = 0;
      if (parts.length > 1) {
         var timeParts = parts[1].split(":");
         hour = parseInt(timeParts[0]) + (timeParts.length > 1 ? parseInt(timeParts[1]) / 60.0 : 0);
      }

      // Julian Date calculation
      if (m <= 2) { y -= 1; m += 12; }
      var A = Math.floor(y / 100);
      var B = 2 - A + Math.floor(A / 4);
      var JD = Math.floor(365.25 * (y + 4716)) +
               Math.floor(30.6001 * (m + 1)) +
               d + hour / 24.0 + B - 1524.5;

      // Days since known new moon
      var daysSinceNew = (JD - 2451550.26) % 29.530588853;
      if (daysSinceNew < 0) daysSinceNew += 29.530588853;

      // Phase angle (0 = new, 0.5 = full)
      var phaseAngle = daysSinceNew / 29.530588853;

      // Illumination percentage (0-100)
      result.illumination = Math.round((1 - Math.cos(phaseAngle * 2 * Math.PI)) / 2 * 100);

      // Phase name
      if (phaseAngle < 0.0625)       result.phase = "New Moon";
      else if (phaseAngle < 0.1875)  result.phase = "Waxing Crescent";
      else if (phaseAngle < 0.3125)  result.phase = "First Quarter";
      else if (phaseAngle < 0.4375)  result.phase = "Waxing Gibbous";
      else if (phaseAngle < 0.5625)  result.phase = "Full Moon";
      else if (phaseAngle < 0.6875)  result.phase = "Waning Gibbous";
      else if (phaseAngle < 0.8125)  result.phase = "Last Quarter";
      else if (phaseAngle < 0.9375)  result.phase = "Waning Crescent";
      else                           result.phase = "New Moon";
   } catch(e) {
      Console.warningln("  Moon phase calculation failed: " + e.message);
   }

   return result;
}

// ============================================================================
// Recursive file discovery
// ============================================================================

function findFilesRecursive(dir, extensions) {
   // Returns array of full paths for files matching any extension
   var results = [];
   var ff = new FileFind;

   // Search for matching files in this directory (skip non-light frames)
   for (var e = 0; e < extensions.length; e++) {
      if (ff.begin(dir + "/*" + extensions[e])) {
         do {
            if (!ff.isDirectory) {
               var ln = ff.name.toLowerCase();
               // Skip flat, dark, bias, and master files by name prefix
               if (ln.indexOf("flat") != 0 && ln.indexOf("dark") != 0 &&
                   ln.indexOf("bias") != 0 && ln.indexOf("master") != 0)
                  results.push(dir + "/" + ff.name);
            }
         } while (ff.next());
      }
   }

   // Recurse into subdirectories
   if (ff.begin(dir + "/*")) {
      do {
         var dn = ff.name.toLowerCase();
         if (ff.isDirectory && dn != "." && dn != ".." &&
             dn != "calibrated" && dn != "diagnostic" && dn != "diagnostics" &&
             dn != "registered" && dn != "master" && dn != "masters" &&
             dn != "flats" && dn != "flat" && dn != "darks" && dn != "dark" &&
             dn != "bias" && dn != "biases" &&
             dn != "ldd_lps" && dn != "logs" && dn != "output") {
            var subResults = findFilesRecursive(dir + "/" + ff.name, extensions);
            for (var i = 0; i < subResults.length; i++)
               results.push(subResults[i]);
         }
      } while (ff.next());
   }

   return results;
}

// ============================================================================
// Auto-find master flat matching a filter
// ============================================================================

function findMasterFlat(mastersDir, filterName) {
   // Search for master_flat_FilterName*.xisf in mastersDir and subdirs
   var searchDirs = [
      mastersDir,
      mastersDir + "/nb",
      mastersDir + "/rgb",
      mastersDir + "/masters",
      mastersDir + "/masters/nb",
      mastersDir + "/masters/rgb"
   ];
   var candidates = [];

   for (var d = 0; d < searchDirs.length; d++) {
      var ff = new FileFind;
      var patterns = [
         "master_flat_" + filterName + "*.xisf",
         "master_flat_" + filterName + "*.fit",
         "master_flat_" + filterName + "*.fits"
      ];
      for (var p = 0; p < patterns.length; p++) {
         if (ff.begin(searchDirs[d] + "/" + patterns[p])) {
            do {
               if (!ff.isDirectory)
                  candidates.push(searchDirs[d] + "/" + ff.name);
            } while (ff.next());
         }
      }
   }

   if (candidates.length > 0) {
      candidates.sort();
      return candidates[0];  // first match alphabetically
   }
   return "";
}

function findMasterDark(mastersDir, gain) {
   // Search for master_dark matching gain in mastersDir and subdirs
   var searchDirs = [
      mastersDir,
      mastersDir + "/nb",
      mastersDir + "/rgb",
      mastersDir + "/masters",
      mastersDir + "/masters/nb",
      mastersDir + "/masters/rgb"
   ];
   var candidates = [];
   var gainStr = "gain" + gain;

   for (var d = 0; d < searchDirs.length; d++) {
      var ff = new FileFind;
      var patterns = ["master_dark*.xisf", "master_dark*.fit", "master_dark*.fits"];
      for (var p = 0; p < patterns.length; p++) {
         if (ff.begin(searchDirs[d] + "/" + patterns[p])) {
            do {
               if (!ff.isDirectory) {
                  var fullPath = searchDirs[d] + "/" + ff.name;
                  // Prefer gain-matched dark
                  if (ff.name.indexOf(gainStr) >= 0)
                     candidates.unshift(fullPath);  // priority
                  else
                     candidates.push(fullPath);
               }
            } while (ff.next());
         }
      }
   }

   return candidates.length > 0 ? candidates[0] : "";
}

// ============================================================================
// Parameters (persistent across invocations via Settings)
// ============================================================================

function CalDiagParameters() {
   // Light frame selection
   this.lightDir        = "";
   this.maxFrames       = 3;         // subset for fast testing (0 = all)

   // Filter selection
   this.filterIndex     = 0;         // index into FILTER_CHOICES (Ha=0)

   // Masters directory (auto-discovers dark and flat)
   this.mastersDir      = "";

   // Master calibration files (auto-filled or manual override)
   this.darkPath        = "";
   this.flatPath        = "";
   this.biasPath        = "";

   // Toggles
   this.useDark         = true;
   this.useFlat         = true;
   this.useBias         = false;     // usually disabled for CMOS

   // ImageCalibration parameters (match pipeline defaults)
   this.pedestal        = 150;
   this.outputPostfix   = "_c";
   this.outputExtension = ".xisf";

   // Diagnostic mode
   this.diagnosticMode  = false;     // A/B: run 3 combinations (no raw — PI rejects it)

   // Observation context (user-specified)
   this.targetType      = 0;         // index into TARGET_TYPES

   // Output
   this.outputDir       = "";        // empty = auto
   this.autoSTF         = true;      // apply auto-STF to results

   // ---

   this.save = function() {
      Settings.write(TITLE + "/lightDir",       DataType_String,  this.lightDir);
      Settings.write(TITLE + "/mastersDir",     DataType_String,  this.mastersDir);
      Settings.write(TITLE + "/darkPath",       DataType_String,  this.darkPath);
      Settings.write(TITLE + "/flatPath",       DataType_String,  this.flatPath);
      Settings.write(TITLE + "/biasPath",       DataType_String,  this.biasPath);
      Settings.write(TITLE + "/useDark",        DataType_Boolean, this.useDark);
      Settings.write(TITLE + "/useFlat",        DataType_Boolean, this.useFlat);
      Settings.write(TITLE + "/useBias",        DataType_Boolean, this.useBias);
      Settings.write(TITLE + "/pedestal",       DataType_Int32,   this.pedestal);
      Settings.write(TITLE + "/maxFrames",      DataType_Int32,   this.maxFrames);
      Settings.write(TITLE + "/filterIndex",    DataType_Int32,   this.filterIndex);
      Settings.write(TITLE + "/diagnosticMode", DataType_Boolean, this.diagnosticMode);
      Settings.write(TITLE + "/autoSTF",        DataType_Boolean, this.autoSTF);
      Settings.write(TITLE + "/outputDir",      DataType_String,  this.outputDir);
      Settings.write(TITLE + "/targetType",    DataType_Int32,   this.targetType);
   };

   this.load = function() {
      var v;
      v = Settings.read(TITLE + "/lightDir",       DataType_String);  if (v != null) this.lightDir       = v;
      v = Settings.read(TITLE + "/mastersDir",     DataType_String);  if (v != null) this.mastersDir     = v;
      v = Settings.read(TITLE + "/darkPath",       DataType_String);  if (v != null) this.darkPath       = v;
      v = Settings.read(TITLE + "/flatPath",       DataType_String);  if (v != null) this.flatPath       = v;
      v = Settings.read(TITLE + "/biasPath",       DataType_String);  if (v != null) this.biasPath       = v;
      v = Settings.read(TITLE + "/useDark",        DataType_Boolean); if (v != null) this.useDark        = v;
      v = Settings.read(TITLE + "/useFlat",        DataType_Boolean); if (v != null) this.useFlat        = v;
      v = Settings.read(TITLE + "/useBias",        DataType_Boolean); if (v != null) this.useBias        = v;
      v = Settings.read(TITLE + "/pedestal",       DataType_Int32);   if (v != null) this.pedestal       = v;
      v = Settings.read(TITLE + "/maxFrames",      DataType_Int32);   if (v != null) this.maxFrames      = v;
      v = Settings.read(TITLE + "/filterIndex",    DataType_Int32);   if (v != null) this.filterIndex    = v;
      v = Settings.read(TITLE + "/diagnosticMode", DataType_Boolean); if (v != null) this.diagnosticMode = v;
      v = Settings.read(TITLE + "/autoSTF",        DataType_Boolean); if (v != null) this.autoSTF        = v;
      v = Settings.read(TITLE + "/outputDir",      DataType_String);  if (v != null) this.outputDir      = v;
      v = Settings.read(TITLE + "/targetType",    DataType_Int32);   if (v != null) this.targetType     = v;
   };
}

// ============================================================================
// Core calibration engine
// ============================================================================

// Discover and subset light frames for a filter (called once, shared across combos)
function discoverLightFrames(params) {
   var lightDir = params.lightDir;
   if (lightDir.length == 0) return [];

   var filterName = FILTER_CHOICES[params.filterIndex];

   Console.writeln("  Scanning for light frames in: " + lightDir);
   var allFiles = findFilesRecursive(lightDir, [".xisf", ".fit", ".fits"]);
   Console.writeln("  Found " + allFiles.length + " total image files");

   var lightFiles = [];
   for (var i = 0; i < allFiles.length; i++) {
      var parts = allFiles[i].replace(/\\/g, "/").split("/");
      var basename = parts[parts.length - 1];
      var detected = extractFilter(basename);
      if (detected == filterName)
         lightFiles.push(allFiles[i]);
   }

   lightFiles.sort();

   if (lightFiles.length == 0) {
      Console.criticalln("No " + filterName + " frames found in: " + lightDir);
      Console.criticalln("(Searched " + allFiles.length + " files recursively, none matched filter '" + filterName + "')");
      return [];
   }

   Console.writeln("  Matched " + lightFiles.length + " " + filterName + " frames");

   // Subset for speed
   var maxN = params.maxFrames;
   if (maxN > 0 && maxN < lightFiles.length) {
      var step = lightFiles.length / maxN;
      var subset = [];
      for (var i = 0; i < maxN; i++)
         subset.push(lightFiles[Math.floor(i * step)]);
      lightFiles = subset;
   }

   return lightFiles;
}

function runCalibration(params, label, useDark, useFlat, useBias, outSubdir, resultMeta, lightFiles) {
   var lightDir = params.lightDir;
   var filterName = FILTER_CHOICES[params.filterIndex];

   if (lightFiles.length == 0) {
      Console.criticalln("No light frames to calibrate.");
      return [];
   }

   // Resolve dark and flat paths
   var darkPath = useDark ? params.darkPath : "";
   var flatPath = useFlat ? params.flatPath : "";
   var biasPath = useBias ? params.biasPath : "";

   Console.writeln("");
   Console.writeln("=== " + label + " [" + filterName + "] ===");
   Console.writeln("  Light frames: " + lightFiles.length + " " + filterName);
   Console.writeln("  Dark: " + (useDark && darkPath.length > 0 ? darkPath : "(disabled)"));
   Console.writeln("  Flat: " + (useFlat && flatPath.length > 0 ? flatPath : "(disabled)"));
   Console.writeln("  Bias: " + (useBias && biasPath.length > 0 ? biasPath : "(disabled)"));
   Console.writeln("  Pedestal: " + params.pedestal);

   // Determine output directory
   var baseOutDir = params.outputDir;
   if (baseOutDir.length == 0)
      baseOutDir = lightDir + "/diagnostic";
   var outDir = baseOutDir + "/" + filterName + "/" + outSubdir;

   // Create output directory tree
   function ensureDir(path) {
      if (!File.directoryExists(path)) {
         // Try to create parent first
         var parent = path.replace(/\/[^\/]+\/?$/, "");
         if (parent.length > 0 && parent != path)
            ensureDir(parent);
         File.createDirectory(path, true);
      }
   }
   ensureDir(outDir);

   // Build target frames array
   var targets = [];
   for (var i = 0; i < lightFiles.length; i++)
      targets.push([true, lightFiles[i]]);

   // ---- ImageCalibration ----
   // These settings match pjsr_generator.generate_image_calibration() exactly
   var P = new ImageCalibration;

   P.targetFrames = targets;

   P.masterBiasEnabled          = useBias && biasPath.length > 0;
   P.masterBiasPath             = useBias ? biasPath : "";

   P.masterDarkEnabled          = useDark && darkPath.length > 0;
   P.masterDarkPath             = useDark ? darkPath : "";
   P.masterDarkOptimizationLow  = 3.0;
   P.masterDarkOptimizationWindow = 1024;

   P.masterFlatEnabled          = useFlat && flatPath.length > 0;
   P.masterFlatPath             = useFlat ? flatPath : "";

   P.outputDirectory            = outDir;
   P.outputExtension            = params.outputExtension;
   P.outputPostfix              = params.outputPostfix;
   P.outputSampleFormat         = 4;    // f32
   P.pedestal                   = params.pedestal;
   P.enableCFA                  = false;
   P.noiseEvaluation            = true;
   P.overwriteExistingFiles     = true;

   P.executeGlobal();

   Console.writeln("  Calibration complete: " + lightFiles.length + " frames -> " + outDir);

   // Collect output file paths — only files matching our input light basenames
   // Build expected output names from input lights + postfix
   var expectedNames = {};
   for (var ei = 0; ei < lightFiles.length; ei++) {
      var parts = lightFiles[ei].replace(/\\/g, "/").split("/");
      var bn = parts[parts.length - 1];
      // Strip extension, add postfix + output extension
      var dotIdx = bn.lastIndexOf(".");
      if (dotIdx > 0) bn = bn.substring(0, dotIdx);
      expectedNames[bn + params.outputPostfix + params.outputExtension] = true;
   }

   var outFiles = [];
   var outSearch = new FileFind;
   if (outSearch.begin(outDir + "/*" + params.outputPostfix + params.outputExtension)) {
      do {
         if (!outSearch.isDirectory && expectedNames[outSearch.name])
            outFiles.push(outDir + "/" + outSearch.name);
      } while (outSearch.next());
   }
   outFiles.sort();

   // Measure noise/signal on output files and store in resultMeta
   for (var i = 0; i < outFiles.length; i++) {
      var metrics = { median: 0, mad: 0, noise: 0, snr: 0, mean: 0 };

      // Open temporarily to measure (don't show)
      try {
         var mWins = ImageWindow.open(outFiles[i]);
         if (mWins.length > 0 && !mWins[0].isNull) {
            var mView = mWins[0].currentView;
            var img = mView.image;
            img.selectedChannel = 0;

            metrics.median = mView.computeOrFetchProperty("Median").at(0);
            metrics.mad    = mView.computeOrFetchProperty("MAD").at(0) * 1.4826;
            metrics.mean   = mView.computeOrFetchProperty("Mean").at(0);

            // Noise estimate via MRS (same as PI's noise evaluation)
            try {
               var ne = mView.computeOrFetchProperty("NoiseEstimate_MRS");
               if (ne) metrics.noise = ne.at(0);
            } catch(e3) {
               // Fallback: use MAD as noise proxy
               metrics.noise = metrics.mad;
            }

            // SNR = (median - background) / noise
            // For linear data, median IS approximately signal+background
            if (metrics.noise > 0)
               metrics.snr = metrics.median / metrics.noise;

            img.resetSelections();
            mWins[0].forceClose();
         }
      } catch(e4) {
         Console.warningln("  Could not measure metrics for: " + outFiles[i]);
      }

      resultMeta.push({
         path: outFiles[i],
         filter: filterName,
         combo: outSubdir,
         label: label,
         frameNum: i + 1,
         metrics: metrics
      });

      Console.writeln("  Metrics [" + filterName + "/" + outSubdir + " #" + (i+1) + "]: " +
         "median=" + metrics.median.toFixed(6) +
         " MAD=" + metrics.mad.toFixed(6) +
         " noise=" + metrics.noise.toFixed(6) +
         " SNR=" + metrics.snr.toFixed(1));
   }

   return outFiles;
}

// ============================================================================
// Main execution
// ============================================================================

function executeCalDiagForFilter(params, filterName, allOutputs, resultMeta, rawLights) {
   // Auto-discover masters for this specific filter
   var savedDark = params.darkPath;
   var savedFlat = params.flatPath;

   if (params.mastersDir.length > 0) {
      var autoFlat = findMasterFlat(params.mastersDir, filterName);
      if (autoFlat.length > 0) {
         params.flatPath = autoFlat;
         Console.writeln("Auto-matched flat for " + filterName + ": " + autoFlat);
      }
      var gain = (filterName == "Ha" || filterName == "OIII" || filterName == "SII") ? "100" : "-25";
      var autoDark = findMasterDark(params.mastersDir, gain);
      if (autoDark.length > 0) {
         params.darkPath = autoDark;
         Console.writeln("Auto-matched dark for " + filterName + ": " + autoDark);
      }
   }

   // Temporarily set filterIndex to match this filter
   var savedIndex = params.filterIndex;
   for (var fi = 0; fi < ALL_FILTERS_INDEX; fi++) {
      if (FILTER_CHOICES[fi] == filterName) {
         params.filterIndex = fi;
         break;
      }
   }

   // Discover frames ONCE so the same light(s) are used across all combos
   var selectedLights = discoverLightFrames(params);

   // Track raw lights for reference display
   for (var ri = 0; ri < selectedLights.length; ri++) {
      if (rawLights.indexOf(selectedLights[ri]) < 0)
         rawLights.push(selectedLights[ri]);
   }

   if (params.diagnosticMode) {
      var combos = [
         { label: "Dark+Flat",  dark: true,  flat: true,  bias: params.useBias, dir: "dark_flat"  },
         { label: "Dark Only",  dark: true,  flat: false, bias: params.useBias, dir: "dark_only"  },
         { label: "Flat Only",  dark: false, flat: true,  bias: false,          dir: "flat_only"  }
      ];

      for (var c = 0; c < combos.length; c++) {
         var combo = combos[c];
         var files = runCalibration(
            params, combo.label, combo.dark, combo.flat, combo.bias, combo.dir, resultMeta, selectedLights
         );
         for (var fi = 0; fi < files.length; fi++)
            allOutputs.push(files[fi]);
      }
   } else {
      var files = runCalibration(
         params, "Calibration", params.useDark, params.useFlat, params.useBias, "single", resultMeta, selectedLights
      );
      for (var i = 0; i < files.length; i++)
         allOutputs.push(files[i]);
   }

   // Restore
   params.filterIndex = savedIndex;
   params.darkPath = savedDark;
   params.flatPath = savedFlat;
}

function executeCalDiag(params) {
   var allOutputs = [];
   var resultMeta = [];  // metadata per output for naming and scoring
   var rawLights = [];   // raw uncalibrated light paths for reference display

   // Extract observation context from FITS headers of first light frame
   Console.writeln("Extracting observation context from FITS headers...");
   var allLightFiles = findFilesRecursive(params.lightDir, [".xisf", ".fit", ".fits"]);
   var obsContext = extractObservationContext(allLightFiles);
   if (obsContext.object.length > 0)
      Console.writeln("  Target: " + obsContext.object);
   if (obsContext.ra.length > 0)
      Console.writeln("  RA/Dec: " + obsContext.ra + " / " + obsContext.dec);
   if (obsContext.dateObs.length > 0) {
      Console.writeln("  Date: " + obsContext.dateObs);
      Console.writeln("  Moon: " + obsContext.moonPhase + " (" + obsContext.moonIllumination + "% illumination)");
   }
   if (obsContext.altitude > 0)
      Console.writeln("  Target altitude: ~" + obsContext.altitude + " deg (from airmass " + obsContext.airmass.toFixed(2) + ")");
   if (obsContext.camera.length > 0)
      Console.writeln("  Camera: " + obsContext.camera);
   if (obsContext.telescope.length > 0)
      Console.writeln("  Telescope: " + obsContext.telescope);
   Console.writeln("");

   if (params.filterIndex == ALL_FILTERS_INDEX) {
      var savedMax = params.maxFrames;
      if (params.maxFrames == 0 || params.maxFrames > 1)
         params.maxFrames = 1;

      Console.writeln("=== ALL FILTERS MODE: one frame per filter ===");

      var allFiles = findFilesRecursive(params.lightDir, [".xisf", ".fit", ".fits"]);
      var foundFilters = {};
      for (var i = 0; i < allFiles.length; i++) {
         var parts = allFiles[i].replace(/\\/g, "/").split("/");
         var basename = parts[parts.length - 1];
         var detected = extractFilter(basename);
         if (detected != null)
            foundFilters[detected] = true;
      }

      var filterList = [];
      for (var f = 0; f < ALL_FILTERS_INDEX; f++) {
         if (foundFilters[FILTER_CHOICES[f]])
            filterList.push(FILTER_CHOICES[f]);
      }

      Console.writeln("  Filters found: " + filterList.join(", "));
      Console.writeln("  Running 1 frame per filter" +
         (params.diagnosticMode ? " x 3 diagnostic combos" : "") + "...");
      Console.writeln("");

      for (var f = 0; f < filterList.length; f++) {
         executeCalDiagForFilter(params, filterList[f], allOutputs, resultMeta, rawLights);
      }

      params.maxFrames = savedMax;
   } else {
      var filterName = FILTER_CHOICES[params.filterIndex];
      executeCalDiagForFilter(params, filterName, allOutputs, resultMeta, rawLights);
   }

   // Open results with clear window names and auto-STF
   if (allOutputs.length > 0) {
      Console.writeln("");
      Console.writeln("=== Opening " + allOutputs.length + " result(s) for review ===");

      var openedWindows = [];
      for (var i = 0; i < allOutputs.length; i++) {
         var wins = ImageWindow.open(allOutputs[i]);
         if (wins.length > 0 && !wins[0].isNull) {
            var win = wins[0];

            // Find matching metadata by file path
            var meta = null;
            for (var mi = 0; mi < resultMeta.length; mi++) {
               if (resultMeta[mi].path == allOutputs[i]) {
                  meta = resultMeta[mi];
                  break;
               }
            }
            if (meta) {
               // e.g. "Ha_DarkFlat_001" or "OIII_FlatOnly_002"
               var comboLabel = meta.combo.replace(/_/g, "");
               // Capitalize first letter of each word
               comboLabel = comboLabel.charAt(0).toUpperCase() + comboLabel.slice(1);
               var frameStr = ("00" + meta.frameNum).slice(-3);
               var newId = meta.filter + "_" + comboLabel + "_" + frameStr;
               // PI view IDs can't have spaces or special chars
               newId = newId.replace(/[^A-Za-z0-9_]/g, "");
               try {
                  win.currentView.id = newId;
               } catch(e) {
                  // View ID collision — append index
                  try { win.currentView.id = newId + "_" + i; } catch(e2) {}
               }
            }

            if (params.autoSTF)
               applyAutoSTF(win.currentView, -2.8, 0.25);
            win.show();

            var displayName = meta ? (meta.filter + " / " + meta.label) : allOutputs[i];
            Console.writeln("  [" + (i + 1) + "/" + allOutputs.length + "] " + displayName);
            openedWindows.push(win);
         }
      }

      // Open raw reference frames for before/after comparison
      if (rawLights.length > 0) {
         Console.writeln("");
         Console.writeln("=== Opening " + rawLights.length + " raw reference frame(s) ===");
         for (var ri = 0; ri < rawLights.length; ri++) {
            try {
               var rWins = ImageWindow.open(rawLights[ri]);
               if (rWins.length > 0 && !rWins[0].isNull) {
                  var rWin = rWins[0];
                  // Name it clearly as raw reference
                  var rParts = rawLights[ri].replace(/\\/g, "/").split("/");
                  var rBase = rParts[rParts.length - 1];
                  var rFilter = extractFilter(rBase) || "Raw";
                  var rawId = rFilter + "_Raw_" + ("00" + (ri + 1)).slice(-3);
                  rawId = rawId.replace(/[^A-Za-z0-9_]/g, "");
                  try { rWin.currentView.id = rawId; } catch(e) {
                     try { rWin.currentView.id = rawId + "_r" + ri; } catch(e2) {}
                  }
                  if (params.autoSTF)
                     applyAutoSTF(rWin.currentView, -2.8, 0.25);
                  rWin.show();
                  Console.writeln("  [RAW " + (ri + 1) + "/" + rawLights.length + "] " + rBase);
                  openedWindows.push(rWin);
               }
            } catch(e) {
               Console.warningln("  Could not open raw reference: " + rawLights[ri]);
            }
         }
      }

      try { ImageWindow.tileWindows(); } catch(e) {
         try { ImageWindow.tile(); } catch(e2) {
            Console.warningln("Auto-tile not available -- please tile windows manually (Window > Tile).");
         }
      }
      Console.writeln("");
      Console.writeln("Review ready. " + allOutputs.length + " image(s) tiled with auto-STF.");

      if (params.diagnosticMode) {
         Console.noteln("");
         if (params.filterIndex == ALL_FILTERS_INDEX) {
            Console.noteln("DIAGNOSTIC MODE [All Filters]: " + allOutputs.length + " images tiled");
            Console.noteln("  Groups of 3 per filter: Dark+Flat / Dark Only / Flat Only");
         } else {
            Console.noteln("DIAGNOSTIC MODE [" + FILTER_CHOICES[params.filterIndex] + "]: Compare the 3 tiled images:");
         }
         Console.noteln("  1. Dark+Flat  (full calibration)");
         Console.noteln("  2. Dark Only  (flat disabled -- look for vignetting/dust rings)");
         Console.noteln("  3. Flat Only  (dark disabled -- look for hot pixels/speckle)");
         Console.noteln("");
         Console.noteln("Interpretation:");
         Console.noteln("  Dark speckle in #1 but NOT #3:");
         Console.noteln("    -> Darks are the problem (mismatched temp/gain/exposure)");
         Console.noteln("  Images look worse with flats (#1 vs #2):");
         Console.noteln("    -> Flats are overcorrecting (dust moved, wrong gain, wrong rotation)");
         Console.noteln("  Speckle in all 3:");
         Console.noteln("    -> Hot pixels in raw data (need cosmetic correction or more dithering)");
         Console.noteln("  Vignetting in #2 but not #1:");
         Console.noteln("    -> Flats are working correctly (keep them)");
      }

      // Save result metadata for deferred scoring
      if (params.diagnosticMode && resultMeta.length > 0) {
         saveResultMeta(params, resultMeta, obsContext);
      }
   }
}

// ============================================================================
// Save result metadata for deferred scoring (Score Last Run script)
// ============================================================================

function saveResultMeta(params, resultMeta, obsContext) {
   var oc = obsContext || {};
   var targetTypeStr = (params.targetType > 0 && params.targetType < TARGET_TYPES.length) ? TARGET_TYPES[params.targetType] : "";

   // Build the pending record with everything except human scores/notes
   var record = '{\n';
   record += '  "timestamp": "' + new Date().toISOString() + '",\n';
   record += '  "version": "' + VERSION + '",\n';
   record += '  "lightDir": "' + params.lightDir.replace(/\\/g, "/") + '",\n';
   record += '  "mastersDir": "' + params.mastersDir.replace(/\\/g, "/") + '",\n';
   record += '  "pedestal": ' + params.pedestal + ',\n';
   record += '  "observation_context": {\n';
   record += '    "object": "' + (oc.object || "") + '",\n';
   record += '    "ra": "' + (oc.ra || "") + '",\n';
   record += '    "dec": "' + (oc.dec || "") + '",\n';
   record += '    "date_obs": "' + (oc.dateObs || "") + '",\n';
   record += '    "exposure_s": ' + (oc.exposure || 0) + ',\n';
   record += '    "gain": ' + (oc.gain || 0) + ',\n';
   record += '    "ccd_temp_c": ' + (oc.ccdTemp || 0) + ',\n';
   record += '    "binning": ' + (oc.binning || 1) + ',\n';
   record += '    "camera": "' + (oc.camera || "") + '",\n';
   record += '    "telescope": "' + (oc.telescope || "") + '",\n';
   record += '    "focal_length_mm": ' + (oc.focalLength || 0) + ',\n';
   record += '    "aperture_mm": ' + (oc.aperture || 0) + ',\n';
   record += '    "airmass": ' + (oc.airmass || 0).toFixed(3) + ',\n';
   record += '    "altitude_deg": ' + (oc.altitude || 0) + ',\n';
   record += '    "site_latitude": ' + (oc.siteLatitude || 0).toFixed(4) + ',\n';
   record += '    "site_longitude": ' + (oc.siteLongitude || 0).toFixed(4) + ',\n';
   record += '    "fits_filter": "' + (oc.filter || "") + '",\n';
   record += '    "is_narrowband": ' + (oc.isNarrowband ? 'true' : 'false') + ',\n';
   record += '    "moon_illumination_pct": ' + (oc.moonIllumination || 0) + ',\n';
   record += '    "moon_phase": "' + (oc.moonPhase || "") + '",\n';
   record += '    "target_type": "' + targetTypeStr + '",\n';
   record += '    "bortle_class": 7\n';
   record += '  },\n';

   // Filters involved
   var filters = [];
   var seenFilters = {};
   for (var i = 0; i < resultMeta.length; i++) {
      if (!seenFilters[resultMeta[i].filter]) {
         filters.push(resultMeta[i].filter);
         seenFilters[resultMeta[i].filter] = true;
      }
   }
   record += '  "filters": ' + JSON.stringify(filters) + ',\n';

   // Quantitative metrics per combo per filter
   record += '  "metrics": [\n';
   for (var m = 0; m < resultMeta.length; m++) {
      var rm = resultMeta[m];
      var met = rm.metrics || {};
      record += '    {\n';
      record += '      "filter": "' + rm.filter + '",\n';
      record += '      "combo": "' + rm.combo + '",\n';
      record += '      "median": ' + (met.median || 0).toFixed(8) + ',\n';
      record += '      "mad": ' + (met.mad || 0).toFixed(8) + ',\n';
      record += '      "noise": ' + (met.noise || 0).toFixed(8) + ',\n';
      record += '      "snr": ' + (met.snr || 0).toFixed(2) + ',\n';
      record += '      "mean": ' + (met.mean || 0).toFixed(8) + '\n';
      record += '    }' + (m < resultMeta.length - 1 ? ',' : '') + '\n';
   }
   record += '  ]\n';
   record += '}';

   // Write pending result to temp file for Score Last Run to pick up
   var baseOutDir = params.outputDir;
   if (baseOutDir.length == 0)
      baseOutDir = params.lightDir + "/diagnostic";

   var pendingPath = baseOutDir + "/pending_score.json";

   try {
      var fOut = new File;
      fOut.createForWriting(pendingPath);
      fOut.write(ByteArray.stringToUTF8(record));
      fOut.close();
      Console.noteln("");
      Console.noteln("Result metadata saved: " + pendingPath);
      Console.noteln("When ready, run: Script > CascadiaPhotoelectric > Score Last Run");
   } catch(e) {
      Console.criticalln("Failed to write pending score: " + e.message);
   }
}

// ============================================================================
// Dialog
// ============================================================================

function CalDiagDialog(params) {
   this.__base__ = Dialog;
   this.__base__();

   this.windowTitle = TITLE + " v" + VERSION;
   this.minWidth = 640;

   var self = this;

   // ---- Title label ----
   this.titleLabel = new Label(this);
   this.titleLabel.text = TITLE;
   this.titleLabel.textAlignment = TextAlign_Center;
   this.titleLabel.styleSheet = "font-size: 14px; font-weight: bold; padding: 4px;";

   this.descLabel = new Label(this);
   this.descLabel.text = "Filter-aware calibration with togglable masters. " +
      "Diagnostic mode tests dark/flat combinations per filter.";
   this.descLabel.textAlignment = TextAlign_Center;
   this.descLabel.styleSheet = "color: #888; padding-bottom: 8px;";

   // ---- Light frames section ----
   this.lightGroup = new GroupBox(this);
   this.lightGroup.title = "Light Frames";

   this.lightDirLabel = new Label(this);
   this.lightDirLabel.text = "Lights root directory (scanned recursively):";
   this.lightDirLabel.textAlignment = TextAlign_Left;

   this.lightDirEdit = new Edit(this);
   this.lightDirEdit.text = params.lightDir;
   this.lightDirEdit.toolTip = "Root directory containing light frames. Subdirectories " +
      "(date folders) are scanned recursively. Only frames matching the selected filter " +
      "are calibrated. Skips: calibrated/, diagnostic/, registered/, master/, logs/";
   this.lightDirEdit.onTextUpdated = function() { params.lightDir = this.text; };

   this.lightDirButton = new ToolButton(this);
   this.lightDirButton.icon = this.scaledResource(":/browser/select-file.png");
   this.lightDirButton.setScaledFixedSize(22, 22);
   this.lightDirButton.toolTip = "Select light frames root directory";
   this.lightDirButton.onClick = function() {
      var dir = new GetDirectoryDialog;
      dir.caption = "Select Light Frames Root Directory";
      if (params.lightDir.length > 0)
         dir.initialPath = params.lightDir;
      if (dir.execute()) {
         params.lightDir = dir.directory;
         self.lightDirEdit.text = dir.directory;
      }
   };

   this.lightDirSizer = new HorizontalSizer;
   this.lightDirSizer.spacing = 4;
   this.lightDirSizer.add(this.lightDirEdit, 100);
   this.lightDirSizer.add(this.lightDirButton);

   // Filter selection
   this.filterLabel = new Label(this);
   this.filterLabel.text = "Filter:";
   this.filterLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.filterLabel.setFixedWidth(50);

   this.filterCombo = new ComboBox(this);
   for (var i = 0; i < FILTER_CHOICES.length; i++)
      this.filterCombo.addItem(FILTER_CHOICES[i]);
   this.filterCombo.currentItem = params.filterIndex;
   this.filterCombo.toolTip = "Select which filter's frames to calibrate. " +
      "Extracts filter from ASIAIR naming (_Bin1_H_gain...) or NINA naming (_Ha_...).";
   this.filterCombo.onItemSelected = function(idx) { params.filterIndex = idx; };

   // Max frames
   this.maxFramesLabel = new Label(this);
   this.maxFramesLabel.text = "Test frames (0=all):";
   this.maxFramesLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.maxFramesLabel.setFixedWidth(130);

   this.maxFramesSpinBox = new SpinBox(this);
   this.maxFramesSpinBox.minValue = 0;
   this.maxFramesSpinBox.maxValue = 999;
   this.maxFramesSpinBox.value = params.maxFrames;
   this.maxFramesSpinBox.toolTip = "Number of frames to calibrate (0 = all). " +
      "Use 1-5 for fast A/B diagnostic testing.";
   this.maxFramesSpinBox.onValueUpdated = function(v) { params.maxFrames = v; };

   this.filterFrameSizer = new HorizontalSizer;
   this.filterFrameSizer.spacing = 8;
   this.filterFrameSizer.add(this.filterLabel);
   this.filterFrameSizer.add(this.filterCombo);
   this.filterFrameSizer.addSpacing(20);
   this.filterFrameSizer.add(this.maxFramesLabel);
   this.filterFrameSizer.add(this.maxFramesSpinBox);
   this.filterFrameSizer.addStretch();

   this.lightGroup.sizer = new VerticalSizer;
   this.lightGroup.sizer.margin = 6;
   this.lightGroup.sizer.spacing = 4;
   this.lightGroup.sizer.add(this.lightDirLabel);
   this.lightGroup.sizer.add(this.lightDirSizer);
   this.lightGroup.sizer.add(this.filterFrameSizer);

   // ---- Masters directory (auto-discovery) ----
   this.mastersGroup = new GroupBox(this);
   this.mastersGroup.title = "Calibration Masters";

   this.mastersDirLabel = new Label(this);
   this.mastersDirLabel.text = "Masters directory (auto-finds dark + flat for selected filter):";
   this.mastersDirLabel.textAlignment = TextAlign_Left;

   this.mastersDirEdit = new Edit(this);
   this.mastersDirEdit.text = params.mastersDir;
   this.mastersDirEdit.toolTip = "Directory containing master calibration files. " +
      "Searches for master_dark* and master_flat_FilterName* in this dir and nb/, rgb/ subdirs. " +
      "Automatically matches dark by gain (NB=gain100, RGB=gain-25) and flat by filter name.";
   this.mastersDirEdit.onTextUpdated = function() { params.mastersDir = this.text; };

   this.mastersDirButton = new ToolButton(this);
   this.mastersDirButton.icon = this.scaledResource(":/browser/select-file.png");
   this.mastersDirButton.setScaledFixedSize(22, 22);
   this.mastersDirButton.toolTip = "Select masters directory";
   this.mastersDirButton.onClick = function() {
      var dir = new GetDirectoryDialog;
      dir.caption = "Select Masters Directory";
      if (params.mastersDir.length > 0)
         dir.initialPath = params.mastersDir;
      if (dir.execute()) {
         params.mastersDir = dir.directory;
         self.mastersDirEdit.text = dir.directory;
      }
   };

   this.mastersDirSizer = new HorizontalSizer;
   this.mastersDirSizer.spacing = 4;
   this.mastersDirSizer.add(this.mastersDirEdit, 100);
   this.mastersDirSizer.add(this.mastersDirButton);

   this.orLabel = new Label(this);
   this.orLabel.text = "-- or select individual master files below --";
   this.orLabel.textAlignment = TextAlign_Center;
   this.orLabel.styleSheet = "color: #666; font-style: italic; padding: 2px;";

   // Helper to create a file-picker row with enable checkbox
   function masterRow(parent, label, pathProp, enableProp, tooltip) {
      var row = {};

      row.check = new CheckBox(parent);
      row.check.text = label;
      row.check.checked = params[enableProp];
      row.check.toolTip = "Enable/disable " + label.toLowerCase() + " calibration";
      row.check.setFixedWidth(80);
      row.check.onCheck = function(checked) { params[enableProp] = checked; };

      row.edit = new Edit(parent);
      row.edit.text = params[pathProp];
      row.edit.toolTip = tooltip;
      row.edit.onTextUpdated = function() { params[pathProp] = this.text; };

      row.button = new ToolButton(parent);
      row.button.icon = parent.scaledResource(":/browser/select-file.png");
      row.button.setScaledFixedSize(22, 22);
      row.button.toolTip = "Select " + label.toLowerCase() + " master file";
      row.button.onClick = function() {
         var ofd = new OpenFileDialog;
         ofd.caption = "Select Master " + label;
         ofd.filters = [
            ["XISF Files", "*.xisf"],
            ["FITS Files", "*.fit;*.fits"],
            ["All Files", "*.*"]
         ];
         if (ofd.execute()) {
            params[pathProp] = ofd.fileName;
            row.edit.text = ofd.fileName;
         }
      };

      row.sizer = new HorizontalSizer;
      row.sizer.spacing = 4;
      row.sizer.add(row.check);
      row.sizer.add(row.edit, 100);
      row.sizer.add(row.button);

      return row;
   }

   this.darkRow = masterRow(this, "Dark", "darkPath", "useDark",
      "Master dark frame. Auto-filled from masters dir, or select manually. " +
      "Must match lights gain/temp/exposure.");
   this.flatRow = masterRow(this, "Flat", "flatPath", "useFlat",
      "Master flat frame. Auto-filled from masters dir for selected filter, or select manually.");
   this.biasRow = masterRow(this, "Bias", "biasPath", "useBias",
      "Master bias frame. Usually disabled for CMOS cameras (use pedestal instead).");

   this.mastersGroup.sizer = new VerticalSizer;
   this.mastersGroup.sizer.margin = 6;
   this.mastersGroup.sizer.spacing = 4;
   this.mastersGroup.sizer.add(this.mastersDirLabel);
   this.mastersGroup.sizer.add(this.mastersDirSizer);
   this.mastersGroup.sizer.add(this.orLabel);
   this.mastersGroup.sizer.add(this.darkRow.sizer);
   this.mastersGroup.sizer.add(this.flatRow.sizer);
   this.mastersGroup.sizer.add(this.biasRow.sizer);

   // ---- Parameters section ----
   this.paramsGroup = new GroupBox(this);
   this.paramsGroup.title = "ImageCalibration Parameters";

   this.pedestalLabel = new Label(this);
   this.pedestalLabel.text = "Output pedestal (DN):";
   this.pedestalLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.pedestalLabel.setFixedWidth(150);

   this.pedestalSpinBox = new SpinBox(this);
   this.pedestalSpinBox.minValue = 0;
   this.pedestalSpinBox.maxValue = 1000;
   this.pedestalSpinBox.value = params.pedestal;
   this.pedestalSpinBox.toolTip = "Output pedestal in DN. 150 prevents black-point clipping " +
      "after dark subtraction on CMOS narrowband data.";
   this.pedestalSpinBox.onValueUpdated = function(v) { params.pedestal = v; };

   this.pedestalSizer = new HorizontalSizer;
   this.pedestalSizer.spacing = 4;
   this.pedestalSizer.add(this.pedestalLabel);
   this.pedestalSizer.add(this.pedestalSpinBox);
   this.pedestalSizer.addStretch();

   this.paramsGroup.sizer = new VerticalSizer;
   this.paramsGroup.sizer.margin = 6;
   this.paramsGroup.sizer.spacing = 4;
   this.paramsGroup.sizer.add(this.pedestalSizer);

   // ---- Diagnostic mode section ----
   this.diagGroup = new GroupBox(this);
   this.diagGroup.title = "Diagnostic Mode";

   this.diagCheck = new CheckBox(this);
   this.diagCheck.text = "A/B Diagnostic (run 3 dark/flat combinations)";
   this.diagCheck.checked = params.diagnosticMode;
   this.diagCheck.toolTip = "Calibrates with: Dark+Flat, Dark-only, and Flat-only. " +
      "Opens one result from each for side-by-side comparison. " +
      "Immediately reveals whether darks or flats are causing artifacts.";
   this.diagCheck.onCheck = function(checked) { params.diagnosticMode = checked; };

   this.stfCheck = new CheckBox(this);
   this.stfCheck.text = "Apply auto-STF to results (recommended for linear data review)";
   this.stfCheck.checked = params.autoSTF;
   this.stfCheck.toolTip = "Applies linked auto-STF with -2.8 sigma clip and 0.25 target " +
      "background. Same formula used by astro-piper breakpoint review.";
   this.stfCheck.onCheck = function(checked) { params.autoSTF = checked; };

   this.diagGroup.sizer = new VerticalSizer;
   this.diagGroup.sizer.margin = 6;
   this.diagGroup.sizer.spacing = 4;
   this.diagGroup.sizer.add(this.diagCheck);
   this.diagGroup.sizer.add(this.stfCheck);

   // ---- Observation context section ----
   this.ctxGroup = new GroupBox(this);
   this.ctxGroup.title = "Observation Context (logged for calibration learning)";

   this.targetTypeLabel = new Label(this);
   this.targetTypeLabel.text = "Target type:";
   this.targetTypeLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.targetTypeLabel.setFixedWidth(90);

   this.targetTypeCombo = new ComboBox(this);
   for (var i = 0; i < TARGET_TYPES.length; i++)
      this.targetTypeCombo.addItem(TARGET_TYPES[i]);
   this.targetTypeCombo.currentItem = params.targetType;
   this.targetTypeCombo.toolTip = "What kind of object is this? Affects optimal calibration strategy. " +
      "Extended objects are more sensitive to flat field quality.";
   this.targetTypeCombo.onItemSelected = function(idx) { params.targetType = idx; };

   this.ctxRow1 = new HorizontalSizer;
   this.ctxRow1.spacing = 8;
   this.ctxRow1.add(this.targetTypeLabel);
   this.ctxRow1.add(this.targetTypeCombo);
   this.ctxRow1.addStretch();

   this.ctxNote = new Label(this);
   this.ctxNote.text = "Equipment, RA/Dec, date, moon phase, airmass are auto-extracted from FITS headers.";
   this.ctxNote.styleSheet = "color: #666; font-style: italic; font-size: 11px;";

   this.ctxGroup.sizer = new VerticalSizer;
   this.ctxGroup.sizer.margin = 6;
   this.ctxGroup.sizer.spacing = 4;
   this.ctxGroup.sizer.add(this.ctxRow1);
   this.ctxGroup.sizer.add(this.ctxNote);

   // ---- Output directory ----
   this.outDirLabel = new Label(this);
   this.outDirLabel.text = "Output Directory (blank = lightDir/diagnostic/Filter/):";
   this.outDirLabel.textAlignment = TextAlign_Left;

   this.outDirEdit = new Edit(this);
   this.outDirEdit.text = params.outputDir;
   this.outDirEdit.toolTip = "Where calibrated frames are written. " +
      "Leave blank to auto-create diagnostic/Filter/ subfolder in the lights directory.";
   this.outDirEdit.onTextUpdated = function() { params.outputDir = this.text; };

   this.outDirButton = new ToolButton(this);
   this.outDirButton.icon = this.scaledResource(":/browser/select-file.png");
   this.outDirButton.setScaledFixedSize(22, 22);
   this.outDirButton.onClick = function() {
      var dir = new GetDirectoryDialog;
      dir.caption = "Select Output Directory";
      if (dir.execute()) {
         params.outputDir = dir.directory;
         self.outDirEdit.text = dir.directory;
      }
   };

   this.outDirSizer = new HorizontalSizer;
   this.outDirSizer.spacing = 4;
   this.outDirSizer.add(this.outDirEdit, 100);
   this.outDirSizer.add(this.outDirButton);

   // ---- Buttons ----
   this.okButton = new PushButton(this);
   this.okButton.text = "Execute";
   this.okButton.icon = this.scaledResource(":/icons/power.png");
   this.okButton.toolTip = "Run calibration with current settings";
   this.okButton.onClick = function() { self.ok(); };

   this.cancelButton = new PushButton(this);
   this.cancelButton.text = "Close";
   this.cancelButton.icon = this.scaledResource(":/icons/close.png");
   this.cancelButton.onClick = function() { self.cancel(); };

   this.resetButton = new PushButton(this);
   this.resetButton.text = "Reset";
   this.resetButton.toolTip = "Reset all parameters to pipeline defaults";
   this.resetButton.onClick = function() {
      var defaults = new CalDiagParameters;
      self.maxFramesSpinBox.value   = defaults.maxFrames;
      self.pedestalSpinBox.value    = defaults.pedestal;
      self.filterCombo.currentItem  = defaults.filterIndex;
      self.diagCheck.checked        = defaults.diagnosticMode;
      self.stfCheck.checked         = defaults.autoSTF;
      self.darkRow.check.checked    = defaults.useDark;
      self.flatRow.check.checked    = defaults.useFlat;
      self.biasRow.check.checked    = defaults.useBias;
      params.maxFrames      = defaults.maxFrames;
      params.pedestal       = defaults.pedestal;
      params.filterIndex    = defaults.filterIndex;
      params.diagnosticMode = defaults.diagnosticMode;
      params.autoSTF        = defaults.autoSTF;
      params.useDark        = defaults.useDark;
      params.useFlat        = defaults.useFlat;
      params.useBias        = defaults.useBias;
      self.targetTypeCombo.currentItem = defaults.targetType;
      params.targetType     = defaults.targetType;
   };

   this.buttonSizer = new HorizontalSizer;
   this.buttonSizer.spacing = 8;
   this.buttonSizer.add(this.resetButton);
   this.buttonSizer.addStretch();
   this.buttonSizer.add(this.okButton);
   this.buttonSizer.add(this.cancelButton);

   // ---- Main layout ----
   this.sizer = new VerticalSizer;
   this.sizer.margin = 8;
   this.sizer.spacing = 8;
   this.sizer.add(this.titleLabel);
   this.sizer.add(this.descLabel);
   this.sizer.add(this.lightGroup);
   this.sizer.add(this.mastersGroup);
   this.sizer.add(this.paramsGroup);
   this.sizer.add(this.diagGroup);
   this.sizer.add(this.ctxGroup);
   this.sizer.add(this.outDirLabel);
   this.sizer.add(this.outDirSizer);
   this.sizer.addSpacing(4);
   this.sizer.add(this.buttonSizer);

   this.adjustToContents();
}
CalDiagDialog.prototype = new Dialog;

// ============================================================================
// Entry point
// ============================================================================

function main() {
   Console.writeln("");
   Console.writeln(TITLE + " v" + VERSION);
   Console.writeln("Filter-aware calibration with diagnostic A/B testing");
   Console.writeln("");

   var params = new CalDiagParameters;
   params.load();

   var dialog = new CalDiagDialog(params);

   while (true) {
      var result = dialog.execute();
      if (result != StdButton_Ok)
         break;

      // Validate
      if (params.lightDir.length == 0) {
         var mb = new MessageBox(
            "Please select a light frames directory.",
            TITLE, StdIcon_Error, StdButton_Ok
         );
         mb.execute();
         continue;
      }

      // Check that at least one master is available (either via mastersDir or manual)
      var hasAnyMaster = false;
      if (params.useDark && (params.darkPath.length > 0 || params.mastersDir.length > 0))
         hasAnyMaster = true;
      if (params.useFlat && (params.flatPath.length > 0 || params.mastersDir.length > 0))
         hasAnyMaster = true;

      if (!hasAnyMaster) {
         var mb = new MessageBox(
            "No calibration masters available.\n\n" +
            "Either set a Masters Directory (auto-discovers dark + flat),\n" +
            "or manually select dark/flat files and enable their checkboxes.",
            TITLE, StdIcon_Error, StdButton_Ok
         );
         mb.execute();
         continue;
      }

      // Save settings and run
      params.save();
      Console.show();
      executeCalDiag(params);
      break;
   }
}

main();
