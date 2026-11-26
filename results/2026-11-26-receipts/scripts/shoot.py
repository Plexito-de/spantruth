"""Screenshot backend pages, in a browser that aborts every request off loopback.

    uvx --with playwright==1.65.2 python -I shoot.py JOBS.json OUT_DIR

Each job: {"name", "url", "steps": [...]} where a step is {"click": text}, {"hover": text},
{"wait": ms} or {"fill": [placeholder, value]}. A text step clicks or hovers the first element that
shows that exact text. {"css": selector} clicks, {"xy": [x, y]} clicks and {"move": [x, y]}
moves the mouse at a point of the viewport. Each step waits "wait" ms after (default 1200), and a step
with "optional": true is skipped when its target is not on the page. A job may set "width", "height" and "settle" (ms). OUT_DIR/blocked.json lists each aborted request, by host.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

LOOPBACK = {"127.0.0.1", "localhost"}


def _step(page, step: dict) -> None:
    timeout = 3000 if step.get("optional") else 30000
    if "click" in step:
        page.get_by_text(step["click"], exact=True).first.click(timeout=timeout)
    elif "hover" in step:
        page.get_by_text(step["hover"], exact=True).first.hover(timeout=timeout)
    elif "css" in step:
        page.locator(step["css"]).first.click(timeout=timeout)
    elif "xy" in step:
        page.mouse.click(*step["xy"])
    elif "move" in step:
        page.mouse.move(*step["move"])
    elif "press" in step:
        page.keyboard.press(step["press"])


def main(jobs_path: str, out: str) -> int:
    jobs = json.loads(Path(jobs_path).read_text())
    Path(out).mkdir(parents=True, exist_ok=True)
    blocked: dict[str, list[str]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})

        def route(r):
            host = urlparse(r.request.url).hostname or ""
            if host in LOOPBACK or r.request.url.startswith(("data:", "blob:")):
                return r.continue_()
            blocked.setdefault(current[0], []).append(host)
            return r.abort()

        current = [""]
        ctx.route("**/*", route)
        page = ctx.new_page()
        for job in jobs:
            current[0] = job["name"]
            page.set_viewport_size({"width": job.get("width", 1440), "height": job.get("height", 900)})
            page.goto(job["url"], wait_until="load")
            page.wait_for_timeout(job.get("settle", 3000))
            for step in job.get("steps", []):
                try:
                    _step(page, step)
                except PlaywrightError:
                    # An optional step is a dialog or panel that is not always there.
                    if not step.get("optional"):
                        raise
                page.wait_for_timeout(step.get("wait", 1200))
            page.screenshot(path=f"{out}/{job['name']}.png", full_page=job.get("full", False))
            print(job["name"], "ok")
        browser.close()
    hosts = {k: sorted(set(v)) for k, v in blocked.items()}
    Path(out, "blocked.json").write_text(json.dumps(hosts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
