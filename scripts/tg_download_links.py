#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_download_links.py — STAGE3 downloader for the link-harvest pipeline.

Downloads every 'pending' URL from state/linkharvest/link_dl_state.json into
download/link_harvest/, writing one manifest line per attempt.

Handlers:
  drive       -> extract file id (/file/d/<id>/ or ?id=), then
                 uc?export=download (+ confirm-token retry for big files)
  mediafire   -> GET page, scrape direct download*.mediafire.com href
  direct http -> stream to disk
  vk          -> marked 'vk' (dedicated handler tg_dl_vk.py — future work)
  mega        -> marked 'mega' (dedicated handler tg_dl_mega.py — future work)
  drive folders/forms -> 'drive_no_id' (legit fail)
  bookleaks/z-lib etc.  -> 'bookleaks' (host blocked)

Statuses: done | 404 | html | host_blocked | drive_no_id | mediafire_no_link |
          bookleaks | vk | mega | too_large | error:<type>

Termination contract with res_step.sh STAGE3:
  the run report ends with {"downloaded_this_run": N}; when N == 0 the step
  writes download/link_harvest/dl_done.txt.

Env: DL_BUDGET (default 110s), runs under `timeout -s KILL 130`.
"""
import os, re, json, time, asyncio
import requests

BASE = '/home/z/my-project'
STATE = f'{BASE}/state/linkharvest'
FL = f'{STATE}/file_links.json'
DL = f'{STATE}/link_dl_state.json'
DEST = f'{BASE}/download/link_harvest'
DL_DONE = f'{DEST}/dl_done.txt'
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


def safe_name(u, meta):
    fname = (meta.get('fname') or '').strip()
    if not fname:
        tail = u.rstrip('/').split('/')[-1].split('?')[0]
        fname = tail or f'file_{abs(hash(u)) % 10**10}'
    fname = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', fname)[:150]
    return fname


def dl_to(resp, path):
    n = 0
    with open(path, 'wb') as f:
        for chunk in resp.iter_content(1 << 16):
            if chunk:
                f.write(chunk)
                n += len(chunk)
                if time.time() - T0 > BUDGET - 8:
                    raise TimeoutError('budget out mid-download')
    return n


def handle_drive(u, path):
    m = re.search(r'/file/d/([\w-]{10,})', u) or re.search(r'[?&]id=([\w-]{10,})', u)
    if not m:
        return 'drive_no_id', 0, ''
    fid = m.group(1)
    url = f'https://drive.google.com/uc?export=download&id={fid}'
    s = requests.Session()
    s.headers.update(UA)
    r = s.get(url, stream=True, timeout=30)
    cd = r.headers.get('content-disposition', '')
    if 'text/html' in (r.headers.get('content-type') or ''):
        big = re.search(r'name="confirm" value="([^"]+)"', r.text)
        if big:
            r = s.get(f'{url}&confirm={big.group(1)}', stream=True, timeout=30)
        else:
            return 'html', 0, ''
    size = dl_to(r, path)
    name = re.search(r'filename\*?="?([^";]+)', cd)
    return 'done', size, (name.group(1).strip() if name else '')


def handle_mediafire(u, path):
    if '/file/' not in u:
        return 'mediafire_no_link', 0, ''
    r = requests.get(u, headers=UA, timeout=30)
    if r.status_code != 200:
        return ('404' if r.status_code == 404 else f'error:http{r.status_code}'), 0, ''
    hits = re.findall(r'href="(https://download[^"]*mediafire\.com[^"]*)"', r.text)
    if not hits:
        return 'mediafire_no_link', 0, ''
    r2 = requests.get(hits[0], headers=UA, stream=True, timeout=60)
    size = dl_to(r2, path)
    return 'done', size, ''


def handle_direct(u, path):
    r = requests.get(u, headers=UA, stream=True, timeout=60)
    if r.status_code == 404:
        return '404', 0, ''
    if r.status_code >= 400:
        return f'error:http{r.status_code}', 0, ''
    ct = r.headers.get('content-type') or ''
    if 'text/html' in ct and 'octet-stream' not in ct:
        return 'html', 0, ''
    size = dl_to(r, path)
    return 'done', size, ''


def download_one(u, meta):
    st_now = dl.get(u, {})
    status = st_now.get('status', 'pending')
    os.makedirs(DEST, exist_ok=True)
    fname = safe_name(u, meta)
    path = os.path.join(DEST, fname)
    base, ext = os.path.splitext(fname)
    i = 1
    while os.path.exists(path):
        path = os.path.join(DEST, f'{base}_{i}{ext}')
        i += 1
    try:
        if status == 'vk':
            return 'vk', 0, ''
        if status == 'mega':
            return 'mega', 0, ''
        if status == 'bookleaks':
            return 'bookleaks', 0, ''
        if 'drive.google' in u or 'docs.google' in u:
            return handle_drive(u, path)
        if 'mediafire.com' in u:
            return handle_mediafire(u, path)
        if u.lower().startswith('http'):
            return handle_direct(u, path)
        return 'host_blocked', 0, ''
    except TimeoutError:
        return 'budget_out', 0, ''
    except requests.exceptions.ConnectTimeout:
        return 'host_blocked', 0, ''
    except Exception as e:
        return f'error:{type(e).__name__}', 0, ''


dl = {}
links = {}


def download_one_wrapper(u, meta):
    return download_one(u, meta)


async def amain():
    global dl, links
    os.makedirs(DEST, exist_ok=True)
    links = load_json(FL, {})
    dl = load_json(DL, {})
    pending = [u for u, v in dl.items()
               if v.get('status') in ('pending', 'fixed_pending')]
    print(f'PENDING {len(pending)}', flush=True)
    done_this = 0
    for u in pending:
        if budget_left() < 8:
            print('BUDGET_OUT', flush=True)
            break
        st, size, name = download_one_wrapper(u, links.get(u, {}))
        dl[u]['status'] = st
        dl[u]['tries'] = dl[u].get('tries', 0) + 1
        dl[u]['size'] = size
        if name:
            dl[u]['server_fname'] = name
        if st == 'done':
            done_this += 1
        manifest({'url': u, 'status': st, 'size': size, 'ts': int(time.time())})
        if len(pending) >= 20:
            save_json(DL, dl)
        print(f'DL {st:20s} {size:>10d}  {u[:90]}', flush=True)
    save_json(DL, dl)
    report = {'downloaded_this_run': done_this,
              'left_pending': sum(1 for v in dl.values()
                                  if v.get('status') == 'pending')}
    print(json.dumps(report), flush=True)
    if done_this == 0:
        with open(DL_DONE, 'w') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S'))
        print('DL_DONE', flush=True)


if __name__ == '__main__':
    asyncio.run(amain())
