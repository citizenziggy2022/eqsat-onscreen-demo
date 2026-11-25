#!/usr/bin/env python3
"""REVIEW-ONLY collector (used by fetch_gov_review.py; nothing here airs anything). Official road and water-utility bulletins for the eQsat BULLETINS box.
Output matches auto_bulletins.json ({"bulletins": [...]}): the app shows items with title, source, http(s) source_url
and an expires_at in the future. Text is the source's own words; nothing is rewritten or invented.
 roads(): restoration.gov.ua blog (State Agency for Restoration and Infrastructure). Site footer: CC BY 4.0 unless stated
          otherwise, so each article page must carry that statement and no other photo/agency credit.
 water(): public t.me/s previews of water-utility channels. Each channel has a `verified` note saying how its
          official status was established; channels not yet confirmed from the utility's own site are marked.
Politeness: one listing + at most 4 articles for roads, one preview page per channel, descriptive User-Agent."""
import html, json, re, sys
from datetime import datetime, timedelta, timezone
import requests

UA = {"User-Agent": "eQsat-gov-data/0.2 (+https://github.com/citizenziggy2022/eqsat-onscreen-demo; emergency broadcast screen)"}
TG_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
FMT = "%Y-%m-%dT%H:%M:%SZ"
MAX_AGE_H, EXPIRE_H, BODY_MAX = 36, 12, 260
TITLE_MAX = 90  # about 3 lines on the card; longer titles are never cut automatically (a cut can drop a material qualifier)

ROAD = re.compile(r"проїзд|зимов\w+ утримання|снігопад|ожеледиц|ускладнен\w+ руху|перекрит|обмеженн\w+ руху|стан доріг|дорожні служби", re.I)
ROAD_DROP = re.compile(r"тендер|прозорість|корупц|історія|День |капітально ремонт|завершили влаштування|будуєм|вітає|З Днем", re.I)
WATER = re.compile(r"водопост|водовідвед|без води|\bвод[аиуіою]\b|водоканал|тиск у (?:водопровід|мереж)", re.I)
MILITARY = re.compile(r"фронт|позиці|напрямк|штурм|ЗСУ|Сил оборони|ППО|збито|бригад|ТЦК|мобіліз", re.I)
GRAPHIC = re.compile(r"тіл[оа]|кров|розстріл|катуван", re.I)
NOISE = re.compile(r"показан\w+ лічильник|тариф|оплат|вітаєм|реклам|розіграш", re.I)
OTHER_CREDIT = re.compile(r"(?:фото|photo|джерело фото|фото:)\s*[:\-–]?\s*(?!Агентств)\S|©|Reuters|\bAP\b|УНІАН|Укрінформ", re.I)

WATER_SOURCES = [
    {"id": "infox", "name": "Інфоксводоканал (Одеса)", "channel": "InfoxVodokanal", "region": "Одеса", "oblast": "Одеська область",
     "confirmed": True,
     "verified": "confirmed: infoxvod.com.ua links to t.me/infoxvodokanal (read 2026-11-24)"},
    {"id": "kvk", "name": "Київводоканал", "channel": "kvk20", "confirmed": True, "region": "Київ",
     "verified": "confirmed: vodokanal.kyiv.ua links to t.me/kvk20 (read in browser 2026-11-24)"},
    {"id": "kharkiv", "name": "Харківводоканал", "channel": "vodokanal_kharkiv", "confirmed": False, "region": "Харків",
     "verified": "NOT yet confirmed from the utility's own site; channel describes itself as KP Kharkivvodokanal"},
    {"id": "mykolaiv", "name": "Миколаївводоканал", "channel": "vodokanal1", "confirmed": False, "region": "Миколаїв",
     "verified": "NOT yet confirmed from the utility's own site; channel title is MKP Mykolaivvodokanal"},
]

def text(fragment):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", "\n", fragment)))).strip()

ABBR = re.compile(r"\b(вул|просп|пров|бул|пл|ім|м|р|смт|с|обл|буд|кв|тис|млн|млрд|грн|т|ст)\.\s+", re.I)

def sentences(t):
    t = ABBR.sub(lambda m: m.group(1) + "\u2024 ", t)  # keep "вул. Назва" together
    t = re.sub(r"\b([А-ЯІЇЄҐA-Z])\.\s+", "\\1\u2024 ", t)  # initials: "В. Морська"
    parts = re.split(r"(?<=[.!?…])\s+(?=[А-ЯІЇЄҐA-Z0-9«\"])", t)
    return [p.replace("\u2024", ".").strip() for p in parts if len(p.strip()) > 20]

def body_of(sents, limit=BODY_MAX):
    """Whole sentences only. If even the first is too long, cut it at a word and add an ellipsis."""
    out = ""
    for s in sents:
        if len((out + " " + s).strip()) <= limit:
            out = (out + " " + s).strip()
        else:
            break
    if not out and sents:
        head = sents[0][: limit - 1]
        out = head[: head.rfind(" ")].rstrip(" ,;:–—-") + "…" if " " in head else head
    return out

