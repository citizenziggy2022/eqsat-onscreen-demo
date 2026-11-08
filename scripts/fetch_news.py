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
def rss(src):
    t=get(src[3]).text
    out=[]
    for m in re.finditer(r'<(item|entry)[ >].*?</(item|entry)>',t,re.S):
        b=m.group(0); title=clean(tag(b,'title'))
        link=clean(tag(b,'link')) or (re.search(r'<link[^>]*href="([^"]+)"',b) or [None,''])[1]
        d=pdate(clean(tag(b,'pubDate','dc:date','published','updated')))
        if src[2]=='rss' and 'news.google.com' in src[3]: title=re.sub(r'\s+-\s+[^-]+$','',title)  # drop " - Reuters"
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
    status.append(dict(id=src[0],source=src[1],kind=src[2],ok=err is None,count=len(res),error=err))
    for r in res:
        k=re.sub(r'\W+','',r['title'].lower())[:60]
        if k in seen: continue
        seen.add(k); items.append(r)
items.sort(key=lambda r:r['published'],reverse=True)
json.dump(dict(generated=NOW.isoformat().replace('+00:00','Z'),items=items[:300],sources=status),open('news.json','w'),ensure_ascii=False,indent=1)
print(len(items),'items;',sum(s['ok'] for s in status),'/',len(status),'sources ok')
for s in status:
    if not s['ok'] or not s['count']: print('  WARN',s)
