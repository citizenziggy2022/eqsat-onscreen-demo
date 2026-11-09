#!/usr/bin/env python3
"""Writes blocked.json (OONI) and keepiton.json (Access Now #KeepItOn STOP data) at the Pages repo root.
Run by .github/workflows/feeds.yml every hour. Standard library only; no secrets."""
import csv, io, json, time, urllib.request
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "eqsat-onscreen-feeds/1.0 (+https://citizenziggy2022.github.io/eqsat-onscreen-demo/)"}

def get(url, timeout=120):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()

def ooni(cc, since):
    url = ("https://api.ooni.io/api/v1/aggregation?probe_cc=%s&test_name=web_connectivity&axis_x=domain&since=%s" % (cc, since))
    rows = json.loads(get(url))["result"]
    out = []
    for r in rows:
        n, conf, anom = r.get("measurement_count") or 0, r.get("confirmed_count") or 0, r.get("anomaly_count") or 0
        if conf > 0:
            kind = "confirmed"
        elif n >= 5 and anom / n >= 0.6:
            kind = "anomaly"
        else:
            continue
        out.append({"domain": r["domain"], "cc": cc, "kind": kind, "confirmed": conf, "anomaly": anom, "measurements": n})
    out.sort(key=lambda x: (x["kind"] != "confirmed", -x["confirmed"], -x["anomaly"]))
    return out

def blocked():
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    items, total = [], {}
    for cc, cap in (("RU", 300), ("UA", 120)):  # long list (Jason, Nov 9 2026 5:10 PM); was 60 / 30
        rows = ooni(cc, since)
        total[cc] = {"confirmed": sum(1 for r in rows if r["kind"] == "confirmed"), "anomaly": sum(1 for r in rows if r["kind"] == "anomaly")}
        items += rows[:cap]
    return {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "since": since,
            "source": "OONI (ooni.org) web_connectivity, last 7 days; confirmed = block page seen, anomaly = >=60% of >=5 tests anomalous",
            "totals": total, "items": items}

STOP_CSV = "https://docs.google.com/spreadsheets/d/1DvPAuHNLp5BXGb0nnZDGNoiIwEeu2ogdXEIDvT4Hyfk/export?format=csv"
REGION = {"Russian Federation", "Russia", "Ukraine", "Belarus", "Moldova, Republic of", "Georgia", "Kazakhstan", "Azerbaijan", "Armenia"}

def keepiton():
    rows = list(csv.DictReader(io.StringIO(get(STOP_CSV).decode("utf-8", "replace"))))
    def when(s):
        try:
            return datetime.strptime(s.strip(), "%m/%d/%Y")
        except Exception:
            return None
    out = []
    for r in rows:
        if (r.get("country") or "").strip() not in REGION:
            continue
        d = when(r.get("start_date") or "")
        out.append({"date": d.strftime("%Y-%m-%d") if d else (r.get("start_date") or ""),
                    "country": "Russia" if r["country"].startswith("Russia") else r["country"].strip(),
                    "area": (r.get("area_name") or "").strip(), "type": (r.get("shutdown_type") or "").strip(),
                    "status": (r.get("shutdown_status") or "").strip(), "cause": (r.get("actual_cause") or "").strip()})
    out.sort(key=lambda x: x["date"], reverse=True)
    return {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "Access Now #KeepItOn STOP dataset (latest published sheet)", "items": out[:40]}

if __name__ == "__main__":
    for name, fn in (("blocked.json", blocked), ("keepiton.json", keepiton)):
        try:
            data = fn()
            with open(name, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            print(name, len(data["items"]))
        except Exception as e:  # keep the last good file
            print(name, "failed:", e)
