#!/usr/bin/env python3
"""Daily map history rollup (user approved Nov 28 2026: days/weeks of history for the map boxes in admin Stats).

The map data files (fires.json, airquality.json, radiation.json, power-cuts.json, border-queues.json) only hold "now".
This script samples them (self-throttled to every 20 min) and keeps small per-Kyiv-day aggregates in maphist.json:
  air     per city id:  max / min / sum / n of AQI
  rad     per site id:  max / sum / n of nSv/h and how many samples were above the normal range
  border  trucks_max / trucks_sum / n, longest wait (hours), per-oblast max trucks
  fires   total_max and per-oblast max counts (24 h window counts)
  power   per oblast: samples in emergency cuts / limited supply
Keeps 120 days. Reads local files only (no network, no secrets). Never touches any other file; a failure keeps the last file.
Run by the live-runner loop and staged with the other data files.
  python3 scripts/map_history.py            normal run (throttled)
  python3 scripts/map_history.py --force    sample now
"""
import datetime as dt, json, os, sys
from zoneinfo import ZoneInfo

OUT = "maphist.json"
KYIV = ZoneInfo("Europe/Kyiv")
KEEP_DAYS = 120
EVERY_S = 1200


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def bump(d, key, v, over=None):
    """aggregate a number into d[key] = {max,min,sum,n[,over]}"""
    v = num(v)
    if v is None:
        return
    a = d.setdefault(key, {"max": v, "min": v, "sum": 0, "n": 0})
    a["max"] = max(a["max"], v)
    a["min"] = min(a["min"], v)
    a["sum"] += v
    a["n"] += 1
    if over is not None and v > over:
        a["over"] = a.get("over", 0) + 1


def sample(day, root="."):
    air = load(os.path.join(root, "airquality.json"))
    if air:
        for c in air.get("cities") or []:
            bump(day.setdefault("air", {}), c.get("id"), c.get("aqi"))
    rad = load(os.path.join(root, "radiation.json"))
    if rad:
        lim = (rad.get("normal_nsv_h") or [80, 200])[1]
        for s in rad.get("sites") or []:
            bump(day.setdefault("rad", {}), s.get("id"), s.get("nsv_h"), over=lim)
    bq = load(os.path.join(root, "border-queues.json"))
    if bq:
        b = day.setdefault("border", {})
        q = bq.get("queue") or []
        tot = sum(e.get("trucks") or 0 for e in q)
        b["trucks_max"] = max(b.get("trucks_max", 0), tot)
        b["trucks_sum"] = b.get("trucks_sum", 0) + tot
        b["n"] = b.get("n", 0) + 1
        b["wait_max_h"] = max([b.get("wait_max_h", 0)] + [e.get("hours") or 0 for e in q])
        ob = {}
        for e in q:
            ob[e.get("oblast")] = ob.get(e.get("oblast"), 0) + (e.get("trucks") or 0)
        mo = b.setdefault("oblast_max", {})
        for k, v in ob.items():
            if k:
                mo[k] = max(mo.get(k, 0), v)
    fi = load(os.path.join(root, "fires.json"))
    if fi:
        f = day.setdefault("fires", {"total_max": 0, "counts_max": {}, "n": 0})
        f["total_max"] = max(f["total_max"], num(fi.get("total")) or 0)
        f["n"] += 1
        for k, v in (fi.get("counts") or {}).items():
            f["counts_max"][k] = max(f["counts_max"].get(k, 0), num(v) or 0)
    pw = load(os.path.join(root, "power-cuts.json"))
    if pw:
        p = day.setdefault("power", {})
        for k, v in (pw.get("regions") or {}).items():
            a = p.setdefault(k, {"outage": 0, "limited": 0})
            st = (v or {}).get("status")
            if st in ("outage", "limited"):
                a[st] += 1
        p["_n"] = p.get("_n", 0) + 1
    day["n"] = day.get("n", 0) + 1


def main(force=False, root=".", out=OUT, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    cur = load(out) or {}
    last = cur.get("last_sample")
    if last and not force:
        try:
            lt = dt.datetime.fromisoformat(last.replace("Z", "+00:00"))
            if (now - lt).total_seconds() < EVERY_S:
                return False
        except Exception:
            pass
    days = cur.get("days") or {}
    key = now.astimezone(KYIV).strftime("%Y-%m-%d")
    sample(days.setdefault(key, {}), root)
    first = (now.astimezone(KYIV) - dt.timedelta(days=KEEP_DAYS - 1)).strftime("%Y-%m-%d")
    days = {k: v for k, v in days.items() if k >= first}
    doc = {"note": "Daily rollups of the map data files, sampled about every 20 min by scripts/map_history.py. Kyiv days. Built from the day this file was created; no backfill.",
           "interval_min": EVERY_S // 60, "last_sample": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "started": cur.get("started") or key, "days": days}
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)
    return True


if __name__ == "__main__":
    try:
        main(force="--force" in sys.argv)
    except Exception as e:  # keep the last file
        print(f"map_history failed: {e}", file=sys.stderr)
        sys.exit(1)
