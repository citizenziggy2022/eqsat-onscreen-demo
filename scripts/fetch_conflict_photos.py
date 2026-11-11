#!/usr/bin/env python3
"""
fetch_conflict_photos.py — candidate fetcher for the eQsat "conflict photos" slide set.

Pulls the newest openly licensed conflict photos (damage, rescue, rebuilding,
daily life) from sources whose licence was checked on 2026-11-11, and returns
items in the photos.json item shape used by the screen
(https://citizenziggy2022.github.io/eqsat-onscreen-demo/photos.json), plus:
date_added, taken_at, place, source, source_url, caption_uk, caption_en,
active, kind:"conflict".

Fetch only. No model here: every item comes out with active=false and
review="pending_vision". Nothing goes on air from this script: an item moves to
photos-conflict.json (the on-air list) only through the Editor's Approve button
(a person looked at it) or a vision check. The runner has no vision check, so the
automatic check below is fail-closed and approves nothing.

Live-runner stage (v2.10, Nov 11 2026): `--runner` is called by fetch_news.py
(every ~2 min), throttles itself to every 10 min, writes photos-pending.json
(new candidates not already approved, rejected or in photos.json, newest first)
and stages it so the runner's commit carries it.

Hard rules (Jason, Nov 11 2026): Commons first; CC0 / CC BY / CC BY-SA / PD only,
CC BY-ND shown uncropped ("contain"), no NC; >= 1280 px wide; no per-event cap.
Never: news agencies (AP, Reuters, Getty, AFP, EPA), Russian sources, Telegram
images, president.gov.ua (NC-ND). Still hard-blocked at review: any visible body,
face, blood or injury, or anything revealing military positions (covered bodies
allowed, Jason 11:53).

Sources (see report for the licence wording):
  commons   Wikimedia Commons API — "Images from dsns.gov.ua" (DSNS photos, CC BY 4.0,
            mirrored to Commons within ~1 day) + per-day "Russo-Ukrainian War on YYYY-MM-DD"
            categories (one level of subcategories). CC / PD only.
  undp      UNDP Ukraine Flickr (NSID 61131792@N05) via the keyless public feed;
            licence read from each photo page. CC BY-ND 4.0 -> display UNCROPPED only.
  mod       mod.gov.ua (Ministry of Defence, footer: CC BY 4.0 "якщо не зазначено інше"),
            homepage news scrape, og:image large_ variant (1920 px).

Rules: landscape only, width >= MIN_WIDTH (default 1600), dedupe by image URL
(and by Commons file title), short captions, NC licences refused.

Usage:
  python3 fetch_conflict_photos.py [--days 7] [--limit 40] [--min-width 1600]
         [--sources commons,undp,mod] [--existing photos.json] [--out pending.json]
"""
import argparse, datetime as dt, hashlib, html, io, json, os, re, subprocess, sys, time, urllib.parse
import requests

UA = {"User-Agent": "eQsat-photo-fetch/0.2 (emergency broadcast; contact jason.roks@mail.tab.bot)"}
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
COMMONS = "https://commons.wikimedia.org/w/api.php"
UNDP_NSID = "61131792@N05"
TIMEOUT = 20
# sources never used, matched against author, credit, source page and description
BLOCKED_SOURCE = re.compile(r"(?i)\b(associated press|ap photo|reuters|getty|afp|agence france|epa[- ]efe|\bepa\b|anadolu|"
                            r"tass|ria novosti|sputnik|mil\.ru|kremlin\.ru|\.ru/|минобороны росс|ministry of defen[cs]e of russia|"
                            r"t\.me/|telegram|president\.gov\.ua|office of the president)")

OK_LICENCE = re.compile(r"^(CC BY(-SA)? [\d.]+|CC0|Public domain|PD.*|CC BY-ND [\d.]+)$", re.I)
NC = re.compile(r"\bNC\b|NonCommercial", re.I)

MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_UK = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]

