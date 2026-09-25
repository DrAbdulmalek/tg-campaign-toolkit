#!/usr/bin/env python3
"""medlineplus_en.py — fetch English counterparts for harvested Arabic PDFs.

GCS rule: <Base>_ARA.pdf|_AR.pdf|_Arabic.pdf  <->  <Base>.pdf
Non-GCS: try sibling candidates; verify via HEAD; log misses.
Env MLP_BUDGET (default 110s).
"""
import os, sys, json, re, time
import requests

BASE = '/home/z/my-project'
OUT = f'{BASE}/download/medlineplus'
ENDIR = f'{OUT}/en'
MANIFEST = f'{OUT}/pairs.jsonl'
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


def en_candidates(ar_url):
    m = re.match(r'(.*/)(.+?)_(ARA|AR|Arabic|ar)\.pdf$', ar_url)
    if m:
        return [f'{m.group(1)}{m.group(2)}.pdf']
    m = re.match(r'(.*/)(.+?)[_-]?[Aa]rabic\.pdf$', ar_url)
    if m:
        return [f'{m.group(1)}{m.group(2)}.pdf',
                f'{m.group(1)}{m.group(2)}English.pdf',
                f'{m.group(1)}{m.group(2)}_English.pdf']
    return []


def main():
    recs = [json.loads(l) for l in open(MANIFEST)]
    todo = [r for r in recs if not r.get('en_file')]
    log(f'records={len(recs)} missing_en={len(todo)}')
    ok = miss = err = 0
    updated = []
    for r in recs:
        if r.get('en_file'):
            continue
        if time.time() > DEADLINE:
            log('BUDGET_OUT')
            break
        got = None
        for c in en_candidates(r['ar']):
            try:
                h = S.head(c, timeout=15)
                if h.status_code == 200:
                    name = slug(r['title']) + '__EN.pdf'
                    dest = f'{ENDIR}/{name}'
                    if not os.path.exists(dest):
                        with S.get(c, stream=True, timeout=(15, 90)) as resp:
                            resp.raise_for_status()
                            tmp = dest + '.part'
                            with open(tmp, 'wb') as f:
                                for ch in resp.iter_content(1 << 16):
                                    f.write(ch)
                            os.replace(tmp, dest)
                    r['en'], r['en_file'] = c, f'en/{name}'
                    got = c
                    break
            except Exception:
                continue
        if got:
            ok += 1
        else:
            miss += 1
            r['en'] = r.get('en') or None
            if ok and ok % 20 == 0:
                log(f'progress ok={ok}')
        updated.append(r)
    # rewrite manifest with updates
    by_id = {id(r): r for r in recs}
    out = []
    ui = iter(updated)
    for r in recs:
        out.append(r if not r.get('_pending') else next(ui))
    # simpler: merge by ar url
    upd_map = {r['ar']: r for r in updated}
    final = [upd_map.get(r['ar'], r) for r in recs]
    with open(MANIFEST, 'w', encoding='utf-8') as f:
        for r in final:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    log(f'RESULT ok={ok} miss={miss}')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback; traceback.print_exc()
        sys.exit(1)
