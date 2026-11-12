#!/usr/bin/env python3
"""DEFENCE TODAY (v2.41, Jason Nov 12 2026 12:08 PM "yes", numbers only, no positions): two official daily figures.
1) Air Force of Ukraine (t.me/kpszsu, public page): the morning "У ніч на ..." summary: launched vs shot down/suppressed.
2) General Staff of the Armed Forces of Ukraine (daily losses post, via russianwarship.rip mirror of it): totals + day change.
Writes defence.json; at most every 30 min. Keeps the last file when a source fails. Numbers only as published; '—' if absent."""
import datetime as dt, html, json, os, re, sys, urllib.request
OUT = next((a for a in sys.argv[1:] if not a.startswith("--")), "defence.json")
EVERY_S = 30 * 60
UA = {"User-Agent": "Mozilla/5.0 (eQsat screen; +https://citizenziggy2022.github.io/eqsat-onscreen-demo/)"}
def now(): return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40) as r: return r.read().decode("utf-8", "replace")
def num(p, s):
    m = re.search(p, s, re.S | re.I)
    return int(m.group(1)) if m else None
def air_force():
    page = get("https://t.me/s/kpszsu?q=" + urllib.request.quote("У ніч на"))
    posts = re.findall(r'<a class="tgme_widget_message_date" href="(https://t\.me/kpszsu/\d+)"><time datetime="([^"]+)".*?', page)
    bodies = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', page, re.S)
    best = None
    for (href, t), b in zip(re.findall(r'tgme_widget_message_date" href="(https://t\.me/kpszsu/\d+)"><time datetime="([^"]+)"', page), bodies):
        txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", b))).strip()
        if "ніч на" not in txt or "збито" not in txt.lower(): continue
        if best is None or t > best[1]: best = (href, t, txt)
    if not best: return None
    href, t, txt = best
    night = re.search(r"ніч на (\d+ \w+)", txt)
    det_m = num(r"(\d+)\s+ракет\b", txt)  # "виявлено ... – 22 ракет та 464 БпЛА" when published
    det_d = num(r"(\d+)\s+(?:ударн\w+ )?БпЛА різних типів", txt) or num(r"атакував\w*.*?(\d+)(?:-ма)?\s+ударн\w*\s+БпЛА", txt) or num(r"(\d+)\s+ударн\w*\s+БпЛА", txt)
    down = num(r"ЗБИТО/ПОДАВЛЕНО\s+(\d+)", txt)
    hits = num(r"влучання.*?на\s+(\d+)\s+локац", txt)
    return {"night": night.group(1) if night else None, "posted_at": t[:19] + "Z", "url": href,
            "launched_drones": det_d, "launched_missiles": det_m, "down_total": down, "hit_locations": hits,
            "note_uk": "За попередніми даними Повітряних Сил"}
def staff():
    d = json.loads(get("https://russianwarship.rip/api/v2/statistics/latest"))["data"]
    s, inc = d["stats"], d["increase"]
    keys = ["personnel_units", "tanks", "armoured_fighting_vehicles", "artillery_systems", "mlrs", "aa_warfare_systems", "planes", "helicopters", "uav_systems", "cruise_missiles", "warships_cutters", "vehicles_fuel_tanks"]
    return {"date": d["date"], "day": d.get("day"), "url": d.get("resource"), "total": {k: s.get(k) for k in keys}, "change": {k: inc.get(k) for k in keys}}
def main(force=False):
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT))
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(old["checked_at"].replace("Z", "+00:00"))).total_seconds()
            if not force and age < EVERY_S: print(f"defence: checked {int(age)} s ago"); return
        except Exception: pass
    out = {"source": "Повітряні Сили ЗСУ (t.me/kpszsu) · Генштаб ЗСУ", "checked_at": now()}
    for k, f in (("air", air_force), ("staff", staff)):
        try: out[k] = f() or old.get(k)
        except Exception as e: print("defence:", k, "not loaded, keeping last", repr(e)[:120]); out[k] = old.get(k)
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print("defence:", json.dumps({"air": out.get("air") and {k: out["air"][k] for k in ("night", "launched_drones", "launched_missiles", "down_total", "hit_locations")}, "staff": out.get("staff") and out["staff"]["date"]}, ensure_ascii=False))
    if OUT == "defence.json":
        import subprocess; subprocess.run(["git", "add", OUT], check=False, timeout=60)
if __name__ == "__main__":
    main("--force" in sys.argv)
