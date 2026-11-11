#!/usr/bin/env python3
"""ioda.json for the eQsat screen's ІНТЕРНЕТ map (v2.11, Nov 11 2026; Jason's standing go 2:55 PM).

Internet signal per oblast from IODA (Internet Outage Detection and Analysis, Georgia Tech), fetched here in the
live-runner instead of in every screen. Two things per region:
  score  0-100: the latest BGP visibility and active-probing (ping /24) values against that region's own 7-day median
         (the same formula the screen used to run in the browser, lib/internetHeat.js)
  level  "critical" while IODA's own outage alert for the region is critical (latest alert of the last 24 h, any
         datasource), else "normal". The cause is never known: a power cut, damage or a shutdown look the same, so the
         screen says "internet signal", never "shutdown".
Run by the live-runner through fetch_news.py; throttles itself to every 5 min, keeps the last good file on failure,
stages ioda.json so the runner's commit carries it.
"""
import datetime as dt, json, statistics, subprocess, sys, time
import requests

API = "https://api.ioda.inetintel.cc.gatech.edu/v2"
OUT = "ioda.json"
EVERY_S = 300
UA = {"User-Agent": "eQsat-ioda/1.0 (emergency broadcast)"}
REGIONS = {4353: "crimea", 4354: "mykolaiv", 4355: "chernihiv", 4356: "rivne", 4357: "chernivtsi", 4358: "ivano",
           4359: "khmelnytskyi", 4360: "lviv", 4361: "ternopil", 4362: "zakarpattia", 4363: "volyn", 4364: "cherkasy",
           4365: "kirovohrad", 4366: "kyivobl", 4367: "odesa", 4368: "vinnytsia", 4369: "zhytomyr", 4370: "sumy",
           4371: "dnipro", 4372: "donetsk", 4373: "kharkiv", 4374: "luhansk", 4375: "poltava", 4376: "zaporizhzhia",
           4377: "kyivcity", 4378: "kherson", 4379: "sevastopol"}
CODES = ",".join(str(c) for c in REGIONS)
WEIGHTS = {"ping-slash24": 0.6, "bgp": 0.4}
FLOOR = {"ping-slash24": 0.5, "bgp": 0.7}
NORMAL = 0.97


def signals(ds, frm, until, max_points=None):
    p = {"from": frm, "until": until, "datasource": ds}
    if max_points:
        p["maxPoints"] = max_points
    r = requests.get(f"{API}/signals/raw/region/{CODES}", params=p, headers=UA, timeout=40)
    r.raise_for_status()
    out = {}
    for group in r.json().get("data") or []:
        for s in group or []:
            if s.get("datasource") == ds and not s.get("subtype"):
                out[str(s.get("entityCode"))] = s
    return out


def latest(s, n=3):
    if not s:
        return None
    pts = [(s["from"] + i * s["step"], v) for i, v in enumerate(s.get("values") or []) if isinstance(v, (int, float))]
    if not pts:
        return None
    tail = pts[-n:]
    return sum(v for _, v in tail) / len(tail), tail[-1][0]


def main(force=False):
    now = int(time.time())
    try:
        prev = json.load(open(OUT))
    except Exception:
        prev = {}
    if not force and prev.get("fetched_at"):
        try:
            age = now - dt.datetime.fromisoformat(prev["fetched_at"].replace("Z", "+00:00")).timestamp()
            if age < EVERY_S - 20:
                print(f"ioda: fetched {int(age)} s ago, next in {int(EVERY_S - age)} s")
                return
        except Exception:
            pass
    week_from, recent_from = now - 7 * 86400, now - 3 * 3600
    regions, newest = {}, 0
    try:
        week = {ds: signals(ds, week_from, now, 200) for ds in WEIGHTS}
        recent = {ds: signals(ds, recent_from, now) for ds in WEIGHTS}
        for code, key in REGIONS.items():
            total = weight = 0.0
            parts = {}
            for ds in WEIGHTS:
                ws, cur = week[ds].get(str(code)), latest(recent[ds].get(str(code)))
                if not ws or not cur:
                    continue
                hist = [v for i, v in enumerate(ws.get("values") or []) if isinstance(v, (int, float)) and ws["from"] + i * ws["step"] < recent_from]
                if not hist:
                    continue
                base = statistics.median(hist)
                if not base or base <= 0:
                    continue
                ratio = cur[0] / base
                s = min(1, max(0, (ratio - FLOOR[ds]) / (NORMAL - FLOOR[ds]))) * 100
                parts[ds] = {"ratio": round(ratio, 4), "score": round(s)}
                total += s * WEIGHTS[ds]
                weight += WEIGHTS[ds]
                newest = max(newest, cur[1])
            regions[key] = {"score": round(total / weight) if weight else None, "parts": parts, "level": "normal"}
        # IODA's own outage alerts, last 24 h: the latest alert per region and datasource decides
        r = requests.get(f"{API}/outages/alerts", params={"entityType": "region", "relatedTo": "country/UA", "from": now - 86400, "until": now}, headers=UA, timeout=40)
        r.raise_for_status()
        last = {}
        for a in r.json().get("data") or []:
            code = int((a.get("entity") or {}).get("code") or 0)
            if code not in REGIONS:
                continue
            k = (code, a.get("datasource"))
            if k not in last or a.get("time", 0) >= last[k].get("time", 0):
                last[k] = a
        for (code, ds), a in last.items():
            reg = regions.setdefault(REGIONS[code], {"score": None, "parts": {}, "level": "normal"})
            if a.get("level") == "critical":
                since = a.get("time")
                if reg["level"] != "critical" or since < reg.get("since", since + 1):
                    reg["since"] = since
                reg["level"] = "critical"
                reg.setdefault("critical_sources", []).append(ds)
            reg.setdefault("alerts", {})[ds] = {"level": a.get("level"), "time": a.get("time"), "value": a.get("value"), "history": a.get("historyValue")}
    except Exception as e:
        print("ioda: fetch failed, keeping the last file:", repr(e))
        return
    if not any(v.get("score") is not None for v in regions.values()):
        print("ioda: no usable signals, keeping the last file")
        return
    out = {
        "fetched_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at": dt.datetime.fromtimestamp(newest or now, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "IODA (Internet Outage Detection and Analysis), Georgia Tech, https://ioda.inetintel.cc.gatech.edu",
        "note": "Internet signal per oblast. score 0-100 vs the region's own 7-day median (BGP 40%, ping /24 60%); level critical while IODA's outage alert for the region is critical. The cause is unknown (power, damage or shutdown look the same).",
        "critical": sorted(k for k, v in regions.items() if v.get("level") == "critical"),
        "regions": regions,
    }
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"ioda: {len(regions)} regions, critical: {', '.join(out['critical']) or 'none'}")
    subprocess.run(["git", "add", OUT], check=False, timeout=30)


if __name__ == "__main__":
    main(force="--force" in sys.argv)
