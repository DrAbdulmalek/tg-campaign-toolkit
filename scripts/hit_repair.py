#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hit_repair.py — retry versions lookup for healthinfo docs still missing PDFs.
Lenient mode: saves whichever side (AR/EN) exists; marks no-AR docs out of scope.
"""
import os, re, json, time
BASE = '/home/z/my-project'
OUT = f'{BASE}/download/bilingual/healthinfo'
HIT_API = 'https://api.healthinfotranslations.org/v1'
HIT_GCS = 'https://storage.googleapis.com/healthinfotranslations-pdfdocs'
import requests
S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36',
                  'Accept': 'application/json'})
BUDGET = float(os.environ.get('BIL_BUDGET', '110'))
T0 = time.time()
def left(): return BUDGET - (time.time() - T0)

def fetch(url, dest, timeout=40):
    if os.path.exists(dest) and os.path.getsize(dest) > 10240:
        return 'have'
    tmp = dest + '.part'
    try:
        with S.get(url, timeout=timeout, stream=True) as r:
            if r.status_code != 200:
                return f'http{r.status_code}'
            with open(tmp, 'wb') as f:
                for chunk in r.iter_content(65536):
                    f.write(chunk)
        if os.path.getsize(tmp) < 5120:
            os.remove(tmp); return 'tiny'
        os.replace(tmp, dest)
        return 'ok'
    except Exception as e:
        try: os.remove(tmp)
        except Exception: pass
        return f'err:{type(e).__name__}'

mp = f'{OUT}/pairs.jsonl'
rows = {}
order = []
if os.path.exists(mp):
    for line in open(mp):
        j = json.loads(line)
        k = j['id']
        if k not in order: order.append(k)
        rows[k] = j
bad = [rows[k] for k in order if not (rows[k].get('ar_file') or rows[k].get('en_file'))
       and not rows[k].get('note') == 'no_ar_en_versions'
       or (rows[k].get('note') != 'no_ar_en_versions' and not (rows[k].get('ar_file') or rows[k].get('en_file')))]
bad = [rows[k] for k in order if not (rows[k].get('ar_file') or rows[k].get('en_file'))]
print(f'[hit_repair] {len(bad)} docs to retry', flush=True)

fixed = still = 0
for i, row in enumerate(bad):
    if left() < 10:
        print('BUDGET_OUT', flush=True); break
    did = row['id']
    vs = None
    for attempt in range(3):
        try:
            r = S.get(f'{HIT_API}/document/{did}/versions', timeout=25)
            vs = r.json()
            if isinstance(vs, list) and vs:
                break
        except Exception:
            pass
        time.sleep(1.5 + attempt)
    if not (isinstance(vs, list) and vs):
        print(f'  {did}: versions_dead', flush=True)
        still += 1
        continue
    ar_v = next((v for v in vs if v.get('languagename') == 'Arabic'), None)
    en_v = next((v for v in vs if v.get('languagename') == 'English'), None)
    if not (ar_v or en_v):
        row['note'] = 'no_ar_en_versions'
        print(f'  {did}: no_ar_en ({[v["languagename"] for v in vs][:3]}...)', flush=True)
        still += 1
        continue
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', row['title'])[:80]
    ar_file = en_file = None; a1 = a2 = 'skip'
    if ar_v:
        ar_file = f"{OUT}/ar/{safe}__AR.pdf"
        a1 = fetch(f"{HIT_GCS}/{ar_v['pdflink']}", ar_file)
        if a1 not in ('ok', 'have'): ar_file = None
    if en_v:
        en_file = f"{OUT}/en/{safe}__EN.pdf"
        a2 = fetch(f"{HIT_GCS}/{en_v['pdflink']}", en_file)
        if a2 not in ('ok', 'have'): en_file = None
    if ar_file or en_file:
        fixed += 1
    row.update({'ar': ar_v['pdflink'] if ar_v else None, 'en': en_v['pdflink'] if en_v else None,
                'ar_file': ar_file, 'en_file': en_file, 'dl': f'{a1}/{a2}'})
    print(f"  {did} {row['title'][:40]}: {a1}/{a2} ({left():.0f}s)", flush=True)

with open(mp, 'w') as f:
    for k in order:
        f.write(json.dumps(rows[k], ensure_ascii=False) + '\n')
print(f'[hit_repair] fixed={fixed} still={still}', flush=True)
