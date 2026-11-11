#!/usr/bin/env python3
"""feed.json for the eQsat screen: the same payload as the Base44 getBroadcastFeed function (air-raid alert states per
oblast + the Radio Svoboda / Kyiv Independent / Pravda / Ukrinform headlines), built in GitHub Actions instead.
Why (Nov 9 2026): the Base44 function started answering 402 "credit limit reached", so the screen got no alert data.
The screen now reads this file first and calls the Base44 function only when this file is missing or stale.
Sources: alerts https://ubilling.net.ua/aerialalerts/ (the function's own source; no CORS, hence this job), news RSS.
Safety: each part keeps its last good value when its fetch fails; alerts.fetched_at is only moved on a real fetch, so
the screen can tell stale data. Nothing is written when every part fails."""
import json, re, html, sys, datetime as dt, concurrent.futures as cf, requests

UA = {'User-Agent': 'Mozilla/5.0 (compatible; eQsat OnScreen feed bot; +https://citizenziggy2022.github.io/eqsat-onscreen-demo/)'}
ALERTS_URL = 'https://ubilling.net.ua/aerialalerts/'
FEEDS = [
    ('svoboda', 'Радіо Свобода / Radio Svoboda', 'https://www.radiosvoboda.org/api/zrqiteuuir'),
    ('kyivindependent', 'Kyiv Independent', 'https://kyivindependent.com/news-archive/rss/'),
    ('pravda', 'Українська правда / Ukrainska Pravda', 'https://www.pravda.com.ua/eng/rss/'),
    ('ukrinform', 'Укрінформ / Ukrinform', 'https://www.ukrinform.net/rss/block-lastnews'),
]
MAX_ITEMS = 8
NOW = dt.datetime.now(dt.timezone.utc)
iso = lambda d: d.strftime('%Y-%m-%dT%H:%M:%S.000Z')

def clean(s):
    s = re.sub(r'<!\[CDATA\[([\s\S]*?)\]\]>', r'\1', s or '')
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()

def tag(b, t):
    m = re.search(r'<%s[^>]*>([\s\S]*?)</%s>' % (t, t), b, re.I)
    return clean(m.group(1)) if m else ''

def pdate(s):
    import email.utils
    try: d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try: d = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
        except Exception: return ''
    if not d.tzinfo: d = d.replace(tzinfo=dt.timezone.utc)
    return iso(d.astimezone(dt.timezone.utc))

def load_feed(f):
    key, name, url = f
    r = requests.get(url, headers=UA, timeout=15); r.raise_for_status()
    blocks = re.findall(r'<item[\s>][\s\S]*?</item>', r.text, re.I) or re.findall(r'<entry[\s>][\s\S]*?</entry>', r.text, re.I)
    items = []
    for b in blocks:
        title = tag(b, 'title')
        if not title: continue
        link = tag(b, 'link') or ((re.search(r'<link[^>]*href="([^"]+)"', b) or [None, ''])[1])
        pub = tag(b, 'pubDate') or tag(b, 'published') or tag(b, 'updated') or tag(b, 'dc:date')
        items.append(dict(title=title, link=link, published_at=pdate(pub) if pub else ''))
        if len(items) >= MAX_ITEMS: break
    if not items: raise ValueError('empty')
    return dict(key=key, name=name, items=items)

def load_alerts():
    r = requests.get(ALERTS_URL, headers={**UA, 'Accept': 'application/json'}, timeout=15); r.raise_for_status()
    j = r.json(); states = j.get('states')
    if not isinstance(states, dict) or len(states) < 20: raise ValueError('bad payload')
    for v in states.values():
        if not isinstance(v, dict) or 'alertnow' not in v: raise ValueError('bad state')
    return dict(states=states, fetched_at=iso(NOW), source='ubilling.net.ua/aerialalerts', upstream_cachedat=j.get('cachedat'))

def main():
    try: prev = json.load(open('feed.json', encoding='utf-8'))
    except Exception: prev = {}
    ok = 0
    try:
        alerts = load_alerts(); ok += 1
    except Exception as e:
        print('alerts failed, keeping last:', str(e)[:150]); alerts = prev.get('alerts')
    old_news = {n.get('key'): n for n in prev.get('news') or [] if isinstance(n, dict)}
    news = []
    with cf.ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(load_feed, f): f for f in FEEDS}
        res = {}
        for fu, f in futs.items():
            try: res[f[0]] = fu.result(); ok += 1
            except Exception as e:
                print('news', f[0], 'failed, keeping last:', str(e)[:120])
                if f[0] in old_news: res[f[0]] = old_news[f[0]]
    news = [res[f[0]] for f in FEEDS if f[0] in res]
    if not ok:
        print('every part failed, feed.json left untouched'); sys.exit(1)
    doc = dict(news=news, alerts=alerts, news_ok=bool(news), server_time=iso(NOW), generator='scripts/fetch_feed.py (GitHub Actions)')
    json.dump(doc, open('feed.json.tmp', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    json.load(open('feed.json.tmp', encoding='utf-8'))
    import os; os.replace('feed.json.tmp', 'feed.json')
    on = sum(1 for v in (alerts or {}).get('states', {}).values() if v.get('alertnow'))
    print(f'feed.json: alerts {"fresh" if alerts and alerts.get("fetched_at")==iso(NOW) else "STALE"} ({on} states alerting), news {len(news)}/4')

def bulletins():
    # auto bulletins every live-runner cycle (Jason, Nov 11 2026 9:37 AM: "updates should be as realtime as possible"):
    # fetch_bulletins.py throttles itself to 55 s, backs off per channel on 429 and stages auto_bulletins.json itself
    try:
        import subprocess, sys as _s
        subprocess.run([_s.executable, 'scripts/fetch_bulletins.py'], timeout=75, check=False)
    except Exception as e:
        print('auto bulletins skipped:', str(e)[:120])
    # Air Force threat warnings (v2.15, Nov 11 2026): fetch_airforce.py throttles itself to 50 s, writes airforce.json
    # (t.me/s/kpszsu, threat warnings only, word for word) for the red line in the live news crawl, stages it itself
    try:
        import subprocess, sys as _s
        subprocess.run([_s.executable, 'scripts/fetch_airforce.py'], timeout=60, check=False)
    except Exception as e:
        print('air force skipped:', str(e)[:120])
    # POW exchanges (v2.24, Nov 11 2026): fetch_pow.py throttles itself to 120 s, writes pow.json (t.me/s/Koord_shtab,
    # only posts that report an exchange with the number freed) for the 24 h banner on the bulletins panel, stages it itself
    try:
        import subprocess, sys as _s
        subprocess.run([_s.executable, 'scripts/fetch_pow.py'], timeout=60, check=False)
    except Exception as e:
        print('pow skipped:', str(e)[:120])
    # power cuts (Ukrenergo, every 2.5 min) + border queues (eCherha, every 3 min) for the WATER / ROADS maps (Nov 11 2026)
    try:
        import subprocess, sys as _s
        subprocess.run([_s.executable, 'scripts/fetch_water_roads.py'], timeout=60, check=False)
    except Exception as e:
        print('water/roads skipped:', str(e)[:120])


if __name__ == '__main__':
    try:
        main()
    finally:
        bulletins()
