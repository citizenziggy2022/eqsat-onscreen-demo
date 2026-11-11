#!/usr/bin/env python3
"""Admin "Stats" tab rollup (Jason, Nov 11 2026 5:26 AM: "sounds good" to the stats dashboard).

Writes stats.json in the repo root: small DAILY rollups the data files themselves do not keep (they only hold "now"):
  alerts  - air-raid alert STARTS per oblast per Kyiv day (from feed.json alerts.states: a start = an oblast whose
            alertnow went false -> true between two readings; the last reading per oblast is kept in alerts.last)
  news    - distinct headlines per outlet per Kyiv day (feed.json news[] = the 4 on-screen outlets, news.json = all)
  blocked - first day each blocked domain was seen (blocked.json OONI + blocked_extra.json RKN / Censored Planet), so
            "new today" = domains first seen today; daily totals
  uptime  - data commits and feed.json updates per Kyiv day (GitHub GraphQL with the runner's own token; one query)
  errors  - the newest problems the runner or the data files reported (runner log lines, sources with ok:false)
Run by the live-runner through fetch_news.py (every 2 min; this script throttles itself to every 5 min) and stages
stats.json itself, like fetch_bulletins.py. Keeps 8 days. Never touches any other file; a failure keeps the last file.
  python3 scripts/stats.py              normal run (throttled)
  python3 scripts/stats.py --force      run now
  python3 scripts/stats.py --backfill <git checkout>   seed the rollups from the repo's own git history
"""
import datetime as dt, hashlib, json, os, subprocess, sys, urllib.request
from zoneinfo import ZoneInfo

OUT = "stats.json"
KYIV = ZoneInfo("Europe/Kyiv")
KEEP_DAYS = 8
EVERY_S = 300
REPO = os.environ.get("GITHUB_REPOSITORY", "citizenziggy2022/eqsat-onscreen-demo")
NOW = dt.datetime.now(dt.timezone.utc)


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def kday(t):
    """Kyiv calendar day of a UTC datetime."""
    return t.astimezone(KYIV).strftime("%Y-%m-%d")


