/* Snap Judgment - browser demo.
 *
 * Pure stats core (verdictForCount, isScreenshotFile, parseDimensions,
 * computeStats) is environment-agnostic and unit-tested from Node via
 * tools/demo_js_test.js. Browser wiring lives at the bottom, guarded by
 * typeof window checks.
 *
 * Privacy: the demo reads file names, sizes, dates, and a few header
 * bytes for dimensions. It never displays, uploads, or stores pictures.
 */
"use strict";

/* ------------------------------------------------------------------ */
/* Detection                                                           */
/* ------------------------------------------------------------------ */

var IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "heic"];

var NAME_PATTERNS = [
  /screenshot/i,          /* Screenshot, Screenshot_2026-01-01 (Android) */
  /screen[\s_-]?shot/i,   /* Screen Shot, Screen-shot, ScreenShot */
  /cleanshot/i,           /* CleanShot */
  /bildschirmfoto/i       /* German macOS */
];

function fileExtension(name) {
  var dot = name.lastIndexOf(".");
  if (dot < 0) return "";
  return name.slice(dot + 1).toLowerCase();
}

function isImageName(name) {
  return IMAGE_EXTENSIONS.indexOf(fileExtension(name)) !== -1;
}

/* A file counts as a screenshot when it is an image whose own name matches
 * a screenshot pattern, or whose parent folder's name mentions screenshots. */
function isScreenshotFile(name, parentName) {
  if (!isImageName(name)) return false;
  for (var i = 0; i < NAME_PATTERNS.length; i++) {
    if (NAME_PATTERNS[i].test(name)) return true;
  }
  if (parentName && /screenshot/i.test(parentName)) return true;
  return false;
}

/* ------------------------------------------------------------------ */
/* Dimension parsing from header bytes only                            */
/* ------------------------------------------------------------------ */

function u32be(b, o) { return ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0; }
function u32le(b, o) { return ((b[o]) | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0; }
function u16be(b, o) { return (b[o] << 8) | b[o + 1]; }
function u16le(b, o) { return b[o] | (b[o + 1] << 8); }
function i32le(b, o) { return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24); }

/* Parse width/height from the first bytes of an image file.
 * Returns {width, height} or null when the format is unknown or the
 * header is incomplete. Never decodes image data. */
function parseDimensions(b) {
  if (!b || b.length < 10) return null;
  try {
    /* PNG: signature, then IHDR width/height as BE u32 at 16/20. */
    if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) {
      if (b.length < 24) return null;
      return { width: u32be(b, 16), height: u32be(b, 20) };
    }
    /* GIF: "GIF8", LE u16 at 6/8. */
    if (b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x38) {
      return { width: u16le(b, 6), height: u16le(b, 8) };
    }
    /* BMP: "BM", LE i32 at 18/22 (height may be negative, top-down). */
    if (b[0] === 0x42 && b[1] === 0x4d) {
      if (b.length < 26) return null;
      return { width: i32le(b, 18), height: Math.abs(i32le(b, 22)) };
    }
    /* JPEG: scan markers for a start-of-frame segment. */
    if (b[0] === 0xff && b[1] === 0xd8) {
      var off = 2;
      while (off + 9 < b.length) {
        if (b[off] !== 0xff) { off++; continue; }
        var marker = b[off + 1];
        if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd9)) {
          off += 2; continue;
        }
        var len = u16be(b, off + 2);
        if (len < 2) return null;
        var isSof = marker >= 0xc0 && marker <= 0xcf &&
          marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc;
        if (isSof) {
          return { width: u16be(b, off + 7), height: u16be(b, off + 5) };
        }
        off += 2 + len;
      }
      return null;
    }
    /* WebP: RIFF....WEBP, then a VP8 / VP8L / VP8X chunk. */
    if (b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46 &&
        b[8] === 0x57 && b[9] === 0x45 && b[10] === 0x42 && b[11] === 0x50) {
      var chunk = String.fromCharCode(b[12], b[13], b[14], b[15]);
      if (chunk === "VP8X" && b.length >= 30) {
        var wx = (b[24] | (b[25] << 8) | (b[26] << 16)) + 1;
        var hx = (b[27] | (b[28] << 8) | (b[29] << 16)) + 1;
        return { width: wx, height: hx };
      }
      if (chunk === "VP8L" && b.length >= 25 && b[20] === 0x2f) {
        var bits = u32le(b, 21);
        return { width: (bits & 0x3fff) + 1, height: ((bits >> 14) & 0x3fff) + 1 };
      }
      if (chunk === "VP8 " && b.length >= 30) {
        return { width: u16le(b, 26) & 0x3fff, height: u16le(b, 28) & 0x3fff };
      }
      return null;
    }
  } catch (e) {
    return null;
  }
  return null;
}

