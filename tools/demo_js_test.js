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

/* --- the sample defendant ------------------------------------------------- */

check("sample dataset: 340 synthetic screenshots, deterministic, Repeat Offender", () => {
  const files = demo.makeSampleFiles();
  assert.strictEqual(files.length, 340);
  files.forEach(f => {
    assert.strictEqual(typeof f.name, "string");
    assert.strictEqual(typeof f.size, "number");
    assert.strictEqual(typeof f.mtime, "number");
    assert.strictEqual(typeof f.width, "number");
    assert.strictEqual(typeof f.height, "number");
  });
  /* Two dimension pairs only: phone and desktop. */
  const pairs = new Set(files.map(f => f.width + "x" + f.height));
  assert.deepStrictEqual([...pairs].sort(), ["1170x2532", "1920x1080"]);
  /* Same seed, same folder, byte for byte at a fixed clock. */
  const a = demo.makeSampleFiles(1760000000000);
  const b = demo.makeSampleFiles(1760000000000);
  assert.deepStrictEqual(a, b);
  /* The verdict lands in the expected band, night-heavy by design. */
  const s = demo.computeStats(files);
  assert.strictEqual(s.tier, "Repeat Offender");
  assert.ok(s.nightShare >= 30, "sample night share is " + s.nightShare);
  assert.ok(s.awards.some(x => x.name === "Night Owl"));
  assert.ok(s.awards.some(x => x.name === "Time Traveler"));  /* the future file */
  assert.ok(s.awards.some(x => x.name === "Burst Mode"));     /* the burst cluster */
  assert.ok(s.awards.some(x => x.name === "Repeat Customer")); /* the duplicate group */
});

/* --- share text with a plea bargain --------------------------------------- */

check("share text gains the plea line when the plea improves the tier", () => {
  const files = [];
  for (let i = 0; i < 30; i++) {
    files.push(shot({ size: 2000 + i, mtime: ts(2026, 4, 1, 12) }));
  }
  for (let i = 0; i < 10; i++) {
    files.push(shot({ size: 100, width: null, height: null, mtime: ts(2023, 0, 15, 12) }));
  }
  for (let i = 0; i < 12; i++) {
    files.push(shot({ size: 777, width: 100, height: 100, mtime: ts(2026, 4, 2, 12) }));
  }
  const s = demo.computeStats(files);
  s.pleaBargain = demo.computePleaBargain(files, 2);
  const post = demo.verdictPostText(s);
  assert.ok(post.includes(
    "The plea bargain: delete 21 screenshots (duplicates and older than 2 years) " +
    "and the verdict drops to Innocent Bystander."));
  assert.ok(post.includes("https://littlestjames82-sys.github.io/snap-judgment/demo.html"));
});

check("share text gains the stands line when the plea changes nothing", () => {
  const files = [];
  for (let i = 0; i < 10; i++) {
    files.push(shot({ size: 3000 + i, width: null, height: null, mtime: ts(2026, 5, 1, 12) }));
  }
  const s = demo.computeStats(files);
  s.pleaBargain = demo.computePleaBargain(files, 2);
  const post = demo.verdictPostText(s);
  assert.ok(post.includes("The plea bargain changes nothing. The verdict stands."));
  /* And with no plea computed at all, the text carries no plea line. */
  const bare = demo.verdictPostText(demo.computeStats(files));
  assert.ok(!bare.includes("plea bargain"));
});

/* --- roast intensity bands ---------------------------------------------- */

check("intensity bands map slider values to levels and names", () => {
  const cases = [
    [0, 0, "Gentle"], [19, 0, "Gentle"],
    [20, 1, "Kind"], [39, 1, "Kind"],
    [40, 2, "Honest"], [50, 2, "Honest"], [59, 2, "Honest"],
    [60, 3, "Spicy"], [79, 3, "Spicy"],
    [80, 4, "Full roast"], [100, 4, "Full roast"]
  ];
  cases.forEach(([value, level, name]) => {
    const band = demo.intensityBand(value);
    assert.strictEqual(band.level, level, "level for " + value);
    assert.strictEqual(band.name, name, "name for " + value);
  });
  /* Out-of-range values clamp to the end bands. */
  assert.strictEqual(demo.intensityBand(-5).level, 0);
  assert.strictEqual(demo.intensityBand(250).level, 4);
});

