#!/usr/bin/env python3
"""Refresh lineup.json (eQtv NOW / NEXT on the eQsat screen) from eQtv's own published schedule.
Source: tv.equalit.ie/schedule/ - the page loads its programme list from WordPress admin-ajax
(action eqtv_get_schedule_programs, date_filter=fromtoday, timezone=UTC), once in English and once in Ukrainian.
Runs in GitHub Actions (lineup.yml every 6 h, and the live-runner every 3 h). No secrets.
Safety: if the fetch or the parse fails, or yields too little, the last good lineup.json is kept untouched
(the script exits non-zero and writes nothing). It never writes an empty file."""
import json, re, sys, html, datetime as dt, time
from zoneinfo import ZoneInfo
import requests

URL = 'https://tv.equalit.ie/wp-admin/admin-ajax.php'
UA = {'User-Agent': 'Mozilla/5.0 (eQsat OnScreen schedule bot; +https://citizenziggy2022.github.io/eqsat-onscreen-demo/)'}
KYIV = ZoneInfo('Europe/Kyiv')
NOW = dt.datetime.now(dt.timezone.utc)
OUT = 'lineup.json'
MIN_ITEMS = 5          # fewer future programmes than this = treat as a failed fetch
KEEP_PAST_H = 3        # keep programmes that started up to 3 h ago (NOW needs the one on air)

def fetch(lang):
    last = None
    for attempt in range(3):
        try:
            r = requests.post(URL + ('?lang=uk' if lang == 'uk' else ''), headers=UA, timeout=40,
                              data={'action': 'eqtv_get_schedule_programs', 'date_filter': 'fromtoday', 'timezone': 'UTC', 'lang': lang})
            r.raise_for_status()
            j = r.json()
            if not j.get('success'): raise RuntimeError('success=false')
            return j['data']['html']
        except Exception as e:
            last = e; time.sleep(5 * (attempt + 1))
    raise RuntimeError(f'{lang}: {last}')

def text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()

def parse(h):
    out = []
    for day in re.finditer(r'<div class="eqtv-day" data-date="(\d{4}-\d{2}-\d{2})">(.*?)(?=<div class="eqtv-day" data-date=|\Z)', h, re.S):
        date, body = day.group(1), day.group(2)
        for p in re.finditer(r'<div class="eqtv-program" id="program-[^"]*">(.*?)(?=<div class="eqtv-program" id=|\Z)', body, re.S):
            b = p.group(1)
            hr = re.search(r'eqtv-program-hour">\s*(\d{1,2}):(\d{2})\s*<', b)
            ti = re.search(r'<div class="eqtv-program-title[^"]*">(.*?)</div>', b, re.S)
            if not hr or not ti: continue
            link = re.search(r'<a href="(https://tv\.equalit\.ie/[^"]*video/[^"]+)"', ti.group(1))
            title = text(re.sub(r'<svg.*?</svg>', '', ti.group(1), flags=re.S))
            if not title: continue
            start = dt.datetime.fromisoformat(date).replace(hour=int(hr.group(1)), minute=int(hr.group(2)), tzinfo=dt.timezone.utc)
            vid = None
            if link:
                m = re.search(r'/video/([A-Za-z0-9]+)', link.group(1)); vid = m.group(1) if m else None
            out.append(dict(start=start, title=title, vid=vid))
    out.sort(key=lambda x: x['start'])
    return out

def split(title):
    # eQtv titles read "Programme title | Series"
    if ' | ' in title:
        t, s = title.rsplit(' | ', 1)
        return t.strip(), s.strip()
    return title, ''

def main():
    en = parse(fetch('en'))
    uk = parse(fetch('uk'))
    uk_by = {x['start']: x for x in uk}
    items = []
    for i, x in enumerate(en):
        nxt = en[i + 1]['start'] if i + 1 < len(en) else None
        slot = int((nxt - x['start']).total_seconds() // 60) if nxt else 30
        if slot <= 0 or slot > 600: slot = 30
        end = x['start'] + dt.timedelta(minutes=slot)
        if end < NOW - dt.timedelta(hours=KEEP_PAST_H): continue
        t_en, s_en = split(x['title'])
        u = uk_by.get(x['start'])
        t_uk, s_uk = split(u['title']) if u else (t_en, s_en)
        items.append(dict(
            id='eqtv-' + x['start'].strftime('%Y%m%d%H%M'),
            title_uk=t_uk, title_en=t_en, series_uk=s_uk or s_en, series_en=s_en,
            airs_utc=x['start'].strftime('%Y-%m-%dT%H:%M:00Z'),
            airs_kyiv=x['start'].astimezone(KYIV).strftime('%Y-%m-%d %H:%M'),
            slot_min=slot,
            video_url=('https://tv.equalit.ie/video/' + x['vid']) if x['vid'] else None,
            verified=True, source='https://tv.equalit.ie/schedule/'))
    future = [i for i in items if i['airs_utc'] > NOW.strftime('%Y-%m-%dT%H:%M:00Z')]
    if len(future) < MIN_ITEMS:
        raise RuntimeError(f'only {len(future)} future programmes parsed (en {len(en)}, uk {len(uk)})')
    try:
        prev = json.load(open(OUT, encoding='utf-8'))
    except Exception:
        prev = {}
    panel = prev.get('panel') or {
        'label_uk': 'eQtv · ДАЛІ В ЕФІРІ', 'label_en': 'eQtv · COMING UP', 'logo': 'eqtv-logo-white.png',
        'tune_in': 'Hotbird 13°E · Svoboda transponder · 10950 MHz V · SR 27500 · FEC 2/3', 'watch_url': 'tv.equalit.ie/live'}
    ahead_h = (dt.datetime.fromisoformat(items[-1]['airs_utc'].replace('Z', '+00:00')) - NOW).total_seconds() / 3600
    doc = dict(panel=panel, status='live', generated_at=NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
               source='https://tv.equalit.ie/schedule/ (EN + UK, admin-ajax eqtv_get_schedule_programs, UTC)',
               ahead_hours=round(ahead_h, 1),
               timezone_note='airs_utc is from eQtv\'s published schedule in UTC; airs_kyiv is Europe/Kyiv.',
               slot_min_note='slot_min = minutes until the next programme starts on eQtv\'s schedule.',
               items=items)
    tmp = OUT + '.tmp'
    json.dump(doc, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    json.load(open(tmp, encoding='utf-8'))  # re-read before replacing the good file
    import os; os.replace(tmp, OUT)
    print(f'lineup.json: {len(items)} programmes, {len(future)} ahead, {ahead_h:.0f} h ahead (en {len(en)}, uk {len(uk)}, uk matched {sum(1 for x in en if x["start"] in uk_by)})')

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('lineup fetch failed, keeping last lineup.json:', str(e)[:300]); sys.exit(1)
