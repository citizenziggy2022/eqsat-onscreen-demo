#!/usr/bin/env python3
"""NASA FIRMS active fires per oblast (Jason, Nov 12 2026: counts per oblast, ALL oblasts incl. front-line, NO dots).
Reads NASA's open 24-h CSVs (VIIRS NOAA-20, NOAA-21, MODIS; no key), keeps detections inside Ukraine's oblast shapes
(src/components/playout/ukraineRegions.js, same Web Mercator projection), merges the same spot seen by several satellites
(~1 km grid), and writes fires.json with a count per oblast only. No coordinates are written. Runs at most every 60 min."""
import csv, datetime as dt, io, json, math, os, re, sys
import requests
OUT = "fires.json"
EVERY_S = 60 * 60
SRC = {
    "VIIRS NOAA-20": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Europe_24h.csv",
    "VIIRS NOAA-21": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_Europe_24h.csv",
    "MODIS": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Europe_24h.csv",
}
REGIONS_JS = "regions.json"  # written once from ukraineRegions.js; [{key, rings:[[[x,y],...]]}]
def now(): return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def project(lat, lon):
    m = math.log(math.tan(math.pi / 4 + lat * math.pi / 360))
    return (lon * 54.37430626 - 1197.90258812, 3374.93959119 - 3131.86630543 * m)
def in_ring(p, ring):
    x, y = p; inside = False; j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]; xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi: inside = not inside
        j = i
    return inside
def region_of(p, regions):
    for r in regions:
        n = sum(1 for ring in r["rings"] if in_ring(p, ring))
        if n % 2 == 1: return r["key"]
    return None
def main(force=False):
    if not force and os.path.exists(OUT):
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(json.load(open(OUT))["checked_at"].replace("Z", "+00:00"))).total_seconds()
            if age < EVERY_S: print(f"fires: checked {int(age)} s ago"); return
        except Exception: pass
    regions = [r for r in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), REGIONS_JS))) if r["key"] != "transnistria"]
    regions.sort(key=lambda r: r["key"] not in ("kyivcity", "sevastopol"))  # small enclaves first
    seen, counts, used, newest, errors = set(), {}, [], None, []
    for name, url in SRC.items():
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "eQsat-onscreen/1.0"})
            r.raise_for_status()
        except Exception as e:
            errors.append(f"{name}: {repr(e)[:80]}"); continue
        rows = list(csv.DictReader(io.StringIO(r.text)))
        used.append({"satellite": name, "rows": len(rows)})
        for row in rows:
            try: lat, lon = float(row["latitude"]), float(row["longitude"])
            except Exception: continue
            if not (44.0 <= lat <= 52.5 and 22.0 <= lon <= 40.5): continue
            if row.get("confidence", "").lower() in ("l", "low"): continue
            cell = (round(lat / 0.01), round(lon / 0.01))
            if cell in seen: continue
            key = region_of(project(lat, lon), regions)
            if not key: continue
            seen.add(cell)
            counts[key] = counts.get(key, 0) + 1
            t = f'{row.get("acq_date","")}T{str(row.get("acq_time","")).zfill(4)[:2]}:{str(row.get("acq_time","")).zfill(4)[2:]}:00Z'
            if newest is None or t > newest: newest = t
    if not used:
        print("fires: no source loaded, keeping last fires.json", errors); return
    data = {
        "note": "NASA FIRMS active-fire detections, last 24 h, counted per oblast. Counts only, no locations (by design). "
                "A detection is a hot spot seen from space: wildfire, field burning, industrial heat, or fire after an attack. Low-confidence detections dropped; one spot seen by several satellites counts once.",
        "source": "NASA FIRMS (VIIRS NOAA-20/21, MODIS)",
        "source_url": "https://firms.modaps.eosdis.nasa.gov/",
        "window_hours": 24,
        "checked_at": now(),
        "updated": now(),
        "newest_detection": newest,
        "total": sum(counts.values()),
        "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "satellites": used,
        "errors": errors,
    }
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=2)
    print(f"fires: {data['total']} in {len(counts)} oblasts", errors)
    import subprocess
    subprocess.run(["git", "add", OUT], check=False, timeout=60)
if __name__ == "__main__":
    main("--force" in sys.argv)
