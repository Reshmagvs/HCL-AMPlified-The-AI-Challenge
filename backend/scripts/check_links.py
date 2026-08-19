"""Re-check every URL already in the catalog, and report anything that died.

`verify_catalog.py` gates entries on their way *into* `courses.json`. This one
audits what is already there, which is a different job: links rot, and a catalog
that was clean at build time is not necessarily clean at judging time. It never
edits the catalog -- it reports, and a human decides.

    python -m scripts.check_links
    python -m scripts.check_links --limit 16 --timeout 25
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.retrieval import load_catalog  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from scripts.verify_catalog import USER_AGENT  # noqa: E402

logger = logging.getLogger("check_links")


async def _check(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, resource
) -> tuple[str, str, int, str]:
    """Return (id, url, status, final_url). Status 0 means unreachable."""
    async with semaphore:
        last_status, last_url = 0, resource.url
        for method in ("HEAD", "GET"):
            try:
                response = await client.request(
                    method,
                    resource.url,
                    headers={"Range": "bytes=0-2048"} if method == "GET" else None,
                )
            except (httpx.HTTPError, ValueError):
                continue
            last_status, last_url = response.status_code, str(response.url)
            if 200 <= last_status < 300:
                break
    return resource.id, resource.url, last_status, last_url


async def run(limit: int, timeout: float) -> list[tuple[str, str, int, str]]:
    catalog = load_catalog()
    if not catalog:
        raise SystemExit("courses.json is empty -- nothing to check")

    semaphore = asyncio.Semaphore(limit)
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    ) as client:
        return await asyncio.gather(*(_check(client, semaphore, r) for r in catalog))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit catalog links for rot.")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()

    configure_logging("WARNING")
    results = asyncio.run(run(args.limit, args.timeout))
    dead = [r for r in results if not 200 <= r[2] < 300]

    print()
    print("CATALOG LINK AUDIT")
    print("=" * 72)
    print(f"  checked {len(results)}   alive {len(results) - len(dead)}   dead {len(dead)}")
    print(f"  alive rate: {100 * (len(results) - len(dead)) / len(results):.1f}%")
    if dead:
        print("\n  id        status  url")
        for resource_id, url, status, _final in sorted(dead, key=lambda r: r[0]):
            print(f"  {resource_id:<9} {status:>6}  {url[:88]}")
    else:
        print("\n  every catalog URL still returns 2xx")
    print()

    report: dict[str, Any] = {
        "checked": len(results),
        "dead": [{"id": r[0], "url": r[1], "status": r[2]} for r in dead],
    }
    (get_settings().data_dir / "link_audit.json").write_text(
        json.dumps(report, indent=1) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
