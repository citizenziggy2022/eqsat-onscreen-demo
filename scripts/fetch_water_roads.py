#!/usr/bin/env python3
"""Live data for the screen's WATER and ROADS maps (Jason, Nov 11 2026 9:11 AM: "what happens to updating the water and
roads and traffic problem alerts. maps?"; 9:37 AM: "updates should be as realtime as possible").

water.json  POWER CUTS, not water (labelled so on screen): Ukrenergo's official Telegram (t.me/s/Ukrenergo, the
            national grid operator). The newest post that names oblasts with de-energised consumers ("знеструмлення")
            or emergency cuts ("аварійні відключення") marks exactly those oblasts; nothing else is coloured. Every
            2.5 min. Only the sentence that names the cuts is read (not the one listing where strikes hit).
roads.json  BORDER TRUCK QUEUES: eCherha (єЧерга, the Ministry of Infrastructure / DPSU e-queue), its public JSON
            backend. Per crossing: wait > 72 h = out, > 12 h = limited, else open; an oblast takes its worst crossing.
            Every 3 min, trucks only (its limit is 60 calls a minute; 429 backs off).
Both files keep "updated" = the last successful read; on any failure the last good file stays and the screen shows
its real age (older than 2 h: no colours, "—" and the age). Occupied areas are never coloured from these feeds.
Sources excluded on purpose: occupation authorities, Yandex, the Russian-run Crimean bridge channel, Transnistria's
de facto authorities. Run from scripts/fetch_feed.py every live-runner cycle; this script throttles itself.
"""
import html, json, os, re, subprocess, sys, time
from datetime import datetime, timezone

import requests

STATE = ".cache/water_roads_state.json"
POWER_EVERY_S = 150
ROADS_EVERY_S = 180
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
      "Accept-Language": "uk,en;q=0.8"}
ECH = {"X-Client-Locale": "uk", "X-User-Agent": "UABorder/3.0.0 Web/1.1.0 User/guest", "X-Device-Id": "eqsat-screen-runner",
       "Accept": "application/json", "User-Agent": UA["User-Agent"]}

# oblast adjective stems (Ukrenergo writes "у Дніпропетровській, Житомирській ... областях") -> map region keys
OBLAST = [
    ("вінницьк", "vinnytsia"), ("волинськ", "volyn"), ("дніпропетровськ", "dnipro"), ("донецьк", "donetsk"),
    ("житомирськ", "zhytomyr"), ("закарпатськ", "zakarpattia"), ("запорізьк", "zaporizhzhia"), ("івано-франківськ", "ivano"),
    ("київськ", "kyivobl"), ("кіровоградськ", "kirovohrad"), ("луганськ", "luhansk"), ("львівськ", "lviv"),
    ("миколаївськ", "mykolaiv"), ("одеськ", "odesa"), ("полтавськ", "poltava"), ("рівненськ", "rivne"), ("сумськ", "sumy"),
    ("тернопільськ", "ternopil"), ("харківськ", "kharkiv"), ("херсонськ", "kherson"), ("хмельницьк", "khmelnytskyi"),
    ("черкаськ", "cherkasy"), ("чернівецьк", "chernivtsi"), ("чернігівськ", "chernihiv"),
    # "-щина" forms
    ("вінниччин", "vinnytsia"), ("волин", "volyn"), ("дніпропетровщин", "dnipro"), ("донеччин", "donetsk"), ("житомирщин", "zhytomyr"),
    ("закарпатт", "zakarpattia"), ("запоріжж", "zaporizhzhia"), ("прикарпатт", "ivano"), ("київщин", "kyivobl"), ("кіровоградщин", "kirovohrad"),
    ("луганщин", "luhansk"), ("львівщин", "lviv"), ("миколаївщин", "mykolaiv"), ("одещин", "odesa"), ("полтавщин", "poltava"),
    ("рівненщин", "rivne"), ("сумщин", "sumy"), ("тернопільщин", "ternopil"), ("харківщин", "kharkiv"), ("херсонщин", "kherson"),
    ("хмельниччин", "khmelnytskyi"), ("черкащин", "cherkasy"), ("буковин", "chernivtsi"), ("чернігівщин", "chernihiv"),
    ("києві", "kyivcity"), ("м. київ", "kyivcity"),
]
OCCUPIED_ONLY = {"crimea", "sevastopol"}  # never coloured from a live feed
TG_MSG = re.compile(r'<div class="tgme_widget_message_wrap.*?(?=<div class="tgme_widget_message_wrap|\Z)', re.S)
CUT = re.compile(r"знеструм|аварійн\w* відключ", re.I)
EMERGENCY = re.compile(r"аварійн\w* відключ", re.I)


