/* Snap Judgment - browser demo.
 *
 * Pure stats core (verdictForCount, isScreenshotFile, parseDimensions,
 * computeStats, computePleaBargain, pleaClosing, sentenceFor,
 * intensityBand, courtCommentary, and the Downloads Docket's
 * downloadsVerdictFor, computeDownloadsStats, downloadsCommentary) is
 * environment-agnostic and unit-tested from Node via
 * tools/demo_js_test.js. Browser wiring lives at the bottom, guarded
 * by typeof window checks.
 *
 * The stats core is a faithful port of the Python tool's analyze(),
 * compute_awards(), compute_plea_bargain() and judge voices: same
 * thresholds, same evidence wording. The Downloads Docket ports
 * analyze_downloads() and its awards, sentences, and commentary the
 * same way.
 *
 * Privacy: the screenshots court reads file names, sizes, dates, and
 * a few header bytes for dimensions; the Downloads Docket reads names,
 * sizes, and dates only and never opens a file. The demo never
 * displays, uploads, or stores anything.
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
/* Verdict and judges                                                  */
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

var TIER_ORDER = [
  "Innocent Bystander",
  "Petty Offender",
  "Repeat Offender",
  "Habitual Hoarder",
  "Felony-Level Archivist",
  "Unrepentant Screenshot Felon"
];

/* "No Evidence" ranks below every tier (-1), like the Python plea math. */
function tierRank(tier) {
  return TIER_ORDER.indexOf(tier);
}

/* The fair judge's sentences (the Python TIERS defaults). */
var FAIR_SENTENCES = {
  "Unrepentant Screenshot Felon":
    "Sentence: delete nothing. We both know you won't. The court has stopped pretending otherwise.",
  "Felony-Level Archivist":
    "Sentence: delete nothing. We both know you won't.",
  "Habitual Hoarder":
    "Sentence: mandatory folder organization, suspended, because we both know it won't happen.",
  "Repeat Offender":
    "Sentence: probation. The screenshots folder gets a check-in once a month.",
  "Petty Offender":
    "Sentence: community service. Delete one duplicate. Just one. Baby steps.",
  "Innocent Bystander":
    "Sentence: case dismissed. Go take a screenshot of this verdict."
};

/* The other presiding judges, ported verbatim from the Python
 * VOICE_SENTENCES. Voices change the wording, never the math. */
var VOICE_SENTENCES = {
  "strict": {
    "Unrepentant Screenshot Felon":
      "Sentence: maximum. Every screenshot will be reviewed, named, and justified in writing. All of them.",
    "Felony-Level Archivist":
      "Sentence: a full audit of the evidence locker, this weekend. No extensions. No excuses.",
    "Habitual Hoarder":
      "Sentence: the folder is seized. Nothing new enters it until the backlog is halved.",
    "Repeat Offender":
      "Sentence: weekly inspections of the screenshots folder until this court says otherwise.",
    "Petty Offender":
      "Sentence: the duplicates are deleted today. Not tomorrow. Today.",
    "Innocent Bystander":
      "Sentence: none. This time. The court will be watching the folder."
  },
  "lenient": {
    "Unrepentant Screenshot Felon":
      "Encouragement: every archive tells a story, and yours is a long one. There is no rush to change.",
    "Felony-Level Archivist":
      "Encouragement: you kept it all because it all mattered at the time. That is not a crime in this court.",
    "Habitual Hoarder":
      "Encouragement: a healthy collection. Sort one folder someday, if you ever feel like it.",
    "Repeat Offender":
      "Encouragement: honestly, this is most people. A little tidy now and then, if the mood strikes.",
    "Petty Offender":
      "Encouragement: barely a habit at all. Keep doing what you are doing.",
    "Innocent Bystander":
      "Encouragement: case dismissed, with applause. Screenshot this verdict; you have earned one."
  }
};

var JUDGE_NAMES = {
  "strict": "The Hanging Judge",
  "fair": "The Fair Judge",
  "lenient": "The Sympathetic Judge"
};

/* The sentence for a tier in a judge's voice ("strict" | "fair" |
 * "lenient"); missing entries fall back to the fair judge, as in Python. */
