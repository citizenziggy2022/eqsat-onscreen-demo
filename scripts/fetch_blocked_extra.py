#!/usr/bin/env python3
"""fetch_blocked_extra.py -> blocked_extra.json for the eQsat Hotbird screen's BLOCKED flip line.

Adds two sources beside OONI (blocked.json) and Access Now #KeepItOn (keepiton.json):
  1. Censored Planet (University of Michigan), GraphQL at https://data.censoredplanet.org/query
     - remote HTTPS (Hyperquack) measurements, last 7 days, RU / UA / BY / MD
  2. Russia's official Roskomnadzor (RKN) blocking registry, via the antifilter.download mirror
     (fallback: ProstoVPN AntiZapret domains-export.txt). reestr.rublacklist.net (Roskomsvoboda)
     answers HTTP 500 on every path as of 2026-11-08, and the zapret-info/z-i GitHub mirror
     stopped updating on 2025-10-01, so neither is used.

Plain Python 3 + requests. No keys, no secrets. Runs in a GitHub Action (ubuntu-latest, ~2-4 min).
Keeps the last good blocked_extra.json if everything fails.
Usage: python3 fetch_blocked_extra.py [--ooni blocked.json] [--out blocked_extra.json] [--days 7]
"""
import argparse, csv, io, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "eqsat-onscreen-feeds/1.0 (+https://citizenziggy2022.github.io/eqsat-onscreen-demo/)"}
S = requests.Session()
S.headers.update(UA)

CP_URL = "https://data.censoredplanet.org/query"
CP_COUNTRIES = {"RU": "Russia", "UA": "Ukraine", "BY": "Belarus", "MD": "Moldova"}  # dashboard wants English names
CP_BATCH = 10            # server rule: "you can only query up to 10 domains at once"
CP_WORKERS = 3           # be polite: 3 requests in flight at most
CP_MIN_N = 20            # min measurements for a 'likely' call
CP_MIN_RATE = 0.5        # min unexpected share for a 'likely' call (country baseline is subtracted too)
CL_LISTS = "https://raw.githubusercontent.com/citizenlab/test-lists/master/lists/%s.csv"
KEEP_CATS = {"NEWS", "HUMR", "ANON", "POLR", "COMT", "FEXP", "MMED", "GRP", "LGBT"}

RKN_SOURCES = [
    ("antifilter.download", "https://antifilter.download/list/domains.lst"),
    ("antizapret", "https://antizapret.prostovpn.org/domains-export.txt"),
]

# Well-known sites, shown first (news, human rights, VPN/circumvention, platforms). Order = screen priority.
WELL_KNOWN = [
    # independent Russian-language / Russia-focused media
    "meduza.io", "novayagazeta.eu", "tvrain.tv", "svoboda.org", "currenttime.tv", "theins.ru", "istories.media",
    "zona.media", "mediazona.ca", "holod.media", "proekt.media", "thebell.io", "themoscowtimes.com", "echofm.online", "doxa.team",
    # Ukrainian media
    "kyivindependent.com", "pravda.com.ua", "ukrinform.net", "nv.ua", "unian.net", "censor.net", "kyivpost.com", "hromadske.ua", "suspilne.media",
    # international media
    "bbc.com", "dw.com", "rferl.org", "voanews.com", "euronews.com", "bellingcat.com", "occrp.org",
    # human rights / civil society
    "hrw.org", "amnesty.org", "ovdinfo.org", "navalny.com", "fbk.info", "rsf.org", "cpj.org", "freedomhouse.org", "greenpeace.org", "equalit.ie",
    # VPN / circumvention
    "torproject.org", "nordvpn.com", "protonvpn.com", "proton.me", "expressvpn.com", "psiphon.ca", "surfshark.com", "mullvad.net", "windscribe.com", "getlantern.org",
    # platforms
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "soundcloud.com", "patreon.com", "signal.org", "discord.com", "viber.com",
]
# Russian state media / platforms: blocked by Ukraine and Moldova, so measured in UA/MD/BY too (not ranked first)
RU_STATE = ["rt.com", "sputniknews.com", "ria.ru", "vk.com", "yandex.ru", "mail.ru", "ok.ru", "1tv.ru", "smotrim.ru", "ntv.ru", "kremlin.ru", "vesti.ru", "yandex.com"]
WK_RANK = {d: i for i, d in enumerate(WELL_KNOWN)}
WK_CAT = {}
for i, d in enumerate(WELL_KNOWN):
    WK_CAT[d] = "news" if i < 31 else "rights" if i < 42 else "vpn" if i < 52 else "platform"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(d):
    d = (d or "").strip().strip('"').lower().rstrip(".")
    return d[4:] if d.startswith("www.") else d


