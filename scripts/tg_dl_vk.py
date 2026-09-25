#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_dl_vk.py — VK doc download handler for the link-harvest pipeline.

VK doc links look like: https://vk.com/doc{owner_id}_{doc_id}
(e.g. vk.com/doc243798239_682992721). Public docs redirect to a presigned
download URL (vk.com/doc?... / vk.cn/doc...) or serve the file directly.
Some docs are private -> HTML login page instead (marked 'vk_private').

Usage:
  python tg_dl_vk.py            # process ALL 'vk' links in link_dl_state.json
  python tg_dl_vk.py <url> ...  # process specific URLs (used by res pipeline)

Downloads into download/link_harvest/ and updates link_dl_state.json +
manifest.jsonl exactly like tg_download_links.py (same statuses contract).
Pure HTTP — NO Telegram client (safe to run any time, single-client rule N/A).

Env: DL_BUDGET (default 110s).
"""
import os, re, sys, json, time
import requests

BASE = '/home/z/my-project'
STATE = f'{BASE}/state/linkharvest'
FL = f'{STATE}/file_links.json'
DL = f'{STATE}/link_dl_state.json'
DEST = f'{BASE}/download/link_harvest'
MANIFEST = f'{DEST}/manifest.jsonl'

BUDGET = float(os.environ.get('DL_BUDGET', '110'))
T0 = time.time()
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'}


def budget_left():
    return BUDGET - (time.time() - T0)


def load_json(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return json.loads(json.dumps(d))


def save_json(p, o):
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(o, f, ensure_ascii=False)
    os.replace(tmp, p)


def manifest(rec):
    os.makedirs(DEST, exist_ok=True)
    with open(MANIFEST, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def vk_doc_id(u):
    m = re.search(r'vk\.com/doc(-?\d+)_(\d+)', u)
    return (m.group(1), m.group(2)) if m else (None, None)


def fname_from_headers(r, fallback):
    cd = r.headers.get('content-disposition', '')
    m = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", cd)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1)).strip()
    return fallback


def download_vk(u):
    """Returns (status, size, fname)."""
    owner, doc = vk_doc_id(u)
    if not owner:
        return 'vk_bad_url', 0, ''
    s = requests.Session()
    s.headers.update(UA)
    try:
        r = s.get(u, timeout=40, allow_redirects=True, stream=True)
    except requests.exceptions.SSLError:
        u2 = u.replace('vk.com', 'vk.ru', 1)
        try:
            r = s.get(u2, timeout=40, allow_redirects=True, stream=True)
        except Exception as e:
            return f'error:{type(e).__name__}', 0, ''
    except Exception as e:
        return f'error:{type(e).__name__}', 0, ''
    if r.status_code == 404:
        return '404', 0, ''
    if r.status_code != 200:
        return f'error:http{r.status_code}', 0, ''
    ct = (r.headers.get('content-type') or '').lower()
    # login/privacy wall or doc-deleted page come back as HTML
    if 'text/html' in ct:
        body = r.text[:4000]
        if 'doc_deleted' in body or 'Документ удален' in body:
            return '404', 0, ''
        return 'vk_private', 0, ''
    fname = fname_from_headers(r, f'vk_{owner}_{doc}')
    path = os.path.join(DEST, re.sub(r'[\\/:*?"<>|]', '_', fname)[:150])
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(path):
        path = f'{base}_{i}{ext}'
        i += 1
    n = 0
    try:
        with open(path, 'wb') as f:
            for chunk in r.iter_content(1 << 16):
                if chunk:
                    f.write(chunk)
                    n += len(chunk)
                    if time.time() - T0 > BUDGET - 8:
                        raise TimeoutError('budget out mid-download')
    except TimeoutError:
        os.unlink(path) if os.path.exists(path) else None
        return 'budget_out', n, ''
    except Exception as e:
        return f'error:{type(e).__name__}', n, ''
    return 'done', n, os.path.basename(path)


def main():
    links = load_json(FL, {})
    dl = load_json(DL, {})
    if len(sys.argv) > 1:
        urls = [a for a in sys.argv[1:] if a.startswith('http')]
    else:
        urls = [u for u, v in dl.items() if v.get('status') == 'vk']
    print(f'VK_QUEUE {len(urls)}', flush=True)
    done = 0
    for u in urls:
        if budget_left() < 8:
            print('BUDGET_OUT', flush=True)
            break
        st, size, fname = download_vk(u)
        dl[u]['status'] = st if st in ('done',) or st.startswith('error') or st in (
            '404', 'vk_private', 'vk_bad_url', 'budget_out') else dl[u]['status']
        dl[u]['status'] = st
        dl[u]['size'] = size
        if fname:
            dl[u]['fname'] = fname
        if st == 'done':
            done += 1
        manifest({'url': u, 'status': st, 'size': size, 'fname': fname,
                  'handler': 'vk', 'ts': int(time.time())})
        print(f'VK {st:14s} {size:>10d}  {u[:80]}', flush=True)
        save_json(DL, dl)
    save_json(DL, dl)
    left = sum(1 for v in dl.values() if v.get('status') == 'vk')
    print(json.dumps({'vk_done_this_run': done, 'vk_left': left}), flush=True)


if __name__ == '__main__':
    main()
