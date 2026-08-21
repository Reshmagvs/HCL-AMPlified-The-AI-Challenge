"""Re-read the description of every discovered resource from its page.

A discovered resource's description is read off the page when it is first
verified, so it is only ever as good as the extractor that was current that
day. When that extractor improves, everything already stored keeps the old
text -- and the old text was site furniture: 109 of 111 stored descriptions
began "Jump to content Main menu" or simply repeated the title.

So this exists to close that gap. It re-fetches each stored URL, takes the
description again, and writes back only that field. Nothing else is touched:
not the id, not the skills it is bound to, not the embedding. A page that no
longer answers keeps its existing text and is reported, because a dead link is
a separate decision from a stale description and should not be made silently
here.

    python -m scripts.refresh_descriptions            # report only
    python -m scripts.refresh_descriptions --write    # apply
"""

from __future__ import annotations

import json
import sys

from app.core import store
from app.core.websearch import verify


def main(argv: list[str]) -> int:
    apply = "--write" in argv
    path = store.generated_dir() / store.COURSES_FILE
    if not path.exists():
        print("no discovered resources yet")
        return 0

    courses = json.loads(path.read_text(encoding="utf-8"))
    improved, unchanged, unreachable = 0, 0, []

    for entry in courses:
        page = verify(entry["url"])
        if page is None:
            unreachable.append(entry["url"])
            continue
        if page.description and page.description != entry.get("description"):
            entry["description"] = page.description
            improved += 1
        else:
            unchanged += 1

    print(f"{improved} descriptions re-read, {unchanged} unchanged, {len(unreachable)} unreachable")
    for url in unreachable:
        print(f"  unreachable: {url}")

    if apply and improved:
        store.write_courses(courses)
        print(f"written to {path}")
    elif improved:
        print("re-run with --write to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
