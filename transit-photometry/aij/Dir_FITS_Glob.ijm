// Dir_FITS_Glob.ijm
args = getArgument();
dir = "";
pattern = "*.fits";
if (args != "") {
  args = replace(args, "\r", "");
  lines = split(args, "\n");
  for (i = 0; i < lines.length; i++) {
    line = trim(lines[i]);
    if (line == "" || indexOf(line, "=") < 0) continue;
    key = trim(substring(line, 0, indexOf(line, "=")));
    value = trim(substring(line, indexOf(line, "=") + 1));
    if (key == "dir") dir = value;
    else if (key == "pattern") pattern = value;
  }
}
if (dir == "") exit("Dir_FITS_Glob: missing 'dir' argument");
dir = replace(dir, "\\", "/");
if (!endsWith_local(dir, "/")) dir += "/";
pattern = toLowerCase(pattern);

print("[glob] dir=" + dir + " pattern=" + pattern);
fileList = getFileList(dir);
if (fileList == null) exit("Dir_FITS_Glob: directory not found: " + dir);

function wildcardToRegex(w) {
  w = replace(w, "\\.", "\\\\.");
  w = replace(w, "\\*", ".*");
  w = replace(w, "\\?", ".");
  return "^" + w + "$";
}
regex = wildcardToRegex(pattern);

count = 0;
for (i = 0; i < fileList.length; i++) {
  name = fileList[i];
  lower = toLowerCase(name);
  if (endsWith_local(lower, "/")) continue;
  if (!endsWith_local(lower, ".fits") && !endsWith_local(lower, ".fit") && !endsWith_local(lower, ".fz")) continue;
  if (!matches(lower, regex)) continue;
  path = dir + name;
  print("[glob] open: " + path);
  open(path);
  count++;
}
if (count == 0) exit("Dir_FITS_Glob: no FITS matched pattern " + pattern + " in " + dir);
print("[glob] opened files: " + count);

function endsWith_local(str, suffix) {
  sl = lengthOf(str);
  pl = lengthOf(suffix);
  if (pl > sl) return false;
  return substring(str, sl - pl, sl) == suffix;
}