def make_item(id_, title, body, source, url, region, pub, now, extra=None):
    exp = pub + timedelta(hours=EXPIRE_H)
    if exp <= now or pub > now + timedelta(minutes=5):
        return None  # already expired, or a future-dated post
    it = {"id": id_, "title": title[:200], "body": body, "source": source, "source_url": url, "region": region,
          "category": "Офіційно / Official", "severity": "info", "auto": True,
          "issued_at": pub.strftime(FMT), "expires_at": exp.strftime(FMT)}
    it.update(extra or {})
    return it

# ---------- roads ----------
def listing(page):
    out = []
    blocks = re.findall(r'<section[^>]*class="blog-item.*?</section>', page, re.S) or re.findall(r"<article.*?</article>", page, re.S) or re.split(r'(?=<a[^>]+href="https://restoration\.gov\.ua/blog/)', page)
    for a in blocks:
        h = re.search(r'href="(https://restoration\.gov\.ua/blog/[^"#?]+)"', a)
        d = re.search(r'datetime="([^"]+)"', a)
        if h and d and not h.group(1).rstrip("/").endswith("/blog"):
            out.append((h.group(1), d.group(1)))
    return list(dict.fromkeys(out))

def parse_article(p):
    title = text(re.search(r"<h1[^>]*>(.*?)</h1>", p, re.S).group(1))
    m = re.search(r"<article.*?</article>", p, re.S)
    t = text(m.group(0)) if m else text(p)
    t = t.split(title, 1)[-1].replace("Дороги і мости Агентство відновлення", " ")
    t = re.sub(r"^\s*Назад\s*\d\d\.\d\d\.\d{4}\s*Новини\s*", "", t)
    return title, sentences(t.strip()), bool(re.search(r"Creative Commons Attribution 4", p)), bool(OTHER_CREDIT.search(t))

def roads(now=None, get=None):
    now = now or datetime.now(timezone.utc)
    get = get or (lambda u: requests.get(u, headers=UA, timeout=20))
    r = get("https://restoration.gov.ua/blog/"); r.raise_for_status()
    items, log = [], []
    for url, when in listing(r.text):
        pub = datetime.fromisoformat(when).astimezone(timezone.utc)
        if now - pub > timedelta(hours=MAX_AGE_H): continue
        a = get(url); a.raise_for_status()
        title, sents, lic, other = parse_article(a.text)
        if ROAD_DROP.search(title) or not ROAD.search(title + " " + " ".join(sents[:1])): log.append("skip topic: " + title[:50]); continue
        if not lic: log.append("skip, no licence statement: " + title[:50]); continue
        if other: log.append("skip, other credit: " + title[:50]); continue
        if len(title) > TITLE_MAX:
            log.append("needs review (title %d chars > %d, not auto-shortened): %s" % (len(title), TITLE_MAX, title)); continue
        it = make_item("gov-roads-" + url.rstrip("/").rsplit("/", 1)[-1][:40], title, body_of(sents),
                       "Агентство відновлення · CC BY 4.0", url, "Україна · дороги", pub, now, {"licence": "CC BY 4.0"})
        if it: items.append(it)
        else: log.append("skip, expired: " + title[:50])
        if len(items) >= 3: break
    return items, log

# ---------- water ----------
MSG = re.compile(r'<div class="tgme_widget_message_wrap.*?(?=<div class="tgme_widget_message_wrap|\Z)', re.S)

STREET = re.compile(r"\b(вул|просп|пров|бул|пл)\.|вулиц|провулок|будинк", re.I)

PARTIAL = re.compile(r"у частині|частин[аиі]|окремих|деяких|частков", re.I)

def scope_of(t):
    return "street-level" if STREET.search(t) else "area"

MONTHS = {"січня":1,"лютого":2,"березня":3,"квітня":4,"травня":5,"червня":6,"липня":7,"серпня":8,"вересня":9,"жовтня":10,"листопада":11,"грудня":12}
PLAN_DATE = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\s+(\d{4})", re.I)
PLAN_TIME = re.compile(r"\bз\s+(\d{1,2}):(\d{2})\s+до\s+(\d{1,2}):(\d{2})", re.I)
PLAN_PLACES = re.compile(r"відсутнє:\s*([^.]{3,160})\.", re.I)
PLANNED_HINT = re.compile(r"\bз\s+\d{1,2}:\d{2}\s+до\b|\d{1,2}\s+(?:" + "|".join(MONTHS) + r")\s+\d{4}", re.I)

def parse_planned(raw, now):
    """Planned-outage post: returns (start_utc, end_utc, day_local, places) or None if any part can't be read.
    Source times are Ukrainian local time (Europe/Kyiv)."""
    from zoneinfo import ZoneInfo
    d, t, pl = PLAN_DATE.search(raw), PLAN_TIME.search(raw), PLAN_PLACES.search(raw)
    if not (d and t and pl): return None
    tz = ZoneInfo("Europe/Kyiv")
    try:
        day = datetime(int(d.group(3)), MONTHS[d.group(2).lower()], int(d.group(1)))
        h1, m1, h2, m2 = map(int, t.groups())
        if not (0 <= h1 <= 23 and 0 <= m1 <= 59 and 0 <= h2 <= 24 and 0 <= m2 <= 59): return None
        start = day.replace(hour=h1, minute=m1, tzinfo=tz)
        end = (day + timedelta(days=1)).replace(tzinfo=tz) if (h2, m2) == (24, 0) else day.replace(hour=h2, minute=m2, tzinfo=tz)
    except (ValueError, KeyError):
        return None
    if end <= start: return None
    places = [x.strip() for x in pl.group(1).split(",") if x.strip()]
    if not places: return None
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), day, places

