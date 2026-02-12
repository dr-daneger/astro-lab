// Probe_Environment.ijm
setBatchMode(true);
run("Close All");
print("\\Clear");
print("[probe] Starting environment probe…");

commandsTxt = call("ij.Menus.getCommands");
print("[probe] Commands map (subset):");
print(commandsTxt);

multiClass = "";
entries = split(commandsTxt, ", ");
for (i = 0; i < entries.length; i++) {
  entry = replace(entries[i], " ", "");
  if (indexOf(entry, "Multi-Aperture=") >= 0 || indexOf(entry, "MultiAperture=") >= 0) {
    pair = split(entry, "=");
    if (pair.length >= 2) {
      multiClass = pair[pair.length - 1];
      break;
    }
  }
}
print("[probe] Multi-Aperture backing class (parsed): " + multiClass);

function jsDump(className) {
  if (className == "") return "[skip]";
  code =
    "importClass(java.lang.Class);" +
    "var out='';" +
    "try { var c = Class.forName('" + className + "');" +
    "  out += '[ok] class ' + c.getName() + '\\n';" +
    "  var m = c.getMethods();" +
    "  for (var i=0;i<m.length;i++) out += '  ' + m[i].toString() + '\\n';" +
    "} catch(e) { out = '[missing] " + className + "'; }" +
    "out;";
  return eval("script", code);
}

candidates = newArray(multiClass, "astroj.gui.MultiApertureController", "ij.plugin.frame.MultiAperture_");
for (i = 0; i < lengthOf(candidates); i++) {
  cn = candidates[i];
  if (cn == "") continue;
  print("[probe] Reflecting: " + cn);
  print(jsDump(cn));
}

print("[probe] Done.");
