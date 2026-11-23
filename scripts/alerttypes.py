#!/usr/bin/env python3
"""alerttypes.json for the eQsat screen: active alert TYPES per oblast from siren.pp.ua (UkraineAlarm mirror).
siren.pp.ua sends no CORS header, so this runs in the live-runner approximately every minute and writes the file at the Pages root."""
import json, sys, urllib.request, datetime, os
from zoneinfo import ZoneInfo
from pathlib import Path

UA = {"User-Agent": "eqsat-onscreen/1.0 (+https://citizenziggy2022.github.io/eqsat-onscreen-demo/)"}
NAME2KEY = {
 "Черкаська область": "cherkasy",
 "Чернігівська область": "chernihiv",
 "Чернівецька область": "chernivtsi",
 "Крим (АРК)": "crimea",
 "Дніпропетровська область": "dnipro",
 "Донецька область": "donetsk",
 "Івано-Франківська область": "ivano",
 "Харківська область": "kharkiv",
 "Херсонська область": "kherson",
 "Хмельницька область": "khmelnytskyi",
 "Київська область": "kyivobl",
 "м. Київ": "kyivcity",
 "Кіровоградська область": "kirovohrad",
 "Львівська область": "lviv",
 "Луганська область": "luhansk",
 "Миколаївська область": "mykolaiv",
 "Одеська область": "odesa",
 "Полтавська область": "poltava",
 "Рівненська область": "rivne",
 "Севастополь": "sevastopol",
 "Сумська область": "sumy",
 "Тернопільська область": "ternopil",
 "Вінницька область": "vinnytsia",
 "Волинська область": "volyn",
 "Закарпатська область": "zakarpattia",
 "Запорізька область": "zaporizhzhia",
 "Житомирська область": "zhytomyr",
 "Автономна Республіка Крим": "crimea",
 "м. Запоріжжя та Запорізька територіальна громада": "zaporizhzhia",
 "м. Харків та Харківська територіальна громада": "kharkiv"
}
TYPES = ["AIR", "ARTILLERY", "URBAN_FIGHTS", "CHEMICAL", "NUCLEAR"]

def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def update_history(old, active, now):
    """Observed oblast/type starts, not incidents. No backfill across gaps."""
    h = old if isinstance(old, dict) and old.get("schema") == 1 else {"schema": 1, "days": {}}
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    day = now.astimezone(ZoneInfo("Europe/Kyiv")).date().isoformat()
    try:
        last = datetime.datetime.fromisoformat(h["last_at"].replace("Z", "+00:00")) if h.get("last_at") else None
    except (ValueError, TypeError):
        last = None
    if not isinstance(h.get("days"),dict): h["days"]={}

    delta = (now-last).total_seconds() if last else None
    continuous = delta is not None and 0 < delta <= 180
    if delta is not None and delta <= 0:
        return h
    prev = set(h.get("active", []))
    current = set(active)
    d = h["days"].setdefault(day, {"counts": {}, "partial": not continuous, "first_at": stamp})
    if not continuous:
        d["partial"] = True
        d["gap_count"] = d.get("gap_count", 0) + (1 if last else 0)
        if last:
            previous_day=last.astimezone(ZoneInfo("Europe/Kyiv")).date().isoformat()
            if previous_day in h["days"]: h["days"][previous_day]["partial"] = True
    if continuous:
        for key in current-prev:
            oblast, typ = key.split(":", 1)
            counts=d["counts"].setdefault(oblast, {})
            counts[typ]=counts.get(typ,0)+1
    d["samples"] = d.get("samples", 0) + 1
    d["last_at"] = stamp
    h.update(last_at=stamp, active=sorted(current), coverage_from=h.get("coverage_from", stamp))
    cutoff=(now.astimezone(ZoneInfo("Europe/Kyiv")).date()-datetime.timedelta(days=31)).isoformat()
    h["days"]={k:v for k,v in h["days"].items() if k>=cutoff}
    return h

def main():
    regions = get("https://siren.pp.ua/api/v3/regions")["states"]
    parent = {}
    def walk(node, top):
        if not isinstance(node,dict) or not node.get("regionId") or not isinstance(node.get("regionChildIds",[]),list):
            raise ValueError("Invalid region child")
        parent[str(node["regionId"])] = top
        for child in node.get("regionChildIds") or []:
            walk(child, top)
    if not isinstance(regions,list) or not regions or not all(isinstance(r,dict) and r.get("regionId") and r.get("regionName") for r in regions):
        raise ValueError("Invalid region hierarchy")
    for state in regions:
        key = NAME2KEY.get(state["regionName"])
        if key:
            walk(state, key)
    if not (set(NAME2KEY.values())-{"sevastopol"}).issubset(set(parent.values())):
        raise ValueError("Incomplete mapped region hierarchy")
    alerts = get("https://siren.pp.ua/api/v3/alerts")
    if not isinstance(alerts, list) or not regions or not all(isinstance(r,dict) and "regionId" in r and isinstance(r.get("activeAlerts"),list) for r in alerts):
        raise ValueError("Invalid alert snapshot, keeping previous data")
    out = {t: {"regions": set(), "areas": 0} for t in TYPES}
    other = {}
    for region in alerts:
        key = NAME2KEY.get(region.get("regionName")) or parent.get(str(region.get("regionId")))
        if not key and region.get("activeAlerts"):
            raise ValueError("Unmapped active region, keeping previous data")
        for alert in region.get("activeAlerts") or []:
            if not isinstance(alert,dict) or not isinstance(alert.get("type"),str):
                raise ValueError("Invalid alert type")
            t = alert["type"].upper()
            if t not in out:
                other[t] = other.get(t, 0) + 1
                continue
            out[t]["areas"] += 1
            if key:
                out[t]["regions"].add(key)
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        previous = json.loads(Path("alerttypes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    active_keys = {f"{oblast}:{typ}" for typ, value in out.items() for oblast in value["regions"]}
    history = update_history(previous.get("daily_history"), active_keys, now)
    history["covered_oblasts"]=sorted(set(parent.values()))
    history["days"][now.astimezone(ZoneInfo("Europe/Kyiv")).date().isoformat()]["covered_oblasts"]=sorted(set(parent.values()))
    doc = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "siren.pp.ua (UkraineAlarm)",
        "types": {t: {"regions": sorted(v["regions"]), "areas": v["areas"]} for t, v in out.items()},
        "other": other,
        "daily_history": history,
    }
    with open("alerttypes.json.tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace("alerttypes.json.tmp", "alerttypes.json")
    print(json.dumps({t: len(v["regions"]) for t, v in doc["types"].items()}))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # keep the last good file
        print("alerttypes failed:", e, file=sys.stderr)