/* --- court commentary ----------------------------------------------------- */

/* A hand-built stats object in the shape computeStats returns. */
function fakeStats(overrides) {
  return Object.assign({
    count: 100, nightCount: 10, nightShare: 10,
    duplicateSuspects: 0, longestStreak: 2, oldestAgeYears: 0
  }, overrides || {});
}

check("commentary: gentle and full roast differ on the same night folder", () => {
  const s = fakeStats({ count: 266, nightCount: 127, nightShare: 47.7, longestStreak: 3, oldestAgeYears: 1 });
  const gentle = demo.courtCommentary(s, 0);
  const roast = demo.courtCommentary(s, 4);
  assert.strictEqual(gentle,
    "The court sees 127 screenshots taken at night and simply notes the defendant keeps late hours.");
  assert.strictEqual(roast,
    "127 screenshots after 10 PM. The court has reviewed the defendant's sleep schedule and found it guilty too.");
  assert.notStrictEqual(gentle, roast);
});

check("commentary priority: a night case beats a duplicates case", () => {
  /* Qualifies for both night (share 52 >= 40) and duplicates (40 >= 10);
   * the night template must win. */
  const s = fakeStats({ count: 300, nightCount: 156, nightShare: 52, duplicateSuspects: 40 });
  assert.strictEqual(demo.courtCommentary(s, 2),
    "156 of these screenshots, 52.0%, were taken between 10 PM and 5 AM. That is not a habit. That is a second shift.");
});

check("commentary: the gentle band stays gentle for a 2,500 folder", () => {
  const s = fakeStats({ count: 2500, nightCount: 250, nightShare: 10 });
  assert.strictEqual(demo.courtCommentary(s, 0),
    "The court counts 2,500 screenshots and sees a thorough archivist at work. Every one of them meant something once.");
});

check("commentary never mutates the stats it reads", () => {
  function deepFreeze(obj) {
    if (obj && typeof obj === "object") {
      Object.keys(obj).forEach(k => deepFreeze(obj[k]));
      Object.freeze(obj);
    }
    return obj;
  }
  const s = deepFreeze(fakeStats({
    count: 266, nightCount: 127, nightShare: 47.7, duplicateSuspects: 3,
    longestStreak: 9, oldestAgeYears: 2,
    oldest: { name: "old.png", mtime: 1700000000000, label: "Nov 14, 2023" },
    awards: [{ name: "Night Owl", detail: "frozen" }]
  }));
  const snapshot = JSON.stringify(s);
  for (let lvl = 0; lvl <= 4; lvl++) demo.courtCommentary(s, lvl);
  assert.strictEqual(JSON.stringify(s), snapshot);
});

check("commentary: No Evidence gets the same gentle line at every band", () => {
  const empty = demo.computeStats([]);
  for (let lvl = 0; lvl <= 4; lvl++) {
    assert.strictEqual(demo.courtCommentary(empty, lvl),
      "No evidence, no commentary. The court does not roast an empty folder.");
  }
});

/* --- regression: the screenshots core is untouched by the docket ------ */

check("regression: screenshots computeStats output is unchanged", () => {
  const files = [];
  for (let i = 0; i < 60; i++) {
    files.push(shot({
      mtime: ts(2026, i % 3, 1 + (i % 27), i % 2 === 0 ? 23 : 13),
      size: 100000
    }));
  }
  const s = demo.computeStats(files);
  assert.deepStrictEqual({
    count: s.count, tier: s.tier, totalBytes: s.totalBytes,
    nightCount: s.nightCount, nightShare: s.nightShare,
    weekendShare: s.weekendShare, longestStreak: s.longestStreak,
    duplicateGroups: s.duplicateGroups,
    duplicateSuspects: s.duplicateSuspects, awards: s.awards
  }, {
    count: 60, tier: "Petty Offender", totalBytes: 6000000,
    nightCount: 30, nightShare: 50, weekendShare: 25, longestStreak: 1,
    duplicateGroups: 1, duplicateSuspects: 59,
    awards: [
      { name: "Night Owl",
        detail: "30 screenshots between 10 PM and 5 AM (50.0% of the total)." },
      { name: "Repeat Customer",
        detail: "60 files share an exact size and dimensions with a sibling. The same screenshot, taken again." },
      { name: "Creature of Habit",
        detail: "50.0% of all screenshots were taken in the 1 PM hour. Same time, same impulse." }
    ]
  });
});