function sentenceFor(tier, voice) {
  var fair = FAIR_SENTENCES[tier] || "";
  var pack = VOICE_SENTENCES[voice];
  if (pack && pack[tier]) return pack[tier];
  return fair;
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

function dayNumOf(ms) {
  var d = new Date(ms);
  return Math.floor(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000);
}

/* Python's _fmt_hour: 0 -> "12 AM", 13 -> "1 PM", 21 -> "9 PM". */
function fmtHour(hour) {
  var suffix = hour < 12 ? "AM" : "PM";
  return (hour % 12 || 12) + " " + suffix;
}

function round1(x) { return Math.round(x * 10) / 10; }

function hasDims(f) {
  return f.width !== null && f.width !== undefined &&
         f.height !== null && f.height !== undefined;
}

/* ------------------------------------------------------------------ */
/* Stats core (a port of the Python analyze + compute_awards)          */
/* ------------------------------------------------------------------ */

/* files: [{name, size, mtime (ms epoch), width, height}] - width/height
 * may be null. nowMs is the court's clock (Time Traveler and The
 * Archivist are judged against it); it defaults to Date.now().
 * All other date math uses local time, like the Python tool. */
function computeStats(files, nowMs) {
  if (nowMs === undefined || nowMs === null) nowMs = Date.now();
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
    threeAmCount: 0,
    workCount: 0, workShare: 0,
    busiestDay: null,
    busiestHour: 0, busiestHourCount: 0, habitShare: 0,
    longestStreak: 0,
    longestGapDays: 0,
    burstRecord: 0,
    largest: null,
    oldest: null,
    exhibits: [],
    futureShot: null,
    oldestAgeYears: 0,
    anniversary: null,
    heatmap: [],
    duplicateGroups: 0,
    duplicateFiles: 0,
    duplicateSuspects: 0,
    awards: [],
    pleaBargain: null
  };
  var r, c;
  for (r = 0; r < 7; r++) {
    var row = [];
    for (c = 0; c < 24; c++) row.push(0);
    stats.heatmap.push(row);
  }
  if (count === 0) return stats;

  /* Work on a copy sorted by (mtime, name), the Python shot order. */
  var sorted = files.slice().sort(function (a, b) {
    if (a.mtime !== b.mtime) return a.mtime - b.mtime;
    return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
  });

  var total = 0, night = 0, weekend = 0, threeAm = 0, work = 0;
  var perYear = {}, dayCounts = {}, dupGroups = {}, monthDays = {};
  var hourCounts = [];
  for (var h = 0; h < 24; h++) hourCounts.push(0);
  var largest = null;

  for (var i = 0; i < sorted.length; i++) {
    var f = sorted[i];
    var d = new Date(f.mtime);
    total += f.size;
    if (!largest || f.size > largest.size) largest = f;

    var year = d.getFullYear();
    perYear[year] = (perYear[year] || 0) + 1;

    var hour = d.getHours();
    hourCounts[hour]++;
    if (hour >= 22 || hour < 5) night++;
    if (hour === 2 || hour === 3) threeAm++;

    var jsDow = d.getDay();               /* Sun=0 .. Sat=6 */
    var pyDow = (jsDow + 6) % 7;          /* Mon=0 .. Sun=6, as Python */
    if (pyDow >= 5) weekend++;
    if (pyDow < 5 && hour >= 9 && hour < 17) work++;

    stats.heatmap[pyDow][hour]++;

    var dayNum = dayNumOf(f.mtime);
    dayCounts[dayNum] = (dayCounts[dayNum] || 0) + 1;

    var mdKey = d.getMonth() + "-" + d.getDate();
    if (!monthDays[mdKey]) {
      monthDays[mdKey] = { month: d.getMonth(), day: d.getDate(), count: 0, years: {} };
    }
    monthDays[mdKey].count++;
    monthDays[mdKey].years[year] = true;

    if (hasDims(f)) {
      var key = f.size + "|" + f.width + "x" + f.height;
      dupGroups[key] = (dupGroups[key] || 0) + 1;
    }
  }

  var start = sorted[0].mtime, end = sorted[count - 1].mtime;
  var firstDayNum = dayNumOf(start), lastDayNum = dayNumOf(end);

  /* Busiest day, longest streak, longest clean stretch. */
  var days = Object.keys(dayCounts).map(Number).sort(function (a, b) { return a - b; });
  var busiest = null, streak = 0, run = 0, prev = null, longestGap = 0;
  for (var j = 0; j < days.length; j++) {
    var dn = days[j];
    if (!busiest || dayCounts[dn] > busiest.count) {
      busiest = { dayNum: dn, count: dayCounts[dn] };
    }
    run = (prev !== null && dn === prev + 1) ? run + 1 : 1;
    if (run > streak) streak = run;
    if (prev !== null && dn - prev - 1 > longestGap) longestGap = dn - prev - 1;
    prev = dn;
  }

  /* Busiest hour: most shots in one clock hour; ties go to the
   * earliest hour, as in Python. */
  var busiestHour = 0, busiestHourCount = 0;
  for (var hh = 0; hh < 24; hh++) {
    if (hourCounts[hh] > busiestHourCount) {
      busiestHourCount = hourCounts[hh];
      busiestHour = hh;
    }
  }

  /* Burst record: most screenshots inside any rolling 60-second window. */
  var burst = 0, jj = 0;
  for (var bi = 0; bi < sorted.length; bi++) {
    jj = Math.max(jj, bi);
    while (jj + 1 < sorted.length && sorted[jj + 1].mtime - sorted[bi].mtime <= 60000) jj++;
    if (jj - bi + 1 > burst) burst = jj - bi + 1;
  }

  /* Duplicate suspects: groups sharing exact (size, dimensions). */
  var dupFiles = 0, dupExtras = 0, dupGroupCount = 0;
  for (var k in dupGroups) {
    if (dupGroups[k] >= 2) {
      dupGroupCount++;
      dupFiles += dupGroups[k];
      dupExtras += dupGroups[k] - 1;
    }
  }

  /* The earliest screenshot dated in the future, if any. */
  var futureShot = null;
  for (var fi = 0; fi < sorted.length; fi++) {
    if (sorted[fi].mtime > nowMs) {
      futureShot = { name: sorted[fi].name, mtime: sorted[fi].mtime };
      break;
    }
  }

  /* Screenshot anniversary: the month-day with the most screenshots
   * summed across years (ties to the earliest month-day); it only
   * counts once it has at least five to its name. */
  var anniversary = null, annivBest = null;
  for (var mk in monthDays) {
    var md = monthDays[mk];
    if (!annivBest || md.count > annivBest.count ||
        (md.count === annivBest.count &&
         (md.month < annivBest.month ||
          (md.month === annivBest.month && md.day < annivBest.day)))) {
      annivBest = md;
    }
  }
  if (annivBest && annivBest.count >= 5) {
    anniversary = {
      monthDay: MONTHS[annivBest.month] + " " + annivBest.day,
      count: annivBest.count,
      years: Object.keys(annivBest.years).length
    };
  }

  var oldestFile = sorted[0];
  stats.totalBytes = total;
  stats.totalSizeHuman = humanSize(total);
  stats.spanStart = start;
  stats.spanEnd = end;
  stats.spanDays = lastDayNum - firstDayNum;
  stats.spanYears = (end - start) / (365.25 * 86400000);
  stats.spanStartLabel = dateLabel(start);
  stats.spanEndLabel = dateLabel(end);
  stats.perYear = perYear;
  stats.nightCount = night;
  stats.nightShare = round1(100 * night / count);
  stats.weekendCount = weekend;
  stats.weekendShare = round1(100 * weekend / count);
  stats.threeAmCount = threeAm;
  stats.workCount = work;
  stats.workShare = round1(100 * work / count);
  stats.busiestDay = { label: dayNumLabel(busiest.dayNum), count: busiest.count };
  stats.busiestHour = busiestHour;
  stats.busiestHourCount = busiestHourCount;
  stats.habitShare = round1(100 * busiestHourCount / count);
  stats.longestStreak = streak;
  stats.longestGapDays = longestGap;
  stats.burstRecord = burst;
  stats.largest = { name: largest.name, size: largest.size, sizeHuman: humanSize(largest.size) };
  stats.oldest = { name: oldestFile.name, mtime: oldestFile.mtime, label: dateLabel(oldestFile.mtime) };
  stats.exhibits = sorted.slice(0, 5).map(function (s) {
    return { name: s.name, mtime: s.mtime, label: dateLabel(s.mtime) };
  });
  stats.futureShot = futureShot;
  stats.oldestAgeYears = Math.max(0, Math.floor((nowMs - start) / (365.25 * 86400000)));
  stats.anniversary = anniversary;
  stats.duplicateGroups = dupGroupCount;
  stats.duplicateFiles = dupFiles;
  stats.duplicateSuspects = dupExtras;

  /* Awards: every Python award whose inputs the demo collects, in the
   * Python order, with the Python thresholds and evidence wording.
   * Nothing here needs the court record, so nothing is skipped. */
  var awards = [];
  if (night >= 20 && stats.nightShare >= 25) {
    awards.push({ name: "Night Owl",
      detail: night + " screenshots between 10 PM and 5 AM (" + stats.nightShare.toFixed(1) + "% of the total)." });
  }
  if (threeAm >= 10) {
    awards.push({ name: "The 3 AM Club",
      detail: threeAm + " screenshots taken between 2 AM and 4 AM, when nothing on screen can be urgent." });
  }
  if (burst >= 8) {
    awards.push({ name: "Burst Mode",
      detail: burst + " screenshots inside a single 60-second window." });
  }
  if (Math.floor((nowMs - start) / 86400000) >= 3 * 365) {
    awards.push({ name: "The Archivist",
      detail: "The oldest screenshot dates to " + stats.oldest.label + ", over three years ago, and it is still here." });
  }
  if (count >= 50 && stats.workShare >= 50) {
    awards.push({ name: "Receipt Keeper",
      detail: stats.workShare.toFixed(1) + "% of shots were taken on weekdays between 9 AM and 5 PM. Very professional." });
  }
  if (dupFiles >= 10) {
    awards.push({ name: "Repeat Customer",
      detail: dupFiles + " files share an exact size and dimensions with a sibling. The same screenshot, taken again." });
  }
  if (stats.habitShare >= 25) {
    awards.push({ name: "Creature of Habit",
      detail: stats.habitShare.toFixed(1) + "% of all screenshots were taken in the " + fmtHour(busiestHour) + " hour. Same time, same impulse." });
  }
  if (total >= 1024 * 1024 * 1024) {
    awards.push({ name: "Gigabyte Club",
      detail: "The screenshots alone total " + stats.totalSizeHuman + ". The evidence locker needs a bigger room." });
  }
  if (futureShot) {
    awards.push({ name: "Time Traveler",
      detail: futureShot.name + " is dated " + dateLabel(futureShot.mtime) + ", which has not happened yet. The court admits it anyway." });
  }
  if (stats.spanDays >= 4 * 365) {
    awards.push({ name: "The Long Haul",
      detail: "The collection spans " + (stats.spanDays / 365.25).toFixed(1) + " years of screenshots." });
  }
  if (stats.spanDays >= 8 * 365) {
    awards.push({ name: "Lifer",
      detail: "The collection spans " + (stats.spanDays / 365.25).toFixed(1) + " years. The screenshots have seniority now." });
  }
  if (stats.spanDays >= 10 * 365) {
    awards.push({ name: "Ancient Relic",
      detail: stats.oldest.name + " dates to " + stats.oldest.label + ", over ten years before the newest screenshot in evidence." });
  }
  if (anniversary && anniversary.count >= 15) {
    awards.push({ name: "Anniversary",
      detail: anniversary.count + " screenshots fall on " + anniversary.monthDay + ", gathered across " +
        anniversary.years + " different years. Some dates just attract evidence." });
  }
  if (streak >= 7) {
    awards.push({ name: "Screenshot Streak",
      detail: streak + " consecutive days with at least one screenshot." });
  }
  if (longestGap >= 30) {
    awards.push({ name: "Clean Stretch",
      detail: longestGap + " days without a single screenshot. The court's longest recorded moment of restraint." });
  }
  if (count > 0 && count < 50) {
    awards.push({ name: "Minimalist",
      detail: "Only " + count + " screenshots. The court rarely sees such restraint." });
  }
  stats.awards = awards;
  return stats;
}

/* ------------------------------------------------------------------ */
/* The plea bargain (a port of the Python compute_plea_bargain)        */
/* ------------------------------------------------------------------ */

/* What would happen IF the defendant deleted (a) every duplicate
 * suspect except one keeper per exact size+dimensions group, and
 * (b) every screenshot older than pleaYears years, measured from the
 * newest screenshot's date. Nothing is deleted; this is math. */
function computePleaBargain(files, pleaYears) {
  if (pleaYears === undefined || pleaYears === null) pleaYears = 2;
  var plea = {
    years: pleaYears,
    oldRemoved: 0,
    duplicateRemoved: 0,
    removedCount: 0,
    removedBytes: 0,
    removedHuman: "0 B",
    remainingCount: files.length,
    projectedTier: verdictForCount(files.length)
  };
  if (files.length === 0) return plea;

  var newest = -Infinity;
  for (var i = 0; i < files.length; i++) {
    if (files[i].mtime > newest) newest = files[i].mtime;
  }
  var nd = new Date(newest);
  var cy = nd.getFullYear() - pleaYears, cm = nd.getMonth(), cd = nd.getDate();
  var cutoffDayNum = Math.floor(Date.UTC(cy, cm, cd) / 86400000);
  if (new Date(cutoffDayNum * 86400000).getUTCMonth() !== cm) {
    /* Feb 29 lands on Feb 28, as in Python's _minus_years. */
    cutoffDayNum = Math.floor(Date.UTC(cy, 1, 28) / 86400000);
  }

  var groups = {};
  for (var j = 0; j < files.length; j++) {
    var f = files[j];
    if (hasDims(f)) {
      var key = f.size + "|" + f.width + "x" + f.height;
      if (!groups[key]) groups[key] = [];
      groups[key].push(f);
    }
  }
  var extraSet = new Set();
  for (var k in groups) {
    var members = groups[k];
    if (members.length >= 2) {
      members.sort(function (a, b) {
        if (a.mtime !== b.mtime) return a.mtime - b.mtime;
        return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
      });
      for (var m = 1; m < members.length; m++) extraSet.add(members[m]);
    }
  }

  var removedBytes = 0;
  for (var n = 0; n < files.length; n++) {
    var shot = files[n];
    var isOld = dayNumOf(shot.mtime) < cutoffDayNum;
    var isDuplicate = extraSet.has(shot);
    if (isOld) {
      plea.oldRemoved++;
      removedBytes += shot.size;
    } else if (isDuplicate) {
      plea.duplicateRemoved++;
      removedBytes += shot.size;
    }
  }
  plea.removedCount = plea.oldRemoved + plea.duplicateRemoved;
  plea.removedBytes = removedBytes;
  plea.removedHuman = humanSize(removedBytes);
  plea.remainingCount = files.length - plea.removedCount;
  plea.projectedTier = verdictForCount(plea.remainingCount);
  return plea;
}

function pleaClosing(plea, currentTier) {
  if (tierRank(plea.projectedTier) < tierRank(currentTier)) {
    return "The court would accept this plea.";
  }
  return "The court notes this plea changes nothing. The verdict stands.";
}

/* ------------------------------------------------------------------ */
/* Roast intensity and court commentary                                */
/* ------------------------------------------------------------------ */

