#!/usr/bin/env python3
"""radiation.json for the eQsat screen's РАДІАЦІЯ / RADIATION view (v2.14, Nov 11 2026; Jason's queue item 1).

Ambient gamma dose rate (nSv/h) at a handful of named sites only: the Zaporizhzhia NPP area (Nikopol, Marhanets and the
plant's daily IAEA figure), Chornobyl town, the ChNPP site, Kyiv, the South Ukraine / Rivne / Khmelnytskyi NPP towns and
Rozdilna (near Transnistria). Each site is the median of its fresh readings (automatic sensors preferred).

Source: the SaveEcoBot radiation API (server side only; key in the SAVEECOBOT_KEY Actions secret, sent as a header,
never written or printed). Their terms: every view credits "Data were obtained from the SaveEcoBot system" with a link
and each station's platform (network) and notes; only these selected sites are ever written here (no mirroring, no
full map), and no alerting: the screen shows values beside the normal background, it never declares anything.
Fallback when SaveEcoBot fails: Safecast realtime devices (CC0, tt.safecast.org/devices, CPM / 334 = uSv/h), labelled
"Safecast (backup)".

Run by the live-runner through fetch_news.py (about every 2 min); sensor details are re-read once a day into /tmp
(never committed). Stages radiation.json so the runner's commit carries it; a total failure keeps the last file.
"""
import datetime as dt, json, os, statistics, subprocess, sys, time
from zoneinfo import ZoneInfo
import requests

OUT = "radiation.json"
DETAILS = "/tmp/seb-sensors-details.json"
EVERY_S = 110
BASE = "https://api.saveecobot.com/api/v1/radiation-ukraine"
SAFECAST = "https://tt.safecast.org/devices"
KYIV = ZoneInfo("Europe/Kyiv")
UA = "eQsat-live-runner/1.0 (+https://citizenziggy2022.github.io/eqsat-onscreen-demo/)"
MAX_AGE_AUTO_H = 6      # automatic sensors (Ecocentre / zone data arrive ~3 h late)
MAX_AGE_MANUAL_H = 30   # manual / daily posts (MOH, Ukrhydromet, Energoatom-IAEA daily)
# ZNPP's own plant monitoring network: nothing since 2022-02-28 (before the occupation). Never used.
DEAD_PLATFORM = "Запорізька Атомна Електростанція"
DEAD_BEFORE = "2022-03-01"

# sensor ids read from SaveEcoBot's /sensors-details on 2026-11-11; lat/lon is the place, for the Safecast backup only
SITES = [
    {"id": "nikopol", "group": "znpp", "uk": "Нікополь", "en": "Nikopol", "sub_uk": "ЗАЕС, 16 км", "sub_en": "ZNPP area, 16 km",
     "sensors": [20720, 3887], "lat": 47.567, "lon": 34.397, "km": 15},
    {"id": "marhanets", "group": "znpp", "uk": "Марганець", "en": "Marhanets", "sub_uk": "ЗАЕС, 16 км", "sub_en": "ZNPP area, 16 km",
     "sensors": [24050, 24382], "lat": 47.643, "lon": 34.628, "km": 30},
    {"id": "znpp_site", "group": "znpp", "uk": "ЗАЕС, майданчик", "en": "ZNPP site", "sub_uk": "МАГАТЕ, щодоби", "sub_en": "IAEA, daily",
     "sensors": [23481], "daily": True, "lat": 47.512, "lon": 34.585, "km": 0},
    {"id": "chornobyl_town", "uk": "Чорнобиль", "en": "Chornobyl town", "sensors": [3713, 3739, 22415, 24281, 3797],
     "lat": 51.273, "lon": 30.222, "km": 3},
    {"id": "chnpp_site", "uk": "ЧАЕС, майданчик", "en": "ChNPP site", "zone": True,
     "sub_uk": "зона відчуження — високі значення звичні", "sub_en": "exclusion zone — high values normal",
     "sensors": [3734, 3733, 3730], "lat": 51.389, "lon": 30.099, "km": 6},
    {"id": "kyiv", "uk": "Київ", "en": "Kyiv", "sensors": [3779, 25998, 24276, 24279], "lat": 50.45, "lon": 30.523, "km": 12},
    {"id": "sunpp", "uk": "Южноукраїнськ", "en": "Yuzhnoukrainsk", "sub_uk": "Південноукр. АЕС", "sub_en": "South Ukraine NPP",
     "sensors": [24332, 25965, 20832, 23482], "lat": 47.83, "lon": 31.17, "km": 12},
    {"id": "rnpp", "uk": "Вараш", "en": "Varash", "sub_uk": "Рівненська АЕС", "sub_en": "Rivne NPP",
     "sensors": [24717, 20857, 23484], "lat": 51.33, "lon": 25.86, "km": 10},
    {"id": "khnpp", "uk": "Нетішин", "en": "Netishyn", "sub_uk": "Хмельницька АЕС", "sub_en": "Khmelnytskyi NPP",
     "sensors": [22877, 20908, 23483], "lat": 50.33, "lon": 26.65, "km": 10},
    {"id": "rozdilna", "uk": "Роздільна", "en": "Rozdilna", "sub_uk": "біля Придністров'я", "sub_en": "near Transnistria",
     "sensors": [25826, 3890], "lat": 46.845, "lon": 30.08, "km": 8},
]
COMMON = {
    "unit": "nSv/h",
    "normal_nsv_h": [80, 200],
    "note_uk": "Дані не верифіковані. Про надзвичайну ситуацію оголошують лише органи влади (Держатомрегулювання, ДСНС).",
    "note_en": "Unverified readings. Only the authorities (SNRIU, State Emergency Service) declare an emergency.",
}


