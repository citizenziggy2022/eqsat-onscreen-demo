#!/usr/bin/env python3
"""pow.json for the eQsat screen's POW-exchange banner (v2.24, Nov 11 2026; Jason's queue item E).

Source: the Coordination Headquarters for the Treatment of Prisoners of War (t.me/s/Koord_shtab, the channel linked from
koordshtab.gov.ua), read with the same Telegram preview scraper as the auto bulletins (scripts/fetch_bulletins.py: ETag,
polite back-off on 429). The screen shows a slim banner for 24 h after an exchange.

What counts as an exchange (checked on 343 posts, Aug 27 - Nov 11 2026): nearly every post of the channel says "обмін"
(meetings with families talk about exchanges), so a post counts only when "обмін" is in its opening AND the opening
gives the number of people freed / returned home. Posts about meetings, fakes / scams, bodies or repatriation never
count. In that sample exactly two posts matched, both about the Oct 23 2026 exchange (270 people). One exchange per Kyiv
day: the highest number the channel gave that day, linked to the day's first matching post. Forwarded posts are skipped.
Run every live-runner cycle from fetch_feed.py; throttles itself to 120 s; keeps the last file on failure.
"""
import json, os, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_bulletins as fb  # the shared scraper

OUT = "pow.json"
STATE = ".cache/fetch_pow_state.json"
CHANNEL = "Koord_shtab"
MIN_INTERVAL_S = 120
KEEP_H = 24
KYIV = ZoneInfo("Europe/Kyiv")
EXCLUDE = re.compile(r"фейк|шахра|тіл[аох]\b|репатріац|зустріч", re.I)
COUNT = re.compile(r"(?:визволен\w*|звільнен\w*|повертаються|повернул\w*)[^.\d]{0,60}?(\d{2,4})|(\d{2,4})\s+(?:українц|українськ|військов|захисник|оборонц)", re.I)


def exchange_count(text):
    """the number of people freed when this post reports an exchange, else None"""
    head = text[:400]
    if not re.search(r"обмін", head, re.I) or EXCLUDE.search(head):
        return None
    m = COUNT.search(head)
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    return n if 1 <= n <= 5000 else None


def parse(page, now):
    out = []
    for block in fb.MSG.findall(page):
        post = re.search(r'data-post="([^"]+)"', block)
        when = re.search(r'<time datetime="([^"]+)"', block)
        body = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not (post and when and body) or "tgme_widget_message_forwarded_from" in block:
            continue
        try:
            published = datetime.fromisoformat(when.group(1).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
        if now - published > timedelta(hours=KEEP_H) or published > now + timedelta(minutes=5):
            continue
        text = re.sub(r"\s+", " ", fb.text_of(body.group(1))).strip()
        n = exchange_count(text)
        if n:
            out.append({"id": post.group(1), "url": f"https://t.me/{post.group(1)}", "time": published.strftime("%Y-%m-%dT%H:%M:%SZ"), "count": n})
    return out


def by_day(posts):
    days = {}
    for p in sorted(posts, key=lambda p: p["time"]):
        day = datetime.fromisoformat(p["time"].replace("Z", "+00:00")).astimezone(KYIV).strftime("%Y-%m-%d")
        d = days.setdefault(day, {"day": day, "time": p["time"], "url": p["url"], "count": 0, "posts": []})
        d["count"] = max(d["count"], p["count"])
        d["posts"].append(p["id"])
    return sorted(days.values(), key=lambda d: d["time"], reverse=True)


def main(force=False):
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {}
    if not force and time.time() - st.get("ran_at", 0) < MIN_INTERVAL_S:
        return 0
    st["ran_at"] = time.time()
    try:
        old = json.load(open(OUT))
    except Exception:
        old = {}
    now = datetime.now(timezone.utc)
    keep = lambda ps: [p for p in ps or [] if now - datetime.fromisoformat(p["time"].replace("Z", "+00:00")) < timedelta(hours=KEEP_H)]
    status = "ok"
    try:
        b = st.setdefault("backoff", {}).get(CHANNEL)
        if b and time.time() < b.get("until", 0):
            raise fb.Backoff(f"backing off {int(b['until'] - time.time())} s more")
        page = fb.get_page(f"https://t.me/s/{CHANNEL}", st, CHANNEL)
        if "tgme_channel_info" not in page:
            raise RuntimeError("no channel preview")
        posts = parse(page, now)
        # posts seen earlier today stay even when newer posts push them off the preview page
        seen = {p["id"]: p for p in keep(old.get("posts"))}
        seen.update({p["id"]: p for p in posts})
        posts = list(seen.values())
    except fb.NotModified:
        posts = keep(old.get("posts"))
        status = old.get("status") or "ok"
    except Exception as exc:
        posts = keep(old.get("posts"))
        status = f"failed ({str(exc)[:80]}), kept {len(posts)}"
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"))
    posts = sorted(posts, key=lambda p: p["time"], reverse=True)
    data = {
        "note": "POW exchanges reported by the Coordination HQ for the Treatment of POWs (t.me/s/Koord_shtab) in the last 24 h. The screen shows a banner while one is listed. Written by scripts/fetch_pow.py.",
        "source": "Координаційний штаб з питань поводження з військовополоненими",
        "source_url": "https://t.me/s/Koord_shtab",
        "status": status,
        "posts": posts,
        "exchanges": by_day(posts),
    }
    if not force and old.get("posts") == data["posts"] and old.get("status") == status and old.get("generated_at") and \
            (now - datetime.fromisoformat(old["generated_at"].replace("Z", "+00:00"))).total_seconds() < 600:
        print(f"pow: unchanged ({len(data['exchanges'])} exchange(s) in 24 h)")
        return 0
    data["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = OUT + ".tmp"
    json.dump(data, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print(f"pow: {len(data['exchanges'])} exchange(s) in 24 h, {status}")
    subprocess.run(["git", "add", OUT], check=False, timeout=30)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(force="--force" in sys.argv))
    except Exception as exc:
        print("pow failed, keeping last file:", exc)
        sys.exit(1)