/* The roast intensity slider runs 0-100 in five bands. Intensity is
 * wording only: it flavors the court commentary below and never
 * touches the verdict, the counts, the awards, or the plea math. */
var INTENSITY_BANDS = ["Gentle", "Kind", "Honest", "Spicy", "Full roast"];

function intensityBand(value) {
  var v = Number(value);
  if (isNaN(v)) v = 50;
  v = Math.max(0, Math.min(100, v));
  var level = v < 20 ? 0 : v < 40 ? 1 : v < 60 ? 2 : v < 80 ? 3 : 4;
  return { level: level, name: INTENSITY_BANDS[level] };
}

var NO_COMMENTARY =
  "No evidence, no commentary. The court does not roast an empty folder.";

/* One template per band (Gentle, Kind, Honest, Spicy, Full roast) for
 * each case the court can comment on. Tokens: {night} night count,
 * {share} night share, {count} total screenshots, {dup} duplicate
 * extras, {streak} longest streak, {age} oldest screenshot's age in
 * years. The roasts are about the habit, never the person. */
var COMMENTARY = {
  night: [
    "The court sees {night} screenshots taken at night and simply notes the defendant keeps late hours.",
    "{night} screenshots after dark, {share}% of the whole folder. The court suspects the defendant's best ideas simply arrive after 10 PM.",
    "{night} of these screenshots, {share}%, were taken between 10 PM and 5 AM. That is not a habit. That is a second shift.",
    "{share}% of this folder was captured in the dark, {night} screenshots in total. The court is beginning to think the defendant considers daylight a rumor.",
    "{night} screenshots after 10 PM. The court has reviewed the defendant's sleep schedule and found it guilty too."
  ],
  hoard: [
    "The court counts {count} screenshots and sees a thorough archivist at work. Every one of them meant something once.",
    "{count} screenshots is a serious collection. The court admires the commitment, even if the filing system is more of a pile.",
    "{count} screenshots. The court notes the defendant has never once paused to think, perhaps I already have this one.",
    "{count} screenshots. Somewhere around the halfway mark this stopped being a folder and became a storage unit.",
    "{count} screenshots. The court would ask the defendant to produce one specific screenshot, but nobody involved has that kind of time. That is rather the point."
  ],
  dup: [
    "The court found {dup} screenshots that duplicate a sibling, and chooses to believe the defendant simply wanted to be sure.",
    "{dup} duplicate extras. The defendant screenshots the way nervous travelers check for their passport, repeatedly and just in case.",
    "{dup} of these screenshots are exact copies of another screenshot. The same moment, filed twice, for safekeeping.",
    "{dup} duplicates. The defendant's working theory appears to be that a screenshot does not count until it exists twice.",
    "{dup} duplicate screenshots. The court has now reviewed the same evidence multiple times, and it was not that compelling the first time."
  ],
  streak: [
    "The court notes a {streak}-day streak of screenshots and sees real consistency in it. Some people keep a journal. The defendant keeps screenshots.",
    "{streak} days in a row with at least one screenshot. The streak itself is impressive. The restraint is harder to find.",
    "{streak} consecutive days of screenshots. The court has seen shorter relationships, and less committed ones.",
    "{streak} straight days, not one missed. Whatever appeared on that screen, it never stood a chance of going uncaptured.",
    "A {streak}-day streak, unbroken. The court hereby promotes screenshotting from habit to lifestyle, effective retroactively."
  ],
  old: [
    "The oldest screenshot here is {age} years old, and the court finds something sweet in that. Some things are simply worth keeping.",
    "A screenshot from {age} years ago still lives in this folder. The court respects a long memory, even a digital one.",
    "The oldest evidence is {age} years old. The court asks, for the record, if the defendant remembers taking it, or why.",
    "{age} years old, the senior screenshot in this folder has survived new phones, new updates, and several opportunities to delete it.",
    "The court presents a screenshot {age} years old. It has been migrated from device to device like a family heirloom, except an heirloom is occasionally looked at."
  ],
  plain: [
    "The court has reviewed {count} screenshots and finds a perfectly ordinary folder. The defendant is free to go.",
    "{count} screenshots, and nothing the court has not seen a hundred times. A respectable, unremarkable collection.",
    "{count} screenshots. Not a crime scene, not a model of restraint. The court records a shrug and moves on.",
    "{count} screenshots, and the most notable thing about them is that there are {count} of them. The court expected either far more drama or far less folder.",
    "{count} screenshots. The court searched this folder for a scandal and found only volume. Even the commentary is disappointed."
  ]
};

function fillCommentary(tpl, values) {
  return tpl.replace(/\{(\w+)\}/g, function (m, key) {
    return values[key] !== undefined ? values[key] : m;
  });
}

/* The court's commentary on a hearing: one line, flavored by the
 * roast intensity band (level 0-4), aimed at the defendant's most
 * damning stat. Priority: night share of 40% or more, then a count of
 * 1,000 or more, then 10 or more duplicate extras, then a streak of
 * 14 days or more, then an oldest screenshot 5 years old or more,
 * then the plain count. Pure: reads stats, never writes to it. */
function courtCommentary(stats, level) {
  if (!stats || !stats.count) return NO_COMMENTARY;
  var lvl = Math.floor(Number(level));
  if (isNaN(lvl)) lvl = 2;
  lvl = Math.max(0, Math.min(4, lvl));
  var values = {
    night: stats.nightCount || 0,
    share: Number(stats.nightShare || 0).toFixed(1),
    count: Number(stats.count).toLocaleString("en-US"),
    dup: stats.duplicateSuspects || 0,
    streak: stats.longestStreak || 0,
    age: stats.oldestAgeYears || 0
  };
  var key = "plain";
  if (stats.nightShare >= 40) key = "night";
  else if (stats.count >= 1000) key = "hoard";
  else if ((stats.duplicateSuspects || 0) >= 10) key = "dup";
  else if ((stats.longestStreak || 0) >= 14) key = "streak";
  else if ((stats.oldestAgeYears || 0) >= 5) key = "old";
  return fillCommentary(COMMENTARY[key][lvl], values);
}

/* The ready-to-post verdict text for the copy button. When a plea
 * bargain rides along on the stats, the text gains the plea line:
 * the projected drop when the plea improves the tier, the standing
 * verdict when it changes nothing. */
function verdictPostText(stats) {
  if (stats && stats.court === "downloads") return downloadsPostText(stats);
  var text = "My screenshots folder just got judged by Snap Judgment: " +
    stats.count + " screenshots, verdict " + stats.tier + ", " +
    stats.nightShare.toFixed(1) + "% taken at night. " +
    "It reads metadata only, never the pictures.";
  var plea = stats.pleaBargain;
  if (plea) {
    if (tierRank(plea.projectedTier) < tierRank(stats.tier)) {
      text += " The plea bargain: delete " + plea.removedCount +
        " screenshots (duplicates and older than " + plea.years +
        " years) and the verdict drops to " + plea.projectedTier + ".";
    } else {
      text += " The plea bargain changes nothing. The verdict stands.";
    }
  }
  return text + " https://littlestjames82-sys.github.io/snap-judgment/demo.html";
}

/* ------------------------------------------------------------------ */
/* The Downloads Docket (a port of the Python downloads court)         */
/* ------------------------------------------------------------------ */

/* The docket judges every file on the surface of a folder, any type,
 * from names, sizes, and dates only: no header reads, no file is ever
 * opened. Records are {name, size, mtime} - the same shape the
 * screenshots core uses, minus dimensions. */

var DL_INSTALLER_EXTS = [".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage"];
var DL_ARCHIVE_EXTS = [".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"];

/* Exact thresholds from the Python DL_TIERS. */
function downloadsVerdictFor(count) {
  if (count <= 0) return "No Evidence";
  if (count >= 1000) return "Superfund Site";
  if (count >= 500) return "Hoarding Situation";
  if (count >= 250) return "Landfill Adjacent";
  if (count >= 100) return "Digital Junk Drawer";
  if (count >= 25) return "Mild Clutter";
  return "Tidy Desk";
}

/* The fair judge's downloads sentences (the Python DL_TIERS defaults). */
var DL_FAIR_SENTENCES = {
  "Superfund Site":
    "Sentence: the court recommends hazmat suits and a weekend. Bring help.",
  "Hoarding Situation":
    "Sentence: delete nothing. We both know you won't.",
  "Landfill Adjacent":
    "Sentence: mandatory organization, suspended, because we both know it won't happen.",
  "Digital Junk Drawer":
    "Sentence: probation. The downloads folder gets a check-in once a month.",
  "Mild Clutter":
    "Sentence: community service. Delete one installer. Just one. Baby steps.",
  "Tidy Desk":
    "Sentence: case dismissed. Keep the desk exactly as it is."
};

/* The other presiding judges on the docket, ported verbatim from the
 * Python VOICE_SENTENCES downloads entries. */
var DL_VOICE_SENTENCES = {
  "strict": {
    "Superfund Site":
      "Sentence: maximum. Every file will be reviewed, named, and justified in writing. All of them.",
    "Hoarding Situation":
      "Sentence: a full audit of the downloads folder, this weekend. No extensions. No excuses.",
    "Landfill Adjacent":
      "Sentence: the folder is seized. Nothing new enters it until the backlog is halved.",
    "Digital Junk Drawer":
      "Sentence: weekly inspections of the downloads folder until this court says otherwise.",
    "Mild Clutter":
      "Sentence: the installers are deleted today. Not tomorrow. Today.",
    "Tidy Desk":
      "Sentence: none. This time. The court will be watching the folder."
  },
  "lenient": {
    "Superfund Site":
      "Encouragement: every archive tells a story, and yours is an epic. There is no rush to change.",
    "Hoarding Situation":
      "Encouragement: you kept it all because it all mattered at the time. That is not a crime in this court.",
    "Landfill Adjacent":
      "Encouragement: a healthy collection. Sort one folder someday, if you ever feel like it.",
    "Digital Junk Drawer":
      "Encouragement: honestly, this is most people. A little tidy now and then, if the mood strikes.",
    "Mild Clutter":
      "Encouragement: barely a habit at all. Keep doing what you are doing.",
    "Tidy Desk":
      "Encouragement: case dismissed, with applause. Download something nice; you have earned the space."
  }
};

