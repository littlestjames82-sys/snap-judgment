/* Node tests for the browser demo's pure stats core (docs/demo.js).
 * Run: node tools/demo_js_test.js
 * Builds synthetic file-object arrays ({name, size, mtime, width, height})
 * and asserts the verdict math matches the Python tool's rules. */
"use strict";

const assert = require("assert");
const path = require("path");
const demo = require(path.join(__dirname, "..", "docs", "demo.js"));

let passed = 0;
function check(name, fn) {
  fn();
  passed++;
  console.log("ok - " + name);
}

/* Local-time timestamp builder: Jan = month 0. */
function ts(year, month, day, hour, minute) {
  return new Date(year, month, day, hour || 12, minute || 0).getTime();
}

let seq = 0;
function shot(overrides) {
  seq++;
  const base = {
    name: "Screenshot " + seq + ".png",
    size: 50000 + seq * 137, /* distinct sizes unless overridden */
    mtime: ts(2026, 0, 5, 12),
    width: 1170,
    height: 2532
  };
  return Object.assign(base, overrides || {});
}

function shots(n, overrides) {
  const out = [];
  for (let i = 0; i < n; i++) out.push(shot(overrides));
  return out;
}

/* --- tier boundaries ---------------------------------------------- */

check("verdict thresholds match the Python tool exactly", () => {
  assert.strictEqual(demo.verdictForCount(0), "No Evidence");
  assert.strictEqual(demo.verdictForCount(1), "Innocent Bystander");
  assert.strictEqual(demo.verdictForCount(49), "Innocent Bystander");
  assert.strictEqual(demo.verdictForCount(50), "Petty Offender");
  assert.strictEqual(demo.verdictForCount(199), "Petty Offender");
  assert.strictEqual(demo.verdictForCount(200), "Repeat Offender");
  assert.strictEqual(demo.verdictForCount(499), "Repeat Offender");
  assert.strictEqual(demo.verdictForCount(500), "Habitual Hoarder");
  assert.strictEqual(demo.verdictForCount(999), "Habitual Hoarder");
  assert.strictEqual(demo.verdictForCount(1000), "Felony-Level Archivist");
  assert.strictEqual(demo.verdictForCount(2499), "Felony-Level Archivist");
  assert.strictEqual(demo.verdictForCount(2500), "Unrepentant Screenshot Felon");
});

check("49 vs 50 vs 2500 files through computeStats", () => {
  assert.strictEqual(demo.computeStats(shots(49)).tier, "Innocent Bystander");
  assert.strictEqual(demo.computeStats(shots(50)).tier, "Petty Offender");
  assert.strictEqual(demo.computeStats(shots(2500)).tier, "Unrepentant Screenshot Felon");
  assert.strictEqual(demo.computeStats([]).tier, "No Evidence");
});

/* --- night share ---------------------------------------------------- */

check("night share math (22:00-05:00 local)", () => {
  const files = [];
  for (let i = 0; i < 5; i++) files.push(shot({ mtime: ts(2026, 0, 5 + i, 23) })); /* night */
  files.push(shot({ mtime: ts(2026, 0, 10, 4) }));  /* night, before 5 AM */
  files.push(shot({ mtime: ts(2026, 0, 11, 5) }));  /* 5 AM sharp: not night */
  for (let i = 0; i < 3; i++) files.push(shot({ mtime: ts(2026, 0, 12 + i, 12) })); /* midday */
  const s = demo.computeStats(files);
  assert.strictEqual(s.count, 10);
  assert.strictEqual(s.nightCount, 6);
  assert.strictEqual(s.nightShare, 60);
});

/* --- streak --------------------------------------------------------- */

check("longest daily streak math", () => {
  const files = [];
  for (let d = 1; d <= 7; d++) files.push(shot({ mtime: ts(2026, 2, d, 10) })); /* Mar 1-7 */
  files.push(shot({ mtime: ts(2026, 2, 7, 18) }));  /* same day, no streak change */
  files.push(shot({ mtime: ts(2026, 2, 20, 10) })); /* isolated */
  files.push(shot({ mtime: ts(2026, 2, 21, 10) })); /* run of 2 */
  const s = demo.computeStats(files);
  assert.strictEqual(s.longestStreak, 7);
  assert.ok(s.awards.some(a => a.name === "Screenshot Streak"));
  assert.strictEqual(s.busiestDay.count, 2);
});

/* --- duplicates ------------------------------------------------------- */

