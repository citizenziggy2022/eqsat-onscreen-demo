#!/usr/bin/env python3
"""Auto bulletins for the eQsat screen's BULLETINS box (Jason, Nov 9 2026 1:23 PM "Auto fill bulletins").

Reads the public web previews (t.me/s/<channel>) of official emergency channels, each one checked against the
authority's own .gov.ua site or Telegram's verified badge on 2026-11-09 (see SOURCES). Keeps posts from the last
12 h (safety posts only, see SAFETY), skips anything about troop positions / front lines and anything graphic, shortens the post's own words (no
text is written here: title = the post's first sentence, body = the next ones, cut at a word with "…"), and writes
auto_bulletins.json with source name, source URL, published time and an expiry 8 h after publishing.
A failed fetch keeps the last good items for that source; a total failure leaves the file untouched.
Run from fetch_feed.py every live-runner cycle (about 1 min; throttled to 55 s), politely: one preview page per
channel per run, the older page at most every 10 min, ETag / If-Modified-Since when Telegram sends them, and a doubling
back-off per channel on 429 / errors (the channel keeps its last good items meanwhile); it stages auto_bulletins.json itself so the live-runner commits it.
"""
import html, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone

import requests

OUT = "auto_bulletins.json"
MIN_INTERVAL_S = 55  # every live-runner cycle (Jason, Nov 11 9:37 AM: "as realtime as possible")
OLDER_PAGE_S = 10 * 60  # the second (older) preview page per channel, at most this often
STATE = ".cache/fetch_bulletins_state.json"  # untracked: throttle, ETags, back-off per channel
MAX_AGE_H = 12
EXPIRE_H = 12
PER_SOURCE = 2
MAX_ITEMS = 10

# name shown on the card, Telegram channel, how it was verified (2026-11-09)
SOURCES = [
    {"id": "dsns", "name": "ДСНС України", "channel": "dsns_telegram", "region": "Україна",
     "verified": "Telegram verified badge; linked as the official channel of dsns.gov.ua"},
    {"id": "kherson", "name": "Херсонська ОВА", "channel": "khersonskaODA", "region": "Херсонщина",
     "verified": "linked from khoda.gov.ua; channel links back to khoda.gov.ua"},
    {"id": "zaporizhzhia", "name": "Запорізька ОВА", "channel": "zoda_gov_ua", "region": "Запоріжжя",
     "verified": "linked from zoda.gov.ua"},
    {"id": "donetsk", "name": "Донецька ОВА", "channel": "VadymFilashkin", "region": "Донеччина",
     "verified": "channel of the OVA head (dn.gov.ua/persons/vadim-filashkin), links to dn.gov.ua"},
    {"id": "luhansk", "name": "Луганська ОВА", "channel": "luhanskaVTSA", "region": "Луганщина",
     "verified": "linked from lova.gov.ua (Luhansk OVA site)"},
    {"id": "kharkiv", "name": "Харківська ОВА", "channel": "kharkivoda", "region": "Харківщина",
     "verified": "linked from kharkivoda.gov.ua"},
]

# military / front-line content is never shown (positions, directions, assaults, units, losses)
MILITARY = re.compile(
    r"фронт|позиці|напрямк|\bнаступ(?:у|ом|і|ає|ають|али)?\b|наступальн|штурм|контрнаступ|оперативна ситуація|окупант\w* втрат|втрати ворога|ліквідовано|знищено|бригад|батальйон|"
    r"угрупован|ЗСУ|Сил оборони|Генштаб|артилері|ворожий наступ|лінії зіткнення|бойов\w+ зіткнен|просуван|"
    r"ППО|збито|дрон\w* збит|БпЛА збит|мобіліз|ТЦК|військовослужбов|захисник|загиблих захисників",
    re.I,
)
# nothing graphic: bodies, blood, executions (plain casualty notices, "загиблі / поранені", pass: Jason, Nov 11 9:13 AM)
GRAPHIC = re.compile(r"тіл[оа]|тіл |кров|розстріл|катуван|страт|тела убит|растрел|пытк", re.I)
# not bulletins: congratulations, ads, donations, interviews
NOISE = re.compile(r"відбій|вітаю|вітаєм|привітан|поздоровл|поздравл|день народження|донат|збір кошт|реклам|інтерв.ю|ефір|підписуйтесь|розіграш|конкурс", re.I)

