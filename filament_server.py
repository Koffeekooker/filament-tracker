#!/usr/bin/env python3
"""
Filament NFC – optional local server with automatic backup (for Termux)
=======================================================================
NOT needed when the app runs from GitHub Pages. Only if you start the app
on the phone via Termux instead: then this replaces "python -m http.server".

It does two things:
  1. Serve the app files (same folder as this script) like a web server.
  2. Accept backups from the app via a tiny HTTP API:
       POST /api/journal   append change entries (incremental)
       POST /api/snapshot  store a full copy of the data
       GET  /api/state     latest snapshot + later journal entries (restore)
       GET  /api/ping      "is the server there?"

Files written:
  backups/journal-YYYY-MM.jsonl     one JSON object per line per change
                                    (append-only, like a transaction log)
  backups/snapshot-YYYY-MM-DD.json  daily full copy, the last 30 are kept

Start in Termux:
  cd ~/storage/downloads/filament
  python filament_server.py
Then open in Chrome: http://localhost:8080/index.html
Only reachable from the phone itself (bound to 127.0.0.1), not from the LAN.
"""
import json      # read/write JSON text
import os        # file/path operations
import glob
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

# ---- configuration -------------------------------------------------------
PORT = 8080
BASE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE, "backups")
KEEP_SNAPSHOTS = 30
os.makedirs(BACKUP_DIR, exist_ok=True)   # create backups/ if missing


def latest_snapshot():
    """Return the newest snapshot as a dict, or None. File names contain the
    date as YYYY-MM-DD, so alphabetical order = chronological order."""
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "snapshot-*.json")))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def journal_entries():
    """Generator ("yield"): delivers all journal entries of all months one by
    one, oldest first, without loading everything into memory at once."""
    for path in sorted(glob.glob(os.path.join(BACKUP_DIR, "journal-*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        pass  # skip a half-written line (e.g. battery died mid-write)


def current_state():
    """Reconstruct the current state: newest snapshot, then replay every journal
    entry written after it (same principle as a database restore from a full
    backup plus transaction log). Spools are kept in a dict keyed by ID, so a
    later entry for the same spool simply replaces the earlier one."""
    snap = latest_snapshot() or {"settings": None, "spools": [], "exported": ""}
    since = snap.get("exported", "")
    spools = {s["id"]: s for s in snap.get("spools", []) if s.get("id")}
    settings = snap.get("settings")
    for e in journal_entries():
        if e.get("ts", "") <= since:
            continue
        op = e.get("op")
        if op == "spool" and e.get("data", {}).get("id"):
            spools[e["data"]["id"]] = e["data"]
        elif op == "delete":
            spools.pop(e.get("id"), None)
        elif op == "settings":
            settings = e.get("data")
    return {"app": "filament-nfc", "version": 2,
            "exported": datetime.now().astimezone().isoformat(),
            "settings": settings, "spools": list(spools.values())}


class Handler(SimpleHTTPRequestHandler):
    """Request handler. The base class already serves files from a directory;
    we only add the /api/ endpoints. Python calls do_GET/do_POST for each
    incoming HTTP request (one thread per request, see ThreadingHTTPServer)."""
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    def _json(self, code, obj):
        """Send a JSON answer with HTTP status code (200 = OK, 400 = bad request …)."""
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """API reads; everything else falls through to normal file serving."""
        if self.path == "/api/ping":
            snaps = sorted(glob.glob(os.path.join(BACKUP_DIR, "snapshot-*.json")))
            return self._json(200, {"ok": True,
                                    "lastSnapshot": os.path.basename(snaps[-1])[9:19] if snaps else None})
        if self.path == "/api/state":
            return self._json(200, current_state())
        return super().do_GET()

    def do_POST(self):
        """API writes. The request body is JSON sent by the app's fetch()."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            return self._json(400, {"ok": False, "error": str(e)})

        if self.path == "/api/journal":
            # Append entries; the app may send several queued ones at once.
            # flush + fsync = force the data onto the storage chip right now.
            entries = data if isinstance(data, list) else [data]
            path = os.path.join(BACKUP_DIR, f"journal-{datetime.now():%Y-%m}.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            return self._json(200, {"ok": True, "count": len(entries)})

        if self.path == "/api/snapshot":
            path = os.path.join(BACKUP_DIR, f"snapshot-{datetime.now():%Y-%m-%d}.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)  # atomic rename: never a half-written snapshot
            for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "snapshot-*.json")))[:-KEEP_SNAPSHOTS]:
                os.remove(old)
            return self._json(200, {"ok": True, "file": os.path.basename(path)})

        return self._json(404, {"ok": False, "error": "unbekannt"})

    def log_message(self, fmt, *args):
        """Console log: only API calls, not every icon/file request."""
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print(f"Filament NFC läuft: http://localhost:{PORT}/index.html")
    print(f"Sicherungen in: {BACKUP_DIR}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
