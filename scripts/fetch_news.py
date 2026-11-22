#!/usr/bin/env python3
"""Build news.json for the eQsat screen. Runs in a GitHub Action every 5 min.
Sources: RSS feeds, Bluesky public API, Telegram t.me/s previews. Stdlib + requests only."""
import re, json, html, email.utils, datetime as dt, concurrent.futures as cf, requests
from urllib.parse import urlparse, parse_qs
UA={'User-Agent':'Mozilla/5.0 (eQsat OnScreen news bot; +https://citizenziggy2022.github.io/eqsat-onscreen-demo/)'}  # a browser-like UA got 403 from Suspilne; this one works
UA_RE=re.compile(r'ukrain|kyiv|kiev|zelensk|kharkiv|odesa|donbas|crimea|україн|київ|зеленськ|харків|одес', re.I)
# News hygiene (2026-11-21): clean the panel at the data level.
# PROMO_RE drops commercial/promotional posts (gift guides, sales); keep the patterns specific so real
# reporting ("peace deal", "strikes a deal") is never matched.
PROMO_RE=re.compile(r'gift[ -]guide|gift ideas|gifts for|gift-giving|holiday gifts?|black friday|cyber monday|boxing day sale|promo codes?|coupons?|discount codes?|\d+\s?% off|\d+ percent off|sale is live|on sale now|best deals|deals of the day|deal of the day|shop now|sponsored content|affiliate links?', re.I)
# SECTION_URL_RE drops section/index links instead of articles (checked on direct feed URLs; Google News
# search URLs are opaque, so their stub headlines are caught by the two-word rule in run()).
SECTION_URL_RE=re.compile(r'/(hub|hubs|topic|topics|section|sections|category|categories)(/|$)', re.I)
# Urgent alert channels are exempt from near-duplicate merging: their repeated alerts are intentional.
EXEMPT_DEDUP={'tg_kpszsu','tg_dsns'}
def gnews_terms(url):
    # Plain query terms of a Google News search feed (minus site:/when: operators). A saved admin config can
    # drop the built-in Ukraine filter for these feeds, so enforce the feed's own query instead (2026-11-21:
    # an AP Haiti story reached the panel through the "Ukraine site:apnews.com" feed without it).
    if 'news.google.com/rss/search' not in url: return []
    q=' '.join(parse_qs(urlparse(url).query).get('q',[]))
    out=[]
    for t in re.findall(r'"([^"]+)"|(\S+)',q):
        t=(t[0] or t[1]).strip()
        if not t or t.startswith('-'): continue
        if re.match(r'(?i)^(site|when|before|after|intitle|inanchor|allintitle):',t): continue
        if t.upper() in ('OR','AND'): continue
        out.append(t)
    return out
def canon_url(u):
    # Canonical article URL for cross-source dedup: no scheme/www/fragment/tracking params/trailing slash.
    u=(u or '').strip().lower()
    u=re.sub(r'^https?://(www\.)?','',u).split('#')[0]
    u=re.sub(r'[?&](utm_[^=&]*|fbclid|gclid|oc)=[^&]*','',u)
    return u.rstrip('/?&')