def now_utc():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z") if t else None


def scrub(text, key):
    text = str(text)
    return text.replace(key, "***") if key else text


def seb(path, key):
    r = requests.get(BASE + path, headers={"apikey": key, "User-Agent": UA}, timeout=50)
    r.raise_for_status()
    return r.json()["data"]


def details(key, now):
    try:
        if now.timestamp() - os.stat(DETAILS).st_mtime < 86400:
            return json.load(open(DETAILS))
    except (OSError, ValueError):
        pass
    det = seb("/sensors-details", key)
    json.dump(det, open(DETAILS, "w"), ensure_ascii=False)
    return det


def from_saveecobot(key, now):
    meta = {d["sensor_id"]: d for d in details(key, now)}
    last = {d["sensor_id"]: d for d in seb("/sensors-last-data", key)}
    sites = []
    for s in SITES:
        readings = []
        for sid in s["sensors"]:
            m, v = meta.get(sid), last.get(sid)
            if not m or not v or v.get("gamma_nsv_h") is None or not v.get("updated_at"):
                continue
            if DEAD_PLATFORM in (m.get("platform_name") or "") or v["updated_at"] < DEAD_BEFORE:
                continue
            t = dt.datetime.strptime(v["updated_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KYIV)
            age_h = (now - t).total_seconds() / 3600
            manual = (m.get("measurement_type") or "").startswith("вручну")
            fresh = (not v.get("is_old")) and -0.2 <= age_h <= (MAX_AGE_MANUAL_H if manual else MAX_AGE_AUTO_H)
            readings.append({
                "sensor_id": sid,
                "station": " ".join((m.get("sensor_name") or "").split()),
                "city": m.get("city_name"),
                "nsv_h": round(float(v["gamma_nsv_h"]), 1),
                "time": iso(t),
                "fresh": fresh,
                "manual": manual,
                "platform": " ".join((m.get("platform_name") or "").split()) or None,
                "notes": " ".join((m.get("notes") or "").split()) or None,
            })
        fresh = [r for r in readings if r["fresh"]]
        use = [r for r in fresh if not r["manual"]] or fresh
        sites.append(site_row(s, use, readings))
    return sites


def site_row(s, use, readings):
    keep = {k: s[k] for k in ("id", "group", "uk", "en", "sub_uk", "sub_en", "daily", "zone") if k in s}
    med = statistics.median([r["nsv_h"] for r in use]) if use else None
    return {
        **keep,
        "nsv_h": int(round(med)) if med is not None else None,
        "time": max((r["time"] for r in use), default=None),
        "oldest": min((r["time"] for r in use), default=None),
        "n": len(use),
        "platforms": sorted({r["platform"] for r in use if r.get("platform")}),
        "notes": sorted({r["notes"] for r in use if r.get("notes")}),
        "readings": readings,
    }


def hav(a, b, c, d):
    from math import radians, sin, cos, asin, sqrt
    a, b, c, d = map(radians, (a, b, c, d))
    return 6371 * 2 * asin(sqrt(sin((c - a) / 2) ** 2 + cos(a) * cos(c) * sin((d - b) / 2) ** 2))


def from_safecast(now):
    r = requests.get(SAFECAST, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    devs = r.json()
    sites = []
    for s in SITES:
        readings = []
        if s.get("km"):
            for d in devs:
                try:
                    lat, lon, cpm = float(d["loc_lat"]), float(d["loc_lon"]), float(d["lnd_7318c"])
                    t = dt.datetime.fromisoformat(d["when_captured"].replace("Z", "+00:00"))
                except (KeyError, TypeError, ValueError):
                    continue
                if hav(s["lat"], s["lon"], lat, lon) > s["km"]:
                    continue
                age_h = (now - t).total_seconds() / 3600
                readings.append({
                    "device": d.get("device_urn"), "station": d.get("loc_name"), "nsv_h": round(cpm / 334 * 1000, 1),
                    "time": iso(t), "fresh": -0.2 <= age_h <= MAX_AGE_AUTO_H, "manual": False,
                    "platform": "Safecast", "notes": None,
                })
        use = [x for x in readings if x["fresh"]]
        sites.append(site_row(s, use, readings))
    return sites


def main(force=False):
    now = now_utc()
    try:
        prev = json.load(open(OUT))
    except Exception:
        prev = {}
    if not force and prev.get("fetched_at"):
        age = (now - dt.datetime.fromisoformat(prev["fetched_at"].replace("Z", "+00:00"))).total_seconds()
        if age < EVERY_S:
            print(f"radiation: fetched {int(age)} s ago")
            return
    key = (os.environ.get("SAVEECOBOT_KEY") or "").strip()
    sites, source, err = None, None, None
    if key:
        try:
            sites = from_saveecobot(key, now)
            if not any(s["nsv_h"] is not None for s in sites):
                raise RuntimeError("no fresh SaveEcoBot reading at any site")
            source = {
                "id": "saveecobot",
                "name": "SaveEcoBot",
                "credit": "Data were obtained from the SaveEcoBot system",
                "credit_uk": "Дані отримано із системи SaveEcoBot",
                "url": "https://www.saveecobot.com/radiation-maps",
                "short_url": "saveecobot.com",
            }
        except Exception as e:
            err = scrub(repr(e), key)[:300]
            sites = None
            print("radiation: SaveEcoBot failed:", err)
    else:
        err = "no SAVEECOBOT_KEY in this run"
        print("radiation:", err)
    if sites is None:
        try:
            sites = from_safecast(now)
            if not any(s["nsv_h"] is not None for s in sites):
                raise RuntimeError("no fresh Safecast reading at any site")
            source = {
                "id": "safecast",
                "name": "Safecast (backup)",
                "credit": "Safecast (backup) · CC0 · safecast.org",
                "credit_uk": "Safecast (резервне джерело) · CC0",
                "url": "https://safecast.org",
                "short_url": "safecast.org",
                "why": err,
            }
        except Exception as e:
            print("radiation: Safecast failed too, keeping the last file:", scrub(repr(e), key)[:300])
            return
    out = {"fetched_at": iso(now), "source": source, **COMMON, "sites": sites}
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if key and key in text:
        print("radiation: refusing to write (key found in output)")
        return
    tmp = OUT + ".tmp"
    open(tmp, "w").write(text)
    os.replace(tmp, OUT)
    vals = ", ".join(f"{s['id']} {s['nsv_h']}" for s in sites)
    print(f"radiation ({source['id']}): {vals}")
    subprocess.run(["git", "add", OUT], check=False, timeout=30)


if __name__ == "__main__":
    main(force="--force" in sys.argv)
