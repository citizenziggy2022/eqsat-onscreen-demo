#!/usr/bin/env python3
"""airquality.json for the eQsat screen's ПОВІТРЯ / AIR QUALITY map view (v2.26, Nov 11 2026; Jason's queue item I).

US EPA AQI (NowCast, PM2.5: the index SaveEcoBot computes for each air-quality station) at a short list of named
cities only: Kyiv, Kharkiv, Dnipro, Zaporizhzhia, Odesa, Mykolaiv, Kherson, Lviv, and the radiation sites' towns that
have SaveEcoBot air sensors (Nikopol, Marhanets, Chornobyl, Yuzhnoukrainsk, Varash, Netishyn). Each city is the median
of its fresh stations; a city with no fresh station is written with aqi null (the screen shows "—", never a stale number).

Source: the SaveEcoBot API /api/v1/sensors (server side only; key in the SAVEECOBOT_KEY Actions secret, sent as a header,
never written or printed). Their terms: every view credits "Data were obtained from the SaveEcoBot system" with a link
and the stations' networks; only these selected cities are ever written here (no mirroring, no full map, no
interpolation), and no alerting: band names are the US EPA's own, nothing on the screen declares anything.

Run by the live-runner through fetch_news.py; throttles itself to about once an hour (the AQI is hourly). Stages
airquality.json so the runner's commit carries it; a failure keeps the last file.
"""
import datetime as dt, json, os, statistics, subprocess, sys
from zoneinfo import ZoneInfo
import requests

OUT = "airquality.json"
EVERY_S = 55 * 60
BASE = "https://api.saveecobot.com/api/v1/sensors"
KYIV = ZoneInfo("Europe/Kyiv")
UA = "eQsat-live-runner/1.0 (+https://citizenziggy2022.github.io/eqsat-onscreen-demo/)"
MAX_AGE_H = 3
AIR_TYPE = 1  # "Станція моніторингу якості повітря" (type 3 is the radiation posts)

CITIES = [
    {"id": "kyiv", "city": "Київ", "uk": "Київ", "en": "Kyiv", "lat": 50.450, "lon": 30.523},
    {"id": "kharkiv", "city": "Харків", "uk": "Харків", "en": "Kharkiv", "lat": 49.994, "lon": 36.231},
    {"id": "dnipro", "city": "Дніпро", "uk": "Дніпро", "en": "Dnipro", "lat": 48.465, "lon": 35.046},
    {"id": "zaporizhzhia", "city": "Запоріжжя", "uk": "Запоріжжя", "en": "Zaporizhzhia", "lat": 47.838, "lon": 35.139},
    {"id": "odesa", "city": "Одеса", "uk": "Одеса", "en": "Odesa", "lat": 46.482, "lon": 30.723},
    {"id": "mykolaiv", "city": "Миколаїв", "uk": "Миколаїв", "en": "Mykolaiv", "lat": 46.975, "lon": 31.995},
    {"id": "kherson", "city": "Херсон", "uk": "Херсон", "en": "Kherson", "lat": 46.636, "lon": 32.617},
    {"id": "lviv", "city": "Львів", "uk": "Львів", "en": "Lviv", "lat": 49.839, "lon": 24.030},
    # the radiation view's towns that have SaveEcoBot air stations (Rozdilna has none)
    {"id": "nikopol", "city": "Нікополь", "uk": "Нікополь", "en": "Nikopol", "lat": 47.567, "lon": 34.397, "rad": True},
    {"id": "marhanets", "city": "Марганець", "uk": "Марганець", "en": "Marhanets", "lat": 47.643, "lon": 34.628, "rad": True},
    {"id": "chornobyl", "city": "Чорнобиль", "uk": "Чорнобиль", "en": "Chornobyl", "lat": 51.273, "lon": 30.222, "rad": True},
    {"id": "yuzhnoukrainsk", "city": "Южноукраїнськ", "uk": "Южноукраїнськ", "en": "Yuzhnoukrainsk", "lat": 47.830, "lon": 31.170, "rad": True},
    {"id": "varash", "city": "Вараш", "uk": "Вараш", "en": "Varash", "lat": 51.345, "lon": 25.850, "rad": True},
    {"id": "netishyn", "city": "Нетішин", "uk": "Нетішин", "en": "Netishyn", "lat": 50.340, "lon": 26.640, "rad": True},
]
# US EPA AQI bands, their own names (no other wording)
BANDS = [
    {"max": 50, "id": "good", "en": "Good", "uk": "Добре"},
    {"max": 100, "id": "moderate", "en": "Moderate", "uk": "Помірне"},
    {"max": 150, "id": "usg", "en": "Unhealthy for Sensitive Groups", "uk": "Шкідливе для чутливих груп"},
    {"max": 200, "id": "unhealthy", "en": "Unhealthy", "uk": "Шкідливе"},
    {"max": 300, "id": "very_unhealthy", "en": "Very Unhealthy", "uk": "Дуже шкідливе"},
    {"max": 10**9, "id": "hazardous", "en": "Hazardous", "uk": "Небезпечне"},
]


