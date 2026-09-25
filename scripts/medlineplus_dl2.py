#!/usr/bin/env python3
"""medlineplus_dl2.py — MedlinePlus Arabic-languages page bilingual PDF harvester.

Page structure: <li class="item_ident"> blocks, each with sibling <div class="site">
links: Arabic (title contains '(Arabic)') and its English counterpart.
Sources: healthinfotranslations-pdfdocs (GCS), cancer.org, immunize.org (VIS),
cdc.gov, uofmhealth.org, healthvermont.gov, cms.gov ...

Output:
  download/medlineplus/pairs.jsonl        {title, ar_url, en_url, ar_file, en_file}
  download/medlineplus/ar/<name>.pdf      Arabic documents
  download/medlineplus/en/<name>.pdf      English counterparts
Resumable via manifest. Env MLP_BUDGET (default 110s).
"""
import os, sys, json, re, time, traceback
from urllib.parse import urlparse
import requests

BASE = '/home/z/my-project'
OUT = f'{BASE}/download/medlineplus'
ARDIR, ENDIR = f'{OUT}/ar', f'{OUT}/en'
MANIFEST = f'{OUT}/pairs.jsonl'
LANGPAGE = 'https://medlineplus.gov/languages/arabic.html'
UAH = {'User-Agent': 'Mozilla/5.0 (Linux; Android 13; Pixel 7) Chrome/124.0 Safari/537.36'}
BUDGET = float(os.environ.get('MLP_BUDGET', '110'))
DEADLINE = time.time() + BUDGET

S = requests.Session()
S.headers.update(UAH)


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def slug(s, maxlen=80):
    s = re.sub(r'[^\w\s.\-]', '', s or 'doc')
    s = re.sub(r'\s+', '_', s.strip())
    return s[:maxlen] or 'doc'


def parse_index():
    r = S.get(LANGPAGE, timeout=40)
    r.raise_for_status()
    h = r.text
    blocks = h.split('<li class="item_ident">')
    pairs = []
    for b in blocks[1:]:
        links = re.findall(
            r'<a href="(https?://[^"]+?)"[^>]*?title="([^"]*)"', b)
        if not links:
            links = re.findall(r'<a href="(https?://[^"]+?)"[^>]*>(.*?)</a>', b, re.S)
        ar = en = None
        ar_title = en_title = ''
        for u, t in links:
            t_clean = re.sub(r'<[^>]+>', '', t)
            is_ar = ('(Arabic)' in t) or ('(Arabic)' in t_clean) or re.search(r'[_\-]Arabic\b', u)
            is_en = ('(English)' in t) or ('(English)' in t_clean) or re.search(r'[_\-]English\b|English\.pdf', u)
            if is_ar and not ar:
                ar, ar_title = u, re.sub(r'\s*-\s*Ø§ÙØ¹Ø±Ø¨ÙØ© \(Arabic\).*', '', t_clean).strip()
                ar_title = re.sub(r'\s*-?\s*\(Arabic\)\s*(PDF)?.*$', '', ar_title).strip()
            elif is_en and not en:
                en, en_title = u, re.sub(r'\s*-?\s*\(English\)\s*(PDF)?.*$', '', t_clean).strip()
        if ar:
            pairs.append({'ar': ar, 'en': en, 'title': ar_title or en_title or slug(urlparse(ar).path[-60:])})
    return pairs


def dl(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return 'skip'
    with S.get(url, stream=True, timeout=(15, 90)) as r:
        r.raise_for_status()
        ct = r.headers.get('content-type', '').lower()
        if 'text/html' in ct and not url.lower().endswith('.pdf'):
            return 'html'
        tmp = dest + '.part'
        with open(tmp, 'wb') as f:
            for c in r.iter_content(1 << 16):
                f.write(c)
    os.replace(tmp, dest)
    return 'ok'


def main():
    os.makedirs(ARDIR, exist_ok=True)
    os.makedirs(ENDIR, exist_ok=True)
    done = set()
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            for line in f:
                try:
                    done.add(json.loads(line)['ar'])
                except Exception:
                    pass
    pairs = parse_index()
    todo = [p for p in pairs if p['ar'] not in done]
    log(f'pairs={len(pairs)} done={len(done)} todo={len(todo)}')
    ok = err = 0
    with open(MANIFEST, 'a', encoding='utf-8') as mf:
        for p in todo:
            if time.time() > DEADLINE:
                log('BUDGET_OUT')
                break
            rec = dict(p, ar_file=None, en_file=None)
            try:
                name = slug(p['title']) + '__AR.pdf'
                r = dl(p['ar'], f'{ARDIR}/{name}')
                if r != 'html':
                    rec['ar_file'] = f'ar/{name}'
                if p['en']:
                    ename = slug(p['title']) + '__EN.pdf'
                    r2 = dl(p['en'], f'{ENDIR}/{ename}')
                    if r2 != 'html':
                        rec['en_file'] = f'en/{ename}'
                mf.write(json.dumps(rec, ensure_ascii=False) + '\n')
                mf.flush()
                ok += 1
                if ok % 5 == 0:
                    log(f'progress ok={ok}')
            except Exception as e:
                err += 1
                mf.write(json.dumps(rec, ensure_ascii=False) + '\n')
                mf.flush()
                log(f'ERR {p["title"][:40]}: {type(e).__name__}')
                time.sleep(1)
    log(f'RESULT ok={ok} err={err} total={len(done)+ok}')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
