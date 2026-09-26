#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bilingual_medical.py — crawl bilingual EN<->AR medical resources (owner task E).

Sources (add more here as they are validated):
  immunize_vis  : immunize.org VIS translations page — AR pdfs `vis/arabic_X.pdf`
                  paired with EN `vis/X.pdf` (+ known renames, HEAD-verified).
  immunize_az   : immunize.org a-z facet for imm_language_str:Arabic (HTML
                  handouts; server-rendered WordPress list).
  healthinfo    : healthinfotranslations.org via internal API
                  (api.healthinfotranslations.org/v1) — 293 docs, PDFs on
                  storage.googleapis.com/healthinfotranslations-pdfdocs/.

Layout:
  download/bilingual/<source>/ar/<name>.pdf
  download/bilingual/<source>/en/<name>.pdf
  download/bilingual/<source>/pairs.jsonl   {"id","title","vaccine","ar","en","ar_file","en_file","dl"}

Resumable: skips files already present (size>10KB). Budget via BIL_BUDGET (s).
Never parallel with any Telegram client (safe: no TG usage here).
"""
import os, re, sys, json, time, glob

BASE = '/home/z/my-project'
OUT = f'{BASE}/download/bilingual'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

BUDGET = float(os.environ.get('BIL_BUDGET', '110'))
T0 = time.time()
def left(): return BUDGET - (time.time() - T0)

import requests
S = requests.Session()
S.headers.update({'User-Agent': UA, 'Accept': '*/*',
                  'Accept-Language': 'en-US,en;q=0.9'})

# EN-side rename exceptions discovered while mapping AR vis names
VIS_RENAMES = {
    'meningococcal': ['meningococcal_acwy'],
    'ppsv': ['ppsv23', 'ppsv'],
    'smallpox_monkeypox': ['smallpox_mpox', 'smallpox_monkeypox'],
    'flu_inactive': ['flu_inactive'],
    'flu_live': ['flu_live'],
}

def head_ok(url):
    try:
        r = S.head(url, timeout=12, allow_redirects=True)
        if r.status_code == 200:
            n = int(r.headers.get('content-length', 0) or 0)
            return n > 5000
        if r.status_code == 405:  # some CDNs reject HEAD
            r2 = S.get(url, timeout=15, stream=True)
            r2.close()
            return r2.status_code == 200
    except Exception:
        pass
    return False

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

def manifest_path(src):
    d = f'{OUT}/{src}'
    os.makedirs(d, exist_ok=True)
    return f'{d}/pairs.jsonl'

def load_manifest(src):
    p = manifest_path(src)
    done = set()
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                try:
                    j = json.loads(line)
                    if j.get('ar_file') and j.get('en_file'):
                        done.add(j.get('vaccine') or j.get('title'))
                except Exception:
                    pass
    return done

def append_manifest(src_or_path, obj):
    p = src_or_path if src_or_path.endswith('.jsonl') else manifest_path(src_or_path)
    with open(p, 'a') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n')

# ---------------------------------------------------------------- source: VIS
def immunize_vis():
    print('[immunize_vis] fetching translations page', flush=True)
    r = S.get('https://www.immunize.org/vaccines/vis-translations/?attr-lang=arabic',
              timeout=45)
    html = r.text
    # blocks: vaccine title div followed by arabic pdf href
    pairs = []
    seen = set()
    for m in re.finditer(
            r'vis-translation__vaccine.*?>([^<]{3,120})<.*?href="([^"]*vis/arabic_([a-z0-9_]+)\.pdf)"',
            html, re.S):
        title, ar_url, vacc = m.group(1).strip(), m.group(2), m.group(3)
        if ar_url in seen:
            continue
        seen.add(ar_url)
        pairs.append((title, ar_url, vacc))
    print(f'[immunize_vis] found {len(pairs)} Arabic VIS', flush=True)

    done = load_manifest('immunize_vis')
    d_ar = f'{OUT}/immunize_vis/ar'; d_en = f'{OUT}/immunize_vis/en'
    os.makedirs(d_ar, exist_ok=True); os.makedirs(d_en, exist_ok=True)

    ok = miss = skip = 0
    for title, ar_url, vacc in pairs:
        if left() < 8:
            print('BUDGET_OUT', flush=True); break
        if vacc in done:
            skip += 1; continue
        en_cands = [vacc] + VIS_RENAMES.get(vacc, [])
        en_url = None
        for c in en_cands:
            u = f'https://www.immunize.org/wp-content/uploads/vis/{c}.pdf'
            if head_ok(u):
                en_url = u; break
        if not en_url:
            miss += 1
            append_manifest('immunize_vis', {'title': title, 'vaccine': vacc,
                                             'ar': ar_url, 'en': None})
            print(f'  miss_en {vacc}', flush=True)
            continue
        ar_file = f'{d_ar}/vis_ar_{vacc}.pdf'
        en_file = f'{d_en}/vis_en_{re.sub(chr(47), "_", en_url.rsplit("/", 1)[-1])}'
        a1 = fetch(ar_url, ar_file)
        a2 = fetch(en_url, en_file)
        stat = f'ar={a1} en={a2}'
        if a1 == 'ok' and a2 == 'ok':
            ok += 1
        elif 'have' in (a1, a2):
            skip += 1
        append_manifest('immunize_vis', {'title': title, 'vaccine': vacc,
                                         'ar': ar_url, 'en': en_url,
                                         'ar_file': ar_file, 'en_file': en_file,
                                         'dl': stat})
        print(f'  {vacc}: {stat} ({left():.0f}s left)', flush=True)
    print(f'[immunize_vis] ok={ok} skip={skip} miss={miss}', flush=True)

# ------------------------------------------------------------- source: a-z AR
def immunize_az():
    """All Arabic handouts on immunize.org (a-z facet, server-rendered)."""
    url = 'https://www.immunize.org/clinical/a-z/?wpsolr_fq%5B0%5D=imm_language_str%3AArabic'
    print('[immunize_az] fetching a-z facet', flush=True)
    r = S.get(url, timeout=45)
    html = r.text
    print(f'[immunize_az] page {len(html)}b', flush=True)
    # wp-solr list items: hrefs to /administer/vaccines/... handout pages or pdfs
    links = re.findall(r'href="(https://www\.immunize\.org/wp-content/uploads/[^"]+\.pdf)"', html)
    links = sorted(set(links))
    print(f'[immunize_az] {len(links)} pdfs on page', flush=True)
    d = f'{OUT}/immunize_az/ar'
    d_en = f'{OUT}/immunize_az/en'
    os.makedirs(d, exist_ok=True); os.makedirs(d_en, exist_ok=True)
    mp = manifest_path('immunize_az')
    have_names = set()
    if os.path.exists(mp):
        for line in open(mp):
            try: have_names.add(json.loads(line).get('name'))
            except Exception: pass
    ok = have = 0
    for u in links:
        if left() < 8:
            print('BUDGET_OUT', flush=True); break
        name = u.rsplit('/', 1)[-1].split('?')[0]
        dest = f'{d}/{name}'
        a = fetch(u, dest)  # returns 'have' if already present
        if a not in ('ok', 'have'):
            print(f'  {name}: {a} ({left():.0f}s)', flush=True)
            continue
        if a == 'ok': ok += 1
        else: have += 1
        if name in have_names and a == 'have':
            continue  # manifest already written for this pair
        # EN counterpart rule: p4010-ara.pdf -> p4010.pdf
        stem = name.rsplit('.', 1)[0]
        en_stem = re.sub(r'[-_]ara$|[-_]arabic$', '', stem)
        en_cands = [en_stem] if en_stem == stem else [en_stem, stem]
        en_file = None; en_url = None
        for c in en_cands:
            cu = f'https://www.immunize.org/wp-content/uploads/catg.d/{c}.pdf'
            if head_ok(cu):
                en_url = cu
                en_file = f'{d_en}/{c}.pdf'
                fetch(cu, en_file)
                break
        append_manifest(mp, {'ar': u, 'en': en_url, 'ar_file': dest,
                             'en_file': en_file, 'name': name})
        print(f'  {name}: {a} en={"yes" if en_url else "miss"} ({left():.0f}s)', flush=True)
    print(f'[immunize_az] done ok={ok} have={have}', flush=True)

# --------------------------------------------------- source: healthinfo (HIT)
HIT_API = 'https://api.healthinfotranslations.org/v1'
HIT_GCS = 'https://storage.googleapis.com/healthinfotranslations-pdfdocs'
HIT_AR_LANG_ID = 392138

def hit_list_ar():
    r = S.get(f'{HIT_API}/document?language={HIT_AR_LANG_ID}', timeout=45)
    return r.json()  # [{id,title},...]

def healthinfo():
    """healthinfotranslations.org — 293 docs, AR+EN versions via /versions."""
    d_ar = f'{OUT}/healthinfo/ar'; d_en = f'{OUT}/healthinfo/en'
    os.makedirs(d_ar, exist_ok=True); os.makedirs(d_en, exist_ok=True)
    mp = f'{OUT}/healthinfo/pairs.jsonl'
    done = {}
    if os.path.exists(mp):
        for line in open(mp):
            try:
                j = json.loads(line)
                if j.get('ar_file') and j.get('en_file'): done[j['id']] = 1
            except Exception: pass
    docs = hit_list_ar()
    print(f'[healthinfo] {len(docs)} Arabic docs', flush=True)
    ok = skip = 0
    for i, doc in enumerate(docs):
        if left() < 8:
            print('BUDGET_OUT', flush=True); break
        if doc['id'] in done:
            skip += 1; continue
        try:
            vs = S.get(f"{HIT_API}/document/{doc['id']}/versions", timeout=30).json()
        except Exception as e:
            print(f"  {doc['id']}: ver_err {type(e).__name__}", flush=True); continue
        ar_v = next((v for v in vs if v.get('languagename') == 'Arabic'), None)
        en_v = next((v for v in vs if v.get('languagename') == 'English'), None)
        if not (ar_v or en_v):
            append_manifest(mp, {'id': doc['id'], 'title': doc['title'],
                                 'ar': None, 'en': None,
                                 'note': 'no_ar_en_versions'})
            continue
        ar_file = en_file = None
        safe = re.sub(r'[^A-Za-z0-9._-]+', '_', doc['title'])[:80]
        a1 = a2 = 'skip'
        if ar_v:
            a1 = fetch(f"{HIT_GCS}/{ar_v['pdflink']}", f'{d_ar}/{safe}__AR.pdf')
            ar_file = f'{d_ar}/{safe}__AR.pdf' if a1 in ('ok', 'have') else None
        if en_v:
            a2 = fetch(f"{HIT_GCS}/{en_v['pdflink']}", f'{d_en}/{safe}__EN.pdf')
            en_file = f'{d_en}/{safe}__EN.pdf' if a2 in ('ok', 'have') else None
        append_manifest(mp, {'id': doc['id'], 'title': doc['title'],
                             'ar': ar_v['pdflink'] if ar_v else None,
                             'en': en_v['pdflink'] if en_v else None,
                             'ar_file': ar_file, 'en_file': en_file,
                             'dl': f'{a1}/{a2}'})
        if ar_file and en_file:
            ok += 1
        print(f"  [{i+1}/{len(docs)}] {doc['title'][:45]}: {a1}/{a2} ({left():.0f}s)", flush=True)
    print(f'[healthinfo] ok={ok} skip={skip}', flush=True)

# ------------------------------------------------------------------- dispatch
SOURCES = {
    'immunize_vis': immunize_vis,
    'immunize_az': immunize_az,
    'healthinfo': healthinfo,
}

if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'immunize_vis'
    if which == 'all':
        for k, fn in SOURCES.items():
            if left() < 8:
                print('BUDGET_OUT', flush=True); break
            fn()
    else:
        SOURCES[which]()
    print('DONE', flush=True)