/* --- the Downloads Docket --------------------------------------------- */

const DL_NOW = ts(2026, 10, 19, 12); /* the docket tests' fixed clock */

let dlSeq = 0;
function dlFile(overrides) {
  dlSeq++;
  const base = { name: "file-" + dlSeq + ".dat", size: 1000, mtime: ts(2026, 5, 1, 12) };
  return Object.assign(base, overrides || {});
}
function dlFiles(n, overrides) {
  const out = [];
  for (let i = 0; i < n; i++) out.push(dlFile(overrides));
  return out;
}
function dlAwardKeys(stats) { return stats.awards.map(a => a.key); }

check("downloads verdict thresholds match the Python DL_TIERS", () => {
  assert.strictEqual(demo.downloadsVerdictFor(0), "No Evidence");
  assert.strictEqual(demo.downloadsVerdictFor(1), "Tidy Desk");
  assert.strictEqual(demo.downloadsVerdictFor(24), "Tidy Desk");
  assert.strictEqual(demo.downloadsVerdictFor(25), "Mild Clutter");
  assert.strictEqual(demo.downloadsVerdictFor(99), "Mild Clutter");
  assert.strictEqual(demo.downloadsVerdictFor(100), "Digital Junk Drawer");
  assert.strictEqual(demo.downloadsVerdictFor(249), "Digital Junk Drawer");
  assert.strictEqual(demo.downloadsVerdictFor(250), "Landfill Adjacent");
  assert.strictEqual(demo.downloadsVerdictFor(499), "Landfill Adjacent");
  assert.strictEqual(demo.downloadsVerdictFor(500), "Hoarding Situation");
  assert.strictEqual(demo.downloadsVerdictFor(999), "Hoarding Situation");
  assert.strictEqual(demo.downloadsVerdictFor(1000), "Superfund Site");
  assert.strictEqual(demo.computeDownloadsStats(dlFiles(24), DL_NOW).tier, "Tidy Desk");
  assert.strictEqual(demo.computeDownloadsStats(dlFiles(25), DL_NOW).tier, "Mild Clutter");
  assert.strictEqual(demo.computeDownloadsStats(dlFiles(100), DL_NOW).tier, "Digital Junk Drawer");
  assert.strictEqual(demo.computeDownloadsStats(dlFiles(1000), DL_NOW).tier, "Superfund Site");
  assert.strictEqual(demo.computeDownloadsStats([], DL_NOW).tier, "No Evidence");
});

check("downloads sentences port the Python voices exactly", () => {
  assert.strictEqual(demo.downloadsSentenceFor("Digital Junk Drawer", "fair"),
    "Sentence: probation. The downloads folder gets a check-in once a month.");
  assert.strictEqual(demo.downloadsSentenceFor("Mild Clutter", "fair"),
    "Sentence: community service. Delete one installer. Just one. Baby steps.");
  assert.strictEqual(demo.downloadsSentenceFor("Mild Clutter", "strict"),
    "Sentence: the installers are deleted today. Not tomorrow. Today.");
  assert.strictEqual(demo.downloadsSentenceFor("Superfund Site", "strict"),
    "Sentence: maximum. Every file will be reviewed, named, and justified in writing. All of them.");
  assert.strictEqual(demo.downloadsSentenceFor("Tidy Desk", "lenient"),
    "Encouragement: case dismissed, with applause. Download something nice; you have earned the space.");
  assert.strictEqual(demo.downloadsSentenceFor("Hoarding Situation", "lenient"),
    "Encouragement: you kept it all because it all mattered at the time. That is not a crime in this court.");
  assert.strictEqual(demo.downloadsSentenceFor("No Evidence", "fair"), "");
});

