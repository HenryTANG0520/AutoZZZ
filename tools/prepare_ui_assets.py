"""Rebuild desktop font assets from the public ZZZ website (development only).

Requires fonttools, not needed to run AutoZZZ. Downloads are hash checked; the
application itself never downloads fonts or UI resources.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://zzz.mihoyo.com/_nuxt/"
ASSETS = (
    ("fonts/hongmengti.4285934.woff", "inpin.ttf"),
    ("fonts/Mont-Heavy.88bdd4a.woff", "mont-heavy.ttf"),
    ("img/logo.1e072ee.png", "zzz-logo.png"),
)


def main():
    from fontTools.ttLib import TTFont
    import io

    target = ROOT / "assets" / "ui"
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "sources.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    records = {}
    for remote, local in ASSETS:
        url = SOURCE + remote
        data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "AutoZZZ-asset-preparation"}), timeout=30).read()
        digest = hashlib.sha256(data).hexdigest()
        if local in previous and previous[local]["source_sha256"] != digest:
            raise ValueError(f"Upstream asset changed: {url}; review before updating provenance")
        path = target / local
        if remote.endswith(".woff"):
            font = TTFont(io.BytesIO(data), recalcTimestamp=False)
            font.flavor = None  # Same outlines and naming; only unwrap the WOFF container.
            font.save(path)
        else:
            path.write_bytes(data)
        records[local] = dict(url=url, source_sha256=digest,
                              sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                              modification="WOFF decompressed to sfnt; outlines and names preserved" if remote.endswith(".woff") else "Unmodified",
                              retrieved="2026-10-22")
    manifest_path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Prepared", ", ".join(records))


if __name__ == "__main__":
    main()
