"""Render demo/script.txt to one MP3 per scene using edge-tts.

Recording your own voice four times at 2am produces four different audio
qualities and a lot of room noise. Microsoft's neural voices are free,
unlimited, need no account, and regenerate in seconds when a word changes -- so
the workflow becomes: write the script, render the audio, cut the video to the
audio.

Each scene becomes its own file, and the duration of each is printed, so the
video editor knows exactly how long every shot has to be.

    pip install edge-tts
    python demo/make_voiceover.py
    python demo/make_voiceover.py --voice en-IN-PrabhatNeural --rate -8%
    python demo/make_voiceover.py --list-voices
"""

from __future__ import annotations

import argparse
import asyncio
import re
import subprocess
import sys
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
SCRIPT_PATH = DEMO_DIR / "script.txt"
OUTPUT_DIR = DEMO_DIR / "audio"

# Good voices for a technical demo. Slightly slower than feels natural is right.
SUGGESTED = {
    "en-US-AriaNeural": "clear, neutral American",
    "en-GB-RyanNeural": "calm, authoritative British",
    "en-IN-NeerjaNeural": "Indian English, warm",
    "en-IN-PrabhatNeural": "Indian English, measured",
}
DEFAULT_VOICE = "en-IN-PrabhatNeural"

_SCENE_RE = re.compile(r"^=== SCENE\s+(\d+)\s*[—-]\s*(.+?)\s*\(", re.M)


def parse_scenes(text: str) -> list[tuple[int, str, str]]:
    """Split the script into (number, title, narration) triples.

    The narration is everything after the "On screen:" stage direction, so the
    directions can stay in the file for the person recording without ending up
    in the voiceover.
    """
    scenes: list[tuple[int, str, str]] = []
    for block in text.split("\n---\n"):
        header = _SCENE_RE.search(block)
        if not header:
            continue
        body = block[header.end() :]
        body = body.split("\n", 1)[1] if "\n" in body else body
        lines = [
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("On screen:")
        ]
        narration = " ".join(lines).strip()
        if narration:
            scenes.append((int(header.group(1)), header.group(2).strip(), narration))
    return scenes


async def render(scene: tuple[int, str, str], voice: str, rate: str, pitch: str) -> Path:
    import edge_tts

    number, title, narration = scene
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    target = OUTPUT_DIR / f"scene-{number:02d}-{slug}.mp3"
    await edge_tts.Communicate(narration, voice, rate=rate, pitch=pitch).save(str(target))
    return target


def duration_seconds(path: Path) -> float | None:
    """Read the rendered length with ffprobe, if it is installed."""
    try:
        output = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20, check=True,
        )
        return float(output.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def estimate_seconds(words: int) -> float:
    """Fallback when ffprobe is absent: neural TTS runs near 150 words/minute."""
    return round(words / 150 * 60, 1)


async def main_async(args: argparse.Namespace) -> int:
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        print("edge-tts is not installed. Run:  pip install edge-tts", file=sys.stderr)
        return 2

    if not SCRIPT_PATH.exists():
        print(f"missing {SCRIPT_PATH}", file=sys.stderr)
        return 2

    scenes = parse_scenes(SCRIPT_PATH.read_text(encoding="utf-8"))
    if not scenes:
        print("no scenes parsed -- check the '=== SCENE n — title (' headers", file=sys.stderr)
        return 2

    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"\nRendering {len(scenes)} scenes with {args.voice} "
          f"(rate {args.rate}, pitch {args.pitch})\n")
    print(f"  {'scene':<34} {'words':>6} {'seconds':>9}")

    total = 0.0
    for scene in scenes:
        path = await render(scene, args.voice, args.rate, args.pitch)
        words = len(scene[2].split())
        seconds = duration_seconds(path) or estimate_seconds(words)
        total += seconds
        print(f"  {path.name:<34} {words:>6} {seconds:>9.1f}")

    minutes, remainder = divmod(round(total), 60)
    print(f"\n  total: {minutes}:{remainder:02d}   files in {OUTPUT_DIR}")
    if total > 300:
        print("  !! over the five-minute submission limit -- cut scene 7 before 5 or 8.")
    elif total < 180:
        print("  !! under three minutes -- the brief asks for at least three.")
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the demo narration to MP3s.")
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--rate", default="-6%", help="e.g. -8% to slow delivery")
    parser.add_argument("--pitch", default="+0Hz")
    parser.add_argument("--list-voices", action="store_true")
    args = parser.parse_args()

    if args.list_voices:
        print("\nSuggested voices for a technical demo:\n")
        for voice, description in SUGGESTED.items():
            print(f"  {voice:<24} {description}")
        print("\nFull list:  edge-tts --list-voices\n")
        return 0

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