function downloadsSentenceFor(tier, voice) {
  var fair = DL_FAIR_SENTENCES[tier] || "";
  var pack = DL_VOICE_SENTENCES[voice];
  if (pack && pack[tier]) return pack[tier];
  return fair;
}

/* The Python human_bytes: whole bytes as an integer, everything else
 * with exactly one decimal. (The screenshots core's humanSize rounds
 * large values the way the Python card code does; the docket's
 * evidence lines use this one, matching the CLI report.) */
function dlHumanBytes(n) {
  var units = ["B", "KB", "MB", "GB", "TB"];
  var value = n;
  for (var i = 0; i < units.length; i++) {
    if (value < 1024 || units[i] === "TB") {
      if (units[i] === "B") return Math.floor(value) + " B";
      return value.toFixed(1) + " " + units[i];
    }
    value /= 1024;
  }
  return value.toFixed(1) + " TB";
}

/* Python's Path(name).suffix: the part from the last dot, but only
 * when the dot is neither the first nor the last character. */
function dlExtOf(name) {
  var i = name.lastIndexOf(".");
  if (i > 0 && i < name.length - 1) return name.slice(i).toLowerCase();
  return "";
}

/* Python's Path(name).stem: the name with its suffix removed. */
function dlStemOf(name) {
  var i = name.lastIndexOf(".");
  if (i > 0 && i < name.length - 1) return name.slice(0, i);
  return name;
}

/* The Python _DL_SUFFIX_RE: a trailing " (n)" or " copy"/" copy n". */
var DL_SUFFIX_RE = /\s*\(\d+\)\s*$|\s+copy(?:\s+\d+)?\s*$/i;

/* The identity of a download with its copy suffixes stripped:
 * "setup (2).exe", "setup copy.exe" and "setup.exe" share a key.
 * Returns {stem, ext}; the stem is trimmed and lowercased. */
function downloadsGroupKey(name) {
  var stem = dlStemOf(name);
  var prev;
  do {
    prev = stem;
    stem = stem.replace(DL_SUFFIX_RE, "");
  } while (stem !== prev);
  return { stem: stem.trim().toLowerCase(), ext: dlExtOf(name) };
}

/* Every statistic for a downloads hearing, a port of the Python
 * analyze_downloads. records: [{name, size, mtime (ms epoch)}].
 * nowMs is the court's clock; it defaults to Date.now(). */
function computeDownloadsStats(records, nowMs) {
  if (nowMs === undefined || nowMs === null) nowMs = Date.now();
  var count = records.length;
  var stats = {
    court: "downloads",
    count: count,
    tier: downloadsVerdictFor(count),
    totalBytes: 0,
    totalSizeHuman: "0 B",
    spanStart: null, spanEnd: null, spanDays: 0,
    spanStartLabel: "", spanEndLabel: "",
    installerCount: 0, installerBytes: 0, installerHuman: "0 B",
    archiveCount: 0, archiveBytes: 0,
    duplicateGroups: 0, duplicateExtra: 0, duplicateExtraBytes: 0,
    duplicateWasteHuman: "0 B",
    oldestName: "", oldestLabel: "", oldestAgeYears: 0,
    largestName: "", largestSize: 0, largestHuman: "0 B",
    extensions: [],
    oldCount: 0, oldShare: 0,
    torrentCount: 0, receiptCount: 0,
    awards: []
  };
  if (count === 0) return stats;

  /* Sorted by (mtime, name), the Python DlFile order. */
  var sorted = records.slice().sort(function (a, b) {
    if (a.mtime !== b.mtime) return a.mtime - b.mtime;
    return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
  });

  var total = 0, instCount = 0, instBytes = 0, archCount = 0, archBytes = 0;
  var torrent = 0, receipt = 0, oldCount = 0;
  var groups = {};   /* key -> {stem, members} */
  var extCounts = {};
  var largest = sorted[0];

  for (var i = 0; i < sorted.length; i++) {
    var f = sorted[i];
    var ext = dlExtOf(f.name);
    total += f.size;
    if (DL_INSTALLER_EXTS.indexOf(ext) !== -1) { instCount++; instBytes += f.size; }
    if (DL_ARCHIVE_EXTS.indexOf(ext) !== -1) { archCount++; archBytes += f.size; }
    if (ext === ".torrent") torrent++;
    var lowerName = f.name.toLowerCase();
    if (lowerName.indexOf("invoice") !== -1 || lowerName.indexOf("receipt") !== -1) receipt++;
    if (Math.floor((nowMs - f.mtime) / 86400000) > 365) oldCount++;

    var gk = downloadsGroupKey(f.name);
    var gKey = gk.stem + "|" + gk.ext;
    if (!groups[gKey]) groups[gKey] = { stem: gk.stem, members: [] };
    groups[gKey].members.push(f);

    var extLabel = ext || "(no extension)";
    extCounts[extLabel] = (extCounts[extLabel] || 0) + 1;

    /* The Python largest is max by (size, name). */
    if (f.size > largest.size ||
        (f.size === largest.size && f.name > largest.name)) {
      largest = f;
    }
  }

  /* Duplicate downloads: in each group of two or more, the keeper is
   * the file whose stem is the bare group stem (else the first name
   * alphabetically); every other member is an extra copy. */
  var dupGroups = 0, dupExtra = 0, dupExtraBytes = 0;
  for (var k in groups) {
    var members = groups[k].members;
    if (members.length < 2) continue;
    dupGroups++;
    var keeper = null, keeperFlag = 0;
    for (var m = 0; m < members.length; m++) {
      var flag = dlStemOf(members[m].name).toLowerCase() === groups[k].stem ? 0 : 1;
      if (!keeper || flag < keeperFlag ||
          (flag === keeperFlag &&
           members[m].name.toLowerCase() < keeper.name.toLowerCase())) {
        keeper = members[m];
        keeperFlag = flag;
      }
    }
    for (var n = 0; n < members.length; n++) {
      if (members[n] !== keeper) {
        dupExtra++;
        dupExtraBytes += members[n].size;
      }
    }
  }

  var oldest = sorted[0];
  var oldestDays = Math.floor((nowMs - oldest.mtime) / 86400000);

  var extList = Object.keys(extCounts).map(function (e) {
    return { ext: e, count: extCounts[e] };
  });
  extList.sort(function (a, b) {
    if (a.count !== b.count) return b.count - a.count;
    return a.ext < b.ext ? -1 : (a.ext > b.ext ? 1 : 0);
  });

  stats.totalBytes = total;
  stats.totalSizeHuman = dlHumanBytes(total);
  stats.spanStart = sorted[0].mtime;
  stats.spanEnd = sorted[count - 1].mtime;
  stats.spanDays = dayNumOf(stats.spanEnd) - dayNumOf(stats.spanStart);
  stats.spanStartLabel = dateLabel(stats.spanStart);
  stats.spanEndLabel = dateLabel(stats.spanEnd);
  stats.installerCount = instCount;
  stats.installerBytes = instBytes;
  stats.installerHuman = dlHumanBytes(instBytes);
  stats.archiveCount = archCount;
  stats.archiveBytes = archBytes;
  stats.duplicateGroups = dupGroups;
  stats.duplicateExtra = dupExtra;
  stats.duplicateExtraBytes = dupExtraBytes;
  stats.duplicateWasteHuman = dlHumanBytes(dupExtraBytes);
  stats.oldestName = oldest.name;
  stats.oldestLabel = dateLabel(oldest.mtime);
  stats.oldestAgeYears = Math.max(0, Math.floor(oldestDays / 365));
  stats.largestName = largest.name;
  stats.largestSize = largest.size;
  stats.largestHuman = dlHumanBytes(largest.size);
  stats.extensions = extList.slice(0, 8);
  stats.oldCount = oldCount;
  stats.oldShare = round1(100 * oldCount / count);
  stats.torrentCount = torrent;
  stats.receiptCount = receipt;

  /* The docket's awards, in the Python order, with the Python
   * thresholds and evidence wording. */
  var awards = [];
  if (instCount >= 10) {
    awards.push({ key: "installer_archaeologist", name: "Installer Archaeologist",
      detail: instCount + " installers (" + stats.installerHuman + ") kept after their work was done. The court has excavated older civilizations with fewer setup files." });
  }
  if (archCount >= 15) {
    awards.push({ key: "zip_hoarder", name: "Zip Hoarder",
      detail: archCount + " archives on disk, still zipped. Whatever is inside them, it is staying inside them." });
  }
  if (dupExtra >= 5) {
    awards.push({ key: "double_downloader", name: "Double Downloader",
      detail: dupExtra + " extra copies of files the defendant already had, in " + dupGroups + " duplicate sets. Downloaded twice, trusted once." });
  }
  if (stats.oldestAgeYears >= 3) {
    awards.push({ key: "ancient_relic", name: "Ancient Relic",
      detail: stats.oldestName + " dates to " + stats.oldestLabel + ", over three years ago, and it is still here." });
  }
  if (total >= 5 * 1024 * 1024 * 1024) {
    awards.push({ key: "gigabyte_landfill", name: "Gigabyte Landfill",
      detail: "The downloads alone total " + stats.totalSizeHuman + ". The landfill has its own weather now." });
  }
  if (torrent >= 1) {
    awards.push({ key: "still_seeding", name: "Still Seeding",
      detail: torrent + " torrent file" + (torrent !== 1 ? "s" : "") + " on disk. The court does not ask what they point to. It never asks." });
  }
  if (receipt >= 1) {
    awards.push({ key: "receipt_keeper_dx", name: "Receipt Keeper DX",
      detail: receipt + " file" + (receipt !== 1 ? "s" : "") + " with invoice or receipt in the name. The tax folder sends its regards." });
  }
  if (stats.oldShare >= 60) {
    awards.push({ key: "time_capsule", name: "Time Capsule",
      detail: stats.oldShare.toFixed(1) + "% of these files are over a year old. This is less a folder than a stratum." });
  }
  stats.awards = awards;
  return stats;
}