# Front-line oblasts: a geotag here from a NON-official uploader is refused (targeting risk).
FRONT_OBLASTS = ("Donetsk", "Luhansk", "Zaporizhzhia", "Kherson", "Kharkiv", "Sumy", "Dnipropetrovsk")
OFFICIAL_AUTHORS = ("State Emergency Service of Ukraine", "ДСНС", "Ministry of Defence of Ukraine",
                    "Міноборони", "UNDP", "National Police of Ukraine")

# Small place dictionary EN -> UK for the caption line (extend as needed).
PLACE_UK = {
    "Kyiv": "Київ", "Kharkiv": "Харків", "Odesa": "Одеса", "Dnipro": "Дніпро", "Zaporizhzhia": "Запоріжжя",
    "Sloviansk": "Слов'янськ", "Kramatorsk": "Краматорськ", "Kropyvnytskyi": "Кропивницький",
    "Kamianske": "Кам'янське", "Lozova": "Лозова", "Sumy": "Суми", "Kherson": "Херсон",
    "Chornomorsk": "Чорноморськ", "Mykolaiv": "Миколаїв", "Lviv": "Львів", "Poltava": "Полтава",
    "Chernihiv": "Чернігів", "Kryvyi Rih": "Кривий Ріг", "Bila Tserkva": "Біла Церква",
    "Komyshuvakha": "Комишуваха", "Izmail": "Ізмаїл", "Pavlohrad": "Павлоград", "Vinnytsia": "Вінниця",
    "Zhytomyr": "Житомир", "Cherkasy": "Черкаси", "Chernivtsi": "Чернівці", "Uzhhorod": "Ужгород",
    "Ternopil": "Тернопіль", "Rivne": "Рівне", "Lutsk": "Луцьк", "Ivano-Frankivsk": "Івано-Франківськ",
    "Khmelnytskyi": "Хмельницький", "Bucha": "Буча", "Irpin": "Ірпінь", "Brovary": "Бровари",
}
SOURCE_SHORT = {"State Emergency Service of Ukraine": "ДСНС", "The State Emergency Service of Ukraine": "ДСНС",
                "National Police of Ukraine": "Нацполіція", "Ministry of Defence of Ukraine": "Міноборони",
                "UNDP Ukraine": "UNDP Ukraine"}


