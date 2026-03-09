// ============================================================================
// CascadiaPhotoelectric Master Builder v1.3.0
// ============================================================================
//
// PixInsight Script -- appears under Script > CascadiaPhotoelectric > Master Builder
//
// PURPOSE: Builds master calibration frames (darks, flats, biases) from raw
// sub-frames using ImageIntegration with interactive parameter tuning and
// optional A/B comparison of rejection algorithms.
//
// FEATURES:
//   - Recursive sub-frame discovery by filename prefix (Dark_*, Flat_*, Bias_*)
//   - Filter-aware flat sub matching (extracts filter from ASIAIR/NINA naming)
//   - Optional master dark pre-calibration of flat subs before integration
//   - Full rejection algorithm tuning: ESD, WinsorizedSigma, LinearFit, etc.
//   - A/B mode: runs 2-3 algorithms side by side for comparison
//   - Measures and logs quality metrics (median, MAD, uniformity)
//   - Saves pending_master.json for Score Last Run to pick up
//   - Persistent settings via Settings.read/write
//
// INSTALL: Copy this file to:
//   [PixInsight]/src/scripts/CascadiaPhotoelectric/CascadiaPhotoelectric-MasterBuilder.js
//   Then: Script > Feature Scripts > Add > select the CascadiaPhotoelectric folder
//   It will appear under: Script > CascadiaPhotoelectric > Master Builder
//
// ============================================================================

#feature-id   CascadiaPhotoelectric > Master Builder
#feature-info Build master darks, flats, and biases with A/B rejection comparison.

#include <pjsr/DataType.jsh>
#include <pjsr/Sizer.jsh>
#include <pjsr/FrameStyle.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>
#include <pjsr/NumericControl.jsh>

#define VERSION "1.3.0"
#define TITLE   "CascadiaPhotoelectric Master Builder"

// ============================================================================
// ImageIntegration numeric constants (PI 1.9.x compatible)
// ============================================================================

// Rejection algorithms
var REJECTION_NONE             = 0;
var REJECTION_MINMAX            = 1;
var REJECTION_PERCENTILE_CLIP   = 2;
var REJECTION_SIGMA_CLIP        = 3;
var REJECTION_WINSORIZED_SIGMA  = 4;
var REJECTION_AVERAGED_SIGMA    = 5;
var REJECTION_LINEAR_FIT        = 6;
var REJECTION_CCD_CLIP          = 7;
var REJECTION_ESD               = 8;

// Normalization modes
var NORM_NONE                   = 0;
var NORM_ADDITIVE               = 1;
var NORM_MULTIPLICATIVE         = 2;
var NORM_ADDITIVE_SCALING       = 3;
var NORM_MULTIPLICATIVE_SCALING = 4;
var NORM_LOCAL                  = 5;

// Weight modes
var WEIGHT_DONT_CARE            = 0;
var WEIGHT_EXPOSURE_TIME        = 1;
var WEIGHT_NOISE_EVALUATION     = 2;
var WEIGHT_SIGNAL_WEIGHT        = 3;
var WEIGHT_MEDIAN_WEIGHT        = 4;

// Combination methods
var COMBINATION_AVERAGE         = 0;
var COMBINATION_MEDIAN          = 1;
var COMBINATION_MINIMUM         = 2;
var COMBINATION_MAXIMUM         = 3;

// Rejection choices for the UI dropdown
var REJECTION_CHOICES = [
   "ESD (Generalized Extreme Studentized Deviate)",
   "Winsorized Sigma Clipping",
   "Linear Fit Clipping",
   "Percentile Clipping",
   "No Rejection"
];
// Map dropdown index to PI constant
var REJECTION_MAP = [
   REJECTION_ESD,
   REJECTION_WINSORIZED_SIGMA,
   REJECTION_LINEAR_FIT,
   REJECTION_PERCENTILE_CLIP,
   REJECTION_NONE
];

// Normalization choices for the UI dropdown
var NORMALIZATION_CHOICES = [
   "Multiplicative (for flats)",
   "Additive with Scaling (for darks)",
   "Additive",
   "No Normalization (for bias)"
];
var NORMALIZATION_MAP = [
   NORM_MULTIPLICATIVE,
   NORM_ADDITIVE_SCALING,
   NORM_ADDITIVE,
   NORM_NONE
];

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

var FILTER_MAP = {
   "H":    "Ha",
   "Ha":   "Ha",
   "O":    "OIII",
   "OIII": "OIII",
   "S":    "SII",
   "SII":  "SII",
   "R":    "R",
   "G":    "G",
   "B":    "B",
   "L":    "L"
};

function extractFilter(filename) {
   // Try ASIAIR pattern: _Bin1_X_gain  (single letter filter)
   var m = filename.match(/_Bin\d+_([A-Za-z]+)_gain/);
   if (m) {
      var token = m[1];
      if (FILTER_MAP[token] !== undefined)
         return FILTER_MAP[token];
   }
   // Try NINA pattern: _FilterName_ anywhere in filename
   var tokens = ["OIII", "SII", "Ha", "R", "G", "B", "L"];
   for (var i = 0; i < tokens.length; i++) {
      var re = new RegExp("_" + tokens[i] + "_", "i");
      if (re.test(filename))
         return FILTER_MAP[tokens[i]] || tokens[i];
   }
   return null;
}

// ============================================================================
// File I/O helpers
// ============================================================================

function readTextFile(path) {
   var f = new File;
   f.openForReading(path);
   var buf = f.read(DataType_ByteArray, f.size);
   f.close();
   return buf.toString();
}

function writeTextFile(path, text) {
   var f = new File;
   f.createForWriting(path);
   f.write(ByteArray.stringToUTF8(text));
   f.close();
}

function ensureDir(path) {
   if (!File.directoryExists(path)) {
      var parent = path.replace(/\/[^\/]+\/?$/, "");
      if (parent.length > 0 && parent != path)
         ensureDir(parent);
      File.createDirectory(path, true);
   }
}

// ============================================================================
// Recursive sub-frame discovery (for calibration frames, not lights)
// ============================================================================

