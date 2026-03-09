// ============================================================================
// CascadiaPhotoelectric Score Last Run v1.3.0
// ============================================================================
//
// Reads pending_score.json from the last Calibration Diagnostic run,
// presents a non-blocking scoring dialog, and appends the scored record
// to diagnostic_log.json.
//
// Run this AFTER reviewing the diagnostic images at your leisure.
//
// ============================================================================

#feature-id   CascadiaPhotoelectric > Score Last Run
#feature-info Score the most recent calibration diagnostic run after reviewing results.

#include <pjsr/DataType.jsh>
#include <pjsr/Sizer.jsh>
#include <pjsr/FrameStyle.jsh>
#include <pjsr/TextAlign.jsh>
#include <pjsr/StdButton.jsh>
#include <pjsr/StdIcon.jsh>

#define VERSION "1.3.0"
#define TITLE   "CascadiaPhotoelectric Score Last Run"

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

// ============================================================================
// Locate pending_score.json
// ============================================================================

function findPendingScore() {
   // Check the last-used output directory from Settings
   var diagTitle = "CascadiaPhotoelectric Calibration Diagnostic";
   var lightDir = Settings.read(diagTitle + "/lightDir", DataType_String);
   var outputDir = Settings.read(diagTitle + "/outputDir", DataType_String);

   var baseDir = "";
   if (outputDir != null && outputDir.length > 0)
      baseDir = outputDir;
   else if (lightDir != null && lightDir.length > 0)
      baseDir = lightDir + "/diagnostic";

   if (baseDir.length > 0) {
      var candidate = baseDir + "/pending_score.json";
      if (File.exists(candidate))
         return candidate;
   }

   return "";
}

// ============================================================================
// Main
// ============================================================================

