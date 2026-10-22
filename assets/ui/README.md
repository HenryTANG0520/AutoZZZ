# Desktop UI materials

The desktop uses the **actual publicly served ZZZ website typefaces and mark**,
alongside locally drawn compendium geometry. It does not use a generated image
or a full game screenshot as a non-interactive UI background.

| File | Source | Change |
| --- | --- | --- |
| `inpin.ttf` | [Official site's Hongmeng face](https://zzz.mihoyo.com/_nuxt/fonts/hongmengti.4285934.woff) | WOFF container decompressed to sfnt; names, outlines and copyright records preserved |
| `mont-heavy.ttf` | [Official site's Mont Heavy subset](https://zzz.mihoyo.com/_nuxt/fonts/Mont-Heavy.88bdd4a.woff) | Same container conversion; this subset only covers letters |
| `zzz-logo.png` | [Official site's mark](https://zzz.mihoyo.com/_nuxt/img/logo.1e072ee.png) | Byte-for-byte copy, scaled at display time |

The official site's [application bundle](https://zzz.mihoyo.com/_nuxt/d6e69e2.js)
references these resources. Retrieval date: 2026-10-22. Exact source and local
SHA-256 hashes are recorded in [sources.json](sources.json). Rebuild with
`python tools/prepare_ui_assets.py` using the development-only `fonttools`
package. The script rejects unexpected upstream hash changes.

The reward check mark reuses `assets/templates/daily_completed.png`, already
attributed to its pinned OneDragon source in [LICENSE_NOTICE.md](../../LICENSE_NOTICE.md).
No additional OneDragon files are introduced for this redesign.

These third-party assets retain their original rights holders' rights and are
not relicensed under AutoZZZ's source-code GPL. Availability from a public CDN
is provenance, not an additional license grant. Font metadata remains intact.
AutoZZZ is an unofficial fan utility, not a HoYoverse product.

The rings, stitches, orange spine, handheld charm, tab silhouettes, task cards,
capsules and focus outlines are code-drawn in `autozzz/game_widgets.py` and
`autozzz/theme.py`. They resize with the window and respond to real app state.
The UI references are linked in [REFERENCE_RESEARCH.md](../../REFERENCE_RESEARCH.md).
Original reference screenshots, their account IDs and creator watermarks are
not bundled or committed.
