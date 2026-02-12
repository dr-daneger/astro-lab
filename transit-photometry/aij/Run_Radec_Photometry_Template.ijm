// Run_Radec_Photometry_Template.ijm
args = getArgument();
if (args == null) args = "";
args = replace(args, "\r", "");

function getArg(key, def) {
  if (args == "") return def;
  lines = split(args, "\n");
  for (ii = 0; ii < lines.length; ii++) {
    line = trim(lines[ii]);
    if (line == "" || indexOf(line, "=") < 0) continue;
    k = trim(substring(line, 0, indexOf(line, "=")));
    v = substring(line, indexOf(line, "=") + 1);
    if (k == key) return trim(v);
  }
  return def;
}

fitsDir = getArg("fitsDir", "");
pattern = getArg("pattern", "*.fits");
radec   = getArg("radec", "");
apRad   = parseFloat(getArg("ap", "6"));
inRad   = parseFloat(getArg("in", "12"));
outRad  = parseFloat(getArg("out", "20"));
csvOut  = getArg("csvOut", "");
outName = getArg("outName", "photometry.csv");
globMacro = getArg("globMacro", "");

if (fitsDir == "" || radec == "" || csvOut == "") exit("Run_Radec: missing required args (fitsDir/radec/csvOut)");
fitsDir = replace(fitsDir, "\\", "/");
radec = replace(radec, "\\", "/");
csvOut = replace(csvOut, "\\", "/");
if (!endsWith(fitsDir, "/")) fitsDir += "/";
if (!endsWith(csvOut, "/")) csvOut += "/";

if (globMacro == "") {
  macroDir = File.getParent(getInfo("macro.file"));
  globMacro = macroDir + File.separator + "Dir_FITS_Glob.ijm";
}

print("[run] Starting photometry job…");
print("[run] FITS dir: " + fitsDir + " pattern: " + pattern);
print("[run] RA/Dec file: " + radec);
print("[run] Radii (px): ap=" + apRad + " in=" + inRad + " out=" + outRad);
print("[run] CSV out: " + csvOut + outName);
print("[run] glob macro: " + globMacro);

setBatchMode(true);
run("Close All");
File.makeDirectory(csvOut);

argsGlob = "dir=" + fitsDir + "\npattern=" + pattern;
runMacro(globMacro, argsGlob);

// ---- RA/Dec loading ----
// Preferred approach: recorded command from the user's AIJ build. Replace the placeholder below
// with the exact line captured by the Macro Recorder, for example:
// run("Load Aperture RA/Dec...", "file=" + radec);
// By default we simply print a warning.
print("[run] NOTE: insert your recorded RA/Dec loading command here.");

// ---- Set radii and call Multi-Aperture ----
// Likewise, paste the exact Multi-Aperture invocation recorded from AIJ.
print("[run] NOTE: insert your recorded Multi-Aperture command here.");

// Example placeholders (commented out):
// run("Load Aperture RA/Dec...", "file=" + radec);
// run("Multi-Aperture", "list=radec ap=" + apRad + " in=" + inRad + " out=" + outRad + " radius");

selectWindow("Aperture Photometry");
outPath = csvOut + outName;
saveAs("Results", outPath);
print("[run] Wrote: " + outPath);
print("[run] Completed run.");
