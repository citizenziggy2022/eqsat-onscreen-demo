#!/usr/bin/env python3
"""alerttypes.json for the eQsat screen: active alert TYPES per oblast from siren.pp.ua (UkraineAlarm mirror).
siren.pp.ua sends no CORS header, so this runs in a GitHub Action every 5 min and writes the file at the Pages root."""
import json, sys, urllib.request, datetime

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

def main():
    regions = get("https://siren.pp.ua/api/v3/regions")["states"]
    parent = {}
    def walk(node, top):
        parent[str(node["regionId"])] = top
        for child in node.get("regionChildIds") or []:
            walk(child, top)
    for state in regions:
        key = NAME2KEY.get(state["regionName"])
        if key:
            walk(state, key)
    alerts = get("https://siren.pp.ua/api/v3/alerts")
    out = {t: {"regions": set(), "areas": 0} for t in TYPES}
    other = {}
    for region in alerts:
        key = NAME2KEY.get(region.get("regionName")) or parent.get(str(region.get("regionId")))
        for alert in region.get("activeAlerts") or []:
            t = (alert.get("type") or "").upper()
            if t not in out:
                other[t] = other.get(t, 0) + 1
                continue
            out[t]["areas"] += 1
            if key:
                out[t]["regions"].add(key)
    doc = {
        "updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "siren.pp.ua (UkraineAlarm)",
        "types": {t: {"regions": sorted(v["regions"]), "areas": v["areas"]} for t, v in out.items()},
        "other": other,
    }
    with open("alerttypes.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(json.dumps({t: len(v["regions"]) for t, v in doc["types"].items()}))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # keep the last good file
        print("alerttypes failed:", e, file=sys.stderr)
