#!/usr/bin/env python3
"""Build news.json for the eQsat screen. Runs in a GitHub Action every 5 min.
Sources: RSS feeds, Bluesky public API, Telegram t.me/s previews. Stdlib + requests only."""
import re, json, html, email.utils, datetime as dt, concurrent.futures as cf, requests
UA={'User-Agent':'Mozilla/5.0 (eQsat OnScreen news bot; +https://citizenziggy2022.github.io/eqsat-onscreen-demo/)'}  # a browser-like UA got 403 from Suspilne; this one works
UA_RE=re.compile(r'ukrain|kyiv|kiev|zelensk|kharkiv|odesa|donbas|crimea|україн|київ|зеленськ|харків|одес', re.I)
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
 ('cbc','CBC','rss','https://news.google.com/rss/search?q=Ukraine+site:cbc.ca+when:2d&hl=en-CA&gl=CA&ceid=CA:en','en',True),
 ('reuters','Reuters','rss','https://news.google.com/rss/search?q=Ukraine+site:reuters.com+when:1d&hl=en-US&gl=US&ceid=US:en','en',True),
 ('ap','AP','rss','https://news.google.com/rss/search?q=Ukraine+site:apnews.com+when:1d&hl=en-US&gl=US&ceid=US:en','en',True),
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
def get(url,**kw):
    for _ in range(2):  # one retry: Suspilne sometimes answers 403 for a moment
        r=requests.get(url,headers=UA,timeout=20,**kw)
        if r.status_code==200: return r
    r.raise_for_status(); return r
def gnews_url(feed_url,lang):
    # same outlet through Google News, for official feeds whose CDN blocks datacenter IPs (Suspilne: Cloudflare 403)
    host=re.sub(r'^www\.','',requests.utils.urlparse(feed_url).hostname or '')
    hl,gl,ceid=('uk','UA','UA:uk') if lang=='uk' else ('en-US','US','US:en')
    return f'https://news.google.com/rss/search?q=site:{host}+when:1d&hl={hl}&gl={gl}&ceid={ceid}'
VIA={}
def rss(src):
    try:
        t=get(src[3]).text
        if not re.search(r'<(item|entry)[ >]',t): raise ValueError('no items in feed')
    except Exception as e:
        if 'news.google.com' in src[3]: raise
        gu=gnews_url(src[3],src[4])
        t=get(gu).text; VIA[src[0]]=f'google-news fallback ({str(e)[:60]})'
        src=(src[0],src[1],src[2],gu,src[4],src[5])
    out=[]
    for m in re.finditer(r'<(item|entry)[ >].*?</(item|entry)>',t,re.S):
        b=m.group(0); title=clean(tag(b,'title'))
        link=clean(tag(b,'link')) or (re.search(r'<link[^>]*href="([^"]+)"',b) or [None,''])[1]
        d=pdate(clean(tag(b,'pubDate','dc:date','published','updated')))
        if src[2]=='rss' and 'news.google.com' in src[3]: title=re.sub(r'\s+-\s+[^-]+$','',title)  # drop " - Reuters"
        if src[0] in VIA and src[4]=='uk' and len(re.findall('[а-яіїєґА-ЯІЇЄҐ]',title))<len(re.findall('[A-Za-z]',title)): continue  # fallback: Ukrainian-language headlines only
        if src[0] in VIA and re.search(r'Суспільне Спорт|розклад, наживо',title): continue  # schedule pages, not news
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
    res=[]
    for i in items:
        if not i['title'] or not i['published']: continue
        if (NOW-i['published']).total_seconds()>48*3600 or (i['published']-NOW).total_seconds()>3600: continue
        if src[5] and not UA_RE.search(i['title']+' '+i.get('text','')): continue
        res.append(dict(id=f"{src[0]}:{i['url']}",source=src[1],sourceId=src[0],kind=src[2],lang=src[4],title=i['title'],url=i['url'],published=i['published'].isoformat().replace('+00:00','Z')))
    return src,res[:15],None
with cf.ThreadPoolExecutor(12) as ex: results=list(ex.map(run,SOURCES))
items,status=[],[]
seen=set()
for src,res,err in results:
    status.append(dict(id=src[0],source=src[1],kind=src[2],ok=err is None,count=len(res),error=err,**({'via':VIA[src[0]]} if src[0] in VIA else {})))
    for r in res:
        k=re.sub(r'\W+','',r['title'].lower())[:60]
        if k in seen: continue
        seen.add(k); items.append(r)
items.sort(key=lambda r:r['published'],reverse=True)
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