/* One template per band (Gentle, Kind, Honest, Spicy, Full roast) for
 * each case the downloads court can comment on, ported verbatim from
 * the Python DL_COMMENTARY_TEMPLATES. Tokens: {dup} duplicate extras,
 * {installers} installer count, {age} oldest file's age in years,
 * {size} total size, {count} total files. */
var DL_COMMENTARY = {
  dup: [
    "The court found {dup} files that were downloaded more than once, and chooses to believe the defendant simply wanted to be very sure.",
    "{dup} duplicate downloads. The defendant downloads the way nervous travelers check for their passport, repeatedly and just in case.",
    "{dup} of these files are the same download, fetched again. The internet remembers, even when the defendant does not.",
    "{dup} duplicate downloads. The defendant's working theory appears to be that a file does not exist until it has been downloaded twice.",
    "{dup} duplicate downloads. The court has now reviewed the same installer multiple times, and it was not that compelling the first time."
  ],
  installers: [
    "The court counts {installers} installers and sees a well-equipped machine. Every one of them set something up once.",
    "{installers} installers kept on disk. The defendant holds onto the boxes the software came in, just in case.",
    "{installers} installers. The software is already installed. The installers remain, as a tribute.",
    "{installers} installers, kept long after the software moved in. The court notes the defendant could open a small museum of setup wizards.",
    "{installers} installers. The defendant has kept every key to every door ever opened on this machine, including the doors that were demolished."
  ],
  old: [
    "The oldest file here is {age} years old, and the court finds something sweet in that. Some things are simply worth keeping.",
    "A file from {age} years ago still lives in this folder. The court respects a long memory, even a digital one.",
    "The oldest evidence is {age} years old. The court asks, for the record, if the defendant remembers downloading it, or why.",
    "{age} years old, the senior file in this folder has survived new machines, new updates, and several opportunities to delete it.",
    "The court presents a file {age} years old. It has been carried from machine to machine like a family heirloom, except an heirloom is occasionally looked at."
  ],
  size: [
    "The court weighs the evidence at {size} and sees a thorough collector at work. Every byte meant something once.",
    "{size} of downloads. The court admires the commitment, even if the filing system is more of a pile.",
    "{size}. The court notes the defendant has never once paused to think, perhaps I already have this one.",
    "{size} of downloads. Somewhere around the halfway mark this stopped being a folder and became a storage unit.",
    "{size}. The court would ask the defendant to produce one specific file, but nobody involved has that kind of time. That is rather the point."
  ],
  plain: [
    "The court has reviewed {count} files and finds a perfectly ordinary folder. The defendant is free to go.",
    "{count} files, and nothing the court has not seen a hundred times. A respectable, unremarkable collection.",
    "{count} files. Not a crime scene, not a model of restraint. The court records a shrug and moves on.",
    "{count} files, and the most notable thing about them is that there are {count} of them. The court expected either far more drama or far less folder.",
    "{count} files. The court searched this folder for a scandal and found only volume. Even the commentary is disappointed."
  ]
};

/* The docket's commentary: one line, flavored by the roast intensity
 * band, aimed at the folder's most damning stat. Priority, as in
 * Python: duplicate extras of 5 or more, then 10 or more installers,
 * then an oldest file 3 years old or more, then 5 GB or more, then
 * the plain count. Pure: reads stats, never writes to it. */
function downloadsCommentary(stats, level) {
  if (!stats || !stats.count) return NO_COMMENTARY;
  var lvl = Math.floor(Number(level));
  if (isNaN(lvl)) lvl = 2;
  lvl = Math.max(0, Math.min(4, lvl));
  var values = {
    dup: stats.duplicateExtra || 0,
    installers: stats.installerCount || 0,
    age: stats.oldestAgeYears || 0,
    size: stats.totalSizeHuman || "0 B",
    count: Number(stats.count).toLocaleString("en-US")
  };
  var key = "plain";
  if ((stats.duplicateExtra || 0) >= 5) key = "dup";
  else if ((stats.installerCount || 0) >= 10) key = "installers";
  else if ((stats.oldestAgeYears || 0) >= 3) key = "old";
  else if ((stats.totalBytes || 0) >= 5 * 1024 * 1024 * 1024) key = "size";
  return fillCommentary(DL_COMMENTARY[key][lvl], values);
}

/* The ready-to-post verdict text for a downloads hearing. */
function downloadsPostText(stats) {
  return "My Downloads folder just got judged by Snap Judgment: " +
    stats.count + " files, verdict " + stats.tier + ", " +
    stats.totalSizeHuman + " of evidence. " +
    "It reads names, sizes, and dates only, and never opens a file. " +
    "https://littlestjames82-sys.github.io/snap-judgment/demo.html";
}

/* webkitdirectory paths: "Folder/file" is a top-level file of the
 * picked folder; "Folder/sub/file" is one level too deep for the
 * docket, which judges only the surface. A bare file name (no folder
 * path at all) is top level by definition. */
function isTopLevelPath(relPath) {
  if (!relPath) return true;
  var parts = relPath.split("/").filter(function (p) { return p.length > 0; });
  return parts.length <= 2;
}

/* A synthetic Downloads folder of 120 files, in the same {name, size,
 * mtime} shape as a real docket scan: 17 installers, 16 archives, a
 * setup.exe duplicate chain and a report.pdf chain (2 groups, 5 extra
 * copies between them), and one driver from 2021. Seeded draws make
 * the counts, the verdict (Digital Junk Drawer), and the awards
 * identical on every run; dates anchor to nowMs so the span stays
 * recent, except the 2021 file, which is fixed history. */
function makeSampleDownloadsFiles(nowMs) {
  if (nowMs === undefined || nowMs === null) nowMs = Date.now();
  var rand = mulberry32(20261120);
  var anchor = new Date(nowMs);
  anchor.setDate(anchor.getDate() - 2);

  function at(dayOffset, hour) {
    var d = new Date(anchor.getTime());
    d.setDate(d.getDate() - dayOffset);
    d.setHours(hour || 12, Math.floor(rand() * 60), Math.floor(rand() * 60), 0);
    return d.getTime();
  }
  function spread(maxDays) { return Math.floor(rand() * (maxDays + 1)); }

  var files = [];
  var i;
  /* 12 installers, 15-30 MB each. */
  var instExts = [".exe", ".exe", ".dmg", ".msi", ".exe", ".pkg",
                  ".exe", ".deb", ".exe", ".msi", ".exe", ".appimage"];
  for (i = 0; i < 12; i++) {
    files.push({ name: "installer-" + (i + 1) + instExts[i],
                 size: 15000000 + Math.floor(rand() * 15000000),
                 mtime: at(spread(350), 10 + Math.floor(rand() * 10)) });
  }
  /* 16 archives, still zipped. */
  for (i = 0; i < 16; i++) {
    files.push({ name: "backup-" + (i + 1) + ".zip",
                 size: 1000000 + Math.floor(rand() * 4000000),
                 mtime: at(spread(350), 9 + Math.floor(rand() * 12)) });
  }
  /* The setup chain: one group, three extra copies. */
  var setupNames = ["setup.exe", "setup (1).exe", "setup (2).exe", "setup (3).exe"];
  for (i = 0; i < setupNames.length; i++) {
    files.push({ name: setupNames[i], size: 5000000, mtime: at(20 + i * 7, 15) });
  }
  /* The report chain: one group, two extra copies. */
  var reportNames = ["report.pdf", "report (1).pdf", "report copy.pdf"];
  for (i = 0; i < reportNames.length; i++) {
    files.push({ name: reportNames[i], size: 120000, mtime: at(40 + i * 11, 11) });
  }
  /* One file from 2021, still here. (Also an installer.) */
  files.push({ name: "ancient-driver.exe", size: 3000000,
               mtime: new Date(2021, 5, 1, 9, 0).getTime() });
  /* 84 everyday files to round the folder out. */
  var filler = [["document", ".pdf"], ["notes", ".txt"], ["photo", ".jpg"],
                ["video", ".mp4"], ["slides", ".pptx"], ["budget", ".xlsx"],
                ["song", ".mp3"], ["page", ".html"]];
  for (i = 0; i < 84; i++) {
    var kind = filler[Math.floor(rand() * filler.length)];
    files.push({ name: kind[0] + "-" + (i + 1) + kind[1],
                 size: 20000 + Math.floor(rand() * 3000000),
                 mtime: at(spread(350), 8 + Math.floor(rand() * 14)) });
  }
  return files;
}

/* ------------------------------------------------------------------ */
/* The sample defendant (a synthetic folder, judged like any other)    */
/* ------------------------------------------------------------------ */

/* mulberry32: a tiny deterministic PRNG, so the sample folder is the
 * same folder on every machine and every run. */
