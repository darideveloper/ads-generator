"""Capture the operations mockup at high density.

Two shots from the same page: the full app (for the demo) and the ad view — fewer lanes,
fewer hours, larger type — because at 1080 a complete dashboard is unreadable.

    python mocks/panel-dia/shot.py
"""

from __future__ import annotations

import functools
import http.server
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = REPO / "capturas"

SHOTS = {
    # file: (query, css width, css height, device scale)
    "panel-dia.png": ("", 1440, 900, 2),
    "panel-dia-ad.png": ("?view=ad", 960, 505, 3),
}

BROWSERS = ("chromium", "chromium-browser", "google-chrome", "brave-browser", "chrome")


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - silence the server
        return


def serve(directory: Path):
    handler = functools.partial(Quiet, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def browser() -> str:
    for name in BROWSERS:
        path = shutil.which(name)
        if path:
            return path
    sys.exit("no chrome-family browser found")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    httpd = serve(REPO)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}/mocks/panel-dia/index.html"
    binary = browser()
    try:
        for name, (query, width, height, scale) in SHOTS.items():
            dest = OUT / name
            subprocess.run(
                [
                    binary,
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    f"--force-device-scale-factor={scale}",
                    f"--window-size={width},{height}",
                    f"--screenshot={dest}",
                    "--virtual-time-budget=3000",
                    f"{base}{query}",
                ],
                check=True,
                capture_output=True,
            )
            with Image.open(dest) as shot:
                print(f"{name}: {shot.size[0]}x{shot.size[1]}")
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