def parse_utc(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def h8(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


def keep_days(now):
    first = (now.astimezone(KYIV) - dt.timedelta(days=KEEP_DAYS - 1)).strftime("%Y-%m-%d")
    return first


def empty():
    return {"alerts": {"days": {}, "last": {}}, "news": {"days": {}, "seen": {}, "names": {}},
            "blocked": {"first_seen": {}, "days": {}}, "uptime": {"days": {}}, "errors": []}


def add_alerts(st, feed):
    """A START = an oblast whose alertnow went false -> true since the previous reading (alerts.last). The upstream
    "changed" time moves with every sub-area change inside an oblast, so it is only used to date the start (Kyiv local)."""
    states = ((feed or {}).get("alerts") or {}).get("states") or {}
    if not states:
        return
    a = st["alerts"]
    last = a.setdefault("last", {})
    when = parse_utc((feed or {}).get("server_time")) or NOW
    first = not last
    for oblast, s in states.items():
        on = bool(s and s.get("alertnow"))
        was = last.get(oblast)
        last[oblast] = on
        if first or was is None or was or not on:
            continue
        ch = str((s or {}).get("changed") or "")
        day = ch[:10] if len(ch) >= 10 and ch[:4].isdigit() else kday(when)
        a["days"].setdefault(day, {})
        a["days"][day][oblast] = a["days"][day].get(oblast, 0) + 1
    a["last_at"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")


def add_news(st, feed, news):
    n = st["news"]
    rows = []
    for src in (feed or {}).get("news") or []:
        for it in src.get("items") or []:
            rows.append((src.get("key"), src.get("name"), it.get("link") or it.get("title"), it.get("published_at"), "screen"))
    for it in (news or {}).get("items") or []:
        rows.append((it.get("sourceId"), it.get("source"), it.get("url") or it.get("id") or it.get("title"), it.get("published"), "all"))
    for key, name, ident, pub, group in rows:
        t = parse_utc(pub)
        if not key or not ident or not t:
            continue
        k = (group + ":" + key)
        if name:
            n["names"][k] = name
        day = kday(t)
        hid = h8(k + "|" + ident)
        seen = n["seen"].setdefault(day, [])
        if hid in seen:
            continue
        seen.append(hid)
        n["days"].setdefault(day, {})
        n["days"][day][k] = n["days"][day].get(k, 0) + 1


def add_blocked(st, blocked, extra, when):
    b = st["blocked"]
    day = kday(when)
    keys = set()
    for it in (blocked or {}).get("items") or []:
        if it.get("domain"):
            keys.add("ooni:%s:%s" % (it.get("cc") or "", it["domain"]))
    for it in (extra or {}).get("items") or []:
        if it.get("domain"):
            keys.add("%s:%s:%s" % (it.get("source") or "list", it.get("country") or "", it["domain"]))
    if not keys:
        return
    fs = b["first_seen"]
    for k in keys:
        fs.setdefault(k, day)
    d = b["days"].setdefault(day, {})
    d["total"] = len(keys)
    d["ooni"] = len((blocked or {}).get("items") or [])
    d["lists"] = len((extra or {}).get("items") or [])


def prune(st, now):
    first = keep_days(now)
    st["alerts"]["days"] = {d: v for d, v in st["alerts"]["days"].items() if d >= first}
    st["alerts"].pop("seen", None)
    for part in ("days", "seen"):
        st["news"][part] = {d: v for d, v in st["news"][part].items() if d >= first}
    st["blocked"]["days"] = {d: v for d, v in st["blocked"]["days"].items() if d >= first}
    st["uptime"]["days"] = {d: v for d, v in st["uptime"]["days"].items() if d >= first}
    st["errors"] = st["errors"][-25:]


def note_error(st, what, at=None):
    at = at or NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    errs = st["errors"]
    for e in errs:
        if e.get("what") == what:
            e["last"] = at
            e["count"] = e.get("count", 1) + 1
            errs.remove(e)
            errs.append(e)
            return
    errs.append({"what": what, "first": at, "last": at, "count": 1})


def data_errors(st, feed, news, bh, lineup):
    if feed is not None and feed.get("news_ok") is False:
        note_error(st, "feed.json: news fetch failed (news_ok false)")
    for s in (news or {}).get("sources") or []:
        if s.get("ok") is False:
            note_error(st, "news.json: %s - %s" % (s.get("source") or s.get("id"), str(s.get("error") or "failed")[:120]))
    if bh is not None and bh.get("ooni_ok") is False:
        note_error(st, "blocked_history.json: OONI read failed (ooni_ok false)")
    if lineup is not None and lineup.get("status") not in (None, "live"):
        note_error(st, "lineup.json: status %s" % lineup.get("status"))
    log = os.environ.get("RUNNER_LOG")
    if log and os.path.exists(log):
        try:
            lines = open(log, encoding="utf-8", errors="replace").read().splitlines()[-50:]
            for ln in lines:
                at, _, what = ln.partition(" ")
                if what:
                    note_error(st, "runner: " + what[:160], at)
            open(log, "w").close()
        except Exception:
            pass


def graphql_uptime(st, now):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return
    owner, name = REPO.split("/", 1)
    today = now.astimezone(KYIV).replace(hour=0, minute=0, second=0, microsecond=0)
    days = [today - dt.timedelta(days=i) for i in range(2)]  # finished older days do not change
    parts = []
    for i, d in enumerate(days):
        since = d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        until = (d + dt.timedelta(days=1)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts.append('c%d: history(since: "%s", until: "%s") { totalCount } f%d: history(path: "feed.json", since: "%s", until: "%s") { totalCount }' % (i, since, until, i, since, until))
    q = 'query { repository(owner: "%s", name: "%s") { object(expression: "main") { ... on Commit { %s } } } }' % (owner, name, " ".join(parts))
    req = urllib.request.Request("https://api.github.com/graphql", data=json.dumps({"query": q}).encode(),
                                 headers={"Authorization": "bearer " + token, "Content-Type": "application/json", "User-Agent": "eqsat-stats"})
    with urllib.request.urlopen(req, timeout=20) as r:
        obj = json.load(r)["data"]["repository"]["object"]
    for i, d in enumerate(days):
        st["uptime"]["days"][d.strftime("%Y-%m-%d")] = {"commits": obj["c%d" % i]["totalCount"], "feed_updates": obj["f%d" % i]["totalCount"]}


def finish(st, now, extra_note=None):
    prune(st, now)
    st["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    st["generator"] = "scripts/stats.py (live-runner, every 5 min)"
    st["tz"] = "Europe/Kyiv"
    days = sorted(set(st["alerts"]["days"]) | set(st["news"]["days"]) | set(st["uptime"]["days"]))
    st["history_from"] = st.get("history_from") or (days[0] if days else kday(now))
    if extra_note:
        st["note"] = extra_note
    st.setdefault("note", "Daily rollups for the admin Stats tab. Days are Kyiv calendar days. History starts when this rollup started (backfilled from the repo's git history to " + st["history_from"] + ").")
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)


def run_normal(force):
    st = load(OUT) or {}
    if not force and st.get("generated_at"):
        t = parse_utc(st["generated_at"])
        if t and (NOW - t).total_seconds() < EVERY_S - 20:
            print("stats: fresh, skipped")
            return 0
    base = empty()
    for k, v in base.items():
        st.setdefault(k, v)
        if isinstance(v, dict):
            for kk, vv in v.items():
                st[k].setdefault(kk, vv)
    feed, news = load("feed.json"), load("news.json")
    blocked, extra, bh, lineup = load("blocked.json"), load("blocked_extra.json"), load("blocked_history.json"), load("lineup.json")
    add_alerts(st, feed)
    add_news(st, feed, news)
    add_blocked(st, blocked, extra, NOW)
    data_errors(st, feed, news, bh, lineup)
    try:
        graphql_uptime(st, NOW)
    except Exception as e:
        note_error(st, "stats: commit counts unavailable (%s)" % str(e)[:80])
    finish(st, NOW)
    print("stats: written", os.path.getsize(OUT), "bytes")
    if os.environ.get("GITHUB_ACTIONS"):
        subprocess.run(["git", "add", OUT], check=False, timeout=30)
    return 0


def run_backfill(repo):
    def git(*a):
        return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, timeout=120).stdout

    st = empty()
    log = git("log", "--reverse", "--format=%H %cI", "--", "feed.json").split("\n")
    for ln in filter(None, log):
        sha, when = ln.split(" ", 1)
        try:
            feed = json.loads(git("show", sha + ":feed.json"))
        except Exception:
            continue
        add_alerts(st, feed)
        add_news(st, feed, None)
    for path in ("blocked.json", "blocked_extra.json"):
        pass
    blog = git("log", "--reverse", "--format=%H %cI", "--", "blocked.json", "blocked_extra.json").split("\n")
    for ln in filter(None, blog):
        sha, when = ln.split(" ", 1)
        try:
            b = json.loads(git("show", sha + ":blocked.json") or "null")
        except Exception:
            b = None
        try:
            e = json.loads(git("show", sha + ":blocked_extra.json") or "null")
        except Exception:
            e = None
        add_blocked(st, b, e, parse_utc(when))
    # commits and feed.json updates per Kyiv day from the git log itself
    for ln in filter(None, git("log", "--format=%cI").split("\n")):
        d = kday(parse_utc(ln))
        st["uptime"]["days"].setdefault(d, {"commits": 0, "feed_updates": 0})["commits"] += 1
    for ln in filter(None, git("log", "--format=%cI", "--", "feed.json").split("\n")):
        d = kday(parse_utc(ln))
        st["uptime"]["days"].setdefault(d, {"commits": 0, "feed_updates": 0})["feed_updates"] += 1
    # history starts on the first Kyiv day feed.json was written by the runner (before that the screen ran on Base44)
    first_feed = git("log", "--reverse", "--format=%cI", "--", "feed.json").split("\n")[0]
    if first_feed:
        st["history_from"] = kday(parse_utc(first_feed))
        for sect in (st["alerts"]["days"], st["news"]["days"], st["news"]["seen"], st["uptime"]["days"]):
            for d in [d for d in sect if d < st["history_from"]]:
                del sect[d]
    # add the current files on top (news.json's window, today's blocked)
    add_news(st, load(os.path.join(repo, "feed.json")), load(os.path.join(repo, "news.json")))
    finish(st, NOW)
    print("stats backfill: written", os.path.getsize(OUT), "bytes")
    return 0


if __name__ == "__main__":
    try:
        if len(sys.argv) > 2 and sys.argv[1] == "--backfill":
            sys.exit(run_backfill(sys.argv[2]))
        sys.exit(run_normal("--force" in sys.argv))
    except Exception as e:  # never break the runner; the last good stats.json stays
        print("stats failed, keeping last file:", e)
        sys.exit(0)