def strip_html(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return html.unescape(re.sub(r"\s+", " ", s)).strip()


def short(s, n):
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:—-") + "…"


def first_sentence(s):
    m = re.match(r"(.+?[.!?])(\s|$)", s or "")
    return m.group(1) if m else (s or "")


def screen_line(place, taken_at, source_short, licence, lang="en"):
    """e.g. 'Kharkiv, Nov 10 · ДСНС, CC BY 4.0'"""
    d = ""
    try:
        t = dt.date.fromisoformat((taken_at or "")[:10])
        d = f"{MONTHS_EN[t.month-1]} {t.day}" if lang == "en" else f"{t.day} {MONTHS_UK[t.month-1]}"
    except ValueError:
        pass
    head = ", ".join(x for x in [place, d] if x)
    return f"{head} · {source_short}, {licence}" if head else f"{source_short}, {licence}"


def make_item(*, image_url, width, height, caption_en, caption_uk, place, taken_at, author, licence,
              source, source_url, source_short, extra=None):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    place_city = (place or "").split(",")[0].strip()
    it = {
        # existing photos.json fields (same names)
        "image_url": image_url,
        "caption_uk": short(caption_uk, 90),
        "caption_en": short(caption_en, 90),
        "place": place or "Ukraine",
        "date": (taken_at or "")[:10],
        "author": author,
        "license": licence,
        "caption": short(caption_uk or caption_en, 90),
        "taken": screen_line(place_city, taken_at, source_short, licence, "en"),          # e.g. "Kharkiv, Nov 10 · ДСНС, CC BY 4.0"
        "order": 0.0,
        "active": False,
        # new fields
        "id": "cf_" + hashlib.sha1(((extra or {}).get("commons_title") or image_url).encode()).hexdigest()[:12],
        "kind": "conflict",
        "date_added": now,
        "taken_at": taken_at,
        "source": source,
        "source_url": source_url,
        "taken_uk": screen_line(PLACE_UK.get(place_city, place_city), taken_at, source_short, licence, "uk"),
        "width": width,
        "height": height,
        "display": "contain" if "ND" in licence else "cover",   # ND = no crop allowed
        "review": "pending_vision",
    }
    if extra:
        it.update(extra)
    return it


MONTH_NAMES = {m.lower(): i+1 for i, m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"])}


def norm_date(raw, title=""):
    """Commons DateTimeOriginal is free text ('2026-11-10', 'Taken on 2 November 2026', ...)."""
    raw = raw or ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", raw)
    if m and m.group(2).lower() in MONTH_NAMES:
        return f"{m.group(3)}-{MONTH_NAMES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", title or "")
    return m.group(0) if m else ""


# Topic filter for sources that also post conferences/portraits (UNDP, MoD).
TOPIC = re.compile(r"(?i)rebuil|reconstruct|repair|restor|debris|rubble|damag|destroy|attack|strike|shelter|"
                   r"generator|\\benergy\\b|power (?:grid|cut|outage)|heating|demin|landmine|\\bmines?\\b|humanitarian|evacuat|displaced|\\bIDPs?\\b|\\baid\\b|"
                   r"відбуд|ремонт|віднов|руйн|удар|атак|укритт|генератор|енерг|розмінув|гуманітар|евакуац|"
                   r"бійц|воїн|військовослужбов|soldier|brigade|бригад")


def oblast_of(cats):
    for c in cats:
        m = re.search(r"in (\w[\w\- ]*?) Oblast", c)
        if m:
            return m.group(1)
    return ""


def place_from(title, cats):
    m = re.search(r" in ([A-Z][\w'\- ]+?) (?:after|during|on|,|\()", title)
    city = m.group(1) if m else ""
    if not city:
        for c in cats:
            m = re.match(r"War in ([\w'\- ]+?),", c)
            if m:
                city = m.group(1)
                break
    obl = oblast_of(cats)
    if city and obl and obl != city:
        return f"{city}, {obl} Oblast", obl
    return (city or (obl + " Oblast" if obl else "")), obl


def uk_caption_from_en(title, place_city):
    """Deterministic Ukrainian caption for the common Commons title patterns; the vision stage may refine."""
    pu = PLACE_UK.get(place_city, place_city)
    t = title.lower()
    if "after russian attack" in t or "destruction" in t or "damage" in t:
        if "rescuer" in t or "rescue" in t:
            return f"{pu}: рятувальники після російського удару"
        return f"{pu}: наслідки російського удару"
    if "repair" in t or "rebuild" in t or "reconstruction" in t:
        return f"{pu}: відбудова"
    return ""


# ---------------------------------------------------------------- Commons
def commons_members(s, cat, typ="file", limit=100):
    p = dict(action="query", list="categorymembers", cmtitle="Category:" + cat, cmtype=typ, cmlimit=limit,
             cmsort="timestamp", cmdir="desc", cmprop="title|timestamp", format="json")
    return s.get(COMMONS, params=p, timeout=TIMEOUT).json().get("query", {}).get("categorymembers", [])


def commons_info(s, titles):
    out = {}
    for i in range(0, len(titles), 40):
        p = dict(action="query", titles="|".join(titles[i:i+40]), prop="imageinfo|coordinates",
                 iiprop="url|size|mime|extmetadata", iiurlwidth=1920, iiextmetadatalanguage="en",
                 coprop="type", format="json")
        for pg in s.get(COMMONS, params=p, timeout=TIMEOUT).json().get("query", {}).get("pages", {}).values():
            out[pg.get("title")] = pg
    return out


def fetch_commons(days=7, min_width=1600, log=None):
    s = requests.Session(); s.headers.update(UA)
    titles, seen = [], set()
    def add(ms):
        for m in ms:
            if m["title"].startswith("File:") and m["title"] not in seen:
                seen.add(m["title"]); titles.append(m["title"])
    add(commons_members(s, "Images from dsns.gov.ua", "file", 150))
    today = dt.datetime.now(dt.timezone.utc).date()
    for i in range(days):
        day = (today - dt.timedelta(days=i)).isoformat()
        cat = f"Russo-Ukrainian War on {day}"
        add(commons_members(s, cat, "file", 200))
        for sc in commons_members(s, cat, "subcat", 50):
            add(commons_members(s, sc["title"][9:], "file", 100))
    info = commons_info(s, titles)
    items = []
    cutoff = today - dt.timedelta(days=days)
    for t in titles:
        pg = info.get(t)
        if not pg or "imageinfo" not in pg:
            continue
        ii = pg["imageinfo"][0]; em = ii.get("extmetadata", {})
        g = lambda k: strip_html(em.get(k, {}).get("value", ""))
        lic = g("LicenseShortName"); artist = g("Artist"); taken = norm_date(g("DateTimeOriginal"), t)
        cats = em.get("Categories", {}).get("value", "").split("|")
        reason = None
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp"):
            reason = "not a photo file (%s)" % ii.get("mime")
        elif NC.search(lic) or not OK_LICENCE.match(lic):
            reason = f"licence not allowed: {lic or 'none'}"
        elif ii["width"] <= ii["height"]:
            reason = f"not landscape ({ii['width']}x{ii['height']})"
        elif ii["width"] < min_width:
            reason = f"too small ({ii['width']} px wide < {min_width})"
        elif taken and taken < cutoff.isoformat():
            reason = f"taken {taken}, older than {days} days"
        if not reason and BLOCKED_SOURCE.search(" ".join([artist, g("Credit"), g("ImageDescription")[:300]])):
            reason = "blocked source (agency / Russian / Telegram / president.gov.ua)"
        place, obl = place_from(t[5:], cats)
        official = any(o.lower() in artist.lower() for o in OFFICIAL_AUTHORS)
        if not reason and pg.get("coordinates") and obl in FRONT_OBLASTS and not official:
            reason = f"geotagged in front-line {obl} Oblast by non-official uploader"
        if reason:
            if log is not None:
                log.append({"source": "commons", "title": t, "reason": reason, "stage": "fetch"})
            continue
        desc_en = first_sentence(g("ImageDescription"))
        city = place.split(",")[0]
        thumb = ii.get("thumburl") or ii["url"]
        if ii["width"] <= 1920:
            thumb = ii["url"]
        thumb = re.sub(r"\?utm_.*$", "", thumb)
        src_short = SOURCE_SHORT.get(artist, short(artist, 28))
        items.append(make_item(
            image_url=thumb, width=ii["width"], height=ii["height"],
            caption_en=desc_en or t[5:].rsplit(".", 1)[0], caption_uk=uk_caption_from_en(t, city),
            place=place, taken_at=taken or None, author=artist, licence=lic,
            source="Wikimedia Commons" + (" (dsns.gov.ua)" if "Images from dsns.gov.ua" in cats else ""),
            source_url=ii.get("descriptionurl"), source_short=src_short,
            extra={"commons_title": t, "orig_url": ii["url"],
                   "preview_url": ii["url"] if ii["width"] <= 640 else None}))
    return items


# ---------------------------------------------------------------- UNDP Ukraine (Flickr, keyless)
FLICKR_LIC = {"creativecommons.org/licenses/by/2.0": "CC BY 2.0", "creativecommons.org/licenses/by/4.0": "CC BY 4.0",
              "creativecommons.org/licenses/by-sa/2.0": "CC BY-SA 2.0", "creativecommons.org/licenses/by-sa/4.0": "CC BY-SA 4.0",
              "creativecommons.org/licenses/by-nd/2.0": "CC BY-ND 2.0", "creativecommons.org/licenses/by-nd/4.0": "CC BY-ND 4.0",
              "creativecommons.org/publicdomain/zero/1.0": "CC0", "creativecommons.org/publicdomain/mark/1.0": "Public domain"}


def fetch_undp(days=7, min_width=1600, log=None):
    feed = requests.get(f"https://www.flickr.com/services/feeds/photos_public.gne?id={UNDP_NSID}&format=json&nojsoncallback=1",
                        headers=BROWSER_UA, timeout=TIMEOUT).json()
    items = []
    cutoff = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=days)).isoformat()
    for it in feed.get("items", []):
        taken = it.get("date_taken", "")[:10]
        if taken and taken < cutoff:
            continue
        page = requests.get(it["link"], headers=BROWSER_UA, timeout=TIMEOUT).text
        m = re.search(r"creativecommons\.org/(?:licenses|publicdomain)/[a-z\-]+/[\d.]+", page)
        lic = FLICKR_LIC.get(m.group(0)) if m else None
        if not lic:
            if log is not None: log.append({"source": "undp", "title": it["link"], "reason": "no CC licence on page", "stage": "fetch"})
            continue
        # largest size from the page's size table (keys like "k" 2048, "h" 1600, "l" 1024)
        sizes = {}
        for sm in re.finditer(r'"(\w{1,2})":\{"displayUrl":"([^"]+)","width":(\d+),"height":(\d+)', page):
            sizes[sm.group(1)] = ("https:" + sm.group(2).replace("\\/", "/"), int(sm.group(3)), int(sm.group(4)))
        best = None
        for k in ("k", "h", "l", "c", "z"):
            if k in sizes:
                best = sizes[k]; break
        if not best:
            if log is not None: log.append({"source": "undp", "title": it["link"], "reason": "no downloadable size", "stage": "fetch"})
            continue
        url, w, h = best
        reason = None
        if w <= h: reason = f"not landscape ({w}x{h})"
        elif w < min_width: reason = f"too small ({w} px < {min_width})"
        if reason:
            if log is not None: log.append({"source": "undp", "title": it["link"], "reason": reason, "stage": "fetch"})
            continue
        desc = strip_html(re.search(r'<meta name="description" content="([^"]*)"', page).group(1)) if 'name="description"' in page else ""
        album = " ".join(re.findall(r'"title":"([^"]{5,120})"', page)[:6])
        if not TOPIC.search(desc + " " + it.get("title", "") + " " + it.get("tags", "") + " " + album):
            if log is not None: log.append({"source": "undp", "title": it["link"], "reason": "off-topic (no war/recovery words in caption or tags): " + short(desc, 60), "stage": "fetch"})
            continue
        small = sizes.get("n") or sizes.get("z") or best
        items.append(make_item(image_url=url, width=w, height=h, caption_en=first_sentence(desc), caption_uk="",
                               place="Ukraine", taken_at=taken or None, author="UNDP Ukraine", licence=lic,
                               source="UNDP Ukraine (Flickr)", source_url=it["link"], source_short="UNDP Ukraine",
                               extra={"preview_url": small[0]}))
    return items