/* ------------------------------------------------------------------ */
/* Verdict                                                             */
/* ------------------------------------------------------------------ */

/* Exact thresholds from the Python tool. */
function verdictForCount(count) {
  if (count <= 0) return "No Evidence";
  if (count < 50) return "Innocent Bystander";
  if (count < 200) return "Petty Offender";
  if (count < 500) return "Repeat Offender";
  if (count < 1000) return "Habitual Hoarder";
  if (count < 2500) return "Felony-Level Archivist";
  return "Unrepentant Screenshot Felon";
}

/* ------------------------------------------------------------------ */
/* Formatting helpers                                                  */
/* ------------------------------------------------------------------ */

var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function humanSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  var units = ["KB", "MB", "GB", "TB"];
  var v = bytes / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (v >= 100 ? Math.round(v).toString() : v.toFixed(1)) + " " + units[i];
}

function dateLabel(ms) {
  var d = new Date(ms);
  return MONTHS[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
}

function monthYearLabel(ms) {
  var d = new Date(ms);
  return MONTHS[d.getMonth()] + " " + d.getFullYear();
}

/* Day numbers in the stats core are built from local Y/M/D via Date.UTC,
 * so their labels must be read back with UTC getters. */
function dayNumLabel(dayNum) {
  var d = new Date(dayNum * 86400000);
  return MONTHS[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + d.getUTCFullYear();
}

/* ------------------------------------------------------------------ */
/* Stats core                                                          */
/* ------------------------------------------------------------------ */

/* files: [{name, size, mtime (ms epoch), width, height}] - width/height
 * may be null. All date math uses local time, like the Python tool. */
function computeStats(files) {
  var count = files.length;
  var stats = {
    count: count,
    tier: verdictForCount(count),
    totalBytes: 0,
    totalSizeHuman: "0 B",
    spanStart: null, spanEnd: null, spanDays: 0, spanYears: 0,
    spanStartLabel: "", spanEndLabel: "",
    perYear: {},
    nightCount: 0, nightShare: 0,
    weekendCount: 0, weekendShare: 0,
    busiestDay: null,
    longestStreak: 0,
    largest: null,
    oldest: null,
    duplicateGroups: 0,
    duplicateSuspects: 0,
    awards: []
  };
  if (count === 0) return stats;

  var total = 0, night = 0, weekend = 0;
  var perYear = {}, dayCounts = {}, dupGroups = {};
  var start = Infinity, end = -Infinity;
  var largest = null, oldest = null;

  for (var i = 0; i < files.length; i++) {
    var f = files[i];
    var d = new Date(f.mtime);
    total += f.size;
    if (f.mtime < start) { start = f.mtime; oldest = f; }
    if (f.mtime > end) { end = f.mtime; }
    if (!largest || f.size > largest.size) largest = f;

    var year = d.getFullYear();
    perYear[year] = (perYear[year] || 0) + 1;

    var hour = d.getHours();
    if (hour >= 22 || hour < 5) night++;

    var dow = d.getDay();
    if (dow === 0 || dow === 6) weekend++;

    var dayNum = Math.floor(Date.UTC(year, d.getMonth(), d.getDate()) / 86400000);
    dayCounts[dayNum] = (dayCounts[dayNum] || 0) + 1;

    if (f.width !== null && f.width !== undefined &&
        f.height !== null && f.height !== undefined) {
      var key = f.size + "|" + f.width + "x" + f.height;
      dupGroups[key] = (dupGroups[key] || 0) + 1;
    }
  }

  /* Busiest day + longest daily streak from sorted day numbers. */
  var days = Object.keys(dayCounts).map(Number).sort(function (a, b) { return a - b; });
  var busiest = null, streak = 0, run = 0, prev = null;
  for (var j = 0; j < days.length; j++) {
    var dn = days[j];
    if (!busiest || dayCounts[dn] > busiest.count) {
      busiest = { dayNum: dn, count: dayCounts[dn] };
    }
    run = (prev !== null && dn === prev + 1) ? run + 1 : 1;
    if (run > streak) streak = run;
    prev = dn;
  }

  var dupSuspects = 0, dupGroupCount = 0;
  for (var k in dupGroups) {
    if (dupGroups[k] >= 2) { dupGroupCount++; dupSuspects += dupGroups[k] - 1; }
  }

  stats.totalBytes = total;
  stats.totalSizeHuman = humanSize(total);
  stats.spanStart = start;
  stats.spanEnd = end;
  stats.spanDays = Math.floor((end - start) / 86400000);
  stats.spanYears = (end - start) / (365.25 * 86400000);
  stats.spanStartLabel = dateLabel(start);
  stats.spanEndLabel = dateLabel(end);
  stats.perYear = perYear;
  stats.nightCount = night;
  stats.nightShare = Math.round((night / count) * 1000) / 10;
  stats.weekendCount = weekend;
  stats.weekendShare = Math.round((weekend / count) * 1000) / 10;
  stats.busiestDay = {
    label: dayNumLabel(busiest.dayNum),
    count: busiest.count
  };
  stats.longestStreak = streak;
  stats.largest = { name: largest.name, size: largest.size, sizeHuman: humanSize(largest.size) };
  stats.oldest = { name: oldest.name, mtime: oldest.mtime, label: dateLabel(oldest.mtime) };
  stats.duplicateGroups = dupGroupCount;
  stats.duplicateSuspects = dupSuspects;

  /* Awards: priority order, at most 3. */
  var awards = [];
  if (stats.nightShare >= 20 && night >= 10) {
    awards.push({ name: "Night Owl",
      detail: night + " screenshots between 10 PM and 5 AM (" + stats.nightShare + "% of the total)." });
  }
  if (streak >= 7) {
    awards.push({ name: "Screenshot Streak",
      detail: streak + " consecutive days with at least one screenshot." });
  }
  if (dupSuspects >= 5) {
    awards.push({ name: "Repeat Customer",
      detail: dupSuspects + " files are exact duplicates of a sibling. The same screenshot, taken again." });
  }
  if (stats.spanYears >= 3) {
    awards.push({ name: "Archivist",
      detail: "The collection spans " + stats.spanYears.toFixed(1) + " years, back to " + stats.spanStartLabel + "." });
  }
  stats.awards = awards.slice(0, 3);
  return stats;
}

/* The ready-to-post verdict text for the copy button. */
function verdictPostText(stats) {
  return "My screenshots folder just got judged by Snap Judgment: " +
    stats.count + " screenshots, verdict " + stats.tier + ", " +
    stats.nightShare.toFixed(1) + "% taken at night. " +
    "It reads metadata only, never the pictures. " +
    "https://littlestjames82-sys.github.io/snap-judgment/demo.html";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    IMAGE_EXTENSIONS: IMAGE_EXTENSIONS,
    isImageName: isImageName,
    isScreenshotFile: isScreenshotFile,
    parseDimensions: parseDimensions,
    verdictForCount: verdictForCount,
    humanSize: humanSize,
    computeStats: computeStats,
    verdictPostText: verdictPostText
  };
}

/* ------------------------------------------------------------------ */
/* Browser wiring (never runs under Node)                              */
/* ------------------------------------------------------------------ */

if (typeof window !== "undefined" && typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDemo);
  } else {
    initDemo();
  }
}