check("duplicate suspects count extra files only, dims required", () => {
  const files = [];
  /* Ten identical files: the Python Repeat Customer threshold counts all
   * files in duplicate groups (>= 10), so the fixture needs ten. */
  for (let i = 0; i < 10; i++) {
    files.push(shot({ size: 424242, width: 100, height: 200, mtime: ts(2026, 3, 1 + i, 9) }));
  }
  /* Same size, different dimensions: not suspects. */
  files.push(shot({ size: 424242, width: 300, height: 200, mtime: ts(2026, 3, 11, 9) }));
  /* Same size, unknown dimensions: not suspects (dims must be known). */
  files.push(shot({ size: 424242, width: null, height: null, mtime: ts(2026, 3, 12, 9) }));
  const s = demo.computeStats(files);
  assert.strictEqual(s.duplicateGroups, 1);
  assert.strictEqual(s.duplicateSuspects, 9);   /* extras only */
  assert.strictEqual(s.duplicateFiles, 10);    /* every file in the group */
  assert.ok(s.awards.some(a => a.name === "Repeat Customer"));
});

/* --- a mixed set ------------------------------------------------------- */

check("mixed set produces the expected verdict and stats", () => {
  const files = [];
  /* 60 screenshots across Jan-Mar 2026, half at 11 PM. */
  for (let i = 0; i < 60; i++) {
    files.push(shot({
      mtime: ts(2026, i % 3, 1 + (i % 27), i % 2 === 0 ? 23 : 13),
      size: 100000
    }));
  }
  const s = demo.computeStats(files);
  assert.strictEqual(s.count, 60);
  assert.strictEqual(s.tier, "Petty Offender");
  assert.strictEqual(s.nightShare, 50);
  assert.strictEqual(s.totalBytes, 60 * 100000);
  assert.strictEqual(s.perYear[2026], 60);
  assert.ok(s.awards.some(a => a.name === "Night Owl"));
  const post = demo.verdictPostText(s);
  assert.ok(post.includes("60 screenshots, verdict Petty Offender, 50.0% taken at night"));
  assert.ok(post.includes("https://littlestjames82-sys.github.io/snap-judgment/demo.html"));
});

/* --- detection --------------------------------------------------------- */

check("screenshot detection rules", () => {
  assert.strictEqual(demo.isScreenshotFile("Screenshot 2026-01-01 at 10.00.00.png", "Desktop"), true);
  assert.strictEqual(demo.isScreenshotFile("Screenshot_20260101-100000.png", "DCIM"), true);
  assert.strictEqual(demo.isScreenshotFile("Screen Shot 2026-01-01.png", "Desktop"), true);
  assert.strictEqual(demo.isScreenshotFile("CleanShot 2026-01-01.png", "Desktop"), true);
  assert.strictEqual(demo.isScreenshotFile("Bildschirmfoto 2026-01-01.png", "Desktop"), true);
  assert.strictEqual(demo.isScreenshotFile("IMG_1234.jpg", "Screenshots"), true);   /* parent folder rule */
  assert.strictEqual(demo.isScreenshotFile("IMG_1234.jpg", "Camera"), false);
  assert.strictEqual(demo.isScreenshotFile("notes.txt", "Screenshots"), false);     /* not an image */
  assert.strictEqual(demo.isScreenshotFile("IMG_1234.heic", "Screenshots"), true);
});

/* --- header parsing ----------------------------------------------------- */

check("PNG header dimensions parse, junk returns null", () => {
  const png = new Uint8Array(24);
  png.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 0);
  png.set([0, 0, 0, 13], 8);
  png.set([0x49, 0x48, 0x44, 0x52], 12); /* IHDR */
  png[16] = 0; png[17] = 0; png[18] = 0x04; png[19] = 0x92; /* 1170 */
  png[20] = 0; png[21] = 0; png[22] = 0x09; png[23] = 0xe4; /* 2532 */
  const dims = demo.parseDimensions(png);
  assert.strictEqual(dims.width, 1170);
  assert.strictEqual(dims.height, 2532);
  assert.strictEqual(demo.parseDimensions(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])), null);
  assert.strictEqual(demo.parseDimensions(new Uint8Array(0)), null);
});

/* --- plea bargain ----------------------------------------------------- */

