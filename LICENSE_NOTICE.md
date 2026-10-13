# Copyright and license notice

Copyright (C) 2026 HenryTANG0520 and AutoZZZ / AutoHSR contributors.

AutoZZZ derives its window capture, OCR, input, configuration, execution-control and desktop scaffolding from the local [AutoHSR](https://github.com/HenryTANG0520/AutoHSR) project at `D:\Projects\AutoHSR`. AutoHSR's source and notice use GNU General Public License version 3 only; AutoZZZ preserves that license, SPDX identifier `GPL-3.0-only`. The full license text is included in [LICENSE](LICENSE).

AutoZZZ is distributed without warranty. Distribution of modified source or binaries must preserve applicable notices and satisfy the corresponding-source requirements of GPL version 3. Third-party packages retain their own licenses and notices. Game artwork, names and trademarks belong to their respective rights holders; this project is not an official game product.

BetterGI informs the screenshot → recognition → simulated-input design. No BetterGI source file or binary is bundled as part of this initial implementation. AutoHSR's HSR-specific templates are not copied into the ZZZ template set.

## OneDragon template attribution

The following five PNG files are renamed, byte-for-byte copies of image templates from [ZenlessZoneZero-OneDragon](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon), maintained by OneDragon-Anything and its contributors. Source revision: [`9633c6a68e42ee7500cdf3fc9677dde2b51f1fe2`](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/tree/9633c6a68e42ee7500cdf3fc9677dde2b51f1fe2).

| AutoZZZ file | Upstream path at the pinned revision | Git blob SHA-1 |
| --- | --- | --- |
| [assets/templates/back.png](assets/templates/back.png) | [assets/template/menu/back/raw.png](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/9633c6a68e42ee7500cdf3fc9677dde2b51f1fe2/assets/template/menu/back/raw.png) | `9c7ec773cc3f3a23c63bbfac244062a5c07128e0` |
| [assets/templates/close.png](assets/templates/close.png) | [assets/template/menu/btn_close/raw.png](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/9633c6a68e42ee7500cdf3fc9677dde2b51f1fe2/assets/template/menu/btn_close/raw.png) | `f9856f412a3b9d0441a0856bd27625835cf855f9` |
| [assets/templates/daily_completed.png](assets/templates/daily_completed.png) | [assets/template/compendium/completed/raw.png](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/9633c6a68e42ee7500cdf3fc9677dde2b51f1fe2/assets/template/compendium/completed/raw.png) | `48729d38cb80456ea71c8ecc9c6821f9bd4609ab` |
| [assets/templates/world_map.png](assets/templates/world_map.png) | [assets/template/normal_world/map/raw.png](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/9633c6a68e42ee7500cdf3fc9677dde2b51f1fe2/assets/template/normal_world/map/raw.png) | `45c1b4c734f37050fc93e42502be7555e31e1abe` |
| [assets/templates/world_menu.png](assets/templates/world_menu.png) | [assets/template/normal_world/compendium/raw.png](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/9633c6a68e42ee7500cdf3fc9677dde2b51f1fe2/assets/template/normal_world/compendium/raw.png) | `ab57e2167a2ef09886bf8b64253019b387b6c0dc` |

The pinned repository's [LICENSE](https://github.com/OneDragon-Anything/ZenlessZoneZero-OneDragon/blob/9633c6a68e42ee7500cdf3fc9677dde2b51f1fe2/LICENSE) contains GNU GPL version 3. Its Git blob SHA-1 is `f288702d2fa16d3cdf0035b15a9fcbc552cd88e7`, identical to this project's included `LICENSE`. The local [upstream tree record](assets/upstream-tree.json) records the revision, template paths and hashes. Attribution identifies the upstream repository materials; rights in the underlying game UI artwork remain with its rights holders.

## Locally captured game UI templates

[assets/templates/interact_f.png](assets/templates/interact_f.png) was cropped from a local mainland-China ZZZ game capture during AutoZZZ calibration on 2026-10-11, with a 3840 × 2160 client area. It shows the in-game F interaction prompt. It is not one of the five copied OneDragon templates above. SHA-256: `4024c85d9c7b3f6bab0c2e610248f1d8c260b0c24ae66bc6698b1fcd8f8ab016`.

[assets/templates/video_slot_plus.png](assets/templates/video_slot_plus.png) was also cropped from the local mainland-China ZZZ calibration capture on 2026-10-11. It shows the plus icon of an empty slot in the video-store business configuration panel, used to select already-owned items. It is independently captured and is not copied from OneDragon. SHA-256: `53242bb1c76f912642d07e603b10276674410241b250aa67b1ab29f9a4663e54`.

The capture provenance does not transfer ownership of the underlying game UI; those rights remain with their respective rights holders.

## Sanitized test fixtures

The OCR geometry in `tests/fixtures/promoter_picker.json` and `promoter_business.json`, and the narrow card-name strip in `promoter_name_pixels.png`, derive from game screenshots provided by the user for promoter selection calibration. Account identifiers, portraits and unrelated screen content are excluded. These are test materials; rights in the underlying game text and UI remain with their respective rights holders.

`tests/fixtures/monthly_card.json` contains only three OCR text anchors and their normalized geometry from the user-reported monthly-card timeout capture on 2026-10-12. It excludes screenshot pixels, account identifiers and unrelated content; the original capture is retained only in ignored local run evidence.

`tests/fixtures/promoter_selected_long.json` preserves seven non-personal OCR anchors from the 2026-10-12 promoter-selection failure, including the long-description layout and the actual “确定” control. No screenshot pixels or account identifiers are included.

`tests/test_video_opening_result.py` preserves only the two text anchors and normalized geometry of the 2026-10-12 opening-success popup. It excludes screenshot pixels, account identifiers and unrelated screen content.

The desktop build includes the template assets, this notice and the GPL license text. Keep the full output folder and its dependency license files when redistributing.

The fifteen small control crops in `tests/fixtures/rewards/` derive from the eight reward-workflow screenshots supplied by the user on 2026-10-13. `provenance.json` records source and crop hashes, dimensions and crop boxes. Only reward controls are retained; full screenshots, character portraits and account identifiers are excluded. Rights in the underlying game artwork and UI remain with their respective rights holders. These fixtures are offline tests, not proof of live claims.
