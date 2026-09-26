#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
medlineplus_en2.py — per-source EN-counterpart rules for the 209 manifest misses.

Rules (HEAD-verified before adopting):
  GCS bucket        : strip _AR/_Ara/_ARA/_Arabic/_Arabic_fin/... suffixes
  immunize.org      : vis/arabic_X.pdf -> vis/X.pdf (+ known renames)
                      also reuses local bilingual/immunize_vis/en files
  cancer.org        : /cancer-control/ar/ -> /cancer-control/en/ (and 'english')
  cdc.gov           : strip _AR_508/_AR/_Arabic(_N) markers
  healthvermont     : strip Arabic_ prefix
  mchoralhealth     : strip _Arabic suffix
  cms.gov           : strip 'arabic' infix
  health.state.mn   : strip arb/arab suffix

Updates download/medlineplus/pairs.jsonl in place (en_file filled).
Env: MLP_BUDGET (s). Resumable: only processes rows with ar_file but no en_file.
"""
import os, re, json, time
from urllib.parse import urlparse, unquote
import requests

BASE = '/home/z/my-project'
MP = f'{BASE}/download/medlineplus'
PAIRS = f'{MP}/pairs.jsonl'
UAH = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'}
S = requests.Session(); S.headers.update(UAH)
BUDGET = float(os.environ.get('MLP_BUDGET', '100'))
T0 = time.time()
def left(): return BUDGET - (time.time() - T0)

GCS = 'https://storage.googleapis.com/healthinfotranslations-pdfdocs'

def head200(u):
    try:
        r = S.head(u, timeout=12, allow_redirects=True)
        if r.status_code == 200:
            return int(r.headers.get('content-length', 0) or 0) > 5000 or \
                   'pdf' in (r.headers.get('content-type', '') or '').lower()
    except Exception:
        pass
    return False

def gcs_candidates(name):
    stem, ext = name.rsplit('.', 1)[0], name.rsplit('.', 1)[1]
    cands = []
    # strong generic strip: ...[_-](arabic|ara|ar)[_-](final|fin[0-9]?)?  (case-ins)
    m = re.search(r'[_-](arabic|ara|ar)([_-](final|fin[0-9]?))?(?=$)', stem, re.I)
    if m:
        base = stem[:m.start()]
        cands.append(base + '.' + ext)
        # also try keeping a trailing _fin marker if present after the lang part
        fin = re.search(r'(_fin(?:al|[0-9])?)$', stem, re.I)
        if fin:
            cands.append(base + fin.group(1) + '.' + ext)
    for suf in ('_fin', '_fin2', '_508', '_final'):
        base = re.sub(re.escape(suf) + r'$', '', stem, flags=re.I)
        if base != stem:
            cands.append(base + '.' + ext)
    return list(dict.fromkeys(cands))

def immunize_en(url):
    m = re.search(r'vis/arabic_([a-z0-9_]+)\.pdf$', url)
    if not m:
        return []
    v = m.group(1)
    ren = {'meningococcal': ['meningococcal_acwy'], 'ppsv': ['ppsv23', 'ppsv'],
           'smallpox_monkeypox': ['smallpox_mpox', 'smallpox_monkeypox']}
    return [f'https://www.immunize.org/wp-content/uploads/vis/{c}.pdf'
            for c in [v] + ren.get(v, [])]

def cancer_en(url):
    out = []
    for repl in ('/cancer-control/en/', '/cancer-control/english/'):
        u2 = url.replace('/cancer-control/ar/', repl)
        if u2 != url:
            out.append(u2)
    return out

def cdc_en(url):
    stem = url.rsplit('/', 1)[-1]
    base = stem
    for pat in (r'_AR_508(?=\.pdf$)', r'_Arabic_\d+(?=\.pdf$)', r'_Arabic(?=\.pdf$)',
                r'_AR(?=\.pdf$)'):
        base = re.sub(pat, '', base)
    if base == stem:
        return []
    return [url.rsplit('/', 1)[0] + '/' + base]

def vermont_en(url):
    b = url.rsplit('/', 1)[-1]
    base = re.sub(r'^Arabic_', '', b)
    return [url.rsplit('/', 1)[0] + '/' + base] if base != b else []

def mchoral_en(url):
    return [re.sub(r'_Arabic(?=\.pdf$)', '', url)]

def cms_en(url):
    return [re.sub(r'arabic(?=\.pdf$)', '', url, flags=re.I)]

def mn_en(url):
    b = url.rsplit('/', 1)[-1]
    base = re.sub(r'(arb|arab)(?=\.pdf$)', '', b)
    return [url.rsplit('/', 1)[0] + '/' + base] if base != b else []

def candidates_for(url):
    h = urlparse(url).netloc
    path = unquote(urlparse(url).path)
    name = path.rsplit('/', 1)[-1]
    out = []
    if h == 'storage.googleapis.com':
        for c in gcs_candidates(name):
            out.append(f'{GCS}/{c}')
        # sometimes the AR file IS the pair (AR+EN in one doc) — skip
    elif h == 'www.immunize.org':
        out += immunize_en(url)
    elif h == 'www.cancer.org':
        out += cancer_en(url)
    elif h == 'www.cdc.gov':
        out += cdc_en(url)
    elif h == 'www.healthvermont.gov':
        out += vermont_en(url)
    elif h == 'www.mchoralhealth.org':
        out += mchoral_en(url)
    elif h == 'www.cms.gov':
        out += cms_en(url)
    elif h == 'www.health.state.mn.us':
        out += mn_en(url)
    return [u for u in out if u != url]

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

def main():
    rows = []
    for line in open(PAIRS, encoding='utf-8', errors='ignore'):
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    d_en = f'{MP}/en'; os.makedirs(d_en, exist_ok=True)
    fixed = fail = 0
    for j in rows:
        if left() < 8:
            print('BUDGET_OUT', flush=True); break
        if j.get('en_file') or not j.get('ar'):
            continue
        cands = candidates_for(j['ar'])
        got = None
        for cu in cands[:4]:
            if head200(cu):
                got = cu; break
        if not got:
            fail += 1
            continue
        name = unquote(urlparse(got).path).rsplit('/', 1)[-1]
        dest = f'{d_en}/{name}'
        a = fetch(got, dest)
        if a in ('ok', 'have'):
            j['en'] = got
            j['en_file'] = dest
            fixed += 1
            print(f"  OK {name[:60]} ({left():.0f}s)", flush=True)
        else:
            fail += 1
            print(f"  dl_fail {name[:50]}: {a}", flush=True)
    with open(PAIRS, 'w', encoding='utf-8') as f:
        for j in rows:
            f.write(json.dumps(j, ensure_ascii=False) + '\n')
    print(f'RESULT fixed={fixed} fail={fail}', flush=True)

if __name__ == '__main__':
    main()