check("downloads byte formatting matches the Python human_bytes", () => {
  assert.strictEqual(demo.dlHumanBytes(0), "0 B");
  assert.strictEqual(demo.dlHumanBytes(500), "500 B");
  assert.strictEqual(demo.dlHumanBytes(1023), "1023 B");
  assert.strictEqual(demo.dlHumanBytes(1024), "1.0 KB");
  assert.strictEqual(demo.dlHumanBytes(6431), "6.3 KB");
  assert.strictEqual(demo.dlHumanBytes(298130000), "284.3 MB");
  assert.strictEqual(demo.dlHumanBytes(5 * 1024 * 1024 * 1024), "5.0 GB");
});

check("downloads duplicate grouping: chains and copy suffixes", () => {
  /* The Python test fixture, byte for byte: a setup chain, a report
   * "copy" pair, and one unique file. */
  const base = ts(2026, 5, 1, 12);
  const files = [
    { name: "setup.exe", size: 500, mtime: base },
    { name: "setup (1).exe", size: 500, mtime: base },
    { name: "setup (2).exe", size: 500, mtime: base },
    { name: "report.pdf", size: 300, mtime: base },
    { name: "report copy.pdf", size: 300, mtime: base },
    { name: "unique.txt", size: 100, mtime: base }
  ];
  const s = demo.computeDownloadsStats(files, DL_NOW);
  assert.strictEqual(s.duplicateGroups, 2);
  assert.strictEqual(s.duplicateExtra, 3);
  assert.strictEqual(s.duplicateExtraBytes, 500 * 2 + 300);
  assert.ok(!dlAwardKeys(s).includes("double_downloader")); /* needs 5 extras */

  /* A chain of four is one group with three extras. */
  const chain = ["setup.exe", "setup (1).exe", "setup (2).exe", "setup (3).exe"]
    .map(name => ({ name, size: 700, mtime: base }));
  const cs = demo.computeDownloadsStats(chain, DL_NOW);
  assert.strictEqual(cs.duplicateGroups, 1);
  assert.strictEqual(cs.duplicateExtra, 3);
  assert.strictEqual(cs.duplicateExtraBytes, 3 * 700);

  /* The group keys themselves, including the " (1) copy" chain and
   * pathlib's suffix rules. */
  assert.deepStrictEqual(demo.downloadsGroupKey("setup (2).exe"), { stem: "setup", ext: ".exe" });
  assert.deepStrictEqual(demo.downloadsGroupKey("setup (1) copy.exe"), { stem: "setup", ext: ".exe" });
  assert.deepStrictEqual(demo.downloadsGroupKey("setup copy 2.exe"), { stem: "setup", ext: ".exe" });
  assert.deepStrictEqual(demo.downloadsGroupKey("archive.tar.gz"), { stem: "archive.tar", ext: ".gz" });
  assert.deepStrictEqual(demo.downloadsGroupKey(".gitignore"), { stem: ".gitignore", ext: "" });
});

check("downloads awards: installer archaeologist and zip hoarder thresholds", () => {
  const ten = dlFiles(10, { size: 100 }).map((f, i) =>
    Object.assign(f, { name: "tool-" + i + ".exe" }));
  const s10 = demo.computeDownloadsStats(ten, DL_NOW);
  assert.ok(dlAwardKeys(s10).includes("installer_archaeologist"));
  assert.strictEqual(s10.awards[0].detail,
    "10 installers (1000 B) kept after their work was done. The court has excavated older civilizations with fewer setup files.");
  const nine = dlFiles(9, { size: 100 }).map((f, i) =>
    Object.assign(f, { name: "tool-" + i + ".exe" }));
  assert.ok(!dlAwardKeys(demo.computeDownloadsStats(nine, DL_NOW))
    .includes("installer_archaeologist"));

  const zips = n => dlFiles(n).map((f, i) =>
    Object.assign(f, { name: "pack-" + i + ".zip" }));
  assert.ok(dlAwardKeys(demo.computeDownloadsStats(zips(15), DL_NOW)).includes("zip_hoarder"));
  assert.ok(!dlAwardKeys(demo.computeDownloadsStats(zips(14), DL_NOW)).includes("zip_hoarder"));
});