# SAFETY FILTER (Jason, Nov 11 2026 9:13 AM, "yes"): a post is kept ONLY if it is about safety: alerts and shelters,
# evacuation, shelling, explosions, fires; power cuts and schedules; water, gas, heat; mines; casualty notices;
# Invincibility Points and humanitarian aid; phone/mobile outages; road closures; ДСНС rescue work. UK and RU forms.
SAFETY = re.compile(
    r"тривог|тревог|укритт|укрит|укрыт|евакуац|эвакуац|обстріл|обстрел|вибух|взрыв|пожеж|пожар|"
    r"знеструм|обесточ|відключен|отключен|світл|\bсвет|електро|электро|графік\w* відключ|график\w* отключ|"
    r"водопост|водоснабж|без води|без воды|\bвод[аиуіы]\b|\bгаз|тепл|опален|отоплен|"
    r"\bмін[иау]\b|\bмин[ыау]\b|мінн|минн|вибухонебезп|взрывоопас|загибл|погиб|поранен|ранен[ыи]|"
    r"незламн|несокруш|гуманітарн\w* допомог|гуманитарн\w* помощ|зв.яз|связ|мобільн|мобильн|"
    r"перекрит|перекрыт|\bрух\b|дорог|ДСНС|ГСЧС|рятувальн|спасател",
    re.I,
)
# dropped even when a safety word appears: holidays, culture, sports, pension Q&A, meetings, visits, awards,
# school / children's leisure, congratulations
DROP = re.compile(
    r"свят|праздн|культур|концерт|виставк|выставк|фестивал|спорт|чемпіон|чемпион|турнір|турнир|матч|пенсі|пенси|"
    r"зустрів|зустріч|встреч|засіданн|заседан|нарад|совещан|візит|визит|нагород|награ|відзнак|грамот|"
    r"знайомтес|знакомьтес|^\s*Це\s*[–—-]\s*[А-ЯІЇЄҐ]|школ|учн|дитяч\w* (?:табор|дозвілл|гурт)|дозвілл|досуг|канікул|каникул|вітаю|вітаєм|поздоров|поздрав|подяк|ювіле|юбиле",
    re.I,
)

# what a bulletin is for: safety, power/water/heat, help, evacuation, hotlines, closures
USEFUL = re.compile(
    r"укрит|евакуац|знеструм|світл|електро|енерг|водопост|вод[аиу]\b|газ|тепл|опален|допомог|виплат|ВПО|переселен|"
    r"гаряч\w+ лін|телефон|номер|вибухонебезп|мін[иа]\b|мінн|пожеж|рятувальн|незламност|графік|відключ|безпек|"
    r"зв.яз|інтернет|пенсі|гуманітар|перекрит|рух |транспорт|ремонт|відновл|аптек|ліки|медичн|прийом|звертатис|увага|оголош|пам.ятк|оповіщ|сирен|обстріл",
    re.I,
)
NOT_BULLETIN = re.compile(r"культур|чемпіон|спорт|нагород|зустрівся|засіданн|нарад|діалог|подяк|ювіле|свято", re.I)

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
      "Accept-Language": "uk,en;q=0.8"}

MSG = re.compile(r'<div class="tgme_widget_message_wrap.*?(?=<div class="tgme_widget_message_wrap|\Z)', re.S)


def text_of(fragment):
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(fragment)


def clean(text):
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]", "", text)  # emoji
    text = re.sub(r"#\S+", "", text)  # hashtags
    text = re.sub(r"https?://\S+|t\.me/\S+|@\w+", "", text)
    lines = [re.sub(r"\s+", " ", l).strip(" -–—•·|") for l in text.split("\n")]
    return [l for l in lines if l]


