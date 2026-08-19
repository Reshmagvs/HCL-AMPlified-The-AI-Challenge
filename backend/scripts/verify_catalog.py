"""Verify every proposed URL over HTTP, then promote survivors into the catalog.

This is the half of the pipeline that makes "zero hallucinated resources" a
structural property rather than a hope. Nothing reaches ``data/courses.json``
without an HTTP response in the 2xx range, so a plausible-looking URL the model
invented dies here rather than in front of a judge.

The checks, in order:

1. **Normalise and deduplicate by URL.** Two skills often propose the same
   freeCodeCamp page; the duplicate is merged, and its ``skills_covered`` lists
   are unioned rather than one copy winning.
2. **Fetch.** HEAD first because it is cheap, then GET whenever HEAD is not
   2xx. A non-2xx HEAD is never conclusive -- Kaggle answers HEAD with 404 and
   GET with 200 on the same URL, and trusting HEAD alone discarded 29 working
   resources before this was fixed. Redirects are followed and the *final* URL
   is what gets stored.
3. **Discard non-2xx**, plus anything whose redirect collapsed onto a bare
   homepage (a 200 that is really a "we moved this" landing page).
4. **Assign stable ids.** Sorted by final URL, so ``c_0001`` means the same
   resource across rebuilds and a regenerated catalog produces a clean diff.

Concurrency is capped (default 10) to stay polite to the hosts being checked.

    python -m scripts.verify_catalog
    python -m scripts.verify_catalog --limit 20 --timeout 15
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.skill_graph import load_graph  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402

logger = logging.getLogger("verify")

DATA_DIR = get_settings().data_dir
RAW_PATH = DATA_DIR / "courses_raw.json"
OUT_PATH = DATA_DIR / "courses.json"
REPORT_PATH = DATA_DIR / "verify_report.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36 Lodestar-LinkCheck/1.0"
)


def normalise(url: str) -> str:
    """Canonical form for deduplication.

    Scheme is dropped entirely and the path is lowercased. Both matter: the same
    resource is proposed as http and https by different passes, and YouTube
    handles differ only by capitalisation. Comparing case-sensitively let two
    copies of the same page reach the catalog.
    """
    parsed = urlparse(url.strip())
    path = (parsed.path.rstrip("/") or "/").lower()
    return urlunparse(("", parsed.netloc.lower(), path, "", parsed.query.lower(), ""))


def merge_candidates(raw: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Collapse duplicates by normalised URL, unioning the skills they cover."""
    merged: dict[str, dict[str, Any]] = {}
    for skill_id, candidates in raw.items():
        for candidate in candidates:
            url = candidate.get("url", "")
            if not url.startswith("https://"):
                continue
            key = normalise(url)
            existing = merged.get(key)
            if existing is None:
                merged[key] = {**candidate, "skills_covered": [skill_id]}
            elif skill_id not in existing["skills_covered"]:
                existing["skills_covered"].append(skill_id)
    return sorted(merged.values(), key=lambda c: normalise(c["url"]))


async def check_one(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, entry: dict[str, Any]
) -> tuple[dict[str, Any], int, str]:
    """Return (entry, status, final_url). Status 0 means the request failed outright."""
    url = entry["url"]
    last_status, last_url = 0, url
    async with semaphore:
        for method in ("HEAD", "GET"):
            try:
                response = await client.request(
                    method, url, headers={"Range": "bytes=0-2048"} if method == "GET" else None
                )
            except (httpx.HTTPError, ValueError) as exc:
                logger.debug("%s %s failed: %s", method, url, exc)
                continue
            last_status, last_url = response.status_code, str(response.url)
            if 200 <= last_status < 300:
                return entry, last_status, last_url
            # A non-2xx HEAD is never conclusive. Plenty of real sites answer
            # HEAD with 404, 403 or 405 while serving the same URL over GET, so
            # GET is always attempted before an entry is discarded.
    return entry, last_status, last_url


async def verify_all(
    entries: list[dict[str, Any]], limit: int, timeout: float
) -> list[tuple[dict[str, Any], int, str]]:
    """Fetch every candidate concurrently, capped at ``limit`` in flight."""
    semaphore = asyncio.Semaphore(limit)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        verify=True,
    ) as client:
        return await asyncio.gather(*(check_one(client, semaphore, e) for e in entries))


def is_homepage_collapse(original: str, final: str) -> bool:
    """A deep link that redirected to the site root is a dead link wearing a 200."""
    original_path = urlparse(original).path.rstrip("/")
    final_path = urlparse(final).path.rstrip("/")
    return bool(original_path) and original_path != "" and final_path == "" and len(original_path) > 1