check("downloads awards: double downloader at five extra copies", () => {
  const files = [];
  ["a", "b", "c"].forEach(stem => {
    files.push({ name: stem + ".exe", size: 100, mtime: ts(2026, 5, 1, 12) });
    files.push({ name: stem + " (1).exe", size: 100, mtime: ts(2026, 5, 1, 12) });
    files.push({ name: stem + " (2).exe", size: 100, mtime: ts(2026, 5, 1, 12) });
  });
  const s = demo.computeDownloadsStats(files, DL_NOW);
  assert.strictEqual(s.duplicateExtra, 6);
  const award = s.awards.find(a => a.key === "double_downloader");
  assert.ok(award);
  assert.strictEqual(award.detail,
    "6 extra copies of files the defendant already had, in 3 duplicate sets. Downloaded twice, trusted once.");
});

check("downloads awards: ancient relic, landfill, seeding, receipts, time capsule", () => {
  const relic = demo.computeDownloadsStats(
    [{ name: "ancient-driver.exe", size: 100, mtime: ts(2021, 5, 1, 9) }], DL_NOW);
  assert.strictEqual(relic.oldestAgeYears, 5);
  const relicAward = relic.awards.find(a => a.key === "ancient_relic");
  assert.ok(relicAward);
  assert.strictEqual(relicAward.detail,
    "ancient-driver.exe dates to Jun 1, 2021, over three years ago, and it is still here.");

  const landfill = demo.computeDownloadsStats(
    [{ name: "huge.iso", size: 5 * 1024 * 1024 * 1024, mtime: ts(2026, 5, 1, 12) }], DL_NOW);
  const lfAward = landfill.awards.find(a => a.key === "gigabyte_landfill");
  assert.ok(lfAward);
  assert.strictEqual(lfAward.detail,
    "The downloads alone total 5.0 GB. The landfill has its own weather now.");

  const mixed = demo.computeDownloadsStats([
    { name: "linux.torrent", size: 100, mtime: ts(2026, 5, 1, 12) },
    { name: "invoice-march.pdf", size: 100, mtime: ts(2026, 5, 1, 12) },
    { name: "plain.txt", size: 100, mtime: ts(2026, 5, 1, 12) }
  ], DL_NOW);
  const seedAward = mixed.awards.find(a => a.key === "still_seeding");
  assert.strictEqual(seedAward.detail,
    "1 torrent file on disk. The court does not ask what they point to. It never asks.");
  const receiptAward = mixed.awards.find(a => a.key === "receipt_keeper_dx");
  assert.strictEqual(receiptAward.detail,
    "1 file with invoice or receipt in the name. The tax folder sends its regards.");

  /* Time Capsule: 6 of 10 files over a year old is exactly 60%. */
  const capsuleFiles = [];
  for (let i = 0; i < 6; i++) capsuleFiles.push(dlFile({ mtime: ts(2024, 0, 15, 12) }));
  for (let i = 0; i < 4; i++) capsuleFiles.push(dlFile({ mtime: ts(2026, 5, 1, 12) }));
  const capsule = demo.computeDownloadsStats(capsuleFiles, DL_NOW);
  assert.strictEqual(capsule.oldShare, 60);
  const tcAward = capsule.awards.find(a => a.key === "time_capsule");
  assert.ok(tcAward);
  assert.strictEqual(tcAward.detail,
    "60.0% of these files are over a year old. This is less a folder than a stratum.");
  const fiveOld = capsuleFiles.slice(1); /* 5 of 9 old: 55.6% */
  assert.ok(!dlAwardKeys(demo.computeDownloadsStats(fiveOld, DL_NOW)).includes("time_capsule"));

  /* A clean desk earns nothing. */
  assert.deepStrictEqual(demo.computeDownloadsStats(dlFiles(5), DL_NOW).awards, []);
});

