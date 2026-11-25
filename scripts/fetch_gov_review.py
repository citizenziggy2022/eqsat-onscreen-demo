#!/usr/bin/env python3
"""REVIEW-ONLY collection of official road and water notices (owner scope: broadcast content is reviewed before it airs).

Runs from fetch_feed.py every live-runner cycle, throttles itself to every 10 minutes, and writes ONE file:
gov_bulletins_pending.json. That file is candidates only. Nothing here reads, merges into, or writes auto_bulletins.json,
bulletins.json or config.json, so nothing it finds can reach the screen. A card goes on air only when the owner approves
that single card and it is added by hand as a reviewed card with an expiry (the 24 Nov road card is the pattern).

Sources (scripts/gov_sources.py): restoration.gov.ua roads blog (CC BY 4.0) and the confirmed Odesa and Kyiv water-utility
Telegram channels (t.me/s). Posts that do not parse cleanly are listed under "needs_review", never shortened or guessed.
On any failure the last good file stays. Expired candidates are dropped."""
import json, os, subprocess, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gov_sources as g

OUT = "gov_bulletins_pending.json"
STATE = ".cache/gov_review_state.json"
EVERY_S = 600
FMT = "%Y-%m-%dT%H:%M:%SZ"
NOTE = ("Official road/water notice CANDIDATES, NOT on air. Written by scripts/fetch_gov_review.py. Nothing is merged "
        "into auto_bulletins.json; an item airs only if the owner approves that single card and it is added by hand with an expiry.")


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def collect(now, old):
    """Return the new file content, or None when both sources failed (keep the old file)."""
    ri, rl = g.roads(now=now)
    wi, wl = g.water(now=now)
    items = [dict(i, status="pending_review", on_air=False) for i in ri + wi if not i.get("preview_only")]
    now_s = now.strftime(FMT)
    by_id = {i["id"]: i for i in (old or {}).get("items", []) if i.get("expires_at", "") > now_s}
    for i in items:
        by_id[i["id"]] = i
    items = sorted((i for i in by_id.values() if i["expires_at"] > now_s), key=lambda i: i["issued_at"], reverse=True)
    return {"note": NOTE, "every_s": EVERY_S, "fetched_at": now_s, "on_air": False,
            "auto_approved": 0, "items": items, "needs_review": [l for l in rl + wl if l.startswith("needs review")]}


def run(now=None, out=OUT, state=STATE, stage=True):
    now = now or datetime.now(timezone.utc)
    st = _load(state) or {}
    if time.time() - st.get("last", 0) < EVERY_S:
        return "throttled"
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state, "w") as fh:
        json.dump({"last": time.time()}, fh)
    old = _load(out)
    try:
        new = collect(now, old)
    except Exception as exc:
        print("gov review: failed, keeping last file:", str(exc)[:120])
        return "failed"
    if old and all(old.get(k) == new.get(k) for k in ("items", "needs_review")):
        return "unchanged"
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(new, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, out)
    if stage:
        subprocess.run(["git", "add", out], check=False, timeout=30)
    return "written"


if __name__ == "__main__":
    print("gov review:", run())
