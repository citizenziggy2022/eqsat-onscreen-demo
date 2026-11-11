#!/usr/bin/env python3
"""weather_warnings.json for the eQsat screen's ПОГОДА card (v2.12, Nov 11 2026; Jason's queue item B).

Official weather and sea warnings from Meteoalarm (EUMETNET), which carries the Ukrainian Hydrometeorological Center
(cap@meteo.gov.ua) and the Hydrometeorological Center of the Black and Azov Seas (seaodesa@meteo.gov.ua) as CAP, in
Ukrainian and English. Licence CC BY 4.0 (feeds.meteoalarm.org footer); credit "Meteoalarm / Укргідрометцентр".
Moldova's feed (State Hydrometeorological Service) is added only when it has warnings.

Active = not expired, not a Cancel, not superseded by a newer alert that references it. One entry per warning,
newest first (by sent time). The issuer's own words: event name and areas exactly as issued.
Run by the live-runner through fetch_news.py; throttles itself to every 10 min (the Ukraine feed is ~560 KB), keeps
the last good file when a feed fails, stages the file so the runner's commit carries it.
"""
import datetime as dt, json, re, subprocess, sys, time
import requests

OUT = "weather_warnings.json"
EVERY_S = 600
UA = {"User-Agent": "eQsat-weather/1.0 (emergency broadcast)"}
FEEDS = {"ukraine": "https://feeds.meteoalarm.org/api/v1/warnings/feeds-ukraine",
         "moldova": "https://feeds.meteoalarm.org/api/v1/warnings/feeds-moldova"}
LEVEL = {"yellow": "yellow", "orange": "orange", "red": "red", "green": "green"}
KYIV = dt.timezone(dt.timedelta(hours=2))  # Ukraine is on EET (UTC+2) in November


def parse(t):
    try:
        return dt.datetime.fromisoformat(t)
    except Exception:
        return None


def short_event(ev):
    return re.sub(r"\s+", " ", ev or "").strip()  # the issuer's words as issued


def tidy_area(a):
    a = re.sub(r"\s+", " ", a or "").strip().rstrip(",")
    return a


def when(onset, expires):
    o, e = parse(onset), parse(expires)
    if not o or not e:
        return ""
    o, e = o.astimezone(KYIV), e.astimezone(KYIV)
    if o.date() == e.date():
        return f"{o:%d.%m} {o:%H:%M}–{e:%H:%M}"
    return f"{o:%d.%m %H:%M} → {e:%d.%m %H:%M}"


def active(feed, country, now):
    ws = feed.get("warnings") or []
    superseded = set()
    for w in ws:
        for ref in ((w.get("alert") or {}).get("references") or "").split():
            parts = ref.split(",")
            superseded.add(parts[1] if len(parts) > 1 else parts[0])
    out = []
    for w in ws:
        a = w.get("alert") or {}
        if a.get("identifier") in superseded or a.get("msgType") == "Cancel" or a.get("status") != "Actual":
            continue
        infos = a.get("info") or []
        uk = [i for i in infos if str(i.get("language", "")).startswith("uk")]
        en = [i for i in infos if str(i.get("language", "")).startswith("en")]
        main = (uk or en or infos or [None])[0]
        if not main:
            continue
        exp = parse(main.get("expires"))
        if not exp or exp <= now:
            continue
        lvl = ""
        for p in main.get("parameter") or []:
            if p.get("valueName") == "awareness_level":
                m = re.search(r"(green|yellow|orange|red)", str(p.get("value")))
                lvl = m.group(1) if m else ""
        e0 = en[0] if en else {}
        out.append({
            "id": a.get("identifier"),
            "country": country,
            "sent": a.get("sent"),
            "onset": main.get("onset") or main.get("effective"),
            "expires": main.get("expires"),
            "level": lvl,
            "severity": main.get("severity"),
            "event_uk": short_event(main.get("event")) if uk else "",
            "event_en": e0.get("event") or (main.get("event") if not uk else ""),
            "area_uk": tidy_area(" ".join(x.get("areaDesc", "") for x in main.get("area") or [])) if uk else "",
            "area_en": tidy_area(" ".join(x.get("areaDesc", "") for x in e0.get("area") or [])),
            "when": when(main.get("onset") or main.get("effective"), main.get("expires")),
            "sender": main.get("senderName") or a.get("sender"),
        })
    return out


def main(force=False):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    try:
        prev = json.load(open(OUT))
    except Exception:
        prev = {}
    if not force and prev.get("fetched_at"):
        age = (now - dt.datetime.fromisoformat(prev["fetched_at"].replace("Z", "+00:00"))).total_seconds()
        if age < EVERY_S - 30:
            print(f"weather warnings: fetched {int(age)} s ago, next in {int(EVERY_S - age)} s")
            return
    warnings, ok = [], {}
    for country, url in FEEDS.items():
        try:
            r = requests.get(url, headers=UA, timeout=40)
            r.raise_for_status()
            warnings += active(r.json(), country, now)
            ok[country] = True
        except Exception as e:
            ok[country] = False
            print(f"weather warnings: {country} failed:", repr(e))
            # keep that country's last good warnings that have not expired
            warnings += [w for w in prev.get("warnings") or [] if w.get("country") == country and (parse(w.get("expires")) or now) > now]
    if not any(ok.values()):
        print("weather warnings: both feeds failed, keeping the last file")
        return
    warnings.sort(key=lambda w: (w.get("country") == "ukraine", w.get("sent") or "", w.get("onset") or ""), reverse=True)
    out = {
        "fetched_at": now.isoformat().replace("+00:00", "Z"),
        "source": "Meteoalarm (EUMETNET) / Укргідрометцентр, CC BY 4.0",
        "source_url": "https://meteoalarm.org/en/live/region/UA",
        "feeds_ok": ok,
        "warnings": warnings,
    }
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    counts = ", ".join("%s %d" % (k, sum(1 for w in warnings if w["country"] == k)) for k in FEEDS)
    print(f"weather warnings: {len(warnings)} active ({counts})")
    subprocess.run(["git", "add", OUT], check=False, timeout=30)


if __name__ == "__main__":
    main(force="--force" in sys.argv)
