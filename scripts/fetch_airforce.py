#!/usr/bin/env python3
"""airforce.json for the eQsat screen's red Air Force line (v2.15, Nov 11 2026; Jason's queue item C).

The Air Force of Ukraine's public warnings about incoming drones and missiles (t.me/s/kpszsu, "Повітряні Сили ЗС
України", the channel linked from the Air Force's official pages), read with the same Telegram preview scraper as the
auto bulletins (scripts/fetch_bulletins.py: ETag, polite back-off on 429) and the same filters, word for word.
The screen shows only posts under 15 minutes old, as a red line in the live news crawl; no map, no coordinates.

Filter: only threat warnings (БпЛА, ракета, балістика, КАБ, загроза, курс, напрямок ...). Dropped like the bulletins:
anything about our forces or positions, interception results (ППО, збито), losses, units (the bulletins' MILITARY list;
"напрямок" alone is allowed here, because the direction of an incoming drone IS the warning), anything graphic, noise
(all-clear posts, ads). Forwarded posts are skipped (the Air Force's own posts only).
Run every live-runner cycle from fetch_feed.py; throttles itself to 50 s; keeps the last file on failure.
"""
import html, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_bulletins as fb  # the shared scraper and filters

OUT = "airforce.json"
STATE = ".cache/fetch_airforce_state.json"
CHANNEL = "kpszsu"
MIN_INTERVAL_S = 50
KEEP_MIN = 60  # the file keeps the last hour; the screen shows only < 15 min
THREAT = re.compile(r"БпЛА|безпілот|дрон|шахед|ракет|балістик|крилат|\bКАБ|авіабомб|загроз|курс|напрямк|швидкісн|реактивн", re.I)


def military_hit(text):
    for m in fb.MILITARY.finditer(text):
        if not m.group(0).lower().startswith("напрямк"):
            return m.group(0)
    return None


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
    status = "ok"
    posts, dropped = [], []
    try:
        b = st.setdefault("backoff", {}).get(CHANNEL)
        if b and time.time() < b.get("until", 0):
            raise fb.Backoff(f"backing off {int(b['until'] - time.time())} s more")
        page = fb.get_page(f"https://t.me/s/{CHANNEL}", st, CHANNEL)
        if "tgme_channel_info" not in page:
            raise RuntimeError("no channel preview")
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
            if now - published > timedelta(minutes=KEEP_MIN) or published > now + timedelta(minutes=5):
                continue
            raw = fb.text_of(body.group(1))
            text = re.sub(r"\s+", " ", raw).strip()  # word for word, whitespace only
            why = None
            if not THREAT.search(text):
                why = "not a threat warning"
            elif military_hit(text):
                why = f"military word: {military_hit(text)}"
            elif fb.GRAPHIC.search(text):
                why = "graphic"
            elif fb.NOISE.search(text):
                why = "noise / all-clear"
            elif len(text) > 400:
                why = "too long for one line"
            if why:
                dropped.append({"url": f"https://t.me/{post.group(1)}", "reason": why})
                continue
            posts.append({"id": post.group(1), "text": text, "time": published.strftime("%Y-%m-%dT%H:%M:%SZ"), "url": f"https://t.me/{post.group(1)}"})
    except fb.NotModified:
        posts = [p for p in old.get("posts") or [] if (now - datetime.fromisoformat(p["time"].replace("Z", "+00:00"))) < timedelta(minutes=KEEP_MIN)]
        status = old.get("status") or "ok"
    except Exception as exc:
        posts = [p for p in old.get("posts") or [] if (now - datetime.fromisoformat(p["time"].replace("Z", "+00:00"))) < timedelta(minutes=KEEP_MIN)]
        status = f"failed ({str(exc)[:80]}), kept {len(posts)}"
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w"))
    posts = sorted({p["id"]: p for p in posts}.values(), key=lambda p: p["time"], reverse=True)
    data = {
        "note": "Air Force of Ukraine public threat warnings (t.me/s/kpszsu), word for word. The screen shows posts under 15 min old as a red line in the live news crawl. Written by scripts/fetch_airforce.py.",
        "source": "Повітряні Сили ЗС України",
        "source_url": "https://t.me/s/kpszsu",
        "status": status,
        "posts": posts,
        "dropped": dropped[:20],
    }
    # rewrite only when the posts or the state changed, or every 5 min so the screen can tell the file is alive
    if not force and old.get("posts") == data["posts"] and old.get("status") == status and old.get("generated_at") and \
            (now - datetime.fromisoformat(old["generated_at"].replace("Z", "+00:00"))).total_seconds() < 300:
        print(f"air force: unchanged ({len(posts)} in the last hour)")
        return 0
    data["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp = OUT + ".tmp"
    json.dump(data, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print(f"air force: {len(posts)} posts in the last hour, {len(dropped)} dropped, {status}")
    subprocess.run(["git", "add", OUT], check=False, timeout=30)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(force="--force" in sys.argv))
    except Exception as exc:
        print("air force failed, keeping last file:", exc)
        sys.exit(1)
