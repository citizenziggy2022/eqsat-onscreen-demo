#!/usr/bin/env python3
"""blocked_history.py -> blocked_history.json for the eQsat screen's BLOCKED line ("available now" marker).

Jason, Nov 9 2026 5:10 PM: "if one was blocked before show it as available now if it is available now."
Per domain and country it keeps when the domain was last measured BLOCKED and when it was last measured OK.
Measured data only, nothing is guessed:
  OONI web_connectivity (RU, UA): the last 24 h aggregation per domain
     blocked = a confirmed block page, or >=60% anomalous of >=5 tests (same rule as blocked.json)
     ok      = >=5 tests, no confirmed block page, <20% anomalous
  Censored Planet HTTPS (RU, UA, BY, MD): the last 2 days of the dashboard rows fetch_blocked_extra.py already read
     blocked = a block page, or the 'likely' rule; ok = >=5 measurements, no block page, <20% unexpected
  every (domain, country) blocked.json / blocked_extra.json list now counts as blocked unless it is measured ok now.
A domain is "available now" when it was blocked in an earlier snapshot, its newest measurement is ok, and that ok
reading has >=5 measurements and <20% anomalies. available_since = the first ok after the last block; the screen shows
the green marker for 24 h from then. A domain measured blocked again loses it at once.
Called at the end of fetch_blocked_extra.py (live-runner, hourly). Stages the file with git add so the runner's
existing commit picks it up. Keeps the last good file if OONI fails.
"""
import json, os, subprocess, sys
from datetime import datetime, timedelta, timezone

import requests

UA = {"User-Agent": "eqsat-onscreen-feeds/1.0 (+https://citizenziggy2022.github.io/eqsat-onscreen-demo/)"}
OUT = "blocked_history.json"
MIN_N = 5
MAX_OK = 0.2
KEEP_DAYS = 30  # drop pairs not measured blocked or ok for 30 days


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(d):
    d = str(d or "").strip().lower().rstrip(".")
    return d[4:] if d.startswith("www.") else d


def ooni_24h(cc):
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    url = "https://api.ooni.io/api/v1/aggregation"
    r = requests.get(url, params={"probe_cc": cc, "test_name": "web_connectivity", "axis_x": "domain", "since": since},
                     headers=UA, timeout=120)
    r.raise_for_status()
    out = {}
    for row in r.json().get("result", []):
        n, conf, anom = row.get("measurement_count") or 0, row.get("confirmed_count") or 0, row.get("anomaly_count") or 0
        d = norm(row.get("domain"))
        if not d or n == 0:
            continue
        if conf > 0 or (n >= MIN_N and anom / n >= 0.6):
            st = "blocked"
        elif n >= MIN_N and conf == 0 and anom / n < MAX_OK:
            st = "ok"
        else:
            continue
        prev = out.get(d)
        # several hosts (www / bare) fold into one domain: any block wins, ok needs every host ok
        if prev and (prev[0] == "blocked" or st == "blocked"):
            out[d] = ("blocked", n + prev[1], (anom + prev[2] * prev[1]) / (n + prev[1]))
        elif prev:
            out[d] = ("ok", n + prev[1], (anom + prev[2] * prev[1]) / (n + prev[1]))
        else:
            out[d] = (st, n, anom / n)
    return out


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def update(cp_recent=None, ooni_path="blocked.json", extra_path="blocked_extra.json", out_path=OUT):
    now = now_iso()
    old = load(out_path) or {}
    items = old.get("items") or {}
    readings = {}  # key -> ("blocked"|"ok", n, share, source)
    ooni_ok = True
    for cc in ("RU", "UA"):
        try:
            for d, (st, n, share) in ooni_24h(cc).items():
                readings["%s:%s" % (cc, d)] = (st, n, share, "ooni")
        except Exception as e:
            ooni_ok = False
            print("history: OONI %s failed: %s" % (cc, e))
    for (cc, d), (st, n, share) in (cp_recent or {}).items():
        k = "%s:%s" % (cc, norm(d))
        prev = readings.get(k)
        if prev and (prev[0] == "blocked" or st != "blocked"):
            continue  # OONI's in-country reading wins unless Censored Planet sees a block it did not
        readings[k] = (st, n, share, "censoredplanet")
    # everything on the current lists is blocked unless measured ok right now
    listed, measured = set(), {}
    ooni_doc, extra_doc = load(ooni_path) or {}, load(extra_path) or {}
    for it in ooni_doc.get("items", []):
        k = "%s:%s" % (it.get("cc"), norm(it.get("domain")))
        listed.add(k)
        measured[k] = ooni_doc.get("since") or now  # OONI 7-day window start
    for it in extra_doc.get("items", []):
        k = "%s:%s" % (it.get("country"), norm(it.get("domain")))
        listed.add(k)
        if it.get("source") == "censoredplanet":  # a measured block (the RKN registry is a list, not a measurement)
            measured.setdefault(k, (((extra_doc.get("sources") or {}).get("censoredplanet") or {}).get("window") or {}).get("startDate") or now)
    for k in listed:
        if k not in readings:
            readings[k] = ("blocked", None, None, "list")

    for k, (st, n, share, src) in readings.items():
        cc, d = k.split(":", 1)
        h = items.get(k)
        if st == "ok" and not h:
            if k not in measured:
                continue  # only domains measured blocked at some point are tracked
            # first sight: on this week's measured block list, measured ok in the newest readings
            h = items[k] = {"domain": d, "cc": cc, "last_blocked": measured[k], "seeded_from": "7-day block list"}
        h = items.setdefault(k, {"domain": d, "cc": cc})
        if st == "blocked":
            h["last_blocked"] = now
            h.pop("available_since", None)
        else:
            h["last_ok"] = now
            h["ok_n"] = n
            h["ok_anomaly_share"] = round(share, 3)
            h["ok_source"] = src
            if h.get("last_blocked") and h["last_blocked"] < now and not h.get("available_since"):
                h["available_since"] = now
    cut = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    items = {k: v for k, v in items.items() if max(v.get("last_blocked", ""), v.get("last_ok", "")) >= cut}
    avail = sorted(k for k, v in items.items() if v.get("available_since"))
    out = {"updated": now, "ooni_ok": ooni_ok,
           "rule": "available = blocked in an earlier snapshot, newest reading ok (>=5 measurements, <20% anomalies, no block page); shown 24 h from available_since",
           "counts": {"tracked": len(items), "available": len(avail)}, "available": avail, "items": items}
    if not ooni_ok and old:
        out["ooni_ok"] = False  # still written: Censored Planet / list readings are real
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=False)
    os.replace(tmp, out_path)
    print("history:", out["counts"], avail[:10])
    try:  # the live-runner's commit picks up whatever is staged
        subprocess.run(["git", "add", out_path], check=False, capture_output=True, timeout=30)
    except Exception:
        pass
    return out


if __name__ == "__main__":
    update()
    sys.exit(0)
