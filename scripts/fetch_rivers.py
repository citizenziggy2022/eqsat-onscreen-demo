#!/usr/bin/env python3
"""Rivers & Dnipro reservoirs: official hydrological warnings of the Ukrainian Hydrometeorological Center (UkrHMC,
meteo.gov.ua), for the eQsat map view "rivers" (Jason, Nov 12 2026: "Dnipro river warnings"). Reads the two JSON files the
UkrHMC warning maps themselves load and writes rivers.json: today's warnings per area, with the level and type exactly as
UkrHMC publishes them. Geometry is static (rivers-geo.json). Stdlib only."""
import json, re, sys, urllib.request, datetime, os, html
UA = {"User-Agent": "Mozilla/5.0 (eQsat emergency screen; +https://citizenziggy2022.github.io/eqsat-onscreen-demo/)",
      "Referer": "https://www.meteo.gov.ua/ua/Hidrolohichni-poperedzhennya", "Accept": "application/json"}
SRC = {"hb": "https://www.meteo.gov.ua/ua/_attns-hydro.json", "vs": "https://www.meteo.gov.ua/ua/_attns-vshov.json"}
HREG = {"19": "Суббасейн річки Західний Буг (Львівська область)", "22": "Суббасейн річки Західний Буг (Волинська область)", "1": "Суббасейн річки Сан (Львівська область)", "38": "Суббасейн Верхнього Дніпра (Чернігівська область)", "39": "Суббасейн річки Сіверський Донець (Донецька область)", "40": "Суббасейн річки Сіверський Донець (Харківська область)", "41": "Суббасейн річки Сіверський Донець (Луганська область)", "23": "Суббасейн Нижнього Дунаю (Одеська область)", "26": "Суббасейн річки Сірет (Чернівецька область)", "27": "Суббасейн річки Тиса (Закарпатська область)", "24": "Суббасейн річки Прут (Чернівецька область)", "25": "Суббасейн річки Прут (Івано-Франківська область)", "2": "Суббасейн Нижнього Дону (Луганська область)", "49": "Суббасейн річки Прип’ять (Житомирська область)", "50": "Суббасейн річки Прип’ять (Хмельницька область)", "51": "Суббасейн річки Прип’ять (Київська область)", "52": "Суббасейн річки Прип’ять (Львівська область)", "53": "Суббасейн річки Прип’ять (Рівненська область)", "54": "Суббасейн річки Прип’ять (Тернопільська область)", "55": "Суббасейн річки Прип’ять (Волинська область)", "35": "Суббасейн річки Десна (Чернігівська область)", "36": "Суббасейн річки Десна (Київська область)", "37": "Суббасейн річки Десна (Сумська область)", "56": "Суббасейн Середнього Дніпра (Черкаська область)", "66": "Суббасейн Середнього Дніпра (Чернігівська область)", "57": "Суббасейн Середнього Дніпра (Чернігівська область)", "58": "Суббасейн Середнього Дніпра (Дніпропетровська область)", "59": "Суббасейн Середнього Дніпра (Житомирська область)", "60": "Суббасейн Середнього Дніпра (Харківська область", "61": "Суббасейн Середнього Дніпра (Київська область)", "62": "Суббасейн Середнього Дніпра (Кіровоградська область)", "63": "Суббасейн Середнього Дніпра (Полтавська область)", "64": "Суббасейн Середнього Дніпра (Сумська область)", "65": "Суббасейн Середнього Дніпра (Вінницька область)", "28": "Район басейну річки Дністер (Чернівецька область)", "29": "Район басейну річки Дністер (Хмельницька область)", "30": "Район басейну річки Дністер (Івано-Франківська область)", "31": "Район басейну річки Дністер (Львівська область)", "32": "Район басейну річки Дністер (Одеська область)", "33": "Район басейну річки Дністер (Тернопільська область)", "34": "Район басейну річки Дністер (Вінницька область)", "42": "Район басейну річки Південний Буг (Черкаська область)", "43": "Район басейну річки Південний Буг (Хмельницька область)", "44": "Район басейну річки Південний Буг (Київська область)", "45": "Район басейну річки Південний Буг (Кіровоградська область)", "46": "Район басейну річки Південний Буг (Миколаївська область)", "47": "Район басейну річки Південний Буг (Одеська область)", "48": "Район басейну річки Південний Буг (Вінницька область)", "17": "Район басейну річок Криму (Автономна Республіка Крим)", "13": "Район басейну річок Приазов’я (Донецька область)", "14": "Район басейну річок Приазов’я (Херсонська область)", "15": "Район басейну річок Приазов’я (Луганська область)", "16": "Район басейну річок Приазов’я (Запорізька область)", "18": "Район басейну річок Причорномор’я (Херсонська область)", "20": "Район басейну річок Причорномор’я (Миколаївська область)", "21": "Район басейну річок Причорномор’я (Одеська область)", "4": "Суббасейн Нижнього Дніпра (Дніпропетровська область)", "5": "Суббасейн Нижнього Дніпра (Донецька область)", "6": "Суббасейн Нижнього Дніпра (Харківська область)", "7": "Суббасейн Нижнього Дніпра (Херсонська область)", "8": "Суббасейн Нижнього Дніпра (Кіровоградська область)", "9": "Суббасейн Нижнього Дніпра (Миколаївська область)", "10": "Суббасейн Нижнього Дніпра (Полтавська область)", "11": "Суббасейн Нижнього Дніпра (Запорізька область)", "12": "Суббасейн Нижнього Дніпра (Миколаївська область)", "sb1": "Суббасейн річки Західний Буг", "sb2": "Суббасейн річки Сан", "sb3": "Суббасейн Верхнього Дніпра", "sb4": "Суббасейн річки Сіверський Донець", "sb5": "Суббасейн Нижнього Дунаю", "sb6": "Суббасейн річки Сірет", "sb7": "Суббасейн річки Тиса", "sb8": "Суббасейн річки Прут", "sb9": "Суббасейн Нижнього Дону", "sb10": "Суббасейн річки Прип’ять", "sb11": "Суббасейн річки Десна", "sb12": "Суббасейн Середнього Дніпра", "sb14": "Район басейну річки Дністер", "sb15": "Район басейну річки Південний Буг", "sb16": "Район басейну річок Криму", "sb17": "Район басейну річок Приазов’я", "sb18": "Район басейну річок Причорномор’я", "sb13": "Суббасейн Нижнього Дніпра", "sb19": "Суббасейн Нижнього Дніпра (Причорномор’я)"}
HTYPES = ["Немає...", "Підвищення рівнів води ", "Льодові явища", "Швидкоплинні паводки", "Хвилювання, вітрові нагони", "Селі", "Зниження рівнів води"]
VREG = {"1": "Київське водосховище", "2": "Канівське водосховище", "3": "Кременчуцьке водосховище", "4": "Кам’янське водосховище", "5": "Дніпровське водосховище", "6": "Каховське водосховище"}
VTYPES = ["Загальне", "Хвилювання, вітрові нагони", "Льодові явища", "Туман", "Вітер"]
OUT = next((a for a in sys.argv[1:] if not a.startswith("--")), "rivers.json")
EVERY_S = 30 * 60
if "--force" not in sys.argv and os.path.exists(OUT):
    try:
        _age = (datetime.datetime.utcnow() - datetime.datetime.strptime(json.load(open(OUT))["checked_at"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds()
        if _age < EVERY_S:
            print(f"rivers: checked {int(_age)} s ago"); sys.exit(0)
    except Exception:
        pass

def get(u):
    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))

def upd_iso(s):
    m = re.match(r"(\d\d)\.(\d\d)\.(\d{4}), (\d\d):(\d\d)", s or "")
    if not m: return None
    d, mo, y, h, mi = map(int, m.groups())
    # UkrHMC times are Kyiv time; Kyiv is UTC+2 in winter, UTC+3 in summer (last Sunday of March / October)
    def last_sun(year, month):
        dt = datetime.date(year, month, 31)
        return dt - datetime.timedelta(days=(dt.weekday() + 1) % 7)
    local = datetime.datetime(y, mo, d, h, mi)
    summer = datetime.datetime.combine(last_sun(y, 3), datetime.time(3)) <= local < datetime.datetime.combine(last_sun(y, 10), datetime.time(4))
    return (local - datetime.timedelta(hours=3 if summer else 2)).strftime("%Y-%m-%dT%H:%M:00Z")

def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()

out = {"source": "Український гідрометеорологічний центр (УкрГМЦ) · meteo.gov.ua", "areas": [], "upd": {}}
prev = None
if os.path.exists(OUT):
    try: prev = json.load(open(OUT))
    except Exception: prev = None
ok = 0
for kind, url in SRC.items():
    try:
        j = get(url)
    except Exception as e:
        print("rivers:", kind, "failed", e)
        if prev:
            out["areas"] += [a for a in prev.get("areas", []) if a.get("kind") == kind]
            out["upd"][kind] = prev.get("upd", {}).get(kind)
        continue
    ok += 1
    out["upd"][kind] = upd_iso(j.get("UPD"))
    today = (j.get("OBJ") or [[]])[0]
    for o in today:
        al = o.get("A") or []
        if not al: continue
        rid = str(o.get("R"))
        names = HREG if kind == "hb" else VREG
        types = HTYPES if kind == "hb" else VTYPES
        for a in al:
            c = a.get("C")
            t = a.get("T")
            out["areas"].append({
                "kind": kind, "id": rid, "name": names.get(rid, ""), "level": c,
                "type": types[t] if isinstance(t, int) and 0 <= t < len(types) else "",
                "period": clean(a.get("P")).replace(" — ", "–"), "text": clean(a.get("D"))[:400],
            })
out["checked_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
if ok == 0 and prev:
    out["checked_at"] = prev.get("checked_at")
json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
print("rivers:", len(out["areas"]), "warnings", out["upd"])
if OUT == "rivers.json":
    import subprocess
    subprocess.run(["git", "add", OUT], check=False, timeout=60)