function initDemo() {
  var MAX_SCANNED = 50000;
  var btn = document.getElementById("judgeBtn");
  var input = document.getElementById("dirInput");
  var status = document.getElementById("status");
  var result = document.getElementById("result");
  var noEvidence = document.getElementById("noEvidence");
  var currentStats = null;

  function setStatus(msg) { status.textContent = msg; }

  btn.addEventListener("click", function () {
    if (typeof window.showDirectoryPicker === "function") {
      scanWithPicker().catch(function () { setStatus(""); }); /* cancel: silent */
    } else {
      input.click();
    }
  });

  input.addEventListener("change", function () {
    if (!input.files || input.files.length === 0) return;
    var picked = Array.prototype.slice.call(input.files, 0, MAX_SCANNED);
    var candidates = [];
    for (var i = 0; i < picked.length; i++) {
      var file = picked[i];
      var parts = (file.webkitRelativePath || "").split("/");
      var parent = parts.length >= 2 ? parts[parts.length - 2] : "";
      if (isScreenshotFile(file.name, parent)) candidates.push(file);
    }
    finishScan(candidates);
    input.value = "";
  });

  document.getElementById("downloadCard").addEventListener("click", function () {
    var canvas = document.getElementById("card");
    if (canvas.toBlob) {
      canvas.toBlob(function (blob) {
        if (!blob) return;
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "snap-judgment-card.png";
        document.body.appendChild(a);
        a.click();
        setTimeout(function () {
          URL.revokeObjectURL(a.href);
          a.remove();
        }, 500);
      }, "image/png");
    }
  });

  document.getElementById("copyVerdict").addEventListener("click", function () {
    if (!currentStats) return;
    var text = verdictPostText(currentStats);
    var done = function () { setStatus("Verdict copied. Post it anywhere."); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else {
      fallbackCopy(text); done();
    }
  });

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) { /* clipboard is a courtesy */ }
    ta.remove();
  }

  async function scanWithPicker() {
    var dir = await window.showDirectoryPicker();
    var candidates = [];
    var scanned = 0;
    async function walk(handle, parentName, depth) {
      for await (var entry of handle.values()) {
        if (scanned >= MAX_SCANNED) return;
        if (entry.kind === "file") {
          scanned++;
          var file = await entry.getFile();
          if (isScreenshotFile(file.name, parentName)) candidates.push(file);
        } else if (entry.kind === "directory" && depth < 1) {
          await walk(entry, entry.name, depth + 1);
        }
      }
    }
    setStatus("Reading the evidence...");
    await walk(dir, dir.name, 0);
    await finishScan(candidates);
  }

  async function finishScan(candidateFiles) {
    setStatus("Reading the evidence: " + candidateFiles.length + " screenshots found...");
    var records = [];
    var BATCH = 32;
    for (var i = 0; i < candidateFiles.length; i += BATCH) {
      var slice = candidateFiles.slice(i, i + BATCH);
      var batch = await Promise.all(slice.map(readRecord));
      records = records.concat(batch);
      if (candidateFiles.length > 200) {
        setStatus("Reading the evidence: " + records.length + " of " +
          candidateFiles.length + " screenshots examined...");
      }
    }
    var stats = computeStats(records);
    currentStats = stats;
    setStatus("");
    render(stats);
  }

  async function readRecord(file) {
    var dims = null;
    try {
      var buf = await file.slice(0, 65536).arrayBuffer();
      dims = parseDimensions(new Uint8Array(buf));
    } catch (e) { dims = null; }
    return {
      name: file.name,
      size: file.size,
      mtime: file.lastModified,
      width: dims ? dims.width : null,
      height: dims ? dims.height : null
    };
  }

  function render(stats) {
    if (stats.count === 0) {
      result.hidden = true;
      noEvidence.hidden = false;
      noEvidence.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    noEvidence.hidden = true;
    document.getElementById("verdictTier").textContent = stats.tier;
    document.getElementById("verdictSummary").textContent =
      stats.count + " screenshots, " + stats.totalSizeHuman + " of evidence, spanning " +
      stats.spanStartLabel + " to " + stats.spanEndLabel + ".";

    var rows = [
      ["Total screenshots", stats.count.toLocaleString("en-US")],
      ["Total size", stats.totalSizeHuman + " (" + stats.totalBytes.toLocaleString("en-US") + " bytes)"],
      ["Date span", stats.spanStartLabel + " - " + stats.spanEndLabel + " (" + stats.spanDays.toLocaleString("en-US") + " days)"],
      ["Taken at night (10 PM-5 AM)", stats.nightCount + " (" + stats.nightShare + "%)"],
      ["Weekend share", stats.weekendCount + " (" + stats.weekendShare + "%)"],
      ["Busiest day", stats.busiestDay.label + " (" + stats.busiestDay.count + " screenshots)"],
      ["Longest daily streak", stats.longestStreak + (stats.longestStreak === 1 ? " day" : " days")],
      ["Largest screenshot", stats.largest.name + " (" + stats.largest.sizeHuman + ")"],
      ["Oldest exhibit", stats.oldest.name + " (" + stats.oldest.label + ")"],
      ["Duplicate suspects", stats.duplicateGroups + (stats.duplicateGroups === 1 ? " group, " : " groups, ") +
        stats.duplicateSuspects + " extra files"]
    ];
    var years = Object.keys(stats.perYear).sort();
    rows.push(["Screenshots per year",
      years.map(function (y) { return y + ": " + stats.perYear[y]; }).join(", ")]);

    var tbody = document.getElementById("evidenceBody");
    tbody.innerHTML = "";
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      var th = document.createElement("th"); th.textContent = r[0];
      var td = document.createElement("td"); td.textContent = r[1];
      tr.appendChild(th); tr.appendChild(td);
      tbody.appendChild(tr);
    });

    var awardsBox = document.getElementById("awardsBox");
    awardsBox.innerHTML = "";
    if (stats.awards.length > 0) {
      var h = document.createElement("h3");
      h.textContent = "Awards";
      awardsBox.appendChild(h);
      stats.awards.forEach(function (a) {
        var p = document.createElement("p");
        var b = document.createElement("b"); b.textContent = a.name + " - ";
        p.appendChild(b);
        p.appendChild(document.createTextNode(a.detail));
        awardsBox.appendChild(p);
      });
    }

    drawCard(stats);
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function drawCard(stats) {
    var canvas = document.getElementById("card");
    var ctx = canvas.getContext("2d");
    var W = 1200, H = 630;
    ctx.fillStyle = "#101014";
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "#d4af37";
    ctx.lineWidth = 3;
    ctx.strokeRect(18, 18, W - 36, H - 36);
    ctx.textAlign = "center";

    try { ctx.letterSpacing = "6px"; } catch (e) { /* cosmetic only */ }
    ctx.fillStyle = "#d4af37";
    ctx.font = "600 27px Georgia, 'Times New Roman', serif";
    ctx.fillText("THE COURT OF SCREENSHOT AFFAIRS", W / 2, 92);

    try { ctx.letterSpacing = "2px"; } catch (e) { /* cosmetic only */ }
    ctx.fillStyle = "#f5f2ea";
    ctx.font = "bold 96px Arial, Helvetica, sans-serif";
    ctx.fillText("SNAP JUDGMENT", W / 2, 208);

    ctx.fillStyle = "#d4af37";
    ctx.font = "bold 68px Arial, Helvetica, sans-serif";
    var tier = stats.tier;
    while (ctx.measureText(tier).width > W - 140 && tier.length > 4) {
      var size = parseInt(ctx.font.match(/(\d+)px/)[1], 10) - 4;
      ctx.font = "bold " + size + "px Arial, Helvetica, sans-serif";
      if (size <= 30) break;
    }
    ctx.fillText(tier, W / 2, 308);

    ctx.strokeStyle = "#4a4638";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(240, 348); ctx.lineTo(W - 240, 348); ctx.stroke();

    ctx.fillStyle = "#f5f2ea";
    ctx.font = "34px Menlo, Consolas, monospace";
    ctx.fillText(stats.count.toLocaleString("en-US") + " screenshots - " + stats.totalSizeHuman +
      " - " + monthYearLabel(stats.spanStart) + " - " + monthYearLabel(stats.spanEnd), W / 2, 408);

    ctx.fillStyle = "#a49fb2";
    ctx.font = "30px Arial, Helvetica, sans-serif";
    ctx.fillText("Night share " + stats.nightShare.toFixed(1) + "%", W / 2, 462);

    if (stats.awards.length > 0) {
      ctx.fillStyle = "#e8e3d8";
      ctx.font = "25px Arial, Helvetica, sans-serif";
      ctx.fillText("Awards: " + stats.awards.map(function (a) { return a.name; }).join(", "), W / 2, 508);
    }

    ctx.fillStyle = "#8f8b7d";
    ctx.font = "23px Arial, Helvetica, sans-serif";
    ctx.fillText("Judged from file metadata only. No pixels were read.", W / 2, 560);
    ctx.fillStyle = "#d4af37";
    ctx.font = "600 23px Menlo, Consolas, monospace";
    ctx.fillText("pip install snap-judgment", W / 2, 594);
  }
}