def get(url, timeout=120, **kw):
    for attempt in range(3):
        try:
            r = S.get(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


# ---------------------------------------------------------------- OONI dedupe
def ooni_pairs(path):
    pairs = set()
    try:
        with open(path, encoding="utf-8") as f:
            for it in json.load(f).get("items", []):
                pairs.add((norm(it.get("domain")), (it.get("cc") or it.get("country") or "").upper()))
    except Exception as e:
        print("ooni dedupe: no %s (%s)" % (path, e))
    return pairs


# ---------------------------------------------------------------- Censored Planet
def cp_query(q, variables=None, timeout=120):
    for attempt in range(3):
        try:
            r = S.post(CP_URL, json={"query": q, "variables": variables or {}}, timeout=timeout)
            d = r.json()
            if d.get("errors"):
                raise RuntimeError(d["errors"][0].get("message"))
            return d["data"]
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def cp_categories():
    cat = {}
    for name in ["global", "ru", "ua", "by", "md"]:
        try:
            for r in csv.DictReader(io.StringIO(get(CL_LISTS % name, timeout=60).text)):
                h = norm(urlparse(r.get("url", "")).hostname)
                if h:
                    cat.setdefault(h, r.get("category_code", ""))
        except Exception as e:
            print("citizenlab list %s failed: %s" % (name, e))
    return cat


CP_RECENT_RAW, CP_RECENT = {}, {}  # (cc, domain) -> counts / reading over the last 2 days

DASH = "query($f:FilterDashboard!){ dashboard(filter:$f){ domain date outcome count unexpectedCount } }"


def censored_planet(days):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    rng = {"startDate": str(start), "endDate": str(end)}
    recent_from = str(end - timedelta(days=1))
    all_domains = cp_query('query($r:DateRange!){ domains(range:$r, protocol:"HTTPS") }', {"r": rng})["domains"]
    cats = cp_categories()
    chosen = []
    for d in all_domains:
        nd = norm(d)
        if nd in WK_RANK or nd in RU_STATE or cats.get(nd, "") in KEEP_CATS:
            chosen.append(d)
    print("censored planet: %d test domains, %d chosen (well-known + Citizen Lab news/rights/anon/political/...)" % (len(all_domains), len(chosen)))

    jobs = []
    for cc, cname in CP_COUNTRIES.items():
        for i in range(0, len(chosen), CP_BATCH):
            jobs.append((cc, cname, chosen[i:i + CP_BATCH]))

    def run(job):
        cc, cname, batch = job
        f = dict(rng, domains=batch, country=cname, source="HTTPS")
        try:
            return cc, cp_query(DASH, {"f": f})["dashboard"], None
        except Exception as e:
            return cc, [], str(e)

    agg, errors = {}, 0
    with ThreadPoolExecutor(CP_WORKERS) as ex:
        for cc, rows, err in ex.map(run, jobs):
            if err:
                errors += 1
            for r in rows:
                key = (cc, norm(r["domain"]))
                a = agg.setdefault(key, {"n": 0, "u": 0, "bp": 0, "last": "", "last_any": ""})
                a["n"] += r.get("count") or 0
                a["u"] += r.get("unexpectedCount") or 0
                if "blockpage" in (r.get("outcome") or "").lower():
                    a["bp"] += r.get("count") or 0
                if (r.get("unexpectedCount") or 0) > 0 and r["date"] > a["last"]:
                    a["last"] = r["date"]
                a["last_any"] = max(a["last_any"], r["date"])
                if r["date"] >= recent_from:  # last 2 days, for blocked_history.json
                    b = CP_RECENT_RAW.setdefault(key, {"n": 0, "u": 0, "bp": 0})
                    b["n"] += r.get("count") or 0
                    b["u"] += r.get("unexpectedCount") or 0
                    if "blockpage" in (r.get("outcome") or "").lower():
                        b["bp"] += r.get("count") or 0

    # per-country baseline: median unexpected share over all measured domains
    base = {}
    for cc in CP_COUNTRIES:
        rates = sorted(a["u"] / a["n"] for (c, _), a in agg.items() if c == cc and a["n"] >= CP_MIN_N)
        base[cc] = rates[len(rates) // 2] if rates else 0.0
    # recent readings for blocked_history.json (the "available now" marker): ok = >=5, no block page, <20% unexpected
    for key, b in CP_RECENT_RAW.items():
        cc = key[0]
        if not b["n"]:
            continue
        rate = b["u"] / b["n"]
        if b["bp"] > 0 or (b["n"] >= CP_MIN_N and rate >= CP_MIN_RATE and rate >= base[cc] + 0.3):
            CP_RECENT[key] = ("blocked", b["n"], rate)
        elif b["n"] >= 5 and rate < 0.2:
            CP_RECENT[key] = ("ok", b["n"], rate)

    items, totals = [], {cc: {"confirmed": 0, "likely": 0, "measured": 0} for cc in CP_COUNTRIES}
    for (cc, dom), a in agg.items():
        if a["n"] == 0:
            continue
        totals[cc]["measured"] += 1
        rate = a["u"] / a["n"]
        if a["bp"] > 0:
            status = "confirmed"
        elif a["n"] >= CP_MIN_N and rate >= CP_MIN_RATE and rate >= base[cc] + 0.3:
            status = "likely"
        else:
            continue
        totals[cc][status] += 1
        items.append({"domain": dom, "country": cc, "source": "censoredplanet", "status": status,
                      "last_seen": a["last"] or a["last_any"], "rate": round(rate, 2), "measurements": a["n"],
                      "category": WK_CAT.get(dom) or ("ru-state" if dom in RU_STATE else None) or (cats.get(dom) or "").lower() or None})
    meta = {"name": "Censored Planet (University of Michigan)", "endpoint": CP_URL, "protocol": "HTTPS (Hyperquack)",
            "window": rng, "domains_tested": len(chosen), "query_errors": errors,
            "baseline_unexpected_share": {k: round(v, 3) for k, v in base.items()}, "totals": totals,
            "rule": "confirmed = a known block page was returned; likely = >=%d measurements, >=%d%% unexpected and >=30 points above the country median" % (CP_MIN_N, CP_MIN_RATE * 100)}
    return meta, items


# ---------------------------------------------------------------- RKN registry
def rkn():
    last_err = None
    for name, url in RKN_SOURCES:
        try:
            r = get(url, timeout=180)
            lines = [norm(x) for x in r.text.split()]
            lines = [x for x in lines if x and "." in x]
            s = set(lines)
            if len(s) < 10000:
                raise RuntimeError("only %d lines" % len(s))
            lm = r.headers.get("Last-Modified")
            try:
                updated = datetime.strptime(lm, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc).isoformat()
            except Exception:
                updated = now_iso()
            def listed(d):
                parts = d.split(".")
                if name == "antizapret":   # antizapret collapses subdomains into the parent
                    return any(".".join(parts[i:]) in s for i in range(len(parts) - 1))
                return d in s or ("*." + d) in s
            items = [{"domain": d, "country": "RU", "source": "rkn", "status": "listed", "last_seen": updated[:10],
                      "category": WK_CAT.get(d)} for d in WELL_KNOWN if listed(d)]
            meta = {"name": "Roskomnadzor registry (via %s)" % name, "endpoint": url, "updated": updated,
                    "total_domains": len(s), "well_known_listed": len(items),
                    "note": "Official RKN blocking registry. Rows are registry entries (incl. wildcard and mirror domains), not distinct sites."}
            return meta, items
        except Exception as e:
            last_err = "%s: %s" % (name, e)
            print("rkn source failed:", last_err)
    raise RuntimeError(last_err)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ooni", default="blocked.json")
    ap.add_argument("--out", default="blocked_extra.json")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    ooni = ooni_pairs(args.ooni)
    sources, items = {}, []
    for key, fn in (("censoredplanet", lambda: censored_planet(args.days)), ("rkn", rkn)):
        t0 = time.time()
        try:
            meta, its = fn()
            meta["seconds"] = round(time.time() - t0, 1)
            sources[key] = meta
            items += its
            print(key, "ok", len(its), "items in", meta["seconds"], "s")
        except Exception as e:
            sources[key] = {"error": str(e)[:300]}
            print(key, "FAILED", e)

    if not items:
        print("nothing fetched; keeping the last good", args.out)
        return 1

    # dedupe: drop (domain,country) OONI already shows; prefer CP confirmed > CP likely > RKN listed for the same pair
    order = {"confirmed": 0, "likely": 1, "listed": 2}
    best = {}
    for it in items:
        k = (it["domain"], it["country"])
        it["also_in_ooni"] = k in ooni
        if k not in best or order[it["status"]] < order[best[k]["status"]]:
            other = best.get(k)
            best[k] = it
            if other:
                it["also"] = sorted(set(it.get("also", []) + [other["source"]]))
        else:
            best[k]["also"] = sorted(set(best[k].get("also", []) + [it["source"]]))
    merged = list(best.values())
    for it in merged:
        it["new_vs_ooni"] = not it.pop("also_in_ooni")

    def rank(it):
        return (WK_RANK.get(it["domain"], 10000), not it["new_vs_ooni"], order[it["status"]], -(it.get("rate") or 0), it["domain"])
    merged.sort(key=rank)
    for it in merged:
        it["well_known"] = it["domain"] in WK_RANK

    out = {"generated": now_iso(), "sources": sources,
           "credit": "Censored Planet (U. Michigan) · RKN registry via antifilter.download",
           "counts": {"items": len(merged), "new_vs_ooni": sum(1 for i in merged if i["new_vs_ooni"]),
                      "by_source_country": {}},
           "items": merged}
    for it in merged:
        k = "%s/%s" % (it["source"], it["country"])
        out["counts"]["by_source_country"][k] = out["counts"]["by_source_country"].get(k, 0) + 1
    tmp = args.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, args.out)
    print("wrote", args.out, len(merged), "items", out["counts"])
    return 0


if __name__ == "__main__":
    rc = main()
    try:  # blocked_history.json ("available now"), Jason Nov 9 2026 5:10 PM; never fails this script
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import blocked_history
        ap = argparse.ArgumentParser()
        ap.add_argument("--ooni", default="blocked.json")
        ap.add_argument("--out", default="blocked_extra.json")
        ap.add_argument("--days", type=int, default=7)
        a = ap.parse_args()
        blocked_history.update(cp_recent=CP_RECENT, ooni_path=a.ooni, extra_path=a.out,
                               out_path=os.path.join(os.path.dirname(os.path.abspath(a.out)), "blocked_history.json"))
    except Exception as e:
        print("blocked_history failed:", e)
    sys.exit(rc)