# ---------------------------------------------------------------- mod.gov.ua
SKIP_MOD = re.compile(r"bojovi-vtrati|zakupiv|tender|posvidok|kontrakt", re.I)


def fetch_mod(days=3, min_width=1600, log=None):
    home = requests.get("https://mod.gov.ua/", headers=BROWSER_UA, timeout=TIMEOUT).text
    slugs = list(dict.fromkeys(re.findall(r"/news/[a-z0-9\-]{25,}", home)))
    items = []
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    for sl in slugs[:20]:
        if SKIP_MOD.search(sl):
            if log is not None: log.append({"source": "mod", "title": sl, "reason": "infographic/admin post (losses, tenders, contracts)", "stage": "fetch"})
            continue
        a = requests.get("https://mod.gov.ua" + sl, headers=BROWSER_UA, timeout=TIMEOUT).text
        og = re.search(r'property="og:image" content="([^"]+)"', a)
        pt = re.search(r'"(?:datePublished|publishedAt|published_at)\\?":\\?"([^"\\]+)', a)
        ttl = re.search(r'property="og:title" content="([^"]+)"', a)
        if not og or "default-image" in og.group(1):
            continue
        pub = pt.group(1) if pt else ""
        if pub and pub < cutoff:
            continue
        img = og.group(1)
        large = img.replace("/assets/", "/assets/large_") if "/assets/large_" not in img else img
        w = h = 0
        try:
            from PIL import Image
            r = requests.get(large, headers=BROWSER_UA, timeout=TIMEOUT)
            if r.status_code != 200:
                large = img; r = requests.get(img, headers=BROWSER_UA, timeout=TIMEOUT)
            w, h = Image.open(io.BytesIO(r.content)).size
        except Exception:
            pass
        reason = None
        if w <= h: reason = f"not landscape ({w}x{h})"
        elif w < min_width: reason = f"too small ({w} px < {min_width})"
        if reason:
            if log is not None: log.append({"source": "mod", "title": sl, "reason": reason, "stage": "fetch"})
            continue
        title_uk = html.unescape(ttl.group(1)) if ttl else ""
        if not TOPIC.search(title_uk):
            if log is not None: log.append({"source": "mod", "title": sl, "reason": "off-topic (meeting/policy post): " + short(title_uk, 60), "stage": "fetch"})
            continue
        items.append(make_item(image_url=large, width=w, height=h, caption_en="", caption_uk=title_uk,
                               place="Ukraine", taken_at=pub[:10] or None, author="Ministry of Defence of Ukraine",
                               licence="CC BY 4.0", source="mod.gov.ua", source_url="https://mod.gov.ua" + sl,
                               source_short="Міноборони",
                               extra={"preview_url": large.replace("/assets/large_", "/assets/small_")}))
    return items