check("plea bargain math: old files, duplicate extras, projected tier", () => {
  const files = [];
  /* 30 recent files, distinct sizes, no duplicates. */
  for (let i = 0; i < 30; i++) {
    files.push(shot({ size: 2000 + i, mtime: ts(2026, 4, 1, 12) }));
  }
  /* 10 old files (Jan 2023), 100 bytes each, no dimensions. */
  for (let i = 0; i < 10; i++) {
    files.push(shot({ size: 100, width: null, height: null, mtime: ts(2023, 0, 15, 12) }));
  }
  /* A 12-file duplicate group on the newest day (May 2, 2026). */
  for (let i = 0; i < 12; i++) {
    files.push(shot({ size: 777, width: 100, height: 100, mtime: ts(2026, 4, 2, 12) }));
  }
  const plea = demo.computePleaBargain(files, 2);
  assert.strictEqual(plea.years, 2);
  assert.strictEqual(plea.oldRemoved, 10);
  assert.strictEqual(plea.duplicateRemoved, 11);
  assert.strictEqual(plea.removedCount, 21);
  assert.strictEqual(plea.removedBytes, 10 * 100 + 11 * 777);
  assert.strictEqual(plea.removedHuman, "9.3 KB");
  assert.strictEqual(plea.remainingCount, 31);
  assert.strictEqual(plea.projectedTier, "Innocent Bystander");
  /* 52 files is a Petty Offender; the plea improves the tier. */
  assert.strictEqual(demo.computeStats(files).tier, "Petty Offender");
  assert.strictEqual(demo.pleaClosing(plea, "Petty Offender"),
    "The court would accept this plea.");
});

check("plea bargain: an old duplicate keeper counts once, as old", () => {
  const files = [
    shot({ size: 555, width: 50, height: 50, mtime: ts(2023, 0, 15, 12) }),
    shot({ size: 555, width: 50, height: 50, mtime: ts(2026, 4, 2, 12) }),
    shot({ size: 555, width: 50, height: 50, mtime: ts(2026, 4, 2, 13) })
  ];
  const plea = demo.computePleaBargain(files, 2);
  assert.strictEqual(plea.oldRemoved, 1);
  assert.strictEqual(plea.duplicateRemoved, 2);
  assert.strictEqual(plea.removedBytes, 3 * 555);
  assert.strictEqual(plea.remainingCount, 0);
  assert.strictEqual(plea.projectedTier, "No Evidence");
});

check("plea bargain: nothing to remove, the verdict stands", () => {
  const files = [];
  for (let i = 0; i < 10; i++) {
    files.push(shot({ size: 3000 + i, width: null, height: null, mtime: ts(2026, 5, 1, 12) }));
  }
  const plea = demo.computePleaBargain(files, 2);
  assert.strictEqual(plea.removedCount, 0);
  assert.strictEqual(plea.remainingCount, 10);
  assert.strictEqual(plea.projectedTier, "Innocent Bystander");
  assert.strictEqual(demo.pleaClosing(plea, "Innocent Bystander"),
    "The court notes this plea changes nothing. The verdict stands.");
});

/* --- newly ported awards ------------------------------------------------ */

check("burst mode award fires at the Python threshold (8 in 60 seconds)", () => {
  const base = ts(2026, 0, 5, 12, 0);
  const files = [];
  for (let i = 0; i < 8; i++) {
    files.push(shot({ mtime: base + i * 5000, width: null, height: null }));
  }
  const s = demo.computeStats(files);
  assert.strictEqual(s.burstRecord, 8);
  const award = s.awards.find(a => a.name === "Burst Mode");
  assert.ok(award);
  assert.strictEqual(award.detail, "8 screenshots inside a single 60-second window.");
  const seven = [];
  for (let i = 0; i < 7; i++) {
    seven.push(shot({ mtime: base + i * 5000, width: null, height: null }));
  }
  assert.ok(!demo.computeStats(seven).awards.some(a => a.name === "Burst Mode"));
});

check("the 3 AM club award fires at 10 shots between 2 AM and 4 AM", () => {
  const files = [];
  for (let i = 0; i < 5; i++) files.push(shot({ mtime: ts(2026, 0, 5 + i, 2, 30) }));
  for (let i = 0; i < 5; i++) files.push(shot({ mtime: ts(2026, 0, 12 + i, 3, 15) }));
  const s = demo.computeStats(files);
  assert.strictEqual(s.threeAmCount, 10);
  const award = s.awards.find(a => a.name === "The 3 AM Club");
  assert.ok(award);
  assert.ok(award.detail.startsWith("10 screenshots taken between 2 AM and 4 AM"));
});

