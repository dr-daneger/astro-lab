// Probe_MultiAperture_Resolved.ijm  (robust map-based resolver)
print("\\Clear");
setBatchMode(true);

// Use the actual Hashtable rather than parsing its toString()
code =
  "importClass(Packages.ij.Menus);\n" +
  "importClass(Packages.java.util.Iterator);\n" +
  "var map = Menus.getCommands();\n" +          // documented API
  "var hitKey = null, hitVal = null;\n" +
  "var it = map.keySet().iterator();\n" +
  "while (it.hasNext()) {\n" +
  "  var k = String(it.next());\n" +
  "  var kl = k.toLowerCase().replace(/\\s+/g,'');\n" + // ignore spaces/hyphens
  "  if (kl.indexOf('multi')>=0 && kl.indexOf('aperture')>=0) { hitKey = k; hitVal = String(map.get(k)); break; }\n" +
  "}\n" +
  "(hitKey?('[ok] key='+hitKey+' class='+hitVal):'[miss]');";

res = eval("js", code);                         // officially supported
print("[resolve] " + res);

// If found, reflect methods (again via JS)
if (startsWith(res, "[ok]")) {
  cls = substring(res, indexOf(res, "class=")+6);
  print("[reflect] " + cls);
  js =
    "importClass(Packages.java.lang.Class);\n" +
    "var out='';\n" +
    "try{var c=Class.forName('"+cls+"'); out+='class '+c.getName()+'\\n';\n" +
    "  var m=c.getMethods(); for (var i=0;i<m.length;i++) out+=('  '+m[i].toString()+'\\n');\n" +
    "}catch(e){out='[missing] "+cls+"';}\n" +
    "out;";
  print(eval("js", js));
}