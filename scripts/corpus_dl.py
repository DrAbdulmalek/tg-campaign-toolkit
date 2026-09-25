#!/usr/bin/env python3
"""corpus_dl.py — resumable ranged downloader for legal EN<->AR corpora.

Sources (all public/free):
  wikimatrix : OPUS WikiMatrix v1 moses ar-en (133MB)  — Wikipedia-aligned
  unpc       : OPUS UNPC v1.0 moses ar-en (1.9GB)      — UN docs, technical/medical
  opensub    : OPUS OpenSubtitles v2024 ar-en (2.3GB)  — colloquial, largest
Env: CORPUS_BUDGET (default 110s). Files -> download/corpora/. .part resumes via Range.
"""
import os, sys, time, requests

BASE = '/home/z/my-project'
OUT = f'{BASE}/download/corpora'
BUDGET = float(os.environ.get('CORPUS_BUDGET', '110'))
DEADLINE = time.time() + BUDGET
UAH = {'User-Agent': 'research-corpus-fetch/1.0'}

FILES = {
    'wikimatrix': ('https://object.pouta.csc.fi/OPUS-WikiMatrix/v1/moses/ar-en.txt.zip', 133),
    'unpc': ('https://object.pouta.csc.fi/OPUS-UNPC/v1.0/moses/ar-en.txt.zip', 1931),
    'opensub': ('https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/moses/ar-en.txt.zip', 2358),
}


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def dl(name, url, est_mb):
    dest = f'{OUT}/{name}.zip'
    part = dest + '.part'
    have = os.path.getsize(part) if os.path.exists(part) else 0
    # total size via HEAD
    try:
        h = requests.head(url, headers=UAH, timeout=20, allow_redirects=True)
        total = int(h.headers.get('content-length', 0))
    except Exception:
        total = est_mb << 20
    if have >= total > 0:
        os.replace(part, dest)
        return 'done'
    headers = dict(UAH)
    if have:
        headers['Range'] = f'bytes={have}-'
    try:
        with requests.get(url, headers=headers, stream=True, timeout=(20, 60)) as r:
            if r.status_code == 200 and have:  # server ignored Range
                have = 0
            r.raise_for_status()
            mode = 'ab' if have else 'wb'
            with open(part, mode) as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    have += len(chunk)
                    if time.time() > DEADLINE:
                        log(f'{name}: {have//1048576}/{total//1048576}MB (resumable)')
                        return 'partial'
        if have >= total > 0 or total == 0:
            os.replace(part, dest)
            return 'done'
        return 'partial'
    except Exception as e:
        log(f'{name} err {type(e).__name__} at {have//1048576}MB')
        return 'partial'


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, (url, mb) in FILES.items():
        if time.time() > DEADLINE:
            break
        if os.path.exists(f'{OUT}/{name}.zip'):
            log(f'{name}: already complete')
            continue
        log(f'{name}: starting (~{mb}MB)')
        res = dl(name, url, mb)
        log(f'{name}: {res}')
        if res == 'partial':
            break


if __name__ == '__main__':
    main()