# ---------------------------------------------------------------- main
def fetch_all(sources=("commons", "undp", "mod"), days=7, min_width=1600, existing_urls=(), limit=40):
    log, items = [], []
    fns = {"commons": fetch_commons, "undp": fetch_undp, "mod": fetch_mod}
    for name in sources:
        try:
            items += fns[name](days=days, min_width=min_width, log=log)
        except Exception as e:
            log.append({"source": name, "reason": f"source failed: {e!r}", "stage": "fetch"})
    seen, out = set(existing_urls), []
    for it in items:
        hay = " ".join(str(it.get(k) or "") for k in ("author", "source", "source_url", "caption_en", "caption_uk", "taken"))
        if BLOCKED_SOURCE.search(hay):
            log.append({"source": it["source"], "title": it.get("commons_title") or it["source_url"], "reason": "blocked source (agency / Russian / Telegram / president.gov.ua)", "stage": "fetch"})
            continue
        key = it["image_url"].split("?")[0]
        if key in seen or it.get("orig_url", "").split("?")[0] in seen:
            log.append({"source": it["source"], "title": it.get("commons_title") or it["source_url"], "reason": "duplicate image URL", "stage": "fetch"})
            continue
        seen.add(key); out.append(it)
    out.sort(key=lambda x: (x.get("taken_at") or "", x["date_added"]), reverse=True)
    return out[:limit], log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--min-width", type=int, default=1600)
    ap.add_argument("--sources", default="commons,undp,mod")
    ap.add_argument("--existing", help="photos.json path or URL; its image URLs are skipped")
    ap.add_argument("--out", default="-")
    a = ap.parse_args()
    existing = []
    if a.existing:
        src = a.existing
        data = requests.get(src, timeout=TIMEOUT).json() if src.startswith("http") else json.load(open(src))
        existing = [i.get("image_url", "").split("?")[0] for i in data.get("items", [])]
    items, log = fetch_all(a.sources.split(","), a.days, a.min_width, existing, a.limit)
    res = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "items": items, "fetch_rejects": log}
    txt = json.dumps(res, ensure_ascii=False, indent=1)
    if a.out == "-": print(txt)
    else: open(a.out, "w").write(txt)
    print(f"{len(items)} candidates, {len(log)} fetch-stage rejects", file=sys.stderr)