check("time traveler award fires for a future-dated screenshot", () => {
  const files = [];
  for (let i = 0; i < 5; i++) files.push(shot({ mtime: ts(2026, 0, 5 + i, 12) }));
  files.push(shot({ name: "screenshot_future.png", mtime: ts(2100, 0, 1, 12) }));
  const s = demo.computeStats(files);
  assert.strictEqual(s.futureShot.name, "screenshot_future.png");
  const award = s.awards.find(a => a.name === "Time Traveler");
  assert.ok(award);
  assert.ok(award.detail.includes("screenshot_future.png is dated Jan 1, 2100"));
});

check("creature of habit award fires at 25% in a single hour", () => {
  const files = [];
  for (let i = 0; i < 12; i++) files.push(shot({ mtime: ts(2026, 0, 5 + i, 21, 5) }));
  for (let i = 0; i < 28; i++) files.push(shot({ mtime: ts(2026, 1, 2 + i, 8 + (i % 13), 40) }));
  const s = demo.computeStats(files);
  assert.strictEqual(s.busiestHour, 21);
  assert.strictEqual(s.busiestHourCount, 12);
  assert.strictEqual(s.habitShare, 30);
  const award = s.awards.find(a => a.name === "Creature of Habit");
  assert.ok(award);
  assert.ok(award.detail.includes("30.0% of all screenshots were taken in the 9 PM hour"));
});

/* --- exhibits ----------------------------------------------------------- */

check("exhibits are the five oldest, mtime ties broken by filename", () => {
  const files = [
    shot({ name: "g.png", mtime: ts(2024, 0, 7, 10) }),
    shot({ name: "b_tie.png", mtime: ts(2024, 0, 1, 10) }),
    shot({ name: "e.png", mtime: ts(2024, 0, 5, 10) }),
    shot({ name: "a_tie.png", mtime: ts(2024, 0, 1, 10) }),
    shot({ name: "d.png", mtime: ts(2024, 0, 4, 10) }),
    shot({ name: "f.png", mtime: ts(2024, 0, 6, 10) }),
    shot({ name: "c.png", mtime: ts(2024, 0, 3, 10) })
  ];
  const s = demo.computeStats(files);
  assert.deepStrictEqual(s.exhibits.map(e => e.name),
    ["a_tie.png", "b_tie.png", "c.png", "d.png", "e.png"]);
  assert.strictEqual(s.exhibits[0].label, "Jan 1, 2024");
});

/* --- heatmap -------------------------------------------------------------- */

check("heatmap cells land on the right weekday and hour (Monday first)", () => {
  const files = [
    shot({ mtime: ts(2026, 10, 16, 23) }),  /* Monday Nov 16, 2026 */
    shot({ mtime: ts(2026, 10, 16, 23) }),
    shot({ mtime: ts(2026, 10, 22, 3) })    /* Sunday Nov 22, 2026 */
  ];
  const s = demo.computeStats(files);
  assert.strictEqual(s.heatmap[0][23], 2);  /* Monday row, 11 PM */
  assert.strictEqual(s.heatmap[6][3], 1);   /* Sunday row, 3 AM */
  let sum = 0;
  s.heatmap.forEach(row => row.forEach(n => { sum += n; }));
  assert.strictEqual(sum, 3);
});

/* --- judge voices ----------------------------------------------------------- */

check("judge sentences port the Python voices exactly", () => {
  assert.strictEqual(demo.sentenceFor("Repeat Offender", "fair"),
    "Sentence: probation. The screenshots folder gets a check-in once a month.");
  assert.strictEqual(demo.sentenceFor("Repeat Offender", "strict"),
    "Sentence: weekly inspections of the screenshots folder until this court says otherwise.");
  assert.strictEqual(demo.sentenceFor("Petty Offender", "lenient"),
    "Encouragement: barely a habit at all. Keep doing what you are doing.");
  assert.strictEqual(demo.sentenceFor("Innocent Bystander", "lenient"),
    "Encouragement: case dismissed, with applause. Screenshot this verdict; you have earned one.");
  assert.strictEqual(demo.sentenceFor("No Evidence", "strict"), "");
  assert.strictEqual(demo.tierRank("No Evidence"), -1);
  assert.strictEqual(demo.tierRank("Repeat Offender"), 2);
});

console.log("\n" + passed + " test groups passed.");