def iso(t=None):
    return (datetime.fromtimestamp(t, timezone.utc) if t else datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    if os.environ.get("GITHUB_ACTIONS"):
        subprocess.run(["git", "add", path], check=False)


def oblasts_in(sentence):
    s = sentence.lower()
    found = []
    for stem, key in OBLAST:
        if stem in s and key not in found and key not in OCCUPIED_ONLY:
            found.append(key)
    return found


def power_cuts():
    r = requests.get("https://t.me/s/Ukrenergo", headers=UA, timeout=20)
    if r.status_code == 429:
        raise RuntimeError("Telegram 429")
    r.raise_for_status()
    posts = []
    for block in TG_MSG.findall(r.text):
        when = re.search(r'<time datetime="([^"]+)"', block)
        body = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        post = re.search(r'data-post="([^"]+)"', block)
        if not (when and body and post) or "tgme_widget_message_forwarded_from" in block:
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", body.group(1))))
        posts.append((datetime.fromisoformat(when.group(1)).astimezone(timezone.utc), post.group(1), text))
    if not posts:
        raise RuntimeError("no posts in the preview")
    now = datetime.now(timezone.utc)
    for at, pid, text in sorted(posts, reverse=True):
        if (now - at).total_seconds() > 30 * 3600:
            break
        regions = {}
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
            if not CUT.search(sentence):
                continue
            status = "outage" if EMERGENCY.search(sentence) else "limited"
            for key in oblasts_in(sentence):
                if regions.get(key, {}).get("status") != "outage":
                    regions[key] = {"status": status, "note": "аварійні відключення / emergency cuts" if status == "outage" else "знеструмлення / partial cuts"}
        if regions:
            return {"post_at": iso(at.timestamp()), "post_url": f"https://t.me/{pid}", "regions": regions}
    return {"post_at": None, "post_url": None, "regions": {}}  # no oblast named in the last 30 h: nothing coloured


# Ukrainian side of each crossing (the title's first part) -> oblast; checked against eCherha's own lat/lng (Nov 11 2026)
CROSSING = {
    "Ягодин": "volyn", "Устилуг": "volyn", "Угринів": "lviv", "Рава": "lviv", "Грушів": "lviv", "Краківець": "lviv",
    "Шегині": "lviv", "Смільниця": "lviv", "Малий Березний": "zakarpattia", "Ужгород": "zakarpattia", "Чоп": "zakarpattia",
    "Лужанка": "zakarpattia", "Дякове": "zakarpattia", "Вилок": "zakarpattia", "Косини": "zakarpattia", "Солотвино": "zakarpattia",
    "Красноїльськ": "chernivtsi", "Порубне": "chernivtsi", "Дяківці": "chernivtsi", "Мамалига": "chernivtsi", "Росошани": "chernivtsi",
    "Сокиряни": "chernivtsi", "Кельменці": "chernivtsi", "Могилів": "vinnytsia", "Бронниця": "vinnytsia",
    "Виноградівка": "odesa", "Рені": "odesa", "Орлівка": "odesa", "Табаки": "odesa", "Малоярославець": "odesa",
    "Нові Трояни": "odesa", "Серпневе": "odesa", "Старокозаче": "odesa", "Маяки": "odesa", "Паланка": "odesa",
}
COUNTRY = {133: "PL", 136: "RO", 112: "MD", 149: "SK", 167: "HU"}


def wait_hours(text):
    t = (text or "").lower()
    d = re.search(r"(\d+)\s*дн", t)
    h = re.search(r"(\d+)\s*год", t)
    m = re.search(r"(\d+)\s*хв", t)
    if not (d or h or m):
        return None
    return (int(d.group(1)) * 24 if d else 0) + (int(h.group(1)) if h else 0) + (int(m.group(1)) / 60 if m else 0)


def border_queues():
    r = requests.get("https://back.echerha.gov.ua/api/v5/workload/1", headers=ECH, timeout=20)
    if r.status_code == 429:
        raise RuntimeError("eCherha 429")
    r.raise_for_status()
    rows = r.json().get("data") or []
    if not rows:
        raise RuntimeError("eCherha: no rows")
    worst, unknown = {}, []
    for row in rows:
        side = re.split(r"\s+[–-]\s+", row.get("title", ""))[0].strip()
        key = next((v for k, v in CROSSING.items() if side.startswith(k)), None)
        if not key:
            unknown.append(side)
            continue
        hours = wait_hours(((row.get("timing") or {}).get("header") or {}).get("text"))
        if hours is None or row.get("is_paused"):
            continue
        trucks = row.get("vehicle_in_active_queues_counts") or 0
        if key not in worst or hours > worst[key]["hours"]:
            worst[key] = {"hours": hours, "trucks": trucks, "name": side, "to": COUNTRY.get(row.get("country_id"), "")}
    regions = {}
    for key, w in worst.items():
        status = "outage" if w["hours"] > 72 else "limited" if w["hours"] > 12 else "ok"
        h = int(round(w["hours"]))
        wait = f"{h // 24} д {h % 24} год" if h >= 24 else f"{h} год"
        regions[key] = {"status": status, "note": f"{w['name']} → {w['to']}: {w['trucks']} вант. · {wait}"}
    return {"regions": regions, "crossings": len(rows), "unknown": sorted(set(unknown))}


def main():
    force = "--force" in sys.argv
    st = load(STATE, {})
    now = time.time()
    did = []
    if force or now - st.get("power_at", 0) >= POWER_EVERY_S:
        st["power_at"] = now
        try:
            p = power_cuts()
            old = load("water.json", {})
            data = {
                "updated": iso(),
                "kind": "power",
                "source": "Укренерго / Ukrenergo" + (f" · пост / post {p['post_at'][11:16]} UTC" if p["post_at"] else ""),
                "post_at": p["post_at"],
                "post_url": p["post_url"],
                "note": "Відключення світла, не води: вода зазвичай зникає слідом / Power cuts, not water: water often follows",
                "regions": p["regions"],
            }
            # rewrite when anything but the read time changed, or every 10 min so the age on screen stays honest
            same = {k: v for k, v in old.items() if k != "updated"} == {k: v for k, v in data.items() if k != "updated"}
            if not same or now - st.get("power_written", 0) >= 600:
                write("water.json", data)
                st["power_written"] = now
            did.append(f"power: {len(p['regions'])} oblasts (post {p['post_at']})")
        except Exception as exc:
            did.append(f"power failed, keeping last: {str(exc)[:100]}")
    if force or now - st.get("roads_at", 0) >= ROADS_EVERY_S:
        st["roads_at"] = now
        try:
            q = border_queues()
            old = load("roads.json", {})
            data = {
                "updated": iso(),
                "kind": "border",
                "source": "єЧерга / eCherha · вантажівки / trucks",
                "regions": q["regions"],
            }
            same = {k: v for k, v in old.items() if k != "updated"} == {k: v for k, v in data.items() if k != "updated"}
            if not same or now - st.get("roads_written", 0) >= 600:
                write("roads.json", data)
                st["roads_written"] = now
            did.append(f"roads: {len(q['regions'])} oblasts from {q['crossings']} crossings" + (f", unmapped {q['unknown']}" if q["unknown"] else ""))
        except Exception as exc:
            did.append(f"roads failed, keeping last: {str(exc)[:100]}")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f)
    if did:
        print("water/roads:", "; ".join(did))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("water/roads failed, keeping last files:", exc)
        sys.exit(1)