# ---------------------------------------------------------------- live-runner stage (v2.10)
EVERY_S = 600
PENDING = "photos-pending.json"
ONAIR = "photos-conflict.json"
KEEP_DAYS = 14


def auto_check(item):
    """The fail-closed check an item must pass to go on air without a person. The runner has no vision model, so it
    approves nothing: every new item waits in photos-pending.json for the Editor's Approve (or a vision check run
    elsewhere that writes photos-conflict.json)."""
    return False


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _previews(items):
    titles = [it["commons_title"] for it in items if it.get("commons_title") and not it.get("preview_url")]
    if not titles:
        return
    s = requests.Session(); s.headers.update(UA)
    got = {}
    for i in range(0, len(titles), 40):
        p = dict(action="query", titles="|".join(titles[i:i+40]), prop="imageinfo", iiprop="url", iiurlwidth=400, format="json")
        for pg in s.get(COMMONS, params=p, timeout=TIMEOUT).json().get("query", {}).get("pages", {}).values():
            ii = (pg.get("imageinfo") or [{}])[0]
            if ii.get("thumburl"):
                got[pg.get("title")] = re.sub(r"\?utm_.*$", "", ii["thumburl"])
    for it in items:
        if it.get("commons_title") in got:
            it["preview_url"] = got[it["commons_title"]]


