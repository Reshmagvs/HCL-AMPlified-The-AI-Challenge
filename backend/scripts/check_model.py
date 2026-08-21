"""Report whether the local language model is ready, in words a person can act on.

Run by the launcher before anything else starts. It exists because the previous
check -- ``where ollama`` -- answered the wrong question: an installed binary
with no daemon running, or a daemon with the wrong model pulled, both look
exactly like a working installation from a batch file, and the product then
falls back to templated wording without ever saying why.

Exit codes are for the launcher, the text is for the person reading the console.

    0  the model is loaded and Lodestar can build new subjects
    1  the daemon is not answering
    2  the daemon is answering but the configured model is not pulled
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from app.config import get_settings

TIMEOUT = 3.0
INDENT = "      "


def pulled_models(host: str) -> set[str] | None:
    """Model names the daemon reports, or None when it is not answering."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=TIMEOUT) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return {entry.get("name", "") for entry in payload.get("models", [])}


def main() -> int:
    settings = get_settings()
    host = settings.ollama_host.rstrip("/")
    model = settings.ollama_model

    names = pulled_models(host)
    if names is None:
        print(f"{INDENT}No Ollama daemon answering at {host}.")
        return 1

    # Ollama reports "qwen2.5:3b-instruct"; a bare family name counts too.
    if model in names or any(name.split(":")[0] == model for name in names):
        print(f"{INDENT}Local model ready: {model}")
        return 0

    have = ", ".join(sorted(names)) or "nothing"
    print(f"{INDENT}Ollama is running but {model} is not pulled (it has: {have}).")
    print(f"{INDENT}Run:  ollama pull {model}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
