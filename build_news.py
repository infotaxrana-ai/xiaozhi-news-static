#!/usr/bin/env python3
"""Scheduled RSS to static JSON. Python standard library only."""
import concurrent.futures
import datetime as dt
import email.utils
import html
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
WINDOW = dt.timedelta(hours=36)
OUTPUT = Path(__file__).resolve().parent / 'data' / 'news.json'
SOURCES = [
    ('প্রথম আলো', 'https://www.prothomalo.com/feed/', 'bn'),
    ('জাগোনিউজ২৪', 'https://www.jagonews24.com/rss/rss.xml', 'bn'),
    ('বিডি২৪লাইভ', 'https://www.bd24live.com/feed', 'bn'),
    ('বিবিসি বাংলা', 'https://feeds.bbci.co.uk/bengali/rss.xml', 'bn'),
    ('বাংলাদেশ টুডে', 'https://thebangladeshtoday.com/feed/', 'en'),
]

def clean(s, limit=1650):
    s = re.sub(r'<[^>]*>', ' ', s or '')
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()[:limit]

def cat(text):
    t = text.lower()
    for name, pattern in [
        ('রাজনীতি', 'রাজনীতি|নির্বাচন|সংসদ'),
        ('খেলাধুলা', 'ক্রিকেট|ফুটবল|খেলা|ম্যাচ'),
        ('প্রযুক্তি', 'প্রযুক্তি|বিজ্ঞান|কৃত্রিম বুদ্ধিমত্তা'),
        ('অর্থনীতি', 'অর্থনীতি|বাণিজ্য|ব্যবসা|শেয়ারবাজার'),
        ('আন্তর্জাতিক', 'আন্তর্জাতিক|বিশ্ব|যুক্তরাষ্ট্র|রাশিয়া|চীন|গাজা'),
        ('বিনোদন', 'বিনোদন|সিনেমা|অভিনেতা|অভিনেত্রী'),
    ]:
        if re.search(pattern, t): return name
    return 'বাংলাদেশ'

def parse_date(value):
    if not value: return None
    try:
        v = email.utils.parsedate_to_datetime(value)
        if v: return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
    except (TypeError, ValueError): pass
    try:
        v = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return v.replace(tzinfo=UTC) if v.tzinfo is None else v.astimezone(UTC)
    except ValueError: return None

def field(node, *names):
    for name in names:
        target = node.find(name)
        if target is not None:
            text = ''.join(target.itertext()).strip()
            if text: return text
    return ''

def get_source(source):
    name, url, language = source
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; XiaozhiNewsStatic/1.0)',
        'Accept': 'application/rss+xml,application/atom+xml,application/xml,text/xml,*/*'
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            xml = response.read(2_000_001)
            if len(xml) > 2_000_000: raise ValueError('Feed too large')
        root = ET.fromstring(xml)
        items = root.findall('.//item') or root.findall('{http://www.w3.org/2005/Atom}entry')
        output=[]
        for n in items[:100]:
            title = clean(field(n, 'title', '{http://www.w3.org/2005/Atom}title'), 300)
            link = field(n, 'link')
            if not link:
                el=n.find('{http://www.w3.org/2005/Atom}link')
                link=el.get('href', '') if el is not None else ''
            date=parse_date(field(n, 'pubDate', 'published', 'updated', '{http://www.w3.org/2005/Atom}published', '{http://www.w3.org/2005/Atom}updated', '{http://purl.org/dc/elements/1.1/}date'))
            desc=clean(field(n, 'description', '{http://purl.org/rss/1.0/modules/content/}encoded', 'summary', '{http://www.w3.org/2005/Atom}summary', '{http://www.w3.org/2005/Atom}content'))
            p=urlparse(link)
            if not title or p.scheme!='https' or not p.hostname or not date or not NOW-WINDOW <= date <= NOW+dt.timedelta(minutes=5): continue
            output.append({'title':title, 'url':link, 'source':name, 'host':p.hostname, 'language':language, 'category':cat(title+' '+desc), 'detail':desc, 'published':int(date.timestamp()), 'published_iso':date.isoformat()})
        return name, output, None
    except Exception as e:
        return name, [], f'{type(e).__name__}: {str(e)[:150]}'

def build():
    old = {}
    if OUTPUT.exists():
        try: old=json.loads(OUTPUT.read_text(encoding='utf-8'))
        except (ValueError, OSError): pass
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        gathered=list(ex.map(get_source, SOURCES))
    unique={}
    for _, articles, _ in gathered:
        for a in articles: unique[a['url'].split('?')[0]]=a
    if not unique:
        for a in old.get('articles', []):
            if a.get('published',0)>=int((NOW-WINDOW).timestamp()):
                unique[a['url'].split('?')[0]]=a
    articles=sorted(unique.values(), key=lambda a: a['published'], reverse=True)[:120]
    status=[{'source':name,'ok':bool(articles),'count':len(articles),'error':error} for name,articles,error in gathered]
    payload={
        'schema_version':1, 'generated_at':NOW.isoformat(), 'lookback_hours':36,
        'count':len(articles), 'fresh_results':sum(len(x[1]) for x in gathered),
        'sources':status, 'articles':articles
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Don't overwrite a previously valid snapshot if every source failed.
    if not articles and old.get('articles'):
        print('All feeds failed; preserving last snapshot')
        return
    OUTPUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    print(f'Wrote {len(articles)} articles; feed successes: {sum(bool(x[1]) for x in gathered)}')

if __name__=='__main__': build()
