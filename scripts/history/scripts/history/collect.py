#!/usr/bin/env python3
"""EQSat feed-history collector, deployed v3.

Runs inside the live-runner loop on the public repo (eqsat-onscreen-demo).
Every cycle it reads each published feed via `git show origin/main:<feed>.json`
(never the live working tree), records an observation (changed / unchanged /
error) so coverage and durations are provable, stores each distinct payload
once, verbatim, forever, and appends the observation to monthly JSONL under
raw/ in a persistent clone of the PRIVATE eqsat-history repo, branch
`history` (orphan, auto-created on first run). Commit+push uses a
union-merge retry: JSONL archives are append-only, so a collision unions
lines - no observation is ever dropped.

The SQLite DB (state/history.db, gitignored) is a rebuildable artifact:
rebuilt from the JSONL alone every night in the UTC 03:00-03:10 window and
stored gzipped (state/history.db.gz, committed) so a fresh runner run
restores it without a full rebuild. reports/size.csv gets one size/churn
row per month.

Auth: the fine-grained PAT comes from env HISTORY_TOKEN (Actions secret).
The token is used only to build the clone/push URL in memory and is
scrubbed from every subprocess output before logging. It never enters
argv, files, or stdout.

PRIVATE BY DEFAULT: the archive is not the public site. Payloads are feed
JSONs only; no env, config or secrets ever enter the archive (FEEDS below
is the complete inclusion list; config.json is excluded - admin settings,
audit pending). Owner direction 2026-11-24: keep ALL historic data, all
feeds, raw forever; gaps/partials are data, never read as zero.
"""
import gzip, hashlib, json, os, shutil, sqlite3, subprocess, sys, time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS feeds (
  feed_id      TEXT PRIMARY KEY,
  source_name  TEXT,
  source_url   TEXT,
  credit       TEXT,
  cadence_sec  INTEGER,
  notes        TEXT
);