def runner_main(force=False):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    nowz = now.isoformat().replace("+00:00", "Z")
    prev = _load(PENDING) or {}
    last = prev.get("fetched_at")
    if last and not force:
        try:
            age = (now - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds()
        except Exception:
            age = EVERY_S
        if age < EVERY_S - 30:
            print(f"conflict photos: fetched {int(age)} s ago, next in {int(EVERY_S - age)} s")
            return
    onair = _load(ONAIR) or {}
    photos = _load("photos.json") or {}
    known = set()
    for it in (onair.get("items") or []) + (onair.get("rejected") or []):
        for k in ("id", "commons_title", "image_url", "source_url"):
            if it.get(k):
                known.add(str(it[k]).split("?")[0])
    existing = [str(i.get("image_url", "")).split("?")[0] for i in photos.get("items", [])] + list(known)
    t0 = time.time()
    try:
        items, log = fetch_all(("commons", "undp"), days=7, min_width=1280, existing_urls=existing, limit=60)
    except Exception as e:  # keep the last good file
        print("conflict photos: fetch failed, keeping the last file:", repr(e))
        return
    items = [it for it in items if not ({it.get("id"), it.get("commons_title"), it.get("source_url")} & known)]
    # still-pending items from earlier fetches stay (a source that failed this time does not empty the list)
    cutoff = (now - dt.timedelta(days=KEEP_DAYS)).isoformat().replace("+00:00", "Z")
    ids = {it["id"] for it in items}
    for it in prev.get("items") or []:
        if it.get("id") in ids or (it.get("date_added") or "") < cutoff:
            continue
        if {it.get("id"), it.get("commons_title"), it.get("source_url"), str(it.get("image_url", "")).split("?")[0]} & known:
            continue
        items.append(it); ids.add(it["id"])
    try:
        _previews(items)
    except Exception as e:
        print("conflict photos: previews skipped:", repr(e))
    for it in items:
        it["active"] = False
        it["review"] = "pending_vision"
    auto = [it for it in items if auto_check(it)]  # fail-closed: always empty here
    items.sort(key=lambda x: (x.get("taken_at") or "", x.get("date_added") or ""), reverse=True)
    out = {
        "fetched_at": nowz,
        "every_s": EVERY_S,
        "note": "Conflict photo candidates, NOT on air. Fetched by scripts/fetch_conflict_photos.py in the live-runner every 10 min. "
                "An item goes on air only when the Editor's Approve moves it to photos-conflict.json.",
        "check": "none in the runner (no vision model): nothing is approved automatically",
        "auto_approved": len(auto),
        "items": items[:80],
        "fetch_rejects": log[-80:],
        "took_s": round(time.time() - t0, 1),
    }
    with open(PENDING, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"conflict photos: {len(out['items'])} pending, {len(log)} rejected at fetch, {out['took_s']} s")
    subprocess.run(["git", "add", PENDING], check=False, timeout=30)


if __name__ == "__main__":
    if "--runner" in sys.argv:
        runner_main(force="--force" in sys.argv)
    else:
        main()
