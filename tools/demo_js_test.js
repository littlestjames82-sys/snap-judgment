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
  for (let i = 0; i < 6; i++) {
    files.push(shot({ size: 424242, width: 100, height: 200, mtime: ts(2026, 3, 1 + i, 9) }));
  }
  /* Same size, different dimensions: not suspects. */
  files.push(shot({ size: 424242, width: 300, height: 200, mtime: ts(2026, 3, 9, 9) }));
  /* Same size, unknown dimensions: not suspects (dims must be known). */
  files.push(shot({ size: 424242, width: null, height: null, mtime: ts(2026, 3, 10, 9) }));
  const s = demo.computeStats(files);
  assert.strictEqual(s.duplicateGroups, 1);
  assert.strictEqual(s.duplicateSuspects, 5);
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

console.log("\n" + passed + " test groups passed.");