def build_report(
    kept: list[dict[str, Any]], discarded: list[tuple[dict[str, Any], int, str]], proposed: int
) -> dict[str, Any]:
    """Everything a human needs to decide whether the catalog is good enough."""
    graph = load_graph()
    track_of = {node_id: node.track for node_id, node in graph.nodes.items()}

    per_track: dict[str, Counter] = defaultdict(Counter)
    for entry in kept:
        for skill in entry["skills_covered"]:
            per_track[track_of.get(skill, "unknown")]["verified"] += 1
    for entry, _status, _final in discarded:
        for skill in entry["skills_covered"]:
            per_track[track_of.get(skill, "unknown")]["discarded"] += 1

    covered = {s for entry in kept for s in entry["skills_covered"]}
    orphans = sorted(
        node_id for node_id, node in graph.nodes.items() if node.assessable and node_id not in covered
    )
    free = sum(1 for e in kept if e["cost"] == "free")

    return {
        "proposed": proposed,
        "verified": len(kept),
        "discarded": len(discarded),
        "free_ratio": round(free / len(kept), 4) if kept else 0.0,
        "per_track": {k: dict(v) for k, v in sorted(per_track.items())},
        "formats": dict(Counter(e["format"] for e in kept).most_common()),
        "levels": dict(Counter(e["level"] for e in kept).most_common()),
        "providers": dict(Counter(e["provider"] for e in kept).most_common(15)),
        "status_codes": dict(Counter(s for _e, s, _f in discarded).most_common()),
        "skills_covered": len(covered),
        "assessable_nodes_without_resource": orphans,
    }


def print_report(report: dict[str, Any]) -> None:
    print()
    print("CATALOG VERIFICATION")
    print("=" * 72)
    print(f"  proposed {report['proposed']}  ->  verified {report['verified']}  "
          f"(discarded {report['discarded']})")
    print(f"  free: {report['free_ratio'] * 100:.1f}%   skills covered: {report['skills_covered']}")
    print()
    print("  track                verified  discarded")
    for track, counts in report["per_track"].items():
        print(f"  {track:<20} {counts.get('verified', 0):>8}  {counts.get('discarded', 0):>9}")
    print()
    print(f"  formats: {report['formats']}")
    print(f"  levels:  {report['levels']}")
    print(f"  discard reasons (http status, 0 = unreachable): {report['status_codes']}")
    print()
    orphans = report["assessable_nodes_without_resource"]
    if orphans:
        print(f"  !! {len(orphans)} assessable nodes have ZERO surviving resources:")
        for skill_id in orphans:
            print(f"       {skill_id}")
        print("     re-run: python -m scripts.harvest_catalog --skills "
              + ",".join(orphans[:20]) + " --force")
    else:
        print("  every assessable node has at least one verified resource")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="HTTP-verify proposed catalog entries.")
    parser.add_argument("--limit", type=int, default=10, help="max concurrent requests")
    parser.add_argument("--timeout", type=float, default=20.0, help="per-request timeout")
    args = parser.parse_args()

    configure_logging(get_settings().log_level)
    if not RAW_PATH.exists():
        logger.error("no %s -- run scripts.harvest_catalog first", RAW_PATH)
        return 2

    raw = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    proposed = sum(len(v) for v in raw.values())
    entries = merge_candidates(raw)
    logger.info("%d proposed, %d unique urls to check", proposed, len(entries))

    results = asyncio.run(verify_all(entries, args.limit, args.timeout))

    kept: list[dict[str, Any]] = []
    discarded: list[tuple[dict[str, Any], int, str]] = []
    for entry, status, final_url in results:
        if not final_url.startswith("https://"):
            # A resource that downgrades to plain http on redirect is dropped:
            # the catalog promises https, and a mixed-content link breaks in the
            # browser anyway.
            discarded.append((entry, status, final_url))
            continue
        if 200 <= status < 300 and not is_homepage_collapse(entry["url"], final_url):
            kept.append({**entry, "url": final_url})
        else:
            discarded.append((entry, status, final_url))

    # Second dedup pass: two candidates can redirect to the same final URL.
    by_final: dict[str, dict[str, Any]] = {}
    for entry in sorted(kept, key=lambda e: normalise(e["url"])):
        key = normalise(entry["url"])
        if key in by_final:
            for skill in entry["skills_covered"]:
                if skill not in by_final[key]["skills_covered"]:
                    by_final[key]["skills_covered"].append(skill)
        else:
            by_final[key] = entry
    kept = list(by_final.values())

    catalog = [
        {
            "id": f"c_{index:04d}",
            "title": entry["title"],
            "provider": entry["provider"],
            "url": entry["url"],
            "format": entry["format"],
            "cost": entry["cost"],
            "duration_hours": float(entry["duration_hours"]),
            "level": entry["level"],
            "skills_covered": sorted(entry["skills_covered"]),
            "rating": float(entry.get("rating", 4.0)),
            "language": entry.get("language", "en"),
            "description": entry.get("description", ""),
        }
        for index, entry in enumerate(kept, start=1)
    ]
    OUT_PATH.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    report = build_report(kept, discarded, proposed)
    REPORT_PATH.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print_report(report)
    logger.info("wrote %s with %d entries", OUT_PATH, len(catalog))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