function findSubFrames(dir, frameType, filterName, extensions) {
   // Discovers sub-frames by filename prefix and optional filter match
   // frameType: "Dark", "Flat", "Bias"
   // filterName: canonical filter name for flats (e.g. "Ha"), or "" for darks/biases
   var results = [];
   var prefix = frameType.toLowerCase();  // "dark", "flat", "bias"
   var ff = new FileFind;

   for (var e = 0; e < extensions.length; e++) {
      if (ff.begin(dir + "/*" + extensions[e])) {
         do {
            if (!ff.isDirectory) {
               var ln = ff.name.toLowerCase();
               // Match by prefix: dark_*, flat_*, bias_*
               if (ln.indexOf(prefix + "_") == 0 || ln.indexOf(prefix + "-") == 0) {
                  // For flats, also filter by filter name if specified
                  if (frameType == "Flat" && filterName.length > 0) {
                     var detected = extractFilter(ff.name);
                     if (detected != filterName)
                        continue;
                  }
                  // Skip master files
                  if (ln.indexOf("master") == 0)
                     continue;
                  results.push(dir + "/" + ff.name);
               }
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
             dn != "ldd_lps" && dn != "logs" && dn != "output") {
            var subResults = findSubFrames(dir + "/" + ff.name, frameType, filterName, extensions);
            for (var i = 0; i < subResults.length; i++)
               results.push(subResults[i]);
         }
      } while (ff.next());
   }

   return results;
}

// ============================================================================
// Metrics measurement
// ============================================================================

function measureMasterMetrics(filePath) {
   var metrics = { median: 0, mad: 0, mean: 0, noise: 0, snr: 0, uniformity: 0 };
   try {
      var wins = ImageWindow.open(filePath);
      if (wins.length > 0 && !wins[0].isNull) {
         var view = wins[0].currentView;
         var img = view.image;
         img.selectedChannel = 0;

         metrics.median = view.computeOrFetchProperty("Median").at(0);
         metrics.mad    = view.computeOrFetchProperty("MAD").at(0) * 1.4826;
         metrics.mean   = view.computeOrFetchProperty("Mean").at(0);

         try {
            var ne = view.computeOrFetchProperty("NoiseEstimate_MRS");
            if (ne) metrics.noise = ne.at(0);
         } catch(e) {
            metrics.noise = metrics.mad;
         }

         if (metrics.noise > 0)
            metrics.snr = metrics.median / metrics.noise;

         // Uniformity: 1 - (MAD / median) -- higher is more uniform
         if (metrics.median > 0)
            metrics.uniformity = 1.0 - (metrics.mad / metrics.median);

         img.resetSelections();
         wins[0].forceClose();
      }
   } catch(e) {
      Console.warningln("  Could not measure metrics: " + e.message);
   }
   return metrics;
}

// ============================================================================
// Pre-calibrate flat subs with master dark
// ============================================================================

function preCalibrateFlats(flatFiles, masterDarkPath, outputDir) {
   Console.writeln("");
   Console.writeln("=== Pre-calibrating flat subs with master dark ===");
   Console.writeln("  Master dark: " + masterDarkPath);
   Console.writeln("  Flat subs: " + flatFiles.length);

   var calDir = outputDir + "/calibrated_flats";
   ensureDir(calDir);

   var targets = [];
   for (var i = 0; i < flatFiles.length; i++)
      targets.push([true, flatFiles[i]]);

   var P = new ImageCalibration;
   P.targetFrames = targets;

   // Dark subtract only -- no flat, no bias
   P.masterBiasEnabled          = false;
   P.masterBiasPath             = "";
   P.masterDarkEnabled          = true;
   P.masterDarkPath             = masterDarkPath;
   P.masterDarkOptimizationLow  = 3.0;
   P.masterDarkOptimizationWindow = 1024;
   P.masterFlatEnabled          = false;
   P.masterFlatPath             = "";
   P.outputDirectory            = calDir;
   P.outputExtension            = ".xisf";
   P.outputPostfix              = "_c";
   P.outputSampleFormat         = 4;    // f32
   P.pedestal                   = 0;    // no pedestal for flat pre-cal
   P.enableCFA                  = false;
   P.noiseEvaluation            = false;
   P.overwriteExistingFiles     = true;

   P.executeGlobal();

   // Collect calibrated flat files
   var calFiles = [];
   var search = new FileFind;
   if (search.begin(calDir + "/*_c.xisf")) {
      do {
         if (!search.isDirectory)
            calFiles.push(calDir + "/" + search.name);
      } while (search.next());
   }
   calFiles.sort();

   Console.writeln("  Pre-calibrated " + calFiles.length + " flat subs -> " + calDir);
   return calFiles;
}

// ============================================================================
// Core integration engine
// ============================================================================

function runIntegration(subFiles, params, rejectionCode, rejectionLabel, outputDir, windowPrefix) {
   Console.writeln("");
   Console.writeln("=== ImageIntegration: " + rejectionLabel + " ===");
   Console.writeln("  Sub-frames: " + subFiles.length);
   Console.writeln("  Rejection: " + rejectionLabel + " (code " + rejectionCode + ")");
   Console.writeln("  Normalization: " + NORMALIZATION_CHOICES[params.normIndex] + " (code " + NORMALIZATION_MAP[params.normIndex] + ")");
   Console.writeln("  Sigma low/high: " + params.sigmaLow.toFixed(2) + " / " + params.sigmaHigh.toFixed(2));

   ensureDir(outputDir);

   // Build image list for ImageIntegration
   var images = [];
   for (var i = 0; i < subFiles.length; i++) {
      // [enabled, path, drizzlePath, localNormPath]
      images.push([true, subFiles[i], "", ""]);
   }

   var P = new ImageIntegration;
   P.images = images;
   P.combination = COMBINATION_AVERAGE;
   P.rejection = rejectionCode;
   P.normalization = NORMALIZATION_MAP[params.normIndex];

   // Weight mode: noise evaluation for flats, don't care for darks/bias
   if (params.frameType == "Flat")
      P.weightMode = WEIGHT_NOISE_EVALUATION;
   else
      P.weightMode = WEIGHT_DONT_CARE;

   // Rejection parameters
   P.rejectionNormalization = NORMALIZATION_MAP[params.normIndex];
   P.clipLow  = true;
   P.clipHigh = true;
   P.rangeClipLow  = true;
   P.rangeLow      = 0.0;
   P.rangeClipHigh = false;
   P.rangeHigh     = 0.98;

   // Sigma parameters (used by WinsorizedSigma, SigmaClip)
   P.sigmaLow  = params.sigmaLow;
   P.sigmaHigh = params.sigmaHigh;

   // LinearFit parameters
   P.linearFitLow  = params.sigmaLow;
   P.linearFitHigh = params.sigmaHigh;

   // Percentile clipping
   P.pcClipLow  = 0.20;
   P.pcClipHigh = 0.10;

   // ESD-specific parameters
   P.esdOutliersFraction = params.esdOutliersFraction;
   P.esdSignificance     = params.esdSignificance;
   P.esdLowRelaxation    = params.esdLowRelaxation;

   // Output
   P.generateDrizzleData     = false;
   P.generateIntegratedImage = true;
   P.generateRejectionMaps   = false;
   P.subtractPedestals       = false;
   P.truncateOnOutOfRange    = false;
   P.noGUIMessages           = true;

   // For darks/bias: evaluate noise; for flats: also evaluate
   P.evaluateNoise = true;

   P.executeGlobal();

   // The integrated image is the active window after executeGlobal
   // Find the integration result window
   var resultWin = null;
   var allWindows = ImageWindow.windows;
   for (var w = allWindows.length - 1; w >= 0; w--) {
      var wid = allWindows[w].currentView.id;
      if (wid.indexOf("integration") >= 0 || wid.indexOf("Integration") >= 0) {
         resultWin = allWindows[w];
         break;
      }
   }

   if (resultWin == null) {
      Console.warningln("  Could not find integration result window");
      return null;
   }

   // Rename the window
   var newId = windowPrefix + "_" + rejectionLabel.replace(/[^A-Za-z0-9]/g, "");
   try {
      resultWin.currentView.id = newId;
   } catch(e) {
      try { resultWin.currentView.id = newId + "_1"; } catch(e2) {}
   }

   // Save to output directory
   var outPath = outputDir + "/" + windowPrefix + "_" + rejectionLabel.replace(/[^A-Za-z0-9]/g, "") + ".xisf";
   resultWin.saveAs(outPath, false, false, false, false);
   Console.writeln("  Saved: " + outPath);

   // Close rejection map windows if they exist
   var currentWindows = ImageWindow.windows;
   for (var w = currentWindows.length - 1; w >= 0; w--) {
      var wid = currentWindows[w].currentView.id;
      if (wid.indexOf("rejection") >= 0 || wid.indexOf("Rejection") >= 0 ||
          wid.indexOf("slope_map") >= 0 || wid.indexOf("SlopeMap") >= 0) {
         currentWindows[w].forceClose();
      }
   }

   return { window: resultWin, path: outPath, rejectionLabel: rejectionLabel };
}

// ============================================================================
// Parameters (persistent via Settings)
// ============================================================================

function MasterBuilderParameters() {
   this.frameType         = 0;       // 0=Dark, 1=Flat, 2=Bias
   this.subsDir           = "";      // raw subs directory
   this.filterName        = "";      // for flats: filter name (auto-detected or manual)
   this.masterDarkPath    = "";      // for flats: optional pre-calibration dark
   this.rejectionIndex    = 0;       // index into REJECTION_CHOICES (0=ESD)
   this.sigmaLow          = 4.0;
   this.sigmaHigh         = 3.0;
   this.esdSignificance   = 0.05;
   this.esdOutliersFraction = 0.30;
   this.esdLowRelaxation  = 2.0;
   this.normIndex         = 0;       // index into NORMALIZATION_CHOICES
   this.outputDir         = "";
   this.abMode            = false;   // A/B rejection comparison
   this.autoSTF           = true;

   var FRAME_TYPES = ["Dark", "Flat", "Bias"];

   this.getFrameTypeName = function() {
      return FRAME_TYPES[this.frameType] || "Dark";
   };

   this.save = function() {
      Settings.write(TITLE + "/frameType",           DataType_Int32,   this.frameType);
      Settings.write(TITLE + "/subsDir",             DataType_String,  this.subsDir);
      Settings.write(TITLE + "/filterName",          DataType_String,  this.filterName);
      Settings.write(TITLE + "/masterDarkPath",      DataType_String,  this.masterDarkPath);
      Settings.write(TITLE + "/rejectionIndex",      DataType_Int32,   this.rejectionIndex);
      Settings.write(TITLE + "/sigmaLow",            DataType_Double,  this.sigmaLow);
      Settings.write(TITLE + "/sigmaHigh",           DataType_Double,  this.sigmaHigh);
      Settings.write(TITLE + "/esdSignificance",     DataType_Double,  this.esdSignificance);
      Settings.write(TITLE + "/esdOutliersFraction", DataType_Double,  this.esdOutliersFraction);
      Settings.write(TITLE + "/esdLowRelaxation",    DataType_Double,  this.esdLowRelaxation);
      Settings.write(TITLE + "/normIndex",           DataType_Int32,   this.normIndex);
      Settings.write(TITLE + "/outputDir",           DataType_String,  this.outputDir);
      Settings.write(TITLE + "/abMode",              DataType_Boolean, this.abMode);
      Settings.write(TITLE + "/autoSTF",             DataType_Boolean, this.autoSTF);
   };

   this.load = function() {
      var v;
      v = Settings.read(TITLE + "/frameType",           DataType_Int32);   if (v != null) this.frameType           = v;
      v = Settings.read(TITLE + "/subsDir",             DataType_String);  if (v != null) this.subsDir             = v;
      v = Settings.read(TITLE + "/filterName",          DataType_String);  if (v != null) this.filterName          = v;
      v = Settings.read(TITLE + "/masterDarkPath",      DataType_String);  if (v != null) this.masterDarkPath      = v;
      v = Settings.read(TITLE + "/rejectionIndex",      DataType_Int32);   if (v != null) this.rejectionIndex      = v;
      v = Settings.read(TITLE + "/sigmaLow",            DataType_Double);  if (v != null) this.sigmaLow            = v;
      v = Settings.read(TITLE + "/sigmaHigh",           DataType_Double);  if (v != null) this.sigmaHigh           = v;
      v = Settings.read(TITLE + "/esdSignificance",     DataType_Double);  if (v != null) this.esdSignificance     = v;
      v = Settings.read(TITLE + "/esdOutliersFraction", DataType_Double);  if (v != null) this.esdOutliersFraction = v;
      v = Settings.read(TITLE + "/esdLowRelaxation",    DataType_Double);  if (v != null) this.esdLowRelaxation    = v;
      v = Settings.read(TITLE + "/normIndex",           DataType_Int32);   if (v != null) this.normIndex           = v;
      v = Settings.read(TITLE + "/outputDir",           DataType_String);  if (v != null) this.outputDir           = v;
      v = Settings.read(TITLE + "/abMode",              DataType_Boolean); if (v != null) this.abMode              = v;
      v = Settings.read(TITLE + "/autoSTF",             DataType_Boolean); if (v != null) this.autoSTF             = v;
   };
}

// ============================================================================
// Dialog
// ============================================================================

function MasterBuilderDialog(params) {
   this.__base__ = Dialog;
   this.__base__();

   this.windowTitle = TITLE + " v" + VERSION;
   this.minWidth = 640;

   var self = this;
   var FRAME_TYPES = ["Dark", "Flat", "Bias"];

   // ---- Title ----
   this.titleLabel = new Label(this);
   this.titleLabel.text = TITLE;
   this.titleLabel.textAlignment = TextAlign_Center;
   this.titleLabel.styleSheet = "font-size: 14px; font-weight: bold; padding: 4px;";

   this.descLabel = new Label(this);
   this.descLabel.text = "Build master calibration frames from raw subs with rejection tuning.";
   this.descLabel.textAlignment = TextAlign_Center;
   this.descLabel.styleSheet = "color: #888; padding-bottom: 8px;";

   // ---- Frame Type ----
   this.frameGroup = new GroupBox(this);
   this.frameGroup.title = "Frame Type and Input";

   this.frameTypeLabel = new Label(this);
   this.frameTypeLabel.text = "Frame type:";
   this.frameTypeLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.frameTypeLabel.setFixedWidth(90);

   this.frameTypeCombo = new ComboBox(this);
   for (var i = 0; i < FRAME_TYPES.length; i++)
      this.frameTypeCombo.addItem(FRAME_TYPES[i]);
   this.frameTypeCombo.currentItem = params.frameType;
   this.frameTypeCombo.toolTip = "Type of master to build. Determines file prefix matching " +
      "and default normalization.";
   this.frameTypeCombo.onItemSelected = function(idx) {
      params.frameType = idx;
      // Update normalization default
      if (idx == 0) { // Dark
         self.normCombo.currentItem = 1;  // AdditiveWithScaling
         params.normIndex = 1;
      } else if (idx == 1) { // Flat
         self.normCombo.currentItem = 0;  // Multiplicative
         params.normIndex = 0;
      } else { // Bias
         self.normCombo.currentItem = 3;  // No Normalization
         params.normIndex = 3;
      }
      // Show/hide flat-specific controls
      self.updateFlatControls();
   };

   this.frameTypeSizer = new HorizontalSizer;
   this.frameTypeSizer.spacing = 8;
   this.frameTypeSizer.add(this.frameTypeLabel);
   this.frameTypeSizer.add(this.frameTypeCombo);
   this.frameTypeSizer.addStretch();

   // ---- Subs directory ----
   this.subsDirLabel = new Label(this);
   this.subsDirLabel.text = "Raw subs directory (scanned recursively):";
   this.subsDirLabel.textAlignment = TextAlign_Left;

   this.subsDirEdit = new Edit(this);
   this.subsDirEdit.text = params.subsDir;
   this.subsDirEdit.toolTip = "Root directory containing raw sub-frames. Scanned recursively for " +
      "files matching the selected frame type prefix (Dark_*, Flat_*, Bias_*).";
   this.subsDirEdit.onTextUpdated = function() { params.subsDir = this.text; };

   this.subsDirButton = new ToolButton(this);
   this.subsDirButton.icon = this.scaledResource(":/browser/select-file.png");
   this.subsDirButton.setScaledFixedSize(22, 22);
   this.subsDirButton.toolTip = "Select raw subs directory";
   this.subsDirButton.onClick = function() {
      var dir = new GetDirectoryDialog;
      dir.caption = "Select Raw Sub-Frames Directory";
      if (params.subsDir.length > 0)
         dir.initialPath = params.subsDir;
      if (dir.execute()) {
         params.subsDir = dir.directory;
         self.subsDirEdit.text = dir.directory;
      }
   };

   this.subsDirSizer = new HorizontalSizer;
   this.subsDirSizer.spacing = 4;
   this.subsDirSizer.add(this.subsDirEdit, 100);
   this.subsDirSizer.add(this.subsDirButton);

   // ---- Filter name (for flats) ----
   this.filterLabel = new Label(this);
   this.filterLabel.text = "Filter (for flats):";
   this.filterLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.filterLabel.setFixedWidth(110);

   this.filterEdit = new Edit(this);
   this.filterEdit.text = params.filterName;
   this.filterEdit.toolTip = "Canonical filter name for flat matching (e.g. Ha, OIII, SII, L, R, G, B). " +
      "Leave blank to include all flat subs regardless of filter.";
   this.filterEdit.onTextUpdated = function() { params.filterName = this.text; };

   this.filterSizer = new HorizontalSizer;
   this.filterSizer.spacing = 4;
   this.filterSizer.add(this.filterLabel);
   this.filterSizer.add(this.filterEdit, 100);

   // ---- Master dark for flat pre-calibration ----
   this.masterDarkLabel = new Label(this);
   this.masterDarkLabel.text = "Master dark for flat pre-cal (optional):";
   this.masterDarkLabel.textAlignment = TextAlign_Left;

   this.masterDarkEdit = new Edit(this);
   this.masterDarkEdit.text = params.masterDarkPath;
   this.masterDarkEdit.toolTip = "Optional master dark to subtract from flat subs before integration. " +
      "Dark-subtracts each flat sub (no bias, no flat, pedestal=0) before stacking.";
   this.masterDarkEdit.onTextUpdated = function() { params.masterDarkPath = this.text; };

   this.masterDarkButton = new ToolButton(this);
   this.masterDarkButton.icon = this.scaledResource(":/browser/select-file.png");
   this.masterDarkButton.setScaledFixedSize(22, 22);
   this.masterDarkButton.toolTip = "Select master dark for flat pre-calibration";
   this.masterDarkButton.onClick = function() {
      var ofd = new OpenFileDialog;
      ofd.caption = "Select Master Dark for Flat Pre-Calibration";
      ofd.filters = [
         ["XISF Files", "*.xisf"],
         ["FITS Files", "*.fit;*.fits"],
         ["All Files", "*.*"]
      ];
      if (ofd.execute()) {
         params.masterDarkPath = ofd.fileName;
         self.masterDarkEdit.text = ofd.fileName;
      }
   };

   this.masterDarkSizer = new HorizontalSizer;
   this.masterDarkSizer.spacing = 4;
   this.masterDarkSizer.add(this.masterDarkEdit, 100);
   this.masterDarkSizer.add(this.masterDarkButton);

   this.frameGroup.sizer = new VerticalSizer;
   this.frameGroup.sizer.margin = 6;
   this.frameGroup.sizer.spacing = 4;
   this.frameGroup.sizer.add(this.frameTypeSizer);
   this.frameGroup.sizer.add(this.subsDirLabel);
   this.frameGroup.sizer.add(this.subsDirSizer);
   this.frameGroup.sizer.add(this.filterSizer);
   this.frameGroup.sizer.add(this.masterDarkLabel);
   this.frameGroup.sizer.add(this.masterDarkSizer);

   // ---- Rejection algorithm ----
   this.rejGroup = new GroupBox(this);
   this.rejGroup.title = "Rejection Algorithm";

   this.rejLabel = new Label(this);
   this.rejLabel.text = "Algorithm:";
   this.rejLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.rejLabel.setFixedWidth(90);

   this.rejCombo = new ComboBox(this);
   for (var i = 0; i < REJECTION_CHOICES.length; i++)
      this.rejCombo.addItem(REJECTION_CHOICES[i]);
   this.rejCombo.currentItem = params.rejectionIndex;
   this.rejCombo.toolTip = "Pixel rejection algorithm for stacking. ESD is the modern default " +
      "for large frame counts (>15). WinsorizedSigma works well for moderate counts.";
   this.rejCombo.onItemSelected = function(idx) {
      params.rejectionIndex = idx;
      self.updateESDControls();
   };

   this.rejAlgoSizer = new HorizontalSizer;
   this.rejAlgoSizer.spacing = 8;
   this.rejAlgoSizer.add(this.rejLabel);
   this.rejAlgoSizer.add(this.rejCombo, 100);

   // Sigma low/high
   this.sigmaLowLabel = new Label(this);
   this.sigmaLowLabel.text = "Sigma low:";
   this.sigmaLowLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.sigmaLowLabel.setFixedWidth(90);

   this.sigmaLowSpin = new SpinBox(this);
   this.sigmaLowSpin.minValue = 10;    // 1.0
   this.sigmaLowSpin.maxValue = 100;   // 10.0
   this.sigmaLowSpin.value = Math.round(params.sigmaLow * 10);
   this.sigmaLowSpin.toolTip = "Low sigma clipping threshold (x10). Default 4.0 = value 40.";
   this.sigmaLowSpin.onValueUpdated = function(v) { params.sigmaLow = v / 10.0; };

   this.sigmaLowValLabel = new Label(this);
   this.sigmaLowValLabel.text = params.sigmaLow.toFixed(1);
   this.sigmaLowValLabel.setFixedWidth(30);
   var sigLowRef = this.sigmaLowValLabel;
   this.sigmaLowSpin.onValueUpdated = function(v) {
      params.sigmaLow = v / 10.0;
      sigLowRef.text = params.sigmaLow.toFixed(1);
   };

   this.sigmaHighLabel = new Label(this);
   this.sigmaHighLabel.text = "Sigma high:";
   this.sigmaHighLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.sigmaHighLabel.setFixedWidth(90);

   this.sigmaHighSpin = new SpinBox(this);
   this.sigmaHighSpin.minValue = 10;
   this.sigmaHighSpin.maxValue = 100;
   this.sigmaHighSpin.value = Math.round(params.sigmaHigh * 10);
   this.sigmaHighSpin.toolTip = "High sigma clipping threshold (x10). Default 3.0 = value 30.";

   this.sigmaHighValLabel = new Label(this);
   this.sigmaHighValLabel.text = params.sigmaHigh.toFixed(1);
   this.sigmaHighValLabel.setFixedWidth(30);
   var sigHighRef = this.sigmaHighValLabel;
   this.sigmaHighSpin.onValueUpdated = function(v) {
      params.sigmaHigh = v / 10.0;
      sigHighRef.text = params.sigmaHigh.toFixed(1);
   };

   this.sigmaSizer = new HorizontalSizer;
   this.sigmaSizer.spacing = 8;
   this.sigmaSizer.add(this.sigmaLowLabel);
   this.sigmaSizer.add(this.sigmaLowSpin);
   this.sigmaSizer.add(this.sigmaLowValLabel);
   this.sigmaSizer.addSpacing(16);
   this.sigmaSizer.add(this.sigmaHighLabel);
   this.sigmaSizer.add(this.sigmaHighSpin);
   this.sigmaSizer.add(this.sigmaHighValLabel);
   this.sigmaSizer.addStretch();

   // ESD-specific controls
   this.esdSigLabel = new Label(this);
   this.esdSigLabel.text = "ESD significance:";
   this.esdSigLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.esdSigLabel.setFixedWidth(120);

   this.esdSigSpin = new SpinBox(this);
   this.esdSigSpin.minValue = 1;    // 0.01
   this.esdSigSpin.maxValue = 50;   // 0.50
   this.esdSigSpin.value = Math.round(params.esdSignificance * 100);
   this.esdSigSpin.toolTip = "ESD significance level (x100). Default 0.05 = value 5.";

   this.esdSigValLabel = new Label(this);
   this.esdSigValLabel.text = params.esdSignificance.toFixed(2);
   this.esdSigValLabel.setFixedWidth(30);
   var esdSigRef = this.esdSigValLabel;
   this.esdSigSpin.onValueUpdated = function(v) {
      params.esdSignificance = v / 100.0;
      esdSigRef.text = params.esdSignificance.toFixed(2);
   };

   this.esdOutLabel = new Label(this);
   this.esdOutLabel.text = "ESD outliers frac:";
   this.esdOutLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.esdOutLabel.setFixedWidth(120);

   this.esdOutSpin = new SpinBox(this);
   this.esdOutSpin.minValue = 5;    // 0.05
   this.esdOutSpin.maxValue = 80;   // 0.80
   this.esdOutSpin.value = Math.round(params.esdOutliersFraction * 100);
   this.esdOutSpin.toolTip = "ESD maximum outliers fraction (x100). Default 0.30 = value 30.";

   this.esdOutValLabel = new Label(this);
   this.esdOutValLabel.text = params.esdOutliersFraction.toFixed(2);
   this.esdOutValLabel.setFixedWidth(30);
   var esdOutRef = this.esdOutValLabel;
   this.esdOutSpin.onValueUpdated = function(v) {
      params.esdOutliersFraction = v / 100.0;
      esdOutRef.text = params.esdOutliersFraction.toFixed(2);
   };

   this.esdRelaxLabel = new Label(this);
   this.esdRelaxLabel.text = "ESD low relaxation:";
   this.esdRelaxLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.esdRelaxLabel.setFixedWidth(120);

   this.esdRelaxSpin = new SpinBox(this);
   this.esdRelaxSpin.minValue = 10;   // 1.0
   this.esdRelaxSpin.maxValue = 50;   // 5.0
   this.esdRelaxSpin.value = Math.round(params.esdLowRelaxation * 10);
   this.esdRelaxSpin.toolTip = "ESD low relaxation factor (x10). Default 2.0 = value 20.";

   this.esdRelaxValLabel = new Label(this);
   this.esdRelaxValLabel.text = params.esdLowRelaxation.toFixed(1);
   this.esdRelaxValLabel.setFixedWidth(30);
   var esdRelaxRef = this.esdRelaxValLabel;
   this.esdRelaxSpin.onValueUpdated = function(v) {
      params.esdLowRelaxation = v / 10.0;
      esdRelaxRef.text = params.esdLowRelaxation.toFixed(1);
   };

   this.esdRow1 = new HorizontalSizer;
   this.esdRow1.spacing = 8;
   this.esdRow1.add(this.esdSigLabel);
   this.esdRow1.add(this.esdSigSpin);
   this.esdRow1.add(this.esdSigValLabel);
   this.esdRow1.addSpacing(8);
   this.esdRow1.add(this.esdOutLabel);
   this.esdRow1.add(this.esdOutSpin);
   this.esdRow1.add(this.esdOutValLabel);
   this.esdRow1.addStretch();

   this.esdRow2 = new HorizontalSizer;
   this.esdRow2.spacing = 8;
   this.esdRow2.add(this.esdRelaxLabel);
   this.esdRow2.add(this.esdRelaxSpin);
   this.esdRow2.add(this.esdRelaxValLabel);
   this.esdRow2.addStretch();

   // Normalization
   this.normLabel = new Label(this);
   this.normLabel.text = "Normalization:";
   this.normLabel.textAlignment = TextAlign_Right | TextAlign_VertCenter;
   this.normLabel.setFixedWidth(90);

   this.normCombo = new ComboBox(this);
   for (var i = 0; i < NORMALIZATION_CHOICES.length; i++)
      this.normCombo.addItem(NORMALIZATION_CHOICES[i]);
   this.normCombo.currentItem = params.normIndex;
   this.normCombo.toolTip = "Normalization mode. Multiplicative for flats (preserves relative levels), " +
      "AdditiveWithScaling for darks (preserves thermal signal structure).";
   this.normCombo.onItemSelected = function(idx) { params.normIndex = idx; };

   this.normSizer = new HorizontalSizer;
   this.normSizer.spacing = 8;
   this.normSizer.add(this.normLabel);
   this.normSizer.add(this.normCombo, 100);

   this.rejGroup.sizer = new VerticalSizer;
   this.rejGroup.sizer.margin = 6;
   this.rejGroup.sizer.spacing = 4;
   this.rejGroup.sizer.add(this.rejAlgoSizer);
   this.rejGroup.sizer.add(this.sigmaSizer);
   this.rejGroup.sizer.add(this.esdRow1);
   this.rejGroup.sizer.add(this.esdRow2);
   this.rejGroup.sizer.add(this.normSizer);

   // ---- Options section ----
   this.optGroup = new GroupBox(this);
   this.optGroup.title = "Options";

   this.abCheck = new CheckBox(this);
   this.abCheck.text = "A/B mode: run ESD, WinsorizedSigma, and LinearFit side by side";
   this.abCheck.checked = params.abMode;
   this.abCheck.toolTip = "Runs integration with three rejection algorithms and opens all results " +
      "for visual comparison. Great for finding the best rejection for your data.";
   this.abCheck.onCheck = function(checked) { params.abMode = checked; };

   this.stfCheck = new CheckBox(this);
   this.stfCheck.text = "Apply auto-STF to results";
   this.stfCheck.checked = params.autoSTF;
   this.stfCheck.toolTip = "Applies linked auto-STF with -2.8 sigma clip and 0.25 target background.";
   this.stfCheck.onCheck = function(checked) { params.autoSTF = checked; };

   this.optGroup.sizer = new VerticalSizer;
   this.optGroup.sizer.margin = 6;
   this.optGroup.sizer.spacing = 4;
   this.optGroup.sizer.add(this.abCheck);
   this.optGroup.sizer.add(this.stfCheck);

   // ---- Output directory ----
   this.outDirLabel = new Label(this);
   this.outDirLabel.text = "Output directory (blank = subsDir/master/):";
   this.outDirLabel.textAlignment = TextAlign_Left;

   this.outDirEdit = new Edit(this);
   this.outDirEdit.text = params.outputDir;
   this.outDirEdit.toolTip = "Where the master frame is saved. Leave blank to auto-create " +
      "a master/ subfolder in the subs directory.";
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
   this.okButton.text = "Build Master";
   this.okButton.icon = this.scaledResource(":/icons/power.png");
   this.okButton.toolTip = "Build the master frame with current settings";
   this.okButton.onClick = function() { self.ok(); };

   this.cancelButton = new PushButton(this);
   this.cancelButton.text = "Close";
   this.cancelButton.icon = this.scaledResource(":/icons/close.png");
   this.cancelButton.onClick = function() { self.cancel(); };

   this.resetButton = new PushButton(this);
   this.resetButton.text = "Reset";
   this.resetButton.toolTip = "Reset all parameters to defaults";
   this.resetButton.onClick = function() {
      var defaults = new MasterBuilderParameters;
      self.frameTypeCombo.currentItem  = defaults.frameType;
      self.rejCombo.currentItem        = defaults.rejectionIndex;
      self.sigmaLowSpin.value           = Math.round(defaults.sigmaLow * 10);
      self.sigmaHighSpin.value          = Math.round(defaults.sigmaHigh * 10);
      self.esdSigSpin.value             = Math.round(defaults.esdSignificance * 100);
      self.esdOutSpin.value             = Math.round(defaults.esdOutliersFraction * 100);
      self.esdRelaxSpin.value           = Math.round(defaults.esdLowRelaxation * 10);
      self.normCombo.currentItem       = defaults.normIndex;
      self.abCheck.checked             = defaults.abMode;
      self.stfCheck.checked            = defaults.autoSTF;
      sigLowRef.text  = defaults.sigmaLow.toFixed(1);
      sigHighRef.text = defaults.sigmaHigh.toFixed(1);
      esdSigRef.text  = defaults.esdSignificance.toFixed(2);
      esdOutRef.text  = defaults.esdOutliersFraction.toFixed(2);
      esdRelaxRef.text = defaults.esdLowRelaxation.toFixed(1);
      params.frameType           = defaults.frameType;
      params.rejectionIndex      = defaults.rejectionIndex;
      params.sigmaLow            = defaults.sigmaLow;
      params.sigmaHigh           = defaults.sigmaHigh;
      params.esdSignificance     = defaults.esdSignificance;
      params.esdOutliersFraction = defaults.esdOutliersFraction;
      params.esdLowRelaxation    = defaults.esdLowRelaxation;
      params.normIndex           = defaults.normIndex;
      params.abMode              = defaults.abMode;
      params.autoSTF             = defaults.autoSTF;
      self.updateFlatControls();
      self.updateESDControls();
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
   this.sizer.add(this.frameGroup);
   this.sizer.add(this.rejGroup);
   this.sizer.add(this.optGroup);
   this.sizer.add(this.outDirLabel);
   this.sizer.add(this.outDirSizer);
   this.sizer.addSpacing(4);
   this.sizer.add(this.buttonSizer);

   // ---- Visibility helpers ----
   this.updateFlatControls = function() {
      var isFlat = (params.frameType == 1);
      self.filterLabel.visible       = isFlat;
      self.filterEdit.visible        = isFlat;
      self.masterDarkLabel.visible   = isFlat;
      self.masterDarkEdit.visible    = isFlat;
      self.masterDarkButton.visible  = isFlat;
   };

   this.updateESDControls = function() {
      var isESD = (params.rejectionIndex == 0);  // ESD is index 0 in our dropdown
      self.esdSigLabel.visible     = isESD;
      self.esdSigSpin.visible      = isESD;
      self.esdSigValLabel.visible  = isESD;
      self.esdOutLabel.visible     = isESD;
      self.esdOutSpin.visible      = isESD;
      self.esdOutValLabel.visible  = isESD;
      self.esdRelaxLabel.visible   = isESD;
      self.esdRelaxSpin.visible    = isESD;
      self.esdRelaxValLabel.visible = isESD;
   };

   this.updateFlatControls();
   this.updateESDControls();
   this.adjustToContents();
}

// ============================================================================
// Save pending_master.json for Score Last Run
// ============================================================================

function savePendingMaster(params, results, subCount) {
   var outDir = params.outputDir;
   if (outDir.length == 0)
      outDir = params.subsDir + "/master";

   var pendingPath = outDir + "/pending_master.json";

   var record = '{\n';
   record += '  "timestamp": "' + new Date().toISOString() + '",\n';
   record += '  "version": "' + VERSION + '",\n';
   record += '  "script": "Master Builder",\n';
   record += '  "frameType": "' + params.getFrameTypeName() + '",\n';
   record += '  "subsDir": "' + params.subsDir.replace(/\\/g, "/") + '",\n';
   record += '  "subCount": ' + subCount + ',\n';
   record += '  "filterName": "' + params.filterName + '",\n';
   record += '  "masterDarkPreCal": "' + params.masterDarkPath.replace(/\\/g, "/") + '",\n';
   record += '  "settings": {\n';
   record += '    "sigmaLow": ' + params.sigmaLow.toFixed(2) + ',\n';
   record += '    "sigmaHigh": ' + params.sigmaHigh.toFixed(2) + ',\n';
   record += '    "esdSignificance": ' + params.esdSignificance.toFixed(3) + ',\n';
   record += '    "esdOutliersFraction": ' + params.esdOutliersFraction.toFixed(3) + ',\n';
   record += '    "esdLowRelaxation": ' + params.esdLowRelaxation.toFixed(2) + ',\n';
   record += '    "normalization": "' + NORMALIZATION_CHOICES[params.normIndex] + '",\n';
   record += '    "abMode": ' + (params.abMode ? 'true' : 'false') + '\n';
   record += '  },\n';

   record += '  "results": [\n';
   for (var i = 0; i < results.length; i++) {
      var r = results[i];
      var m = r.metrics;
      record += '    {\n';
      record += '      "rejection": "' + r.rejectionLabel + '",\n';
      record += '      "path": "' + r.path.replace(/\\/g, "/") + '",\n';
      record += '      "median": ' + m.median.toFixed(8) + ',\n';
      record += '      "mad": ' + m.mad.toFixed(8) + ',\n';
      record += '      "mean": ' + m.mean.toFixed(8) + ',\n';
      record += '      "noise": ' + m.noise.toFixed(8) + ',\n';
      record += '      "snr": ' + m.snr.toFixed(2) + ',\n';
      record += '      "uniformity": ' + m.uniformity.toFixed(6) + '\n';
      record += '    }' + (i < results.length - 1 ? ',' : '') + '\n';
   }
   record += '  ]\n';
   record += '}';

   try {
      ensureDir(outDir);
      writeTextFile(pendingPath, record);
      Console.noteln("");
      Console.noteln("Result metadata saved: " + pendingPath);
      Console.noteln("When ready, run: Script > CascadiaPhotoelectric > Score Last Run");
   } catch(e) {
      Console.criticalln("Failed to write pending master: " + e.message);
   }
}

// ============================================================================
// Main execution
// ============================================================================

function executeMasterBuild(params) {
   var frameType = params.getFrameTypeName();
   var subsDir = params.subsDir;

   if (subsDir.length == 0) {
      Console.criticalln("No sub-frames directory specified.");
      return;
   }

   Console.writeln("");
   Console.writeln("============================================================");
   Console.writeln(TITLE + " v" + VERSION);
   Console.writeln("============================================================");
   Console.writeln("  Frame type: " + frameType);
   Console.writeln("  Subs dir: " + subsDir);
   if (params.frameType == 1 && params.filterName.length > 0)
      Console.writeln("  Filter: " + params.filterName);
   Console.writeln("");

   // Discover sub-frames
   Console.writeln("Scanning for " + frameType + " sub-frames...");
   var subFiles = findSubFrames(subsDir, frameType, params.filterName, [".xisf", ".fit", ".fits"]);
   subFiles.sort();

   if (subFiles.length == 0) {
      Console.criticalln("No " + frameType + " sub-frames found in: " + subsDir);
      Console.criticalln("(Looking for files starting with " + frameType + "_)");
      return;
   }

   Console.writeln("  Found " + subFiles.length + " " + frameType + " sub-frames");

   // For flats with master dark: pre-calibrate
   var integrationFiles = subFiles;
   if (params.frameType == 1 && params.masterDarkPath.length > 0) {
      var outDir = params.outputDir;
      if (outDir.length == 0)
         outDir = subsDir + "/master";
      integrationFiles = preCalibrateFlats(subFiles, params.masterDarkPath, outDir);
      if (integrationFiles.length == 0) {
         Console.criticalln("Flat pre-calibration produced no output files.");
         return;
      }
   }

   // Determine output directory
   var outputDir = params.outputDir;
   if (outputDir.length == 0)
      outputDir = subsDir + "/master";

   // Build window name prefix
   var windowPrefix = "Master" + frameType;
   if (params.frameType == 1 && params.filterName.length > 0)
      windowPrefix += "_" + params.filterName;

   // Run integration(s)
   var results = [];
   var openedWindows = [];

   if (params.abMode) {
      // A/B mode: run ESD, WinsorizedSigma, and LinearFit
      var abAlgorithms = [
         { code: REJECTION_ESD,              label: "ESD" },
         { code: REJECTION_WINSORIZED_SIGMA, label: "WinSigma" },
         { code: REJECTION_LINEAR_FIT,       label: "LinearFit" }
      ];

      for (var a = 0; a < abAlgorithms.length; a++) {
         var algo = abAlgorithms[a];
         var result = runIntegration(
            integrationFiles, params, algo.code, algo.label, outputDir, windowPrefix
         );
         if (result != null) {
            var metrics = measureMasterMetrics(result.path);
            results.push({
               rejectionLabel: algo.label,
               path: result.path,
               metrics: metrics,
               window: result.window
            });
            openedWindows.push(result.window);
         }
      }
   } else {
      // Single algorithm run
      var rejCode = REJECTION_MAP[params.rejectionIndex];
      var rejLabel = REJECTION_CHOICES[params.rejectionIndex].split(" ")[0];
      // Shorten label for window naming
      if (rejLabel == "ESD") rejLabel = "ESD";
      else if (rejLabel == "Winsorized") rejLabel = "WinSigma";
      else if (rejLabel == "Linear") rejLabel = "LinearFit";
      else if (rejLabel == "Percentile") rejLabel = "PctClip";
      else if (rejLabel == "No") rejLabel = "NoRej";

      var result = runIntegration(
         integrationFiles, params, rejCode, rejLabel, outputDir, windowPrefix
      );
      if (result != null) {
         var metrics = measureMasterMetrics(result.path);
         results.push({
            rejectionLabel: rejLabel,
            path: result.path,
            metrics: metrics,
            window: result.window
         });
         openedWindows.push(result.window);
      }
   }

   // Log metrics summary
   if (results.length > 0) {
      Console.writeln("");
      Console.writeln("============================================================");
      Console.writeln("Master Build Results");
      Console.writeln("============================================================");

      for (var i = 0; i < results.length; i++) {
         var r = results[i];
         var m = r.metrics;
         Console.writeln("");
         Console.writeln("  [" + r.rejectionLabel + "]");
         Console.writeln("    Path: " + r.path);
         Console.writeln("    Median:     " + m.median.toFixed(6));
         Console.writeln("    MAD:        " + m.mad.toFixed(6));
         Console.writeln("    Mean:       " + m.mean.toFixed(6));
         Console.writeln("    Noise:      " + m.noise.toFixed(6));
         Console.writeln("    SNR:        " + m.snr.toFixed(1));
         Console.writeln("    Uniformity: " + (m.uniformity * 100).toFixed(2) + "%");
      }

      // Apply auto-STF and show windows
      for (var i = 0; i < openedWindows.length; i++) {
         if (openedWindows[i] != null && !openedWindows[i].isNull) {
            if (params.autoSTF)
               applyAutoSTF(openedWindows[i].currentView, -2.8, 0.25);
            openedWindows[i].show();
         }
      }

      // Tile windows
      if (openedWindows.length > 1) {
         try { ImageWindow.tileWindows(); } catch(e) {
            try { ImageWindow.tile(); } catch(e2) {
               Console.warningln("Auto-tile not available -- please tile windows manually (Window > Tile).");
            }
         }
      }

      // A/B mode comparison summary
      if (params.abMode && results.length > 1) {
         Console.writeln("");
         Console.noteln("A/B COMPARISON:");
         Console.noteln("  Review the tiled images. Lower MAD = cleaner rejection.");
         Console.noteln("  Higher uniformity = better flat field correction.");
         Console.noteln("  ESD is generally best for >15 subs, WinSigma for 8-15, LinearFit for 5-8.");

         // Find best by lowest MAD
         var bestIdx = 0;
         for (var i = 1; i < results.length; i++) {
            if (results[i].metrics.mad < results[bestIdx].metrics.mad)
               bestIdx = i;
         }
         Console.noteln("  Lowest MAD: " + results[bestIdx].rejectionLabel +
            " (" + results[bestIdx].metrics.mad.toFixed(6) + ")");
      }

      Console.writeln("");
      Console.writeln("Done. " + results.length + " master(s) built from " + subFiles.length + " subs.");

      // Save pending record
      savePendingMaster(params, results, subFiles.length);
   }
}

// ============================================================================
// Main
// ============================================================================

function main() {
   var params = new MasterBuilderParameters;
   params.load();

   var dlg = new MasterBuilderDialog(params);
   if (dlg.execute() != StdButton_Ok)
      return;

   params.save();

   Console.show();
   executeMasterBuild(params);
}

main();
