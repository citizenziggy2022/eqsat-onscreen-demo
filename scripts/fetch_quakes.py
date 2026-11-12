#!/usr/bin/env python3
"""EARTHQUAKES (Jason, Nov 12 2026 10:02 AM "yes" to H): EMSC (seismicportal.eu, CC BY 4.0) events of the last 30 days,
M2.5+, in a box around Ukraine, Moldova/Transnistria and Romania's Vrancea zone (felt in Odesa and Transnistria).
Writes quakes.json; runs at most every 10 min."""
import datetime as dt, json, os, sys, requests
OUT = "quakes.json"
EVERY_S = 10 * 60
URL = "https://www.seismicportal.eu/fdsnws/event/1/query"
def now(): return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def main(force=False):
    if not force and os.path.exists(OUT):
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(json.load(open(OUT))["checked_at"].replace("Z", "+00:00"))).total_seconds()
            if age < EVERY_S: print(f"quakes: checked {int(age)} s ago"); return
        except Exception: pass
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        r = requests.get(URL, params={"format": "json", "minlat": 43, "maxlat": 53, "minlon": 20, "maxlon": 41, "minmag": 2.5, "start": start, "limit": 200, "orderby": "time"}, timeout=60)
        r.raise_for_status(); feats = r.json().get("features", [])
    except Exception as e:
        print("quakes: EMSC not loaded, keeping last file", repr(e)[:120]); return
    ev = []
    for f in feats:
        p = f.get("properties", {})
        try: ev.append({"time": p["time"][:19] + "Z", "mag": round(float(p["mag"]), 1), "lat": round(float(p["lat"]), 2), "lon": round(float(p["lon"]), 2), "depth_km": round(float(p.get("depth") or 0)), "region": (p.get("flynn_region") or "").title(), "id": p.get("unid")})
        except Exception: continue
    data = {"note": "EMSC earthquakes, last 30 days, M2.5+, region around Ukraine/Moldova/Vrancea. Magnitudes as EMSC publishes them.",
            "source": "EMSC (seismicportal.eu), CC BY 4.0", "source_url": "https://www.emsc-csem.org/", "window_days": 30, "min_mag": 2.5,
            "checked_at": now(), "updated": now(), "count": len(ev), "count_m4": sum(1 for e in ev if e["mag"] >= 4), "events": ev}
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"quakes: {len(ev)} events, {data['count_m4']} M4+")
    import subprocess
    subprocess.run(["git", "add", OUT], check=False, timeout=60)
if __name__ == "__main__":
    main("--force" in sys.argv)