check("downloads commentary: priority and the mercy bands", () => {
  /* Qualifies for both duplicates (6 extras) and installers (12):
   * the duplicates template wins, as in Python. */
  const files = [];
  for (let i = 0; i < 12; i++) {
    files.push({ name: "tool-" + i + ".exe", size: 100, mtime: ts(2026, 5, 1, 12) });
  }
  ["a", "b", "c"].forEach(stem => {
    files.push({ name: stem + ".dat", size: 100, mtime: ts(2026, 5, 1, 12) });
    files.push({ name: stem + " (1).dat", size: 100, mtime: ts(2026, 5, 1, 12) });
    files.push({ name: stem + " (2).dat", size: 100, mtime: ts(2026, 5, 1, 12) });
  });
  const s = demo.computeDownloadsStats(files, DL_NOW);
  assert.strictEqual(s.duplicateExtra, 6);
  assert.strictEqual(demo.downloadsCommentary(s, 4),
    "6 duplicate downloads. The court has now reviewed the same installer multiple times, and it was not that compelling the first time.");
  assert.strictEqual(demo.downloadsCommentary(s, 0),
    "The court found 6 files that were downloaded more than once, and chooses to believe the defendant simply wanted to be very sure.");

  /* Installers alone: gentle and full roast differ, numbers real. */
  const instOnly = demo.computeDownloadsStats(
    dlFiles(12, { size: 100 }).map((f, i) => Object.assign(f, { name: "tool-" + i + ".exe" })), DL_NOW);
  assert.strictEqual(demo.downloadsCommentary(instOnly, 0),
    "The court counts 12 installers and sees a well-equipped machine. Every one of them set something up once.");
  assert.strictEqual(demo.downloadsCommentary(instOnly, 4),
    "12 installers. The defendant has kept every key to every door ever opened on this machine, including the doors that were demolished.");
  assert.notStrictEqual(demo.downloadsCommentary(instOnly, 0),
    demo.downloadsCommentary(instOnly, 4));

  /* No Evidence: the same gentle line as the screenshots court. */
  assert.strictEqual(demo.downloadsCommentary(demo.computeDownloadsStats([], DL_NOW), 4),
    "No evidence, no commentary. The court does not roast an empty folder.");
});

check("downloads sample defendant: 120 files, Digital Junk Drawer, exact awards", () => {
  const files = demo.makeSampleDownloadsFiles(DL_NOW);
  assert.strictEqual(files.length, 120);
  files.forEach(f => {
    assert.strictEqual(typeof f.name, "string");
    assert.strictEqual(typeof f.size, "number");
    assert.strictEqual(typeof f.mtime, "number");
  });
  /* Same seed, same folder, byte for byte at a fixed clock. */
  assert.deepStrictEqual(demo.makeSampleDownloadsFiles(DL_NOW), files);
  /* One file from 2021. */
  assert.ok(files.some(f => f.name === "ancient-driver.exe" &&
    new Date(f.mtime).getFullYear() === 2021));
  const s = demo.computeDownloadsStats(files, DL_NOW);
  assert.strictEqual(s.tier, "Digital Junk Drawer");
  assert.strictEqual(s.installerCount, 17);
  assert.strictEqual(s.archiveCount, 16);
  assert.strictEqual(s.duplicateGroups, 2);
  assert.strictEqual(s.duplicateExtra, 5);
  assert.strictEqual(s.oldestName, "ancient-driver.exe");
  assert.deepStrictEqual(dlAwardKeys(s),
    ["installer_archaeologist", "zip_hoarder", "double_downloader", "ancient_relic"]);
  const post = demo.downloadsPostText(s);
  assert.ok(post.startsWith("My Downloads folder just got judged by Snap Judgment: " +
    "120 files, verdict Digital Junk Drawer, "));
  assert.ok(post.includes("It reads names, sizes, and dates only, and never opens a file."));
  /* The verdict text dispatcher routes downloads stats correctly. */
  assert.strictEqual(demo.verdictPostText(s), post);
});

check("downloads scan keeps only top-level files", () => {
  assert.strictEqual(demo.isTopLevelPath("Downloads/report.pdf"), true);
  assert.strictEqual(demo.isTopLevelPath("Downloads/nested/report.pdf"), false);
  assert.strictEqual(demo.isTopLevelPath("Downloads/a/b/c.zip"), false);
  assert.strictEqual(demo.isTopLevelPath("report.pdf"), true);
  assert.strictEqual(demo.isTopLevelPath(""), true);
});

console.log("\n" + passed + " test groups passed.");
