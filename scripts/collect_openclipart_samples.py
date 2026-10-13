"""Curate a small CC0 Openclipart seed set with per-item evidence and hashes."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "content-library/web-visuals/external-datasets/openclipart"
INDEX = OUT / "assets.jsonl"
QUERIES = {
    "speech_bubble": "speech bubble",
    "sparkle_emphasis": "sparkle",
    "heart_emphasis": "heart sticker",
    "motion_lines": "speed lines",
    "hand_drawn_underline": "hand drawn underline",
}
BASE = "https://openclipart.org"
CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 AI-CUT-Research/1.0 (curated visual examples)"})


def jsonld(page: str) -> dict:
    match = re.search(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', page, re.S)
    if not match:
        raise ValueError("item page has no JSON-LD metadata")
    return json.loads(match.group(1))


def collect() -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "assets").mkdir(exist_ok=True)
    existing = []
    if INDEX.exists():
        existing = [json.loads(x) for x in INDEX.read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = {r["id"] for r in existing}
    hashes = {r["asset_sha256"] for r in existing if r.get("asset_sha256")}
    additions = []
    for category, query in QUERIES.items():
        search_url = BASE + "/search/"
        response = SESSION.get(search_url, params={"query": query}, timeout=30)
        response.raise_for_status()
        matches = re.findall(r'href="(/detail/(\d+)/[^"?]+)', response.text)
        if not matches:
            print(f"[skip] no result: {category}", flush=True)
            time.sleep(1.5)
            continue
        detail_path, item_id = matches[0]
        identity = f"openclipart-{item_id}"
        if identity in ids:
            print(f"[skip] already collected: {identity}", flush=True)
            time.sleep(1.5)
            continue
        detail_url = urljoin(BASE, detail_path)
        detail_response = SESSION.get(detail_url, timeout=30)
        detail_response.raise_for_status()
        meta = jsonld(detail_response.text)
        asset = meta.get("image") or {}
        license_url = asset.get("license") or meta.get("license")
        author = (asset.get("author") or meta.get("artist") or {}).get("name")
        asset_url = asset.get("url")
        if license_url != CC0 or not author or not asset_url:
            print(f"[skip] item lacks verified CC0/author/asset metadata: {identity}", flush=True)
            time.sleep(1.5)
            continue
        media = SESSION.get(asset_url, timeout=30)
        media.raise_for_status()
        if b"<svg" not in media.content[:1000].lower():
            print(f"[skip] not an SVG asset: {identity}", flush=True)
            time.sleep(1.5)
            continue
        digest = hashlib.sha256(media.content).hexdigest()
        if digest in hashes:
            print(f"[skip] exact duplicate by SHA-256: {identity}", flush=True)
            time.sleep(1.5)
            continue
        filename = Path(asset_url.split("?", 1)[0]).name
        destination = OUT / "assets" / filename
        destination.write_bytes(media.content)
        row = {"id": identity, "category": category, "query": query,
               "title": meta.get("name"), "source_url": meta.get("url", detail_url),
               "asset_url": asset_url, "author": author, "license": "CC0-1.0",
               "license_url": license_url, "asset_path": destination.relative_to(OUT).as_posix(),
               "asset_bytes": len(media.content), "asset_sha256": digest,
               "license_evidence": "item page JSON-LD; site FAQ states artist uploads use CC0",
               "review_status": "human_visual_review_pending",
               "ai_rag_eligible": False, "usage_scope": "public_domain_claim_item_metadata_verified_review_pending",
               "production_asset_eligible": False, "collected_date": "2026-10-06"}
        additions.append(row)
        ids.add(identity)
        hashes.add(digest)
        print(f"[collected] {category}: {identity} {meta.get('name')}", flush=True)
        time.sleep(1.5)
    if additions:
        with INDEX.open("a", encoding="utf-8", newline="\n") as stream:
            for row in additions:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return additions


if __name__ == "__main__":
    rows = collect()
    print(json.dumps({"new_items": len(rows), "ids": [r["id"] for r in rows]}, ensure_ascii=False))
