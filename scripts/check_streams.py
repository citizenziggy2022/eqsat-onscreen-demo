#!/usr/bin/env python3
"""Daily health check of the radio station streams (the editor's saved list, config.json sections.stations).

GETs each enabled https stream URL and reads only the first bytes (icy streams never end). A station failing now
keeps/starts a fail_since stamp carried from the previous committed stream_health.json, so the editor's Stats tab
can flag stations unreachable for hours, not just today. Flags only: this never edits the station list; fixing or
removing a station stays a human edit in Content -> Radio audio. eqtv entries are skipped: eQtv has its own
multi-host failover and offline detection. Run daily from the live-runner; a total failure keeps the last file.
"""
import json
from datetime import datetime, timezone

import requests

OUT = "stream_health.json"
UA = {"User-Agent": "eqsat-screen-runner/1.0", "Icy-MetaData": "0"}


def iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    cfg = json.load(open("config.json", encoding="utf-8"))
    stations = [s for s in (cfg.get("sections") or {}).get("stations") or []
                if s.get("enabled", True) and s.get("kind", "stream") == "stream" and str(s.get("url", "")).startswith("https://")]
    try:
        prev = {s.get("id"): s for s in json.load(open(OUT, encoding="utf-8")).get("stations", [])}
    except Exception:
        prev = {}
    out = []
    for s in stations:
        ok = False
        err = None
        try:
            r = requests.get(s["url"], headers=UA, timeout=10, stream=True)
            ok = r.status_code == 200 and bool(next(r.iter_content(256), None))
            if not ok:
                err = f"HTTP {r.status_code}"
            r.close()
        except Exception as exc:
            err = str(exc)[:100]
        row = {"id": s.get("id"), "en": s.get("en"), "uk": s.get("uk"), "url": s.get("url"), "ok": ok, "error": err}
        if not ok:
            row["fail_since"] = (prev.get(s.get("id")) or {}).get("fail_since") or iso()
        out.append(row)
    json.dump({"generated_at": iso(), "stations": out}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"streams: {sum(1 for s in out if s['ok'])}/{len(out)} ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