def title_words(t): return set(re.sub(r'[^\w\s]','',t.lower()).split())
def title_prefix(t,n=6): return ' '.join(re.sub(r'[^\w\s]','',t.lower()).split()[:n])
NOW=dt.datetime.now(dt.timezone.utc)
SOURCES=[
 # id, label, kind, url/handle, lang, filter_to_ukraine
 ('kyivindependent','Kyiv Independent','rss','https://kyivindependent.com/news-archive/rss/','en',False),
 ('ukrinform','Ukrinform','rss','https://www.ukrinform.net/rss/block-lastnews','en',False),
 ('upravda','Ukrainska Pravda','rss','https://www.pravda.com.ua/eng/rss/','en',False),
 ('kyivpost','Kyiv Post','rss','https://www.kyivpost.com/feed','en',False),
 ('nv','NV (New Voice)','rss','https://english.nv.ua/rss/all_english.xml','en',True),
 ('suspilne','Суспільне','rss','https://suspilne.media/rss/all.rss','uk',False),
 ('guardian','The Guardian','rss','https://www.theguardian.com/world/ukraine/rss','en',False),
 ('meduza','Meduza','rss','https://meduza.io/rss/en/all','en',True),
 ('rferl','RFE/RL','rss','https://www.rferl.org/api/','en',True),
 ('bbc','BBC','rss','https://feeds.bbci.co.uk/news/world/europe/rss.xml','en',True),
 ('dw','DW','rss','https://rss.dw.com/rdf/rss-en-all','en',True),
 ('aljazeera','Al Jazeera','rss','https://www.aljazeera.com/xml/rss/all.xml','en',True),
 ('euronews','Euronews','rss','https://www.euronews.com/rss?format=mrss&level=theme&name=news','en',True),
 # Nov 21 2026: Google News routes removed - the aggregator rate-limits runner traffic (429s). CBC uses its
 # official world feed (cbc.ca/webfeed, items marked "Syndication Allowed"); the Ukraine filter keeps the beat.
 # Reuters and AP have no verified direct feed, so they are off (kept, disabled, in the admin source list)
 # rather than silently replaced by another outlet.
 ('cbc','CBC','rss','https://www.cbc.ca/webfeed/rss/rss-world','en',True),
 # "Twitter-like" social posts
 ('bsky_kyivindependent','Kyiv Independent','bsky','kyivindependent.com','en',False),
 ('bsky_pravda','Ukrainska Pravda','bsky','pravda.ua','en',False),
 ('bsky_kyivpost','Kyiv Post','bsky','kyivpost.com','en',False),
 ('bsky_euromaidan','Euromaidan Press','bsky','euromaidanpress.bsky.social','en',False),
 ('tg_zelensky','Zelenskyy / Official','tg','V_Zelenskiy_official','uk',False),
 ('tg_kpszsu','Повітряні сили ЗСУ','tg','kpszsu','uk',False),
 ('tg_genstaff','Генштаб ЗСУ','tg','GeneralStaffZSU','uk',False),
 ('tg_dsns','ДСНС','tg','dsns_telegram','uk',False),
 ('tg_suspilne','Суспільне Новини','tg','suspilnenews','uk',False),
 ('tg_ukrinform','Укрінформ','tg','ukrinform_news','uk',False),
 ('tg_kyivindependent','Kyiv Independent','tg','KyivIndependent_official','en',False),
]
# Admin page (Jason, Nov 8 2026; moved to GitHub Nov 9): config.json's sections.news_sources (in this repo, committed by
# the admin page) replaces SOURCES when it holds a valid list with at least one enabled source; any error or empty list
# keeps the built-in SOURCES above.
def config_sources():
    try:
        cfg=json.load(open('config.json',encoding='utf-8'))
        lst=(cfg.get('sections') or {}).get('news_sources')
        if not isinstance(lst,list) or not lst: return None
        out=[]
        for s in lst:
            if not isinstance(s,dict) or s.get('enabled') is False or s.get('kind') not in ('rss','bsky','tg'): continue
            url=str(s.get('url') or '').strip(); label=str(s.get('label') or '').strip()
            if not url or not label: continue
            if s['kind']=='rss' and not re.match(r'^https?://',url): continue
            sid=re.sub(r'[^A-Za-z0-9_]+','_',str(s.get('id') or label))[:40]
            out.append((sid,label,s['kind'],url,str(s.get('lang') or 'uk'),bool(s.get('filter_ua'))))
        return out or None
    except Exception as e:
        print('config sources unavailable, using built-in list:',str(e)[:120]); return None
_cfg=config_sources()
if _cfg: SOURCES=_cfg; print('sources from admin config:',len(SOURCES))
# Google News routes are disabled (Nov 21 2026): the aggregator rate-limits runner traffic (429s). Any Google
# News URL that reaches this list - from a saved admin config or an old built-in - is dropped here and reported
# as blocked instead of fetched, so an editor save cannot silently bring the route back.
BLOCKED=[s for s in SOURCES if 'news.google.com' in s[3]]
SOURCES=[s for s in SOURCES if 'news.google.com' not in s[3]]
if BLOCKED: print('blocked Google News routes:',[s[0] for s in BLOCKED])
def clean(s):
    s=re.sub(r'<!\[CDATA\[|\]\]>','',s or '')
    s=html.unescape(s)  # some feeds escape their HTML
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',s))).strip()
def pdate(s):
    s=(s or '').strip()
    try: d=email.utils.parsedate_to_datetime(s)
    except Exception:
        try: d=dt.datetime.fromisoformat(s.replace('Z','+00:00'))
        except Exception: return None
    if not d.tzinfo: d=d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)
def tag(block,*names):
    for n in names:
        m=re.search(r'<%s[^>]*>(.*?)</%s>'%(n,n),block,re.S)
        if m: return m.group(1)
    return ''
