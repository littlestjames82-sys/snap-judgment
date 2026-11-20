# Media pack

Every picture and video in this folder was drawn by the project's own
pure-stdlib renderer (the 5x7 bitmap font from `snap_judgment.py`) from
the deterministic demo folder's real numbers. No image library, no
mockups, no AI generation. Regenerate the whole pack with:

    python3 tools/make_media.py

| File | What it shows | Use it for |
|---|---|---|
| `verdict-ladder.png` | The six verdict tiers as a ladder, colored by severity, mildest to felon | README hero companion, site evidence locker, social posts explaining the tiers |
| `awards-grid.png` | All sixteen awards in a 4x4 grid, each with a plain-language description | Site evidence locker, Product Hunt gallery, "which ones have you earned?" posts |
| `plea-before-after.png` | The demo folder's plea bargain: 310 screenshots before, 210 after deleting duplicates and anything over two years old, 4.5 MB reclaimed, verdict unchanged | Social posts, Product Hunt gallery, anywhere the plea bargain needs explaining in one glance |
| `heatmap.png` | The demo folder's hour-by-weekday heatmap: when the defendant strikes | Site evidence locker, blog and dev.to illustrations |
| `verdict-reveal.gif` | The share card assembling itself, ending with the verdict stamping in | README, site evidence locker, social posts, Product Hunt gallery (motion beats stills) |
| `demo.mp4` | The terminal demo GIF re-encoded as H.264 MP4 | Video-first platforms (X, LinkedIn, TikTok uploads) that prefer MP4 over GIF |
| `authors-verdict.png` | The author's own real verdict card: 266 screenshots, 164 MB, Repeat Offender, 47.7% at night | Launch post #1, anywhere a real (non-demo) verdict is the honest exhibit |

The courtroom palette throughout: near-black `#101014`, panel `#16161f`,
gold `#d4af37` family, off-white text. The demo defendant's numbers
(310 screenshots, Repeat Offender) are the synthetic demo set, labeled
as such wherever they appear; `authors-verdict.png` is a real folder.