def cut(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    head = head[: head.rfind(" ")] if " " in head[20:] else head
    return head.rstrip(" ,;:–—-") + "…"


def sentences(lines):
    joined = " ".join(lines)
    parts = re.split(r"(?<=[.!?…])\s+(?=[А-ЯІЇЄҐA-Z0-9«\"])", joined)
    return [p.strip() for p in parts if p.strip()]


class NotModified(Exception):
    pass


class Backoff(Exception):
    pass


def get_page(url, st, key):
    """GET with the stored validators; 304 -> NotModified, 429 / 5xx -> Backoff (doubling, up to 30 min)."""
    h = dict(UA)
    v = st.setdefault("validators", {}).get(key) or {}
    if v.get("etag"):
        h["If-None-Match"] = v["etag"]
    if v.get("modified"):
        h["If-Modified-Since"] = v["modified"]
    r = requests.get(url, headers=h, timeout=20)
    if r.status_code == 304:
        raise NotModified()
    if r.status_code == 429 or r.status_code >= 500:
        wait = min(1800, max(60, int(st.setdefault("backoff", {}).get(key, {}).get("wait", 30)) * 2))
        try:
            wait = max(wait, int(r.headers.get("Retry-After", "0")))
        except ValueError:
            pass
        st["backoff"][key] = {"until": time.time() + wait, "wait": wait}
        raise Backoff(f"HTTP {r.status_code}, backing off {wait} s")
    r.raise_for_status()
    st.setdefault("backoff", {}).pop(key, None)
    st["validators"][key] = {"etag": r.headers.get("ETag"), "modified": r.headers.get("Last-Modified")}
    return r.text


def fetch_channel(src, st=None):
    st = st if st is not None else {}
    key = src["channel"]
    b = st.setdefault("backoff", {}).get(key)
    if b and time.time() < b.get("until", 0):
        raise Backoff(f"backing off {int(b['until'] - time.time())} s more")
    url = f"https://t.me/s/{src['channel']}"
    page = get_page(url, st, key)
    if "tgme_channel_info" not in page:
        raise RuntimeError("no channel preview")
    ids = [int(x) for x in re.findall(r'data-post="[^"/]+/(\d+)"', page)]
    older_at = st.setdefault("older_at", {})
    cache = st.setdefault("older_page", {})
    if ids and time.time() - older_at.get(key, 0) >= OLDER_PAGE_S:  # one older page, so a busy channel's last 12 h are covered
        try:
            older = requests.get(f"{url}?before={min(ids)}", headers=UA, timeout=20)
            if older.ok:
                cache[key] = older.text[-400000:]
                older_at[key] = time.time()
        except requests.RequestException:
            pass
    if cache.get(key):
        page = cache[key] + page
    items = []
    seen = [0]
    now = datetime.now(timezone.utc)
    for block in MSG.findall(page):
        post = re.search(r'data-post="([^"]+)"', block)
        when = re.search(r'<time datetime="([^"]+)"', block)
        body = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not (post and when and body):
            continue
        if "tgme_widget_message_forwarded_from" in block:
            continue  # only the authority's own posts
        try:
            published = datetime.fromisoformat(when.group(1).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        if now - published > timedelta(hours=MAX_AGE_H) or published > now + timedelta(minutes=5):
            continue
        raw = text_of(body.group(1))
        seen[0] += 1
        if MILITARY.search(raw) or GRAPHIC.search(raw) or NOISE.search(raw) or NOT_BULLETIN.search(raw) or DROP.search(raw) or not SAFETY.search(raw):
            continue
        lines = clean(raw)
        sent = sentences(lines)
        if not sent or len(" ".join(sent)) < 60:
            continue
        if 15 <= len(lines[0]) <= 120 and len(lines) > 1:
            title, rest = lines[0].rstrip("."), " ".join(lines[1:])  # the post's own headline line
        else:
            title, rest = cut(sent[0], 110), " ".join(sent[1:])
        items.append({
            "id": f"auto-{post.group(1).replace('/', '-')}",
            "title": title,
            "body": cut(rest, 260) if rest else "",
            "source": src["name"],
            "source_url": f"https://t.me/{post.group(1)}",
            "region": src["region"],
            "category": "Офіційно / Official",
            "severity": "info",
            "issued_at": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": (published + timedelta(hours=EXPIRE_H)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "auto": True,
        })
    items.sort(key=lambda i: i["issued_at"], reverse=True)
    uniq = {i["id"]: i for i in items}  # the older page can repeat a post
    items = sorted(uniq.values(), key=lambda i: i["issued_at"], reverse=True)
    return items[:PER_SOURCE], len(items), seen[0] - len(items)


def passes(text):
    """The safety filter on stored items too, so nothing kept from before the filter stays on air."""
    return bool(SAFETY.search(text)) and not (MILITARY.search(text) or GRAPHIC.search(text) or DROP.search(text) or NOT_BULLETIN.search(text))


def main():
    force = "--force" in sys.argv
    old = {}
    try:
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        old = {}
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    if not force and time.time() - st.get("ran_at", 0) < MIN_INTERVAL_S:
        return 0
    st["ran_at"] = time.time()
    now = datetime.now(timezone.utc)
    old_by_source = {}
    for item in old.get("bulletins", []):
        old_by_source.setdefault(item.get("source"), []).append(item)
    out, status, ok = [], {}, 0
    for src in SOURCES:
        try:
            got, kept, dropped = fetch_channel(src, st)
            ok += 1
            status[src["id"]] = f"ok: kept {kept} / dropped {dropped}, showing {len(got)}"
        except (NotModified, Backoff) as exc:  # unchanged or resting: keep this source's items, not a failure
            ok += 1
            got = [i for i in old_by_source.get(src["name"], []) if i.get("expires_at", "") > now.strftime("%Y-%m-%dT%H:%M:%SZ") and passes(i.get("title", "") + " " + i.get("body", ""))]
            prev = next((x.get("status") for x in old.get("sources", []) if x.get("name") == src["name"]), None)
            status[src["id"]] = (prev or "ok") if isinstance(exc, NotModified) else f"resting ({exc}), kept {len(got)}"
        except Exception as exc:  # keep this source's last good items
            got = [i for i in old_by_source.get(src["name"], []) if i.get("expires_at", "") > now.strftime("%Y-%m-%dT%H:%M:%SZ") and passes(i.get("title", "") + " " + i.get("body", ""))]
            status[src["id"]] = f"failed ({str(exc)[:80]}), kept {len(got)}"
        out.extend(got)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f)
    if not ok:
        print("auto bulletins: every source failed, keeping last file", status)
        return 1
    out.sort(key=lambda i: i["issued_at"], reverse=True)
    data = {
        "note": "Written by scripts/fetch_bulletins.py from official channels (see sources). Safety posts only (alerts, shelters, evacuation, shelling, fires, power, water, gas, heat, mines, casualty notices, aid points, phone outages, road closures, rescue work). Text is the source's own, shortened. No post passing the filter = no auto bulletins. Manual bulletins in config.json / bulletins.json are shown first.",
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": [{"name": s["name"], "url": f"https://t.me/s/{s['channel']}", "verified": s["verified"], "status": status.get(s["id"])} for s in SOURCES],
        "bulletins": out[:MAX_ITEMS],
    }
    # rewrite only when the bulletins or a source's state changed (no commit churn every minute)
    if not force and old.get("bulletins") == data["bulletins"] and [x.get("status") for x in old.get("sources", [])] == [x.get("status") for x in data["sources"]]:
        print("auto bulletins: unchanged", status)
        return 0
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("auto bulletins:", len(data["bulletins"]), status)
    if os.environ.get("GITHUB_ACTIONS"):
        subprocess.run(["git", "add", OUT], check=False)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("auto bulletins failed, keeping last file:", exc)
        sys.exit(1)
