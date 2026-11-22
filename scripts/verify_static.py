#!/usr/bin/env python3
"""Daily source re-check of the hand-made emergency number cards, shown in the editor only.

What changed (Nov 22 2026 feed repair): the old check stripped every non-digit from a whole page and
tested whether the number's digits appeared anywhere in that string, so almost any number "passed" and a
failed page load still stamped files as checked. This check is label-bound: a line is CONFIRMED only when its own
number sits next to its own label in the text of the source named for it, or (the HFCC shortwave schedule) when the
schedule's own rows list that frequency, language and every transmission slot the card gives.

Each line ends in exactly one state, written to emergency_check.json (the cards themselves are never edited):
  confirmed   the number is bound to its label in the named source; "source_url" and the matched text are recorded
  unverified  no source with a machine-checkable label was found for the line (hand-entered), or the source page loads
              but does not show the number beside its label (said plainly, never counted as a pass)
  failed      the source did not load or could not be read
"updated" in the card files is never moved by this script; only a person editing a card moves it.
Runs at most once per 20 h (fetch_news.py calls it each cycle)."""
import datetime as dt, html, io, json, os, re, sys, time, zipfile
import requests

FILES = ["emergency.json", "emergency_extra.json"]
OUT = "emergency_check.json"
EVERY_S = 20 * 3600
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 eQsat-daily-check"
# Official numbering plan: Ministry of Transport and Communications order of 24.09.2008 No. 1178 (Verkhovna Rada legal base).
# Its clause 4.2.48 lists each emergency number against the service it belongs to.
PLAN = "https://zakon.rada.gov.ua/go/z0976-08"
NATIONAL = {
    "112": (PLAN, r"екстреної допомоги населенню"),
    "101": (PLAN, r"пожежн"),
    "102": (PLAN, r"міліці|поліці"),
    "103": (PLAN, r"швидк"),
    "104": (PLAN, r"газов"),
}
NATIONAL_LINE_LABEL = {"112": r"all services|усі служби", "101": r"rescue|fire|рятувальн|пожеж", "102": r"police|поліці", "103": r"ambulance|швидка", "104": r"gas|газов"}
HFCC_ZIP = "http://www.hfcc.org/data/b26/b26allx2.zip"


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def visible_text(raw):
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t)


_cache = {}


def fetch(url):
    """(content_bytes | None, why)"""
    if url not in _cache:
        for attempt in range(2):
            try:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
                _cache[url] = (r.content, "") if r.ok else (None, f"HTTP {r.status_code}")
                if r.ok:
                    break
            except Exception as e:
                _cache[url] = (None, repr(e)[:100])
            time.sleep(1)
        time.sleep(0.5)
    return _cache[url]


def label_bound(text, number, label_re):
    """The label and the number within one short span, nothing but filler between (no other digits, no ';' list break)."""
    n = re.escape(number)
    m = re.search(rf"(?:{label_re})[^;\d]{{0,80}}?(?<!\d){n}(?!\d)", text, re.I) or \
        re.search(rf"(?<!\d){n}(?!\d)[^;\d]{{0,40}}?(?:{label_re})", text, re.I)
    return m.group(0)[:160] if m else None


def hfcc_check(line):
    """5955 kHz style lines: the HFCC B-season schedule row must carry this frequency, the Ukrainian language code and
    every UTC slot the card lists ('16:00-16:30, 18:00-18:30')."""
    body, why = fetch(HFCC_ZIP)
    if body is None:
        return "failed", HFCC_ZIP, f"schedule file did not load ({why})", None
    freq = re.match(r"\s*(\d{3,5})", line.get("number", ""))
    slots = re.findall(r"(\d{2}):(\d{2})\s*[-–]\s*(\d{2}):(\d{2})", line.get("utc", ""))
    if not freq or not slots:
        return "unverified", HFCC_ZIP, "card line lacks a frequency or UTC slots to compare", None
    try:
        z = zipfile.ZipFile(io.BytesIO(body))
        names = [n for n in z.namelist() if n.lower().endswith(".txt")]
        rows = []
        for n in names:
            rows += z.read(n).decode("latin-1").splitlines()
    except Exception as e:
        return "failed", HFCC_ZIP, f"schedule file could not be read ({repr(e)[:80]})", None
    head = next((r for r in rows if r.startswith("; B")), "")
    found, missing = [], []
    for a, b, c, d in slots:
        s, e = a + b, c + d
        hit = next((r for r in rows if not r.startswith(";") and r.split()[:3] == [freq.group(1), s, e] and re.search(r"\bUkr\b", r)), None)
        (found if hit else missing).append((s, e, hit.strip()[:120] if hit else None))
    if missing:
        return "unverified", HFCC_ZIP, "schedule has no matching Ukrainian row for " + ", ".join(f"{s}-{e} UTC" for s, e, _ in missing), head
    return "confirmed", HFCC_ZIP, found[0][2], head


def check_line(card_id, line):
    num = str(line.get("number") or "").strip()
    digits = re.sub(r"\D", "", num)
    if re.match(r"\s*\d{3,5}\s*kHz", num, re.I) and "hfcc.org" in (line.get("source_url") or ""):
        return hfcc_check(line)
    # the national rule applies only to the national card's own lines (the service label must fit), never to a same
    # number elsewhere (Moldova's 112, an advice line that mentions 101)
    rule = NATIONAL.get(num) if re.search(NATIONAL_LINE_LABEL.get(num, r"$^"), f"{line.get('en','')} {line.get('uk','')}", re.I) else None
    if rule:
        url, label_re = rule
        body, why = fetch(url)
        if body is None:
            return "failed", url, f"source page did not load ({why})", None
        text = visible_text(body.decode("utf-8", "ignore"))
        got = label_bound(text, num, label_re)
        if got:
            return "confirmed", url, got, None
        return "unverified", url, "source loads but does not show this number beside its service label", None
    url = line.get("source_url")
    if url and digits:
        body, why = fetch(url)
        if body is None:
            return "failed", url, f"source page did not load ({why})", None
        return "unverified", url, "source loads; no machine-checkable label for this line, so it is not counted as confirmed", None
    return "unverified", None, "hand-entered, no source named", None


def main(force=False):
    try:
        last = json.load(open(OUT, encoding="utf-8")).get("checked_at")
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() if last else 1e9
    except Exception:
        age = 1e9
    if not force and age < EVERY_S:
        print(f"verify_static: checked {int(age)} s ago")
        return
    out = {"checked_at": now(), "method": "label-bound; see scripts/verify_static.py", "lines": []}
    counts = {"confirmed": 0, "unverified": 0, "failed": 0}
    for f in FILES:
        if not os.path.exists(f):
            continue
        d = json.load(open(f, encoding="utf-8"))
        for card in d.get("cards", []):
            if card.get("enabled") is False:
                continue
            cid = card.get("id") or card.get("title_en") or card.get("title_uk")
            for ln in card.get("lines", []):
                if not str(ln.get("number") or "").strip():
                    continue
                state, url, evidence, extra = check_line(cid, ln)
                counts[state] += 1
                rec = {"file": f, "card": cid, "number": ln.get("number"), "state": state, "source_url": url, "evidence": evidence}
                if state == "confirmed":
                    rec["confirmed_at"] = now()
                if extra:
                    rec["source_note"] = extra
                out["lines"].append(rec)
    out["counts"] = counts
    tmp = OUT + ".tmp"
    json.dump(out, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print(f"verify_static: {counts}")
    import subprocess
    subprocess.run(["git", "add", OUT], check=False, timeout=60)


if __name__ == "__main__":
    main("--force" in sys.argv)
