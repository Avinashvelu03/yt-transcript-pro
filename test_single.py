"""Quick test: try yt-dlp subtitle download with Edge cookies (Edge can be open)."""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
from yt_dlp import YoutubeDL

vid_id = "dQw4w9WgXcQ"

# Try each browser until one works
browsers = [("edge",), ("chrome",), ("firefox",)]
for browser in browsers:
    name = browser[0]
    print(f"\nTrying {name} cookies...")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writeautomaticsub": True,
            "writesubtitles": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "json3",
            "outtmpl": str(tmp_path / "%(id)s"),
            "cookiesfrombrowser": browser,
        }
        try:
            with YoutubeDL(opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={vid_id}"])
            sub_file = tmp_path / f"{vid_id}.en.json3"
            if sub_file.exists():
                data = json.loads(sub_file.read_text(encoding="utf-8"))
                events = data.get("events", [])
                texts = []
                for e in events:
                    for s in e.get("segs", []):
                        t = s.get("utf8", "").strip()
                        if t and t != "\n":
                            texts.append(t)
                print(f"  SUCCESS with {name}! {len(events)} events, {len(texts)} segments")
                print(f"  First 200 chars: {' '.join(texts[:15])[:200]}")
                break
            else:
                for path in tmp_path.iterdir():
                    print(f"  File in tmpdir: {path.name}")
                print(f"  No subtitle file found for {name}")
        except Exception as e:
            err = str(e)[:200]
            print(f"  Failed with {name}: {err}")
else:
    print("\nAll browsers failed. Please close Chrome and try again.")

print("\nDone.")
