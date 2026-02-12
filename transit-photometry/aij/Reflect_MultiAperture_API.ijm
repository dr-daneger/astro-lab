// Reflect_MultiAperture_API.ijm
cls = getArgument();
if (cls == "") cls = "astroj.gui.MultiApertureController";
print("\\Clear");
print("[reflect] class query: " + cls);
code =
  "importClass(java.lang.Class);" +
  "var out='';" +
  "try { var c = Class.forName('" + cls + "');" +
  "  out += '[ok] ' + c.getName() + '\\n';" +
  "  var m = c.getMethods();" +
  "  for (var i=0;i<m.length;i++) {" +
  "    var s=m[i].toString();" +
  "    if (s.indexOf('set')>=0 || s.indexOf('RA')>=0 || s.indexOf('rad')>=0 || s.indexOf('annulus')>=0)" +
  "      out += '  ' + s + '\\n';" +
  "  }" +
  "} catch(e) { out='[missing] " + cls + "'; }" +
  "out;";
print(eval("script", code));
