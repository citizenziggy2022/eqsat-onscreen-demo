#!/usr/bin/env python3
"""Daily re-check of the hand-made emergency number cards (Jason, Nov 12 2026: "every day be sure to update everything at least once").
For every line that carries a source_url, fetch that official page and look for the number's digits. A line found today gets
verified_at = today. A card's numbers are NEVER changed here; a number that can't be found is listed in check_failed so a person
looks at it. The file's "updated" stamp moves to now only when every checked line was found. Runs at most once per 20 h."""
import datetime as dt, json, os, re, sys, time
import requests
FILES = ["emergency.json", "emergency_extra.json"]
EVERY_S = 20 * 3600
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 eQsat-daily-check"
# the national cards in emergency.json carry no per-line url: their official pages
DEFAULT_URLS = {n: "https://mvs.gov.ua/" for n in ("112", "101", "102", "103", "104")}  # the card's own source (mvs.gov.ua lists all five)
def now(): return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def digits(s): return re.sub(r"\D", "", s or "")
_cache = {}
def page(url):
    if url not in _cache:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            _cache[url] = digits(r.text) if r.ok else None
            if not r.ok: print("verify_static:", url, r.status_code)
        except Exception as e:
            print("verify_static:", url, repr(e)[:120]); _cache[url] = None
        time.sleep(0.5)
    return _cache[url]
def main(force=False):
    try:
        last = json.load(open(FILES[0], encoding="utf-8")).get("checked_at")
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() if last else 1e9
    except Exception:
        age = 1e9
    if not force and age < EVERY_S:
        print(f"verify_static: checked {int(age)} s ago"); return
    today = now()[:10]
    for f in FILES:
        if not os.path.exists(f): continue
        d = json.load(open(f, encoding="utf-8"))
        failed, checked = [], 0
        for card in d.get("cards", []):
            if card.get("enabled") is False: continue
            for ln in card.get("lines", []):
                num = digits(ln.get("number"))
                url = ln.get("source_url") or DEFAULT_URLS.get(num)
                if not num or not url: continue
                text = page(url)
                if text is None:
                    failed.append({"number": ln.get("number"), "source_url": url, "why": "page did not load"}); continue
                checked += 1
                if num in text or (num.startswith("380") and num[2:] in text):
                    ln["verified_at"] = today
                else:
                    failed.append({"number": ln.get("number"), "source_url": url, "why": "number not found on page"})
        d["check_failed"] = failed
        d["checked_at"] = now()
        if checked and not failed:
            d["updated"] = now()
        json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"verify_static: {f}: {checked} checked, {len(failed)} not confirmed")
    import subprocess
    subprocess.run(["git", "add"] + [f for f in FILES if os.path.exists(f)], check=False, timeout=60)
if __name__ == "__main__":
    main("--force" in sys.argv)