function main() {
   Console.writeln("");
   Console.writeln(TITLE + " v" + VERSION);
   Console.writeln("");

   // Find pending score file
   var pendingPath = findPendingScore();

   if (pendingPath.length == 0) {
      // Ask user to locate it
      var ofd = new OpenFileDialog;
      ofd.caption = "Select pending_score.json from diagnostic output";
      ofd.filters = [["JSON Files", "*.json"], ["All Files", "*.*"]];
      if (!ofd.execute()) return;
      pendingPath = ofd.fileName;
   }

   Console.writeln("Loading: " + pendingPath);

   var pendingText = "";
   try {
      pendingText = readTextFile(pendingPath);
   } catch(e) {
      Console.criticalln("Cannot read: " + e.message);
      return;
   }

   // Parse the pending data to extract filters
   var filters = [];
   // Simple extraction: find "filters": ["Ha","OIII",...]
   var filtersMatch = pendingText.match(/"filters"\s*:\s*\[([^\]]*)\]/);
   if (filtersMatch) {
      var raw = filtersMatch[1].split(",");
      for (var i = 0; i < raw.length; i++) {
         var f = raw[i].replace(/["\s]/g, "");
         if (f.length > 0) filters.push(f);
      }
   }

   if (filters.length == 0) {
      Console.warningln("No filters found in pending data. Adding generic entry.");
      filters.push("Unknown");
   }

   Console.writeln("Filters to score: " + filters.join(", "));

   // Build scoring dialog
   var comboChoices = ["(undecided)", "Dark+Flat (best)", "Dark Only (best)", "Flat Only (best)", "All look bad"];
   var scoreChoices = ["--", "1 (terrible)", "2 (poor)", "3 (acceptable)", "4 (good)", "5 (excellent)"];

   var dlg = new Dialog;
   dlg.windowTitle = "Score Diagnostic Run";
   dlg.minWidth = 500;

   var titleLabel = new Label(dlg);
   titleLabel.text = "Rate the diagnostic results";
   titleLabel.textAlignment = TextAlign_Center;
   titleLabel.styleSheet = "font-size: 13px; font-weight: bold; padding: 4px;";

   var descLabel = new Label(dlg);
   descLabel.text = "Select the best combo for each filter, add notes, then save.";
   descLabel.textAlignment = TextAlign_Center;
   descLabel.styleSheet = "color: #888; padding-bottom: 6px;";

   var filterRows = [];
   var scoringGroup = new GroupBox(dlg);
   scoringGroup.title = "Per-Filter Assessment";
   scoringGroup.sizer = new VerticalSizer;
   scoringGroup.sizer.margin = 6;
   scoringGroup.sizer.spacing = 4;

   for (var f = 0; f < filters.length; f++) {
      var row = {};
      row.filter = filters[f];

      row.label = new Label(dlg);
      row.label.text = filters[f] + ":";
      row.label.textAlignment = TextAlign_Right | TextAlign_VertCenter;
      row.label.setFixedWidth(50);

      row.bestCombo = new ComboBox(dlg);
      for (var c = 0; c < comboChoices.length; c++)
         row.bestCombo.addItem(comboChoices[c]);
      row.bestCombo.currentItem = 0;

      row.scoreCombo = new ComboBox(dlg);
      for (var s = 0; s < scoreChoices.length; s++)
         row.scoreCombo.addItem(scoreChoices[s]);
      row.scoreCombo.currentItem = 0;

      var rowSizer = new HorizontalSizer;
      rowSizer.spacing = 6;
      rowSizer.add(row.label);
      rowSizer.add(row.bestCombo, 50);
      rowSizer.add(row.scoreCombo);

      scoringGroup.sizer.add(rowSizer);
      filterRows.push(row);
   }

   var notesLabel = new Label(dlg);
   notesLabel.text = "Notes (observations, issues, next steps):";
   notesLabel.textAlignment = TextAlign_Left;

   var notesEdit = new TextBox(dlg);
   notesEdit.text = "";
   notesEdit.setMinSize(480, 80);

   var saveButton = new PushButton(dlg);
   saveButton.text = "Save Score";
   saveButton.icon = dlg.scaledResource(":/icons/document-save.png");
   saveButton.onClick = function() { dlg.ok(); };

   var skipButton = new PushButton(dlg);
   skipButton.text = "Cancel";
   skipButton.onClick = function() { dlg.cancel(); };

   var buttonSizer = new HorizontalSizer;
   buttonSizer.spacing = 8;
   buttonSizer.addStretch();
   buttonSizer.add(saveButton);
   buttonSizer.add(skipButton);

   dlg.sizer = new VerticalSizer;
   dlg.sizer.margin = 8;
   dlg.sizer.spacing = 8;
   dlg.sizer.add(titleLabel);
   dlg.sizer.add(descLabel);
   dlg.sizer.add(scoringGroup);
   dlg.sizer.add(notesLabel);
   dlg.sizer.add(notesEdit);
   dlg.sizer.addSpacing(4);
   dlg.sizer.add(buttonSizer);
   dlg.adjustToContents();

   if (dlg.execute() != StdButton_Ok) {
      Console.writeln("Scoring cancelled.");
      return;
   }

   // Build scores array
   var scoresJson = '  "scores": [\n';
   for (var f = 0; f < filterRows.length; f++) {
      var row = filterRows[f];
      var bestIdx = row.bestCombo.currentItem;
      var scoreIdx = row.scoreCombo.currentItem;
      scoresJson += '    {\n';
      scoresJson += '      "filter": "' + row.filter + '",\n';
      scoresJson += '      "bestCombo": "' + comboChoices[bestIdx] + '",\n';
      scoresJson += '      "score": "' + scoreChoices[scoreIdx] + '"\n';
      scoresJson += '    }' + (f < filterRows.length - 1 ? ',' : '') + '\n';
   }
   scoresJson += '  ],\n';

   // Escape notes
   var notesText = notesEdit.text.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n");
   var notesJson = '  "notes": "' + notesText + '",\n';

   // LLM context block
   var llmJson = '  "llm_context": {\n';
   llmJson += '    "purpose": "Calibration diagnostic run from CascadiaPhotoelectric PI script. Each entry is a fully-contextualized data point for learning optimal calibration settings.",\n';
   llmJson += '    "interpretation_guide": "Higher SNR = better calibration. Compare same filter across combos. If dark_only SNR > dark_flat SNR, flats are degrading the image. If flat_only has hot pixel speckle (high MAD), darks are needed.",\n';
   llmJson += '    "grouping_strategy": "Group by: (1) target_type + is_narrowband, (2) moon_illumination_pct, (3) altitude_deg, (4) camera + telescope. Bortle assumed 7.",\n';
   llmJson += '    "actionable_fields": "scores[].bestCombo = operator visual assessment, scores[].score = quality rating, metrics[].snr = quantitative SNR, notes = operator observations"\n';
   llmJson += '  }\n';

   // Merge into pending record: insert scores, notes, llm_context before closing brace
   // The pending record ends with "  ]\n}" (metrics array then close)
   // We insert scores + notes + llm_context after the metrics array
   var merged = pendingText.trim();
   if (merged.charAt(merged.length - 1) == '}') {
      // Remove trailing }, add comma after metrics, then append new fields
      merged = merged.slice(0, merged.length - 1).trim();
      // Ensure trailing comma after metrics block
      if (merged.charAt(merged.length - 1) != ',')
         merged += ',';
      merged += '\n' + scoresJson + notesJson + llmJson + '}';
   }

   // Append to diagnostic_log.json
   var logDir = pendingPath.replace(/\/[^\/]+$/, "");
   var logPath = logDir + "/diagnostic_log.json";

   var existing = "";
   try {
      if (File.exists(logPath))
         existing = readTextFile(logPath);
   } catch(e) {}

   var newContent;
   if (existing.length > 0 && existing.trim().charAt(0) == '[') {
      var trimmed = existing.trim();
      newContent = trimmed.slice(0, trimmed.length - 1).trim() + ',\n' + merged + '\n]';
   } else if (existing.length > 0) {
      newContent = '[\n' + existing.trim() + ',\n' + merged + '\n]';
   } else {
      newContent = '[\n' + merged + '\n]';
   }

   try {
      writeTextFile(logPath, newContent);
      Console.noteln("Diagnostic score saved to: " + logPath);
   } catch(e) {
      Console.criticalln("Failed to write log: " + e.message);
   }

   // Clean up pending file
   try {
      File.remove(pendingPath);
      Console.writeln("Removed pending file: " + pendingPath);
   } catch(e) {}

   Console.writeln("Done.");
}

main();
