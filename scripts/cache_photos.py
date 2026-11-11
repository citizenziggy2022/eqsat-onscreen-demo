#!/usr/bin/env python3
"""photos-cache/ for the eQsat screen's photo panel (v2.27, Nov 11 2026; Jason 7:58 PM, photo bandwidth fix).

The screen used to load every approved photo as its full-size original from Wikimedia (up to ~720 KB each). This makes
one WebP per ON-AIR photo, at most 960 px wide (quality 75; 1280 px in v2.27, 960 px since Jason's OK
Nov 11 8:39 PM: the on-air photo box is 951 px wide), never cropped (a resize keeps the whole picture, so the
CC BY-ND photos shown "contain" stay uncut), and stores it in the public repo as photos-cache/<hash>.webp, where <hash>
is the first 16 hex of sha1(image_url). photos-cache/index.json maps each original image_url to its file. photos.json
and photos-conflict.json are not touched: the original Wikimedia URL, licence, author and credit fields stay exactly as
they are, and the screen still prints the credit from them. Pending / unapproved photos are never fetched.

The on-air list is the screen's own (src/hooks/usePlayoutData.js): photos-conflict.json items with active true and
review "approved", then photos.json items that are not active false. Run by the live-runner through fetch_news.py; it
throttles itself to every 10 min, does at most MAX_NEW new photos per run, and stages photos-cache/ so the runner's
commit carries it. A photo that fails to download or convert is simply left uncached (the screen then loads the original).
"""
import datetime as dt, hashlib, io, json, os, subprocess, sys, time

OUT_DIR = "photos-cache"
INDEX = os.path.join(OUT_DIR, "index.json")
EVERY_S = 10 * 60
MAX_NEW = 40
MAX_W = 960
QUALITY = 75
UA = "eQsat-photo-cache/1.0 (+https://citizenziggy2022.github.io/eqsat-onscreen-demo/; resizes approved CC photos for a TV screen)"


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def on_air():
    photos = load("photos.json", {})
    conflict = load("photos-conflict.json", {})
    live = [s for s in conflict.get("items") or [] if s and s.get("active") is True and s.get("image_url") and s.get("review") == "approved"]
    seen = {s["image_url"] for s in live}
    rest = [s for s in photos.get("items") or [] if s and s.get("active") is not False and s.get("image_url") and s["image_url"] not in seen]
    return [s["image_url"] for s in live + rest]


def thumb_url(url):
    # Wikimedia's own standard 960 px step for an upload.wikimedia.org original (lighter on their servers); None if not one
    m = __import__("re").match(r"^https://upload\.wikimedia\.org/wikipedia/commons/(?!thumb/)([0-9a-f]/[0-9a-f]{2})/([^/?#]+)$", url)
    if not m:
        return None
    name = m.group(2)
    return f"https://upload.wikimedia.org/wikipedia/commons/thumb/{m.group(1)}/{name}/{MAX_W}px-{name}"


def pil():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pillow"], check=False, timeout=120)
    from PIL import Image
    return Image


def main(force=False):
    idx = load(INDEX, {})
    files = idx.get("files") or {}
    if not force and idx.get("checked_at"):
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(idx["checked_at"].replace("Z", "+00:00"))).total_seconds()
        if age < EVERY_S:
            print(f"photos-cache: checked {int(age)} s ago")
            return
    urls = on_air()
    missing = [u for u in urls if u not in files or not os.path.exists(files[u]["file"])]
    # made at an older, wider size (1280 px before Nov 11): redo at MAX_W, after the missing ones; until then the old file serves
    wide = [u for u in urls if u not in missing and (files[u].get("w") or 0) > MAX_W]
    todo = missing + wide
    if todo:
        import requests
        Image = pil()
        os.makedirs(OUT_DIR, exist_ok=True)
        done = 0
        for url in todo[:MAX_NEW]:
            try:
                r = None
                t = thumb_url(url)
                if t:
                    r = requests.get(t, headers={"User-Agent": UA}, timeout=60)
                    if r.status_code == 429:
                        print("photos-cache: Wikimedia asked us to slow down (429), the rest waits for the next run")
                        break
                    if not r.ok:
                        r = None  # e.g. the original is narrower than 960 px: no such thumb, take the original
                if r is None:
                    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
                    if r.status_code == 429:
                        print("photos-cache: Wikimedia asked us to slow down (429), the rest waits for the next run")
                        break
                r.raise_for_status()
                im = Image.open(io.BytesIO(r.content))
                im.load()
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGBA" if "transparency" in im.info or im.mode in ("LA", "PA") else "RGB")
                w, h = im.size
                if w > MAX_W:  # resize only, the whole picture kept (never a crop)
                    im = im.resize((MAX_W, max(1, round(h * MAX_W / w))), Image.LANCZOS)
                out = io.BytesIO()
                im.save(out, "WEBP", quality=QUALITY, method=6)
                data = out.getvalue()
                if len(data) >= len(r.content) and r.headers.get("content-type", "").startswith("image/webp") and im.size == Image.open(io.BytesIO(r.content)).size:
                    data = r.content  # never bigger than what Wikimedia sent
                name = os.path.join(OUT_DIR, hashlib.sha1(url.encode()).hexdigest()[:16] + ".webp")
                open(name, "wb").write(data)
                files[url] = {"file": name, "w": im.size[0], "h": im.size[1], "bytes": len(data), "fetched_bytes": len(r.content)}
                done += 1
                time.sleep(1.0)
            except Exception as e:
                print("photos-cache: skipped", url[-80:], repr(e)[:160])
        print(f"photos-cache: {done} new, {len(todo) - done} left uncached this run")
    idx = {
        "note": "One WebP per on-air photo, at most 960 px wide, quality 75, resized never cropped (scripts/cache_photos.py). "
        "Keys are the original image_url; credits and licences stay in photos.json / photos-conflict.json.",
        "checked_at": now_iso(),
        "files": files,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = INDEX + ".tmp"
    json.dump(idx, open(tmp, "w"), ensure_ascii=False, indent=0)
    os.replace(tmp, INDEX)
    subprocess.run(["git", "add", OUT_DIR], check=False, timeout=60)


if __name__ == "__main__":
    main(force="--force" in sys.argv)
