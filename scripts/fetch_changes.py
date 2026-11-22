#!/usr/bin/env python3
"""Map change summaries (Nov 22 2026): keeps a short history of REAL readings so the screen can say what changed.
Reads fires.json, ioda.json and water.json (Ukrenergo power cuts, kind "power" only) as they are now and appends one
snapshot to changes.json at most every 30 minutes; snapshots older than 26 h are dropped. Counts and statuses only: no
coordinates, no text from sources. A source that is missing, empty or of another kind is left out of that snapshot
(nothing is invented, and zero readings are never treated as "no incidents"). Runs inside fetch_news.py's loop."""
import datetime as dt, json, os, subprocess
OUT = "changes.json"
EVERY_S = 30 * 60
KEEP_S = 26 * 3600
def now(): return dt.datetime.now(dt.timezone.utc)
def iso(t): return t.strftime("%Y-%m-%dT%H:%M:%SZ")
def load(name):
    try:
        with open(name) as f: return json.load(f)
    except Exception: return None
def parse(s):
    try: return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception: return None
def snapshot():
    snap = {"t": iso(now())}
    f = load("fires.json")
    if f and parse(f.get("checked_at")) and isinstance(f.get("counts"), dict) and isinstance(f.get("total"), int):
        snap["fires"] = {"at": f["checked_at"], "total": f["total"], "counts": f["counts"]}
    i = load("ioda.json")
    regs = (i or {}).get("regions")
    if i and parse(i.get("updated_at")) and isinstance(regs, dict) and regs:
        scores = {k: v["score"] for k, v in regs.items() if isinstance(v, dict) and isinstance(v.get("score"), (int, float))}
        crit = sorted(k for k, v in regs.items() if isinstance(v, dict) and v.get("level") == "critical")
        snap["ioda"] = {"at": i["updated_at"], "scores": scores, "critical": crit}
    w = load("water.json")
    if w and w.get("kind") == "power" and parse(w.get("post_at")) and isinstance(w.get("regions"), dict):
        snap["power"] = {"at": w["post_at"], "regions": {k: v.get("status") for k, v in w["regions"].items() if isinstance(v, dict) and v.get("status")}}
    return snap if len(snap) > 1 else None
def main():
    old = load(OUT) or {}
    snaps = [s for s in old.get("snaps", []) if isinstance(s, dict) and parse(s.get("t"))]
    t = now()
    if snaps:
        last = parse(snaps[-1]["t"])
        if (t - last).total_seconds() < EVERY_S - 20:
            print(f"changes: last snapshot {int((t - last).total_seconds())} s ago"); return
    s = snapshot()
    if not s:
        print("changes: no usable source, keeping last changes.json"); return
    snaps.append(s)
    snaps = [x for x in snaps if (t - parse(x["t"])).total_seconds() <= KEEP_S]
    data = {
        "note": "Earlier real readings of fires.json, ioda.json and the Ukrenergo power post, for the screen's map change strips. Counts and statuses only.",
        "updated": iso(t),
        "snaps": snaps,
    }
    json.dump(data, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"changes: {len(snaps)} snapshots")
    subprocess.run(["git", "add", OUT], check=False, timeout=30)
if __name__ == "__main__":
    main()
