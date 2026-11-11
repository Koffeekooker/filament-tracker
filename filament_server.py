#!/usr/bin/env python3
"""
Filament NFC – lokaler Server mit automatischer Datensicherung
==============================================================
Ersetzt "python -m http.server". Liefert die App aus (gleicher Ordner wie
dieses Skript) und nimmt Sicherungen entgegen:

  backups/journal-JJJJ-MM.jsonl   inkrementell: jede Änderung eine Zeile
  backups/snapshot-JJJJ-MM-TT.json  täglicher Vollstand (die letzten 30 bleiben)

Start in Termux:
  cd ~/storage/downloads/filament
  python filament_server.py

Dann in Chrome: http://localhost:8080/index.html
Nur vom Handy selbst erreichbar (127.0.0.1), nicht aus dem WLAN.
"""
import json
import os
import glob
from datetime import datetime
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

PORT = 8080
BASE = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE, "backups")
KEEP_SNAPSHOTS = 30
os.makedirs(BACKUP_DIR, exist_ok=True)


def latest_snapshot():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "snapshot-*.json")))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def journal_entries():
    for path in sorted(glob.glob(os.path.join(BACKUP_DIR, "journal-*.jsonl"))):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        pass  # halbe Zeile (z.B. Akku leer) überspringen


def current_state():
    """Neuester Snapshot + alle späteren Journal-Einträge = aktueller Stand."""
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
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=BASE, **kw)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/ping":
            snaps = sorted(glob.glob(os.path.join(BACKUP_DIR, "snapshot-*.json")))
            return self._json(200, {"ok": True,
                                    "lastSnapshot": os.path.basename(snaps[-1])[9:19] if snaps else None})
        if self.path == "/api/state":
            return self._json(200, current_state())
        return super().do_GET()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            return self._json(400, {"ok": False, "error": str(e)})

        if self.path == "/api/journal":
            # Liste von Änderungen anhängen (App schickt ggf. mehrere nachgeholte auf einmal)
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
            os.replace(tmp, path)  # atomar: nie eine halbe Datei
            for old in sorted(glob.glob(os.path.join(BACKUP_DIR, "snapshot-*.json")))[:-KEEP_SNAPSHOTS]:
                os.remove(old)
            return self._json(200, {"ok": True, "file": os.path.basename(path)})

        return self._json(404, {"ok": False, "error": "unbekannt"})

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    print(f"Filament NFC läuft: http://localhost:{PORT}/index.html")
    print(f"Sicherungen in: {BACKUP_DIR}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