CREATE TABLE IF NOT EXISTS payloads (
  canon_hash TEXT PRIMARY KEY,
  first_seen TEXT NOT NULL,
  payload    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
  id                INTEGER PRIMARY KEY,
  feed_id           TEXT NOT NULL REFERENCES feeds(feed_id),
  fetched_at        TEXT NOT NULL,
  source_updated_at TEXT,
  canon_hash        TEXT REFERENCES payloads(canon_hash),
  status            TEXT NOT NULL,
  error             TEXT,
  obs_uid           TEXT,
  volatile          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_uid ON observations(obs_uid);
CREATE INDEX IF NOT EXISTS idx_obs_feed_time ON observations(feed_id, fetched_at);

CREATE TABLE IF NOT EXISTS alert_onsets (
  day       TEXT NOT NULL,
  oblast    TEXT NOT NULL,
  type      TEXT NOT NULL,
  count     INTEGER NOT NULL,
  partial   INTEGER NOT NULL DEFAULT 0,
  gap_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, oblast, type)
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

VOLATILE_KEYS = {"updated", "fetched_at", "checked_at", "updated_at", "generated_at", "last_run",
                 # producer-internal bookkeeping that changes every cycle without
                 # the underlying data changing (measured 2026-11-24: alerttypes
                 # daily_history.samples ticks every 1-minute run)
                 "samples", "last_at"}

# Complete inclusion list: every published feed file EXCEPT config.json
# (admin settings, not a feed; audit pending). Provenance entries carry the
# upstream source/attribution where verified (DECISIONS.md r4); None = own
# production or upstream not yet named - never a claim.
FEEDS = {
    "alerttypes":       ("siren.pp.ua (UkraineAlarm)", "https://siren.pp.ua/", None, 60),
    "feed":             ("internal rollup (alert states + Svoboda/KI)", None, None, 60),
    "news":             ("multi-outlet RSS headlines (headline+link only)", None, None, 120),
    "stats":            ("internal admin telemetry rollup", None, None, 300),
    "blocked":          ("OONI-derived blocking data", "https://ooni.org/", "OONI", 3600),
    "blocked_extra":    ("OONI-derived blocking data", "https://ooni.org/", "OONI", 3600),
    "blocked_history":  ("OONI-derived blocking history", "https://ooni.org/", "OONI", 3600),
    "keepiton":         ("OONI-derived keepiton data", "https://ooni.org/", "OONI", 3600),
    "lineup":           ("eQtv schedule (own production)", None, None, 10800),
    "streams":          ("stream URL list (own production)", None, None, 86400),
    "stream_health":    ("stream health checks (own production)", None, None, 86400),
    "fires":            ("NASA FIRMS (VIIRS)", "https://firms.modaps.eosdis.nasa.gov/", None, 1800),
    "ioda":             ("IODA, Georgia Tech", "https://ioda.inetintel.cc.gatech.edu/", None, 1800),
    "radiation":        ("SaveEcoBot", "https://www.saveecobot.com/radiation-maps",
                         "Data were obtained from the SaveEcoBot system", 3600),
    "quakes":           ("EMSC (seismicportal.eu)", "https://www.emsc-csem.org/", "CC BY 4.0", 1800),
    "airquality":       ("SaveEcoBot", "https://www.saveecobot.com/maps",
                         "Data were obtained from the SaveEcoBot system", 3600),
    "water":            ("Ukrenergo power cuts (kind=power)", "https://www.ukrenergo.energy.gov.ua/", None, 1800),
    "roads":            ("eCherha border queues (kind=border)", "https://echerha.gov.ua/", None, 1800),
    "rivers":           ("UkrHMC warnings", "https://meteo.gov.ua/", None, 1800),
    "weather_warnings": ("Meteoalarm / UkrHMC", "https://meteoalarm.org/", "CC BY 4.0", 1800),
    "changes":          ("internal change-strip snapshots", None, None, 1800),
    "queues":           (None, None, None, 1800),
    "cuts":             (None, None, None, 1800),
    "defence":          (None, None, None, 1800),
    "geo":              ("internal screen data", None, None, 1800),
    "output":           ("internal screen data", None, None, 1800),
    "auto_bulletins":   ("internal screen data", None, None, 1800),
    "emergency":        ("internal screen data", None, None, 1800),
    "emergency_extra":  ("internal screen data", None, None, 1800),
    "sponsor":          ("internal screen data", None, None, 86400),
}


# ---------------------------------------------------------------- core logic
# (identical to the prototype that passed the 9-test suite)

def canonical(obj):
    if isinstance(obj, dict):
        return {k: canonical(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [canonical(v) for v in obj]
    return obj


def flatten_volatile(obj, path="", out=None):
    out = out if out is not None else {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k in VOLATILE_KEYS:
                out[p] = v
            else:
                flatten_volatile(v, p, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            flatten_volatile(v, f"{path}[{i}]", out)
    return out


def apply_volatile(obj, vol):
    import copy as _copy
    obj = _copy.deepcopy(obj)
    for p, v in vol.items():
        cur = p
        while "[" in cur:
            cur = cur.replace("[", ".").replace("]", "")
        keys = cur.split(".")
        node = obj
        for k in keys[:-1]:
            node = node[int(k)] if isinstance(node, list) else node.setdefault(k, {})
        if isinstance(node, list):
            node[int(keys[-1])] = v
        else:
            node[keys[-1]] = v
    return obj


def canon_hash(obj):
    blob = json.dumps(canonical(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def source_ts(obj):
    for k in ("updated", "fetched_at", "checked_at", "updated_at"):
        if isinstance(obj, dict) and obj.get(k):
            return obj[k]
    return None


def open_db(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    for fid, (name, url, credit, cad) in FEEDS.items():
        db.execute("INSERT OR IGNORE INTO feeds(feed_id, source_name, source_url, credit, cadence_sec)"
                   " VALUES (?,?,?,?,?)", (fid, name, url, credit, cad))
    db.commit()
    return db


def _observe(db, rawdir, feed_id, fetched_at, status, h=None, payload=None, error=None, src=None,
             vol=None, uid=None):
    db.execute("INSERT INTO observations(feed_id, fetched_at, source_updated_at, canon_hash, status, error, obs_uid, volatile)"
               " VALUES (?,?,?,?,?,?,?,?)",
               (feed_id, fetched_at, src, h, status, error, uid,
                json.dumps(vol, ensure_ascii=False) if vol else None))
    db.commit()
    month = fetched_at[:7]
    d = os.path.join(rawdir, feed_id)
    os.makedirs(d, exist_ok=True)
    rec = {"obs_uid": uid, "feed": feed_id, "fetched_at": fetched_at,
           "source_updated_at": src, "canon_hash": h, "status": status, "error": error,
           "volatile": vol or {}}
    if status == "changed":
        rec["payload"] = payload
    with open(os.path.join(d, month + ".jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _next_obs_seq(db):
    db.execute("INSERT OR IGNORE INTO meta(k, v) VALUES ('obs_seq', '0')")
    n = int(db.execute("SELECT v FROM meta WHERE k='obs_seq'").fetchone()[0]) + 1
    db.execute("UPDATE meta SET v=? WHERE k='obs_seq'", (str(n),))
    return n


def obs_uid(feed_id, fetched_at, seq):
    return hashlib.sha256(f"{feed_id}|{fetched_at}|{seq}".encode()).hexdigest()[:16]


def collect(db, rawdir, feed_id, payload, fetched_at=None, raw_text=None):
    fetched_at = fetched_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = canon_hash(payload)
    vol = flatten_volatile(payload)
    row = db.execute("SELECT canon_hash FROM observations WHERE feed_id=? AND status!='error'"
                     " ORDER BY id DESC LIMIT 1", (feed_id,)).fetchone()
    changed = not (row and row[0] == h)
    if changed:
        db.execute("INSERT OR IGNORE INTO payloads(canon_hash, first_seen, payload) VALUES (?,?,?)",
                   (h, fetched_at, raw_text if raw_text is not None
                    else json.dumps(payload, ensure_ascii=False)))
        if feed_id == "alerttypes":
            upsert_alert_history(db, payload)
    _observe(db, rawdir, feed_id, fetched_at, "changed" if changed else "unchanged",
             h=h, payload=payload if changed else None, src=source_ts(payload),
             vol=vol, uid=obs_uid(feed_id, fetched_at, _next_obs_seq(db)))
    return "changed" if changed else "unchanged"


def reconstruct(db, feed_id, fetched_at):
    row = db.execute("SELECT p.payload, o.volatile FROM observations o"
                     " JOIN payloads p ON p.canon_hash=o.canon_hash"
                     " WHERE o.feed_id=? AND o.fetched_at=? ORDER BY o.id DESC LIMIT 1",
                     (feed_id, fetched_at)).fetchone()
    if not row:
        return None
    return apply_volatile(json.loads(row[0]), json.loads(row[1] or "{}"))


def record_error(db, rawdir, feed_id, error, fetched_at=None):
    fetched_at = fetched_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _observe(db, rawdir, feed_id, fetched_at, "error", error=str(error)[:500])
    return "error"


def upsert_alert_history(db, payload):
    hist = (payload or {}).get("daily_history") or {}
    for day, d in (hist.get("days") or {}).items():
        partial = 1 if d.get("partial") else 0
        gaps = int(d.get("gap_count") or 0)
        for oblast, types in (d.get("counts") or {}).items():
            for t, n in types.items():
                db.execute(
                    "INSERT INTO alert_onsets(day, oblast, type, count, partial, gap_count)"
                    " VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(day, oblast, type) DO UPDATE SET"
                    " count=excluded.count, partial=excluded.partial, gap_count=excluded.gap_count",
                    (day, oblast, t, int(n), partial, gaps))


# ------------------------------------------------------------ git deployment

TOKEN = os.environ.get("HISTORY_TOKEN", "")


def _scrub(text):
    return text.replace(TOKEN, "***") if TOKEN else text


def _run(args, cwd=None, check=False):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    r.scrubbed = _scrub((r.stdout or "") + (r.stderr or "")).strip()
    if check and r.returncode != 0:
        raise RuntimeError(f"git failed: {' '.join(args[:2])}: {r.scrubbed[:300]}")
    return r


def _auth_url(repo):
    if TOKEN:
        return f"https://x-access-token:{TOKEN}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"


def _resolve_jsonl_conflicts(cwd):
    out = _run(["git", "diff", "--name-only", "--diff-filter=U"], cwd).stdout.split()
    if not out:
        return True
    for path in out:
        if not path.endswith(".jsonl"):
            return False
        seen, lines = set(), []
        for stage in ("1", "2", "3"):
            r = _run(["git", "show", f":{stage}:{path}"], cwd)
            for ln in r.stdout.splitlines():
                if ln and ln not in seen:
                    # obs_uid is embedded in every record, so a byte-identical
                    # line can only be the SAME observation (rebase replay);
                    # distinct observations are never collapsed.
                    seen.add(ln)
                    lines.append(ln)

        def _key(ln):
            try:
                d = json.loads(ln)
                return (d.get("fetched_at", ""), d.get("obs_uid", ""))
            except ValueError:
                return (ln, "")
        lines.sort(key=_key)
        with open(f"{cwd}/{path}", "w") as f:
            f.write("\n".join(lines) + "\n")
        _run(["git", "add", path], cwd)
    return True


def push_with_retry(cwd, remote="origin", branch="history", attempts=3):
    for i in range(attempts):
        r = _run(["git", "push", remote, branch], cwd)
        if r.returncode == 0:
            return True
        pull = _run(["git", "pull", "--rebase", remote, branch], cwd)
        if pull.returncode != 0:
            if not _resolve_jsonl_conflicts(cwd):
                _run(["git", "rebase", "--abort"], cwd)
                print(f"history push: non-JSONL conflict, giving up: {pull.scrubbed[:200]}", file=sys.stderr)
                return False
            cont = _run(["git", "-c", "core.editor=true", "rebase", "--continue"], cwd)
            if cont.returncode != 0:
                _run(["git", "rebase", "--abort"], cwd)
                return False
        time.sleep(2 * (i + 1))
    return False


def ensure_clone(clone, repo):
    """Persistent clone of the private history repo, orphan branch `history`.
    Auto-creates the branch on first run. Returns True when ready."""
    if not os.path.isdir(os.path.join(clone, ".git")):
        os.makedirs(os.path.dirname(clone), exist_ok=True)
        r = _run(["git", "clone", "--no-checkout", _auth_url(repo), clone])
        if r.returncode != 0:
            print(f"history clone failed: {r.scrubbed[:300]}", file=sys.stderr)
            return False
        _run(["git", "config", "user.name", "eqsat-live-bot"], clone)
        _run(["git", "config", "user.email", "actions@users.noreply.github.com"], clone)
    if _run(["git", "rev-parse", "--verify", "history"], clone).returncode != 0:
        # first run on an empty repo: create the orphan branch
        _run(["git", "checkout", "--orphan", "history"], clone)
        with open(os.path.join(clone, "README.md"), "w") as f:
            f.write("# eqsat-history\n\nPrivate append-only archive of every published EQSat feed.\n"
                    "raw/<feed>/YYYY-MM.jsonl = one observation per fetch (changed carries the\n"
                    "verbatim payload). state/history.db.gz = nightly rebuilt SQLite snapshot.\n"
                    "reports/size.csv = monthly size/churn. Not part of the public site.\n")
        with open(os.path.join(clone, ".gitignore"), "w") as f:
            f.write("state/history.db*\n!state/history.db.gz\n")
        os.makedirs(os.path.join(clone, "state"), exist_ok=True)
        os.makedirs(os.path.join(clone, "reports"), exist_ok=True)
        _run(["git", "add", "-A"], clone)
        _run(["git", "commit", "-q", "-m", "history: init"], clone)
        if not push_with_retry(clone):
            print("history init push failed", file=sys.stderr)
            return False
    else:
        _run(["git", "checkout", "-q", "history"], clone)
        _run(["git", "pull", "-q", "--rebase", "origin", "history"], clone)
    return True


def feed_from_git(src, feed_id):
    """Read the published feed from origin/main in the public checkout."""
    r = _run(["git", "show", f"origin/main:{feed_id}.json"], src)
    if r.returncode != 0:
        return None
    return r.stdout


def _ever_seen(db, feed_id):
    return db.execute("SELECT 1 FROM observations WHERE feed_id=? AND status!='error' LIMIT 1",
                      (feed_id,)).fetchone() is not None


def nightly_rebuild(db, db_path, rawdir, clone):
    """UTC 03:00-03:10: rebuild the DB from JSONL alone, gzip-commit it."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if not (now.hour == 3 and now.minute < 10):
        return
    row = db.execute("SELECT v FROM meta WHERE k='last_rebuild'").fetchone()
    if row and row[0] == today:
        return
    db.close()
    n_obs = rebuild_db(rawdir, db_path)
    gz = os.path.join(clone, "state", "history.db.gz")
    with open(db_path, "rb") as fi, gzip.open(gz, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    _run(["git", "add", "state/history.db.gz"], clone)
    _run(["git", "commit", "-q", "-m", f"history: nightly db snapshot {today} ({n_obs} obs)"], clone)
    push_with_retry(clone)


def monthly_report(db, clone):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    path = os.path.join(clone, "reports", "size.csv")
    if os.path.exists(path):
        existing = open(path).read()
    else:
        existing = "month,git_bytes,observations,payloads\n"
        with open(path, "w") as f:
            f.write(existing)
    if any(l.startswith(month + ",") for l in existing.splitlines()):
        return
    git_bytes = sum(os.path.getsize(os.path.join(dp, f))
                    for dp, _, fs in os.walk(os.path.join(clone, ".git")) for f in fs)
    obs = db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    blobs = db.execute("SELECT COUNT(*) FROM payloads").fetchone()[0]
    with open(path, "a") as f:
        f.write(f"{month},{git_bytes},{obs},{blobs}\n")
    _run(["git", "add", "reports/size.csv"], clone)


def rebuild_db(rawdir, db_path):
    """Rebuild state DB from the raw JSONL archives alone (recovery path)."""
    if os.path.exists(db_path):
        os.remove(db_path)
    db = open_db(db_path)
    n = 0
    for fid in FEEDS:
        d = os.path.join(rawdir, fid)
        if not os.path.isdir(d):
            continue
        for month in sorted(os.listdir(d)):
            if not month.endswith(".jsonl"):
                continue
            with open(os.path.join(d, month), encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    payload = rec.get("payload")
                    if payload is not None:
                        db.execute(
                            "INSERT OR IGNORE INTO payloads(canon_hash, first_seen, payload)"
                            " VALUES (?,?,?)",
                            (rec["canon_hash"], rec["fetched_at"],
                             json.dumps(payload, ensure_ascii=False)))
                        if fid == "alerttypes":
                            upsert_alert_history(db, payload)
                    vol = rec.get("volatile")
                    db.execute(
                        "INSERT OR IGNORE INTO observations(feed_id, fetched_at, source_updated_at,"
                        " canon_hash, status, error, obs_uid, volatile)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (fid, rec["fetched_at"], rec.get("source_updated_at"),
                         rec.get("canon_hash"), rec["status"], rec.get("error"),
                         rec.get("obs_uid"),
                         json.dumps(vol, ensure_ascii=False) if vol else None))
                    n += 1
    db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES ('last_rebuild', ?)",
               (datetime.now(timezone.utc).strftime("%Y-%m-%d"),))
    db.commit()
    db.close()
    return n


def restore_db(clone, db_path):
    """Fresh run: restore DB from the nightly gz snapshot, else rebuild from
    raw JSONL, else start empty."""
    if os.path.exists(db_path):
        return
    gz = os.path.join(clone, "state", "history.db.gz")
    rawdir = os.path.join(clone, "raw")
    if os.path.exists(gz):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with gzip.open(gz, "rb") as fi, open(db_path, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        print("history: db restored from nightly snapshot")
    elif os.path.isdir(rawdir):
        n = rebuild_db(rawdir, db_path)
        print(f"history: db rebuilt from raw archive ({n} obs)")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="public repo checkout (reads origin/main)")
    ap.add_argument("--clone", required=True, help="persistent eqsat-history clone")
    ap.add_argument("--repo", required=True, help="owner/repo of the private history repo")
    ap.add_argument("--no-push", action="store_true", help="collect locally, skip push (tests)")
    a = ap.parse_args()

    if not ensure_clone(a.clone, a.repo):
        return 1
    _run(["git", "fetch", "-q", "origin", "main"], a.src)

    db_path = os.path.join(a.clone, "state", "history.db")
    restore_db(a.clone, db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db = open_db(db_path)
    rawdir = os.path.join(a.clone, "raw")

    results = {}
    for fid in FEEDS:
        try:
            raw = feed_from_git(a.src, fid)
            if raw is None:
                # file not published: an outage is data only for a feed that
                # existed before; a never-published feed is skipped quietly.
                if _ever_seen(db, fid):
                    results[fid] = record_error(db, rawdir, fid, "not in origin/main")
                continue
            payload = json.loads(raw)
            results[fid] = collect(db, rawdir, fid, payload, raw_text=raw)
        except Exception as e:  # a bad feed must not lose the rest of the cycle
            results[fid] = record_error(db, rawdir, fid, repr(e))
    print(json.dumps(results))

    _run(["git", "add", "raw"], a.clone)
    monthly_report(db, a.clone)
    if _run(["git", "diff", "--cached", "--quiet"], a.clone).returncode != 0:
        _run(["git", "commit", "-q", "-m",
              f"history: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%MZ')}"], a.clone)
        if not a.no_push and not push_with_retry(a.clone):
            print("history push failed after retries", file=sys.stderr)

    nightly_rebuild(db, db_path, rawdir, a.clone)
    return 0


if __name__ == "__main__":
    sys.exit(main())
