#!/usr/bin/env python3
"""Build the static GitHub Pages demo from fictional seed data.

Seeds a throwaway database with scripts/seed_demo.py (every employer, sender and
snippet is invented), records the real API's read responses as JSON files, and
copies the web UI next to them with a read-only api.js. No mailbox, token,
settings file, or .env from this machine is read: the app runs in an empty
temporary home and working directory with secret-looking variables removed.

Usage: python scripts/build_demo.py OUT_DIR
"""

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "jobtrack" / "web"

DEMO_API = """\
// Read-only demo client: answers from JSON snapshots recorded by scripts/build_demo.py.
const READ_ONLY =
  "This demo is read-only and uses fictional data. Run jobtrack locally to sync your own Gmail.";
const file = (path) =>
  "./data/" + path.replace(/^\\/api\\//, "").replace(/[^a-zA-Z0-9]+/g, "_") + ".json";

export async function api(path, options = {}) {
  if ((options.method || "GET").toUpperCase() !== "GET") throw new Error(READ_ONLY);
  if (path.includes("as_of=")) throw new Error("History dates are not available in the demo.");
  const response = await fetch(file(path));
  if (!response.ok) throw new Error("Not available in the demo.");
  return response.json();
}
export const write = () => Promise.reject(new Error(READ_ONLY));
"""

BANNER = (
    '<div style="position:fixed;bottom:12px;right:12px;z-index:999;max-width:340px;'
    "padding:8px 12px;border-radius:8px;background:#111;color:#fff;"
    'font:12px/1.4 system-ui,sans-serif">Demo with fictional applications. '
    "Nothing here comes from a real inbox, and changes are disabled.</div>"
)


def snapshot_name(path: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", re.sub(r"^/api/", "", path)) + ".json"


def main(out: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="jobtrack-demo-"))
    os.environ["JOBTRACK_HOME"] = str(work / "home")
    for name in list(os.environ):
        if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name):
            del os.environ[name]
    os.chdir(work)  # settings read ./.env; make sure there is none

    sys.path.insert(0, str(ROOT / "scripts"))
    import seed_demo
    from fastapi.testclient import TestClient

    from jobtrack.web import create_app

    db = work / "demo.db"
    seed_demo.main(str(db))
    client = TestClient(create_app(db_path=db, llm_mode="off"))

    paths = [
        "/api/false-positives",
        "/api/sync",
        "/api/sync/history",
        "/api/settings",
        "/api/health",
        "/api/reviews",
    ]
    for category in ("employment", "freelance"):
        paths += [
            f"/api/{p}?category={category}"
            for p in ("applications", "overview", "activity", "insights")
        ]
        apps = client.get(f"/api/applications?category={category}").json()["applications"]
        paths += [f"/api/applications/{a['id']}" for a in apps]

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(WEB, out)
    (out / "data").mkdir()
    for path in paths:
        response = client.get(path)
        response.raise_for_status()
        (out / "data" / snapshot_name(path)).write_text(json.dumps(response.json()))

    (out / "api.js").write_text(DEMO_API)
    index = (out / "index.html").read_text(encoding="utf-8")
    index = index.replace('"/assets/', '"./').replace("</body>", BANNER + "</body>")
    (out / "index.html").write_text(index, encoding="utf-8")
    shutil.rmtree(work)
    print(f"wrote {len(paths)} snapshots to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