def now_utc():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z") if t else None


def scrub(text, key):
    text = str(text)
    return text.replace(key, "***") if key else text


def band(v):
    return next(b["id"] for b in BANDS if v <= b["max"]) if v is not None else None


def seb_time(s, now):
    # SaveEcoBot writes Kyiv wall-clock time with a "Z" on it (read 2026-11-11: "…T01:00:00.000000Z" at 23:08 UTC);
    # read it as Kyiv time, and as UTC only if the Kyiv reading would be in the future
    if not s:
        return None
    naive = dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    kyiv = naive.replace(tzinfo=KYIV)
    return kyiv if kyiv <= now + dt.timedelta(minutes=10) else naive.replace(tzinfo=dt.timezone.utc)


def all_sensors(key):
    out, page = [], 1
    while True:
        r = requests.get(BASE, params={"page[size]": 500, "page[number]": page}, headers={"apikey": key, "User-Agent": UA, "Accept": "application/vnd.api+json"}, timeout=50)
        r.raise_for_status()
        j = r.json()
        out += j.get("data") or []
        last = ((j.get("meta") or {}).get("page") or {}).get("last-page") or 1
        if page >= last or page >= 20:
            return out
        page += 1


def build(key, now):
    sensors = [s.get("attributes") or {} for s in all_sensors(key)]
    by_city = {}
    for a in sensors:
        if a.get("type_id") != AIR_TYPE or not a.get("city_name"):
            continue
        by_city.setdefault(a["city_name"], []).append(a)
    cities, newest = [], None
    for c in CITIES:
        fresh = []
        for a in by_city.get(c["city"], []):
            if a.get("aqi") is None or a.get("aqi_is_old"):
                continue
            t = seb_time(a.get("aqi_updated_at"), now)
            if not t or (now - t).total_seconds() > MAX_AGE_H * 3600:
                continue
            fresh.append((a, t))
        aqi = round(statistics.median([a["aqi"] for a, _ in fresh])) if fresh else None
        at = max(t for _, t in fresh) if fresh else None
        if at and (newest is None or at > newest):
            newest = at
        nets = {}
        for a, _ in fresh:
            p = " ".join((a.get("platform_name") or "").split())
            if p:
                nets[p] = nets.get(p, 0) + 1
        cities.append({
            "id": c["id"], "uk": c["uk"], "en": c["en"], "lat": c["lat"], "lon": c["lon"], "rad_site": bool(c.get("rad")),
            "aqi": aqi, "band": band(aqi), "stations": len(fresh), "stations_listed": len(by_city.get(c["city"], [])),
            "networks": [p for p, _ in sorted(nets.items(), key=lambda kv: -kv[1])][:4], "updated_at": iso(at),
        })
    return cities, newest


def main(force=False):
    now = now_utc()
    try:
        prev = json.load(open(OUT))
    except Exception:
        prev = {}
    if not force and prev.get("fetched_at"):
        age = (now - dt.datetime.fromisoformat(prev["fetched_at"].replace("Z", "+00:00"))).total_seconds()
        if age < EVERY_S:
            print(f"airquality: fetched {int(age)} s ago")
            return
    key = (os.environ.get("SAVEECOBOT_KEY") or "").strip()
    if not key:
        print("airquality: no SAVEECOBOT_KEY in this run, keeping the last file")
        return
    try:
        cities, newest = build(key, now)
        if not any(c["aqi"] is not None for c in cities):
            raise RuntimeError("no fresh AQI in any city")
    except Exception as e:
        print("airquality: SaveEcoBot failed, keeping the last file:", scrub(repr(e), key)[:300])
        return
    out = {
        "fetched_at": iso(now),
        "updated_at": iso(newest),
        "source": {
            "id": "saveecobot", "name": "SaveEcoBot",
            "credit": "Data were obtained from the SaveEcoBot system",
            "credit_uk": "Дані отримано із системи SaveEcoBot",
            "url": "https://www.saveecobot.com/maps", "short_url": "saveecobot.com",
        },
        "index": "US EPA AQI (NowCast, PM2.5), per station as SaveEcoBot computes it; each city is the median of its fresh stations (≤ 3 h)",
        "bands": [{k: b[k] for k in ("id", "en", "uk")} | {"max": b["max"] if b["max"] < 10**9 else None} for b in BANDS],
        "cities": cities,
    }
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if key in text:
        print("airquality: refusing to write (key found in output)")
        return
    tmp = OUT + ".tmp"
    open(tmp, "w").write(text)
    os.replace(tmp, OUT)
    print("airquality:", ", ".join(f"{c['id']} {c['aqi']}({c['stations']})" for c in cities))
    subprocess.run(["git", "add", OUT], check=False, timeout=30)


if __name__ == "__main__":
    main(force="--force" in sys.argv)