BROWSER_UA={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36','Accept':'application/rss+xml, application/xml, text/xml, */*'}
# Nov 22 2026 repair: same publisher, different official feed URL, tried only after the primary fails (never an aggregator)
ALT={'https://www.euronews.com/rss?format=mrss&level=theme&name=news':['https://www.euronews.com/rss?level=theme&name=news'],
     'https://www.cbc.ca/webfeed/rss/rss-world':['https://www.cbc.ca/cmlink/rss-world']}
def get(url,**kw):
    import time
    err=None
    # bot UA first (Suspilne answers a browser UA with 403), then backoff retries, then a browser UA, then the publisher's alternate URL
    for u in [url]+ALT.get(url,[]):
        for i,h in enumerate((UA,UA,BROWSER_UA)):
            if i: time.sleep(2*i)
            try:
                r=requests.get(u,headers=h,timeout=20,**kw)
                if r.status_code==200: return r
                err=f'HTTP {r.status_code}'
            except requests.RequestException as e:
                err=type(e).__name__
    raise RuntimeError(f'{err} after retries (UA variants + alternate URL)')
def rss(src):
    # No aggregator fallback (Nov 21 2026): a direct feed that fails is reported as failed, never silently
    # re-routed through Google News.
    t=get(src[3]).text
    if not re.search(r'<(item|entry)[ >]',t): raise ValueError('no items in feed')
    out=[]
    for m in re.finditer(r'<(item|entry)[ >].*?</(item|entry)>',t,re.S):
        b=m.group(0); title=clean(tag(b,'title'))
        link=clean(tag(b,'link')) or (re.search(r'<link[^>]*href="([^"]+)"',b) or [None,''])[1]
        d=pdate(clean(tag(b,'pubDate','dc:date','published','updated')))
        out.append(dict(title=title,url=link,published=d,text=clean(tag(b,'description','summary'))[:280]))
    return out
def bsky(src):
    r=requests.get('https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed',params={'actor':src[3],'limit':30,'filter':'posts_no_replies'},headers=UA,timeout=20).json()
    out=[]
    for x in r.get('feed',[]):
        if 'reason' in x: continue  # skip reposts
        p=x['post']; rid=p['uri'].split('/')[-1]
        out.append(dict(title=clean(p['record'].get('text',''))[:280],url=f"https://bsky.app/profile/{p['author']['handle']}/post/{rid}",published=pdate(p['record'].get('createdAt'))))
    return out
def tg(src):
    t=get('https://t.me/s/'+src[3]).text
    out=[]
    for m in re.finditer(r'data-post="([^"]+)".*?(?=data-post="|$)',t,re.S):
        b=m.group(0); txt=re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',b,re.S)
        d=re.search(r'<time datetime="([^"]+)"',b)
        if not txt: continue
        out.append(dict(title=clean(txt.group(1).replace('<br/>',' '))[:280],url='https://t.me/'+m.group(1),published=pdate(d.group(1)) if d else None))
    return out
def run(src):
    try:
        items={'rss':rss,'bsky':bsky,'tg':tg}[src[2]](src)
    except Exception as e:
        return src,[],str(e)[:120]
    gterms=gnews_terms(src[3])
    res=[]
    for i in items:
        if not i['title'] or not i['published']: continue
        if (NOW-i['published']).total_seconds()>48*3600 or (i['published']-NOW).total_seconds()>3600: continue
        hay=i['title']+' '+i.get('text','')
        if gterms:  # Google News search feed: the item must fit the feed's own topic (Ukraine beat or its query terms)
            if not UA_RE.search(hay) and not any(t.lower() in hay.lower() for t in gterms): continue
        elif src[5] and not UA_RE.search(hay): continue
        if PROMO_RE.search(i['title']): continue  # commercial/promotional post, not news
        if src[2]=='rss':
            path=re.sub(r'^https?://[^/]*','',i['url'] or '')
            if SECTION_URL_RE.search(path): continue  # section/index page, not an article
            if len(i['title'].split())<=2: continue  # stub headline, usually a section link
        res.append(dict(id=f"{src[0]}:{i['url']}",source=src[1],sourceId=src[0],kind=src[2],lang=src[4],title=i['title'],url=i['url'],published=i['published'].isoformat().replace('+00:00','Z')))
    return src,res[:15],None
with cf.ThreadPoolExecutor(12) as ex: results=list(ex.map(run,SOURCES))
# Dedup, newest first. Drop: exact normalized title repeats, same canonical URL across sources, and
# near-identical titles from the same outlet (word overlap >= 0.88, or the same 6-word opening of a recurring
# daily report), e.g. a headline reposted with slightly
# different punctuation or a daily report superseded by today's. Urgent alert channels keep every post.
items_all,status=[],[]
for src,res,err in results:
    status.append(dict(id=src[0],source=src[1],kind=src[2],ok=err is None,count=len(res),error=err))
    items_all.extend(res)
for s in BLOCKED:
    status.append(dict(id=s[0],source=s[1],kind=s[2],ok=False,count=0,blocked=True,error='blocked: Google News route disabled'))
# When a story exists as both an article (rss) and a social repost (tg/bsky), keep the article: it links to the
# full story and carries the original publish time. Within a kind, the newest copy wins.
KIND_RANK={'rss':0,'tg':1,'bsky':2}
items_all.sort(key=lambda r:r['published'],reverse=True)
items_all.sort(key=lambda r:KIND_RANK.get(r['kind'],3))  # stable: newest first within each kind
items=[]; seen_titles=set(); seen_urls=set(); kept=[]
for r in items_all:
    k=re.sub(r'\W+','',r['title'].lower())[:60]
    cu=canon_url(r['url'])
    if r['sourceId'] in EXEMPT_DEDUP:
        if cu and cu in seen_urls: continue  # only the exact same post is a duplicate here
    else:
        if k in seen_titles or (cu and cu in seen_urls): continue
        w=title_words(r['title']); pref=title_prefix(r['title'])
        if len(w)>=4 and any(ks==r['source'] and (len(w&kw)/len(w|kw)>=0.88 or (len(w)>=6 and pref==kp)) for ks,kw,kp in kept): continue
    seen_titles.add(k)
    if cu: seen_urls.add(cu)
    kept.append((r['source'],title_words(r['title']),title_prefix(r['title']))); items.append(r)
items.sort(key=lambda r:r['published'],reverse=True)
# failure streaks: carry each source's fail_since from the previous committed news.json so the editor can flag
# sources dead for hours, not just this run; a source failing now without a stored fail_since starts one
try:
    _prev = {s.get("id"): s for s in json.load(open("news.json", encoding="utf-8")).get("sources", [])}
except Exception:
    _prev = {}
for s in status:
    if s.get("blocked") or (s["ok"] and s["count"]):
        continue
    s["fail_since"] = (_prev.get(s["id"]) or {}).get("fail_since") or NOW.isoformat().replace("+00:00", "Z")
json.dump(dict(generated=NOW.isoformat().replace('+00:00','Z'),items=items[:300],sources=status),open('news.json','w'),ensure_ascii=False,indent=1)
print(len(items),'items;',sum(s['ok'] for s in status),'/',len(status),'sources ok')
for s in status:
    if not s['ok'] or not s['count']: print('  WARN',s)

# auto bulletins for the BULLETINS box (Nov 9 2026): fetch_bulletins.py throttles itself to every 10 min, keeps the
# last good file on failure and stages auto_bulletins.json so the live-runner's commit carries it.
try:
    import subprocess as _sp, sys as _sys
    _sp.run([_sys.executable, 'scripts/fetch_bulletins.py'], timeout=90, check=False)
except Exception as _e:
    print('auto bulletins skipped:', _e)

# admin Stats tab rollup (Nov 11 2026): stats.py throttles itself to every 5 min, keeps the last good file on failure
# and stages stats.json so the live-runner's commit carries it.
try:
    import subprocess as _sp2, sys as _sys2
    _sp2.run([_sys2.executable, 'scripts/stats.py'], timeout=60, check=False)
except Exception as _e2:
    print('stats skipped:', _e2)

# live conflict photos (v2.10, Nov 11 2026): fetch_conflict_photos.py --runner throttles itself to every 10 min, writes
# photos-pending.json (candidates, never on air: only the Editor's Approve puts one in photos-conflict.json) and stages
# it so the live-runner's commit carries it. A failure keeps the last file.
try:
    import subprocess as _sp3, sys as _sys3
    _sp3.run([_sys3.executable, 'scripts/fetch_conflict_photos.py', '--runner'], timeout=150, check=False)
except Exception as _e3:
    print('conflict photos skipped:', _e3)

# internet signal per oblast (v2.11, Nov 11 2026): fetch_ioda.py throttles itself to every 5 min, writes ioda.json for the
# ІНТЕРНЕТ map (score + IODA critical level) and stages it; a failure keeps the last file.
try:
    import subprocess as _sp4, sys as _sys4
    _sp4.run([_sys4.executable, 'scripts/fetch_ioda.py'], timeout=120, check=False)
except Exception as _e4:
    print('ioda skipped:', _e4)

# ПОГОДА card (v2.12, Nov 11 2026): fetch_meteoalarm.py throttles itself to every 10 min, writes weather_warnings.json
# (Meteoalarm / Укргідрометцентр, + Moldova when it has warnings) and stages it; a failed feed keeps its last warnings.
try:
    import subprocess as _sp5, sys as _sys5
    _sp5.run([_sys5.executable, 'scripts/fetch_meteoalarm.py'], timeout=120, check=False)
except Exception as _e5:
    print('weather warnings skipped:', _e5)

# РАДІАЦІЯ view (v2.14, Nov 11 2026): fetch_radiation.py reads SaveEcoBot (key: SAVEECOBOT_KEY secret, live-runner only)
# about every 2 min, Safecast as the labelled backup, writes radiation.json (selected sites only) and stages it.
try:
    import subprocess as _sp6, sys as _sys6
    _sp6.run([_sys6.executable, 'scripts/fetch_radiation.py'], timeout=150, check=False)
except Exception as _e6:
    print('radiation skipped:', _e6)

# ПОВІТРЯ / AIR QUALITY view (v2.26, Nov 11 2026): fetch_airquality.py reads SaveEcoBot /sensors (key: SAVEECOBOT_KEY
# secret, live-runner only) about once an hour, writes airquality.json (named cities only, median US EPA AQI) and stages it.
try:
    import subprocess as _sp7, sys as _sys7
    _sp7.run([_sys7.executable, 'scripts/fetch_airquality.py'], timeout=150, check=False)
except Exception as _e7:
    print('air quality skipped:', _e7)

# photo bandwidth fix (v2.27, Nov 11 2026): cache_photos.py throttles itself to every 10 min and makes one resized WebP
# (≤1280 px wide, never cropped) per on-air photo into photos-cache/ (+ index.json), at most 40 new per run; stages it.
try:
    import subprocess as _sp8, sys as _sys8
    _sp8.run([_sys8.executable, 'scripts/cache_photos.py'], timeout=240, check=False)
except Exception as _e8:
    print('photo cache skipped:', _e8)

# daily source re-check (Jason, Nov 12 2026: "every day be sure to update everything at least once"): verify_static.py
# re-checks every hand-made emergency number against its official page once per 20 h; numbers are never changed by it.
try:
    import subprocess as _sp9, sys as _sys9
    _sp9.run([_sys9.executable, 'scripts/verify_static.py'], timeout=200, check=False)
except Exception as _e9:
    print('daily source check skipped:', _e9)

# FIRES (v2.33, Jason Nov 12 2026): fetch_fires.py writes fires.json (NASA FIRMS, count per oblast only, no coordinates),
# at most once an hour; stages it.
try:
    import subprocess as _sp10, sys as _sys10
    _sp10.run([_sys10.executable, 'scripts/fetch_fires.py'], timeout=200, check=False)
except Exception as _e10:
    print('fires skipped:', _e10)

# MAP CHANGE SUMMARIES (v2.53): fetch_changes.py keeps a short history of real fires / internet / power readings in
# changes.json (at most every 30 min; stages it) for the screen's "what changed" strips.
try:
    import subprocess as _sp12, sys as _sys12
    _sp12.run([_sys12.executable, 'scripts/fetch_changes.py'], timeout=60, check=False)
except Exception as _e12:
    print('changes skipped:', _e12)

# EARTHQUAKES (v2.34): fetch_quakes.py writes quakes.json (EMSC, 30 days, M2.5+), at most every 10 min; stages it.
try:
    import subprocess as _sp11, sys as _sys11
    _sp11.run([_sys11.executable, 'scripts/fetch_quakes.py'], timeout=120, check=False)
except Exception as _e11:
    print('quakes skipped:', _e11)

# RIVERS (v2.40, Jason Nov 12 2026: "Dnipro river warnings"): fetch_rivers.py writes rivers.json (UkrHMC hydrological
# warnings for river basins + Dnipro reservoirs, as published), at most every 30 min; stages it.
try:
    import subprocess as _sp12, sys as _sys12
    _sp12.run([_sys12.executable, 'scripts/fetch_rivers.py'], timeout=120, check=False)
except Exception as _e12:
    print('rivers skipped:', _e12)

# DEFENCE TODAY (v2.41, Jason Nov 12 2026): fetch_defence.py writes defence.json (Air Force overnight launched / shot down,
# General Staff daily losses; numbers only), at most every 30 min; stages it.
try:
    import subprocess as _sp13, sys as _sys13
    _sp13.run([_sys13.executable, 'scripts/fetch_defence.py'], timeout=120, check=False)
except Exception as _e13:
    print('defence skipped:', _e13)