function mulberry32(seed) {
  var a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    var t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* A synthetic folder of 340 screenshots spanning about 14 months, in
 * the same {name, size, mtime, width, height} shape as a real scan:
 * two dimension pairs, about 40% of shots at night, a 12-shot burst
 * inside one minute, a 12-file duplicate group, and one file dated in
 * the future. Anchored to nowMs (default Date.now()) so the future
 * file stays future and the span stays recent; the seeded draws make
 * the counts, the night share, and the verdict identical every run. */
function makeSampleFiles(nowMs) {
  if (nowMs === undefined || nowMs === null) nowMs = Date.now();
  var rand = mulberry32(20261119);
  var PHONE = { width: 1170, height: 2532 };
  var DESKTOP = { width: 1920, height: 1080 };
  var NIGHT_HOURS = [22, 23, 0, 1, 2, 3, 4];
  var SPAN_DAYS = 426; /* about 14 months */

  var anchor = new Date(nowMs);
  anchor.setDate(anchor.getDate() - 2);

  function at(dayOffset, hour, minute, second) {
    var d = new Date(anchor.getTime());
    d.setDate(d.getDate() - dayOffset);
    d.setHours(hour, minute || 0, second || 0, 0);
    return d;
  }
  function pickHour() {
    if (rand() < 0.40) return NIGHT_HOURS[Math.floor(rand() * NIGHT_HOURS.length)];
    return 6 + Math.floor(rand() * 16); /* 6 AM to 9 PM */
  }
  function pickDims() { return rand() < 0.6 ? PHONE : DESKTOP; }
  function pickSize() { return 90000 + Math.floor(rand() * 2300000); }
  function nameFor(d) {
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    return "Screenshot " + d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" +
      pad(d.getDate()) + " at " + pad(d.getHours()) + "." + pad(d.getMinutes()) +
      "." + pad(d.getSeconds()) + ".png";
  }
  function regular(dayOffset) {
    var d = at(dayOffset, pickHour(), Math.floor(rand() * 60), Math.floor(rand() * 60));
    var dims = pickDims();
    return { name: nameFor(d), size: pickSize(), mtime: d.getTime(),
             width: dims.width, height: dims.height };
  }

  var files = [];
  var i;
  /* 315 regular screenshots spread over the span. */
  for (i = 0; i < 315; i++) {
    files.push(regular(Math.floor(rand() * (SPAN_DAYS + 1))));
  }
  /* A 12-shot burst inside a single minute, one evening. */
  var burstDay = 30 + Math.floor(rand() * 300);
  var burstStart = at(burstDay, 21, 14, 0);
  for (i = 0; i < 12; i++) {
    var bd = new Date(burstStart.getTime() + i * 4000);
    var bdims = pickDims();
    files.push({ name: nameFor(bd), size: pickSize(), mtime: bd.getTime(),
                 width: bdims.width, height: bdims.height });
  }
  /* A 12-file duplicate group: one size, one set of dimensions. */
  for (i = 0; i < 12; i++) {
    var dd = at(Math.floor(rand() * (SPAN_DAYS + 1)), pickHour(),
                Math.floor(rand() * 60), Math.floor(rand() * 60));
    files.push({ name: nameFor(dd), size: 654321, mtime: dd.getTime(),
                 width: PHONE.width, height: PHONE.height });
  }
  /* One screenshot dated in the future. The court admits it anyway. */
  var fd = new Date(anchor.getTime());
  fd.setDate(fd.getDate() + 45);
  fd.setHours(14, 5, 0, 0);
  files.push({ name: nameFor(fd), size: 812345, mtime: fd.getTime(),
               width: PHONE.width, height: PHONE.height });
  return files;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    IMAGE_EXTENSIONS: IMAGE_EXTENSIONS,
    isImageName: isImageName,
    isScreenshotFile: isScreenshotFile,
    parseDimensions: parseDimensions,
    verdictForCount: verdictForCount,
    tierRank: tierRank,
    sentenceFor: sentenceFor,
    JUDGE_NAMES: JUDGE_NAMES,
    humanSize: humanSize,
    fmtHour: fmtHour,
    computeStats: computeStats,
    computePleaBargain: computePleaBargain,
    pleaClosing: pleaClosing,
    intensityBand: intensityBand,
    courtCommentary: courtCommentary,
    INTENSITY_BANDS: INTENSITY_BANDS,
    verdictPostText: verdictPostText,
    makeSampleFiles: makeSampleFiles,
    downloadsVerdictFor: downloadsVerdictFor,
    downloadsSentenceFor: downloadsSentenceFor,
    dlHumanBytes: dlHumanBytes,
    downloadsGroupKey: downloadsGroupKey,
    computeDownloadsStats: computeDownloadsStats,
    downloadsCommentary: downloadsCommentary,
    downloadsPostText: downloadsPostText,
    isTopLevelPath: isTopLevelPath,
    makeSampleDownloadsFiles: makeSampleDownloadsFiles
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
  var DEMO_URL = "https://littlestjames82-sys.github.io/snap-judgment/demo.html";
  var btn = document.getElementById("judgeBtn");
  var input = document.getElementById("dirInput");
  var status = document.getElementById("status");
  var result = document.getElementById("result");
  var noEvidence = document.getElementById("noEvidence");
  var overlay = document.getElementById("dropOverlay");
  var currentStats = null;
  var currentVoice = "fair";
  var currentIntensity = 50;
  var currentSample = false;
  var currentCourt = "screenshots";

  function setStatus(msg) { status.textContent = msg; }

  /* --- the two courts -------------------------------------------------
   * The page copy that differs per court is captured from the initial
   * (screenshots) markup at init, so switching back restores it word
   * for word; only the downloads variants live here as strings. */

  var explainerEl = document.getElementById("explainerLine");
  var defendantEl = document.getElementById("defendantLine");
  var noEvidenceTextEl = document.getElementById("noEvidenceText");
  var cliOnlyEl = document.getElementById("cliOnlyLine");
  var shotsCopy = {
    explainer: explainerEl ? explainerEl.innerHTML : "",
    defendant: defendantEl ? defendantEl.textContent : "",
    noEvidence: noEvidenceTextEl ? noEvidenceTextEl.innerHTML : "",
    cliOnly: cliOnlyEl ? cliOnlyEl.innerHTML : ""
  };
  var DL_COPY = {
    explainer: "This is the live demo of <a href=\"index.html\">Snap Judgment</a>. Pick your " +
      "Downloads folder and the Downloads Docket, the court's second chamber, issues its " +
      "verdict on the spot: a tier, the headline evidence, and a share card you can " +
      "download. No install, no account, no waiting.",
    defendant: "Defendant: Your Downloads Folder",
    noEvidence: "The court finds nothing. No files were produced as evidence, which means " +
      "this folder is innocent, empty, or not where your downloads actually live. Pick the " +
      "folder your downloads really land in, usually just called <b>Downloads</b>, and the " +
      "court will reconvene.",
    cliOnly: "Still CLI-only: the cleanup order, award packs, the court record, and " +
      "co-defendant hearings: <code>pip install snap-judgment</code>, or judge another folder above."
  };

  var courtButtons = document.querySelectorAll(".court-btn");
  function setCourt(court) {
    currentCourt = court;
    currentStats = null;
    currentSample = false;
    result.hidden = true;
    noEvidence.hidden = true;
    setStatus("");
    courtButtons.forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-court") === court);
    });
    var dl = court === "downloads";
    btn.textContent = dl ? "Choose your Downloads folder" : "Choose your screenshots folder";
    var privacyShots = document.getElementById("privacyShots");
    var privacyDl = document.getElementById("privacyDl");
    if (privacyShots && privacyDl) {
      privacyShots.hidden = dl;
      privacyDl.hidden = !dl;
    }
    var noticeShots = document.getElementById("noticeShots");
    var noticeDl = document.getElementById("noticeDl");
    if (noticeShots && noticeDl) {
      noticeShots.hidden = dl;
      noticeDl.hidden = !dl;
    }
    if (explainerEl) explainerEl.innerHTML = dl ? DL_COPY.explainer : shotsCopy.explainer;
    if (defendantEl) defendantEl.textContent = dl ? DL_COPY.defendant : shotsCopy.defendant;
    if (noEvidenceTextEl) noEvidenceTextEl.innerHTML = dl ? DL_COPY.noEvidence : shotsCopy.noEvidence;
    if (cliOnlyEl) cliOnlyEl.innerHTML = dl ? DL_COPY.cliOnly : shotsCopy.cliOnly;
  }
  courtButtons.forEach(function (b) {
    b.addEventListener("click", function () {
      var court = b.getAttribute("data-court") || "screenshots";
      if (court !== currentCourt) setCourt(court);
    });
  });

  btn.addEventListener("click", function () {
    if (typeof window.showDirectoryPicker === "function") {
      var scan = currentCourt === "downloads" ? scanDownloadsWithPicker : scanWithPicker;
      scan().catch(function () { setStatus(""); }); /* cancel: silent */
    } else {
      input.click();
    }
  });

  /* The sample defendant: a synthetic folder through the same
   * computeStats / plea / render path as a real one, for browsers
   * (and moments) where no folder can be picked. */
  var sampleBtn = document.getElementById("sampleBtn");
  if (sampleBtn) {
    sampleBtn.addEventListener("click", function () {
      if (currentCourt === "downloads") {
        var dlStats = computeDownloadsStats(makeSampleDownloadsFiles());
        currentStats = dlStats;
        currentSample = true;
        setStatus("");
        render(dlStats);
        return;
      }
      var records = makeSampleFiles();
      var stats = computeStats(records);
      stats.pleaBargain = computePleaBargain(records, 2);
      currentStats = stats;
      currentSample = true;
      setStatus("");
      render(stats);
    });
  }

  input.addEventListener("change", function () {
    if (!input.files || input.files.length === 0) return;
    var picked = Array.prototype.slice.call(input.files, 0, MAX_SCANNED);
    if (currentCourt === "downloads") {
      /* The docket judges the surface only: files exactly one level
       * below the picked folder, nothing deeper. */
      var dlCandidates = [];
      for (var j = 0; j < picked.length; j++) {
        if (isTopLevelPath(picked[j].webkitRelativePath || "")) {
          dlCandidates.push(picked[j]);
        }
      }
      finishDownloadsScan(dlCandidates);
      input.value = "";
      return;
    }
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

  /* --- drag and drop: drop a folder (or files) anywhere ------------ */

  var dragDepth = 0;
  function showDropState() {
    document.body.classList.add("dropping");
    if (overlay) overlay.hidden = false;
  }
  function hideDropState() {
    document.body.classList.remove("dropping");
    if (overlay) overlay.hidden = true;
  }
  window.addEventListener("dragenter", function (e) {
    e.preventDefault();
    dragDepth++;
    showDropState();
  });
  window.addEventListener("dragover", function (e) {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  });
  window.addEventListener("dragleave", function (e) {
    e.preventDefault();
    dragDepth--;
    if (dragDepth <= 0) { dragDepth = 0; hideDropState(); }
  });
  window.addEventListener("drop", function (e) {
    e.preventDefault();
    dragDepth = 0;
    hideDropState();
    handleDrop(e.dataTransfer).catch(function () { setStatus(""); });
  });

  async function handleDrop(dataTransfer) {
    if (!dataTransfer) return;
    var entries = [];
    if (dataTransfer.items) {
      for (var i = 0; i < dataTransfer.items.length; i++) {
        var item = dataTransfer.items[i];
        if (item.kind === "file" && typeof item.webkitGetAsEntry === "function") {
          var entry = item.webkitGetAsEntry();
          if (entry) entries.push(entry);
        }
      }
    }
    if (entries.length === 0) {
      /* Fallback: the flat file list, no folder names available. A
       * flat list is all surface, so the docket takes every file. */
      if (dataTransfer.files && dataTransfer.files.length > 0) {
        var picked = Array.prototype.slice.call(dataTransfer.files, 0, MAX_SCANNED);
        if (currentCourt === "downloads") {
          await finishDownloadsScan(picked);
          return;
        }
        var flat = [];
        for (var p = 0; p < picked.length; p++) {
          if (isScreenshotFile(picked[p].name, "")) flat.push(picked[p]);
        }
        await finishScan(flat);
      }
      return;
    }
    if (currentCourt === "downloads") {
      /* The docket reads the surface of a dropped folder: its own
       * files, and nothing from any subfolder. */
      var dlCandidates = [];
      var dlScanned = 0;
      async function traverseDownloads(entry) {
        if (dlScanned >= MAX_SCANNED) return;
        if (entry.isFile) {
          dlScanned++;
          var file = await new Promise(function (resolve, reject) {
            entry.file(resolve, reject);
          });
          dlCandidates.push(file);
        } else if (entry.isDirectory) {
          var reader = entry.createReader();
          var children;
          do {
            children = await new Promise(function (resolve, reject) {
              reader.readEntries(resolve, reject);
            });
            for (var ch = 0; ch < children.length; ch++) {
              if (children[ch].isFile && dlScanned < MAX_SCANNED) {
                dlScanned++;
                var childFile = await new Promise(function (resolve, reject) {
                  children[ch].file(resolve, reject);
                });
                dlCandidates.push(childFile);
              }
            }
          } while (children.length > 0);
        }
      }
      setStatus("Reading the evidence...");
      for (var de = 0; de < entries.length; de++) {
        await traverseDownloads(entries[de]);
      }
      await finishDownloadsScan(dlCandidates);
      return;
    }
    var candidates = [];
    var scanned = 0;
    async function traverse(entry, parentName, depth) {
      if (scanned >= MAX_SCANNED) return;
      if (entry.isFile) {
        scanned++;
        var file = await new Promise(function (resolve, reject) {
          entry.file(resolve, reject);
        });
        if (isScreenshotFile(file.name, parentName)) candidates.push(file);
      } else if (entry.isDirectory) {
        if (depth > 1) return;
        var reader = entry.createReader();
        var children;
        do {
          children = await new Promise(function (resolve, reject) {
            reader.readEntries(resolve, reject);
          });
          for (var ch = 0; ch < children.length; ch++) {
            await traverse(children[ch], entry.name, depth + 1);
          }
        } while (children.length > 0);
      }
    }
    setStatus("Reading the evidence...");
    for (var e = 0; e < entries.length; e++) {
      await traverse(entries[e], "", 0);
    }
    await finishScan(candidates);
  }

  /* --- actions ------------------------------------------------------ */

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

  function copyVerdict() {
    if (!currentStats) return;
    var text = verdictPostText(currentStats);
    var done = function () { setStatus("Verdict copied. Post it anywhere."); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else {
      fallbackCopy(text); done();
    }
  }

  document.getElementById("copyVerdict").addEventListener("click", copyVerdict);

  document.getElementById("shareVerdict").addEventListener("click", function () {
    if (!currentStats) return;
    if (typeof navigator.share === "function") {
      navigator.share({
        title: "Snap Judgment",
        text: verdictPostText(currentStats),
        url: DEMO_URL
      }).catch(function () { /* user dismissed the share sheet */ });
    } else {
      copyVerdict();
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

  /* --- the presiding judge ------------------------------------------ */

  var judgeButtons = document.querySelectorAll(".judge-btn[data-voice]");
  judgeButtons.forEach(function (b) {
    b.addEventListener("click", function () {
      currentVoice = b.getAttribute("data-voice") || "fair";
      judgeButtons.forEach(function (other) {
        other.classList.toggle("active", other === b);
      });
      if (currentStats && currentStats.count > 0) {
        renderSentence(currentStats);
        drawCurrentCard(currentStats);
      }
    });
  });

  function renderSentence(stats) {
    var el = document.getElementById("sentenceLine");
    el.textContent = stats.court === "downloads"
      ? downloadsSentenceFor(stats.tier, currentVoice)
      : sentenceFor(stats.tier, currentVoice);
    var judgeEl = document.getElementById("judgeName");
    if (judgeEl) judgeEl.textContent = JUDGE_NAMES[currentVoice] + " presiding.";
  }

  /* --- roast intensity: wording only, never the math ----------------- */

  var roastRange = document.getElementById("roastRange");
  var roastBandEl = document.getElementById("roastBand");
  function syncRoastBand() {
    if (roastBandEl) roastBandEl.textContent = intensityBand(currentIntensity).name;
  }
  if (roastRange) {
    roastRange.addEventListener("input", function () {
      currentIntensity = Number(roastRange.value);
      syncRoastBand();
      if (currentStats && currentStats.count > 0) renderCommentary(currentStats);
    });
  }
  syncRoastBand();

  function renderCommentary(stats) {
    var box = document.getElementById("commentaryBox");
    var line = document.getElementById("commentaryLine");
    if (!box || !line) return;
    line.textContent = stats.court === "downloads"
      ? downloadsCommentary(stats, intensityBand(currentIntensity).level)
      : courtCommentary(stats, intensityBand(currentIntensity).level);
    box.hidden = false;
  }

  /* --- scanning ------------------------------------------------------- */

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
    stats.pleaBargain = computePleaBargain(records, 2);
    currentStats = stats;
    currentSample = false;
    setStatus("");
    render(stats);
  }

  /* The docket's picker scan: the picked folder's own files only.
   * Subfolders are not entered, and nothing is read from any file:
   * the File objects' name, size, and lastModified are the evidence. */
  async function scanDownloadsWithPicker() {
    var dir = await window.showDirectoryPicker();
    var candidates = [];
    var scanned = 0;
    setStatus("Reading the evidence...");
    for await (var entry of dir.values()) {
      if (scanned >= MAX_SCANNED) break;
      if (entry.kind === "file") {
        scanned++;
        candidates.push(await entry.getFile());
      }
    }
    await finishDownloadsScan(candidates);
  }

  async function finishDownloadsScan(candidateFiles) {
    setStatus("Reading the evidence: " + candidateFiles.length + " files found...");
    var records = candidateFiles.map(function (f) {
      return { name: f.name, size: f.size, mtime: f.lastModified };
    });
    var stats = computeDownloadsStats(records);
    currentStats = stats;
    currentSample = false;
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

  /* --- rendering ------------------------------------------------------ */

  function render(stats) {
    if (stats && stats.court === "downloads") {
      renderDownloads(stats);
      return;
    }
    if (stats.count === 0) {
      result.hidden = true;
      noEvidence.hidden = false;
      noEvidence.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    noEvidence.hidden = true;
    var banner = document.getElementById("sampleBanner");
    if (banner) banner.hidden = !currentSample;
    document.getElementById("verdictTier").textContent = stats.tier;
    document.getElementById("verdictSummary").textContent =
      stats.count + " screenshots, " + stats.totalSizeHuman + " of evidence, spanning " +
      stats.spanStartLabel + " to " + stats.spanEndLabel + ".";
    renderSentence(stats);
    renderCommentary(stats);

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

    renderExhibits(stats);
    renderHeatmap(stats);
    renderAwards(stats);
    renderPlea(stats);

    var xLink = document.getElementById("postToX");
    if (xLink) {
      xLink.href = "https://twitter.com/intent/tweet?text=" +
        encodeURIComponent(verdictPostText(stats));
    }

    drawCard(stats);
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* The docket's hearing page: the same layout as the screenshots
   * court, with the CLI's downloads evidence table. Exhibits, the
   * heatmap, and the plea bargain are screenshots-court business, so
   * those boxes are emptied here. */
  function renderDownloads(stats) {
    if (stats.count === 0) {
      result.hidden = true;
      noEvidence.hidden = false;
      noEvidence.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    noEvidence.hidden = true;
    var banner = document.getElementById("sampleBanner");
    if (banner) banner.hidden = !currentSample;
    document.getElementById("verdictTier").textContent = stats.tier;
    document.getElementById("verdictSummary").textContent =
      stats.count.toLocaleString("en-US") + " files, " + stats.totalSizeHuman +
      " of evidence, spanning " + stats.spanStartLabel + " to " + stats.spanEndLabel + ".";
    renderSentence(stats);
    renderCommentary(stats);

    var rows = [
      ["Total files", stats.count.toLocaleString("en-US")],
      ["Total size", stats.totalSizeHuman + " (" + stats.totalBytes.toLocaleString("en-US") + " bytes)"],
      ["Largest file", stats.largestName + " (" + stats.largestHuman + ")"],
      ["Date span", stats.spanStartLabel + " - " + stats.spanEndLabel + " (" +
        (stats.spanDays + 1).toLocaleString("en-US") + (stats.spanDays === 0 ? " day" : " days") + ")"],
      ["Oldest file", stats.oldestName + " (" + stats.oldestLabel + ")"],
      ["Installers", stats.installerCount + " (" + stats.installerHuman + ")"],
      ["Archives", String(stats.archiveCount)],
      ["Duplicate downloads", stats.duplicateGroups +
        (stats.duplicateGroups === 1 ? " group, " : " groups, ") +
        stats.duplicateExtra + " extra copies"]
    ];
    if (stats.duplicateExtraBytes > 0) {
      rows.push(["Duplicate waste", stats.duplicateWasteHuman]);
    }
    rows.push(["Over a year old", stats.oldCount + " (" + stats.oldShare.toFixed(1) + "%)"]);
    if (stats.extensions.length > 0) {
      rows.push(["Top file types", stats.extensions.slice(0, 5).map(function (e) {
        return e.ext + " " + e.count;
      }).join(", ")]);
    }

    var tbody = document.getElementById("evidenceBody");
    tbody.innerHTML = "";
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      var th = document.createElement("th"); th.textContent = r[0];
      var td = document.createElement("td"); td.textContent = r[1];
      tr.appendChild(th); tr.appendChild(td);
      tbody.appendChild(tr);
    });

    document.getElementById("exhibitsBox").innerHTML = "";
    document.getElementById("heatmapBox").innerHTML = "";
    document.getElementById("pleaBox").innerHTML = "";
    renderAwards(stats);

    var xLink = document.getElementById("postToX");
    if (xLink) {
      xLink.href = "https://twitter.com/intent/tweet?text=" +
        encodeURIComponent(verdictPostText(stats));
    }

    drawDownloadsCard(stats);
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderExhibits(stats) {
    var box = document.getElementById("exhibitsBox");
    box.innerHTML = "";
    if (stats.count < 5 || stats.exhibits.length === 0) return;
    var h = document.createElement("h3");
    h.textContent = "Exhibits";
    box.appendChild(h);
    var letters = "ABCDE";
    stats.exhibits.forEach(function (ex, idx) {
      var p = document.createElement("p");
      p.textContent = "Exhibit " + letters[idx] + " - " + ex.name + " (" + ex.label + ")";
      box.appendChild(p);
    });
  }

  function renderHeatmap(stats) {
    var box = document.getElementById("heatmapBox");
    box.innerHTML = "";
    var max = 0, r, c;
    for (r = 0; r < 7; r++) {
      for (c = 0; c < 24; c++) {
        if (stats.heatmap[r][c] > max) max = stats.heatmap[r][c];
      }
    }
    if (max === 0) return;
    var h = document.createElement("h3");
    h.textContent = "The Heatmap";
    box.appendChild(h);
    var note = document.createElement("p");
    note.className = "heatmap-note";
    note.textContent = "Screenshots by weekday and hour. Hover a cell for its count.";
    box.appendChild(note);
    var table = document.createElement("table");
    table.className = "heatmap";
    var head = document.createElement("tr");
    head.appendChild(document.createElement("th"));
    for (c = 0; c < 24; c++) {
      var th = document.createElement("th");
      th.textContent = c;
      head.appendChild(th);
    }
    table.appendChild(head);
    var dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    for (r = 0; r < 7; r++) {
      var tr = document.createElement("tr");
      var label = document.createElement("th");
      label.textContent = dayNames[r];
      tr.appendChild(label);
      for (c = 0; c < 24; c++) {
        var n = stats.heatmap[r][c];
        var td = document.createElement("td");
        var opacity = n === 0 ? 0.04 : 0.15 + 0.85 * (n / max);
        td.style.background = "rgba(176, 141, 60, " + opacity.toFixed(2) + ")";
        td.title = n + (n === 1 ? " screenshot" : " screenshots");
        tr.appendChild(td);
      }
      table.appendChild(tr);
    }
    box.appendChild(table);
  }

  function renderAwards(stats) {
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
  }

  function renderPlea(stats) {
    var box = document.getElementById("pleaBox");
    box.innerHTML = "";
    var plea = stats.pleaBargain;
    if (!plea) return;
    var h = document.createElement("h3");
    h.textContent = "The Plea Bargain";
    box.appendChild(h);
    var intro = document.createElement("p");
    intro.textContent = "If the defendant deleted " + plea.oldRemoved.toLocaleString("en-US") +
      " screenshots older than " + plea.years + (plea.years === 1 ? " year" : " years") + " and " +
      plea.duplicateRemoved.toLocaleString("en-US") +
      " duplicate extras - a purely hypothetical exercise; this court deletes nothing - then:";
    box.appendChild(intro);
    var table = document.createElement("table");
    table.className = "evidence";
    var tbody = document.createElement("tbody");
    var rows = [
      ["Files removed", plea.removedCount.toLocaleString("en-US")],
      ["Space reclaimed", plea.removedHuman + " (" + plea.removedBytes.toLocaleString("en-US") + " bytes)"],
      ["Remaining evidence", plea.remainingCount.toLocaleString("en-US") + " screenshots"],
      ["Projected verdict", plea.projectedTier]
    ];
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      var th = document.createElement("th"); th.textContent = r[0];
      var td = document.createElement("td"); td.textContent = r[1];
      tr.appendChild(th); tr.appendChild(td);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);
    var closing = document.createElement("p");
    closing.className = "plea-closing";
    closing.textContent = pleaClosing(plea, stats.tier);
    box.appendChild(closing);
  }

  function drawCurrentCard(stats) {
    if (stats.court === "downloads") drawDownloadsCard(stats);
    else drawCard(stats);
  }

  /* The docket's share card: the screenshots card's layout, with the
   * docket's header, its file evidence, up to two award names, and
   * its own footer line. */
  function drawDownloadsCard(stats) {
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
    ctx.fillText("THE DOWNLOADS DOCKET", W / 2, 92);

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
    ctx.fillText(stats.count.toLocaleString("en-US") + " files - " + stats.totalSizeHuman +
      " - " + monthYearLabel(stats.spanStart) + " - " + monthYearLabel(stats.spanEnd), W / 2, 408);

    ctx.fillStyle = "#a49fb2";
    ctx.font = "30px Arial, Helvetica, sans-serif";
    ctx.fillText(stats.installerCount + " installers - " + stats.archiveCount +
      " archives - " + stats.duplicateExtra + " duplicate extras", W / 2, 458);

    if (stats.awards.length > 0) {
      ctx.fillStyle = "#e8e3d8";
      ctx.font = "25px Arial, Helvetica, sans-serif";
      ctx.fillText("Awards: " + stats.awards.slice(0, 2).map(function (a) { return a.name; }).join(", "), W / 2, 500);
    }

    /* The sentence, in the presiding judge's voice. */
    var sentence = downloadsSentenceFor(stats.tier, currentVoice);
    if (sentence) {
      ctx.fillStyle = "#cfc9ba";
      var sSize = 24;
      ctx.font = "italic " + sSize + "px Georgia, 'Times New Roman', serif";
      while (ctx.measureText(sentence).width > W - 120 && sSize > 14) {
        sSize -= 2;
        ctx.font = "italic " + sSize + "px Georgia, 'Times New Roman', serif";
      }
      var shown = sentence;
      while (ctx.measureText(shown).width > W - 120 && shown.length > 4) {
        shown = shown.slice(0, -2);
      }
      if (shown !== sentence) shown = shown.replace(/[\s,.;:]+$/, "") + "…";
      ctx.fillText(shown, W / 2, 540);
    }

    ctx.fillStyle = "#8f8b7d";
    ctx.font = "23px Arial, Helvetica, sans-serif";
    ctx.fillText("Judged from file names, sizes, and dates only. No file was opened.", W / 2, 574);
    ctx.fillStyle = "#d4af37";
    ctx.font = "600 23px Menlo, Consolas, monospace";
    ctx.fillText("pip install snap-judgment", W / 2, 602);
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
    ctx.fillText("Night share " + stats.nightShare.toFixed(1) + "%", W / 2, 458);

    if (stats.awards.length > 0) {
      ctx.fillStyle = "#e8e3d8";
      ctx.font = "25px Arial, Helvetica, sans-serif";
      ctx.fillText("Awards: " + stats.awards.slice(0, 3).map(function (a) { return a.name; }).join(", "), W / 2, 500);
    }

    /* The sentence, in the presiding judge's voice. */
    var sentence = sentenceFor(stats.tier, currentVoice);
    if (sentence) {
      ctx.fillStyle = "#cfc9ba";
      var sSize = 24;
      ctx.font = "italic " + sSize + "px Georgia, 'Times New Roman', serif";
      while (ctx.measureText(sentence).width > W - 120 && sSize > 14) {
        sSize -= 2;
        ctx.font = "italic " + sSize + "px Georgia, 'Times New Roman', serif";
      }
      var shown = sentence;
      while (ctx.measureText(shown).width > W - 120 && shown.length > 4) {
        shown = shown.slice(0, -2);
      }
      if (shown !== sentence) shown = shown.replace(/[\s,.;:]+$/, "") + "…";
      ctx.fillText(shown, W / 2, 540);
    }

    ctx.fillStyle = "#8f8b7d";
    ctx.font = "23px Arial, Helvetica, sans-serif";
    ctx.fillText("Judged from file metadata only. No pixels were read.", W / 2, 574);
    ctx.fillStyle = "#d4af37";
    ctx.font = "600 23px Menlo, Consolas, monospace";
    ctx.fillText("pip install snap-judgment", W / 2, 602);
  }
}