def parse_channel(page, src, now, log=None):
    items = []
    for blk in MSG.findall(page):
        post = re.search(r'data-post="([^"]+)"', blk); when = re.search(r'<time[^>]*datetime="([^"]+)"', blk)
        txt = re.search(r'<div class="tgme_widget_message_text[^>]*>(.*?)</div>', blk, re.S)
        if not (post and when and txt): continue
        raw = text(txt.group(1))
        raw = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]|#\S+|https?://\S+|t\.me/\S+|@\w+", "", raw)
        raw = re.sub(r"\s+", " ", raw).strip(" -–—•·|")
        if not WATER.search(raw) or MILITARY.search(raw) or GRAPHIC.search(raw) or NOISE.search(raw): continue
        sents = sentences(raw)
        if not sents: continue
        pub = datetime.fromisoformat(when.group(1)).astimezone(timezone.utc)
        if PLANNED_HINT.search(raw) or (PARTIAL.search(raw) and scope_of(raw) != "street-level"):
            pl = parse_planned(raw, now)
            if not pl or not src.get("oblast"):
                if log is not None: log.append("needs review (planned/partial outage not fully parsed): " + post.group(1))
                continue
            start, end, day, places = pl
            if end <= now: continue
            title = re.sub(r"\s+\d{4}\s+року", "", sents[0]).rstrip("!. ")
            body = "Заплановано %s, %s–%s · без води: %s" % (day.strftime("%d.%m"), "%02d:%s" % (int(PLAN_TIME.search(raw).group(1)), PLAN_TIME.search(raw).group(2)), "%02d:%s" % (int(PLAN_TIME.search(raw).group(3)), PLAN_TIME.search(raw).group(4)), ", ".join(places))
            items.append({"id": "gov-water-" + post.group(1).replace("/", "-"), "title": title, "body": body,
                          "source": src["name"], "source_url": "https://t.me/" + post.group(1),
                          "region": src["oblast"] + " · окремі пункти / localities", "category": "Офіційно / Official",
                          "severity": "info", "auto": True, "issued_at": pub.strftime(FMT), "expires_at": end.strftime(FMT),
                          "channel_verified": src["confirmed"], "scope": "planned-localities", "starts_at": start.strftime(FMT)})
            continue
        if len(body_of(sents[:1], 200)) > TITLE_MAX:
            if log is not None: log.append("needs review (title > %d chars, not auto-shortened): %s" % (TITLE_MAX, post.group(1)))
            continue
        reg = src["region"] + (" · окремі вулиці / local streets" if scope_of(raw) == "street-level"
                               else " · частково / partial area" if PARTIAL.search(raw) else "")
        it = make_item("gov-water-" + post.group(1).replace("/", "-"), body_of(sents[:1], 200), body_of(sents[1:]),
                       src["name"], "https://t.me/" + post.group(1), reg, pub, now,
                       {"channel_verified": src["confirmed"], "scope": scope_of(raw)})
        if it: items.append(it)
    return sorted(items, key=lambda i: i["issued_at"], reverse=True)[:2]

def water(now=None, get=None, sources=WATER_SOURCES, preview_unconfirmed=False):
    """Only link-confirmed channels are ingested. Unconfirmed ones are read only when preview_unconfirmed=True, and
    their items then carry preview_only=True and must never be written to the live bulletin file."""
    now = now or datetime.now(timezone.utc)
    sources = [s for s in sources if s["confirmed"] or preview_unconfirmed]
    get = get or (lambda u: requests.get(u, headers=TG_UA, timeout=20))
    items, log = [], []
    for s in sources:
        try:
            r = get("https://t.me/s/" + s["channel"]); r.raise_for_status()
            got = parse_channel(r.text, s, now, log)
            if not s["confirmed"]:
                for g_ in got: g_["preview_only"] = True
            items += got; log.append(f"{s['id']}: ok, showing {len(got)}")
        except Exception as e:
            log.append(f"{s['id']}: failed ({str(e)[:60]}), nothing shown")
    return items, log

if __name__ == "__main__":
    preview = "--preview-unconfirmed" in sys.argv
    ri, rl = roads(); wi, wl = water(preview_unconfirmed=preview)
    out = {"bulletins": [i for i in ri + wi if not i.get("preview_only")], "sources_log": rl + wl}
    if preview:  # clearly separate, never release-ready
        out["PREVIEW_ONLY_NOT_FOR_RELEASE"] = [i for i in wi if i.get("preview_only")]
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
