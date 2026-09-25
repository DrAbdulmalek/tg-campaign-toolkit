#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_dl_mega.py — MEGA.nz file download handler (pure HTTP + AES, NO mega.py).

Handles links of the form:
  https://mega.nz/file/<handle>!<key>       (new style)
  https://mega.nz/#!<handle>!<key>          (old style)

Algorithm (MEGA v1 file links):
  1. fragment key = 32 bytes -> 8 big-endian u32 words [a0..a7]
     AES file key  = a0^a4, a1^a5, a2^a6, a3^a7 (16 bytes)
  2. API {"a":"g","p":handle} -> {at (encrypted attrs), sz, g (temp URL)}
  3. attrs = AES-CBC(key, IV=0) decrypt -> length-prefixed JSON {"n": filename, ...}
  4. content = AES-CTR(key, counter starts at handle_u64 << 64) XOR stream
     (meta_mac verification skipped — size check + manual use only)

Usage:
  python tg_dl_mega.py            # process ALL 'mega' links in link_dl_state.json
  python tg_dl_mega.py <url> ...  # specific URLs

Downloads into download/link_harvest/, updates link_dl_state.json + manifest.jsonl.
Pure HTTP — NO Telegram client (single-client rule N/A).
Env: DL_BUDGET (default 110s).

NOTE: MEGA sometimes rotates API hosts; fallbacks g.api.mega.co.nz / api.mega.co.nz.
"""
import os, re, sys, json, time, struct, base64
import requests
from Crypto.Cipher import AES
from Crypto.Util import Counter

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
API_HOSTS = ['g.api.mega.co.nz', 'api.mega.co.nz']


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


def b64d(s: str) -> bytes:
    s = s.replace('-', '+').replace('_', '/')
    return base64.b64decode(s + '=' * (-len(s) % 4))


def parse_link(u):
    m = re.search(r'mega\.nz/(?:file/|#!)([A-Za-z0-9_-]{8})[!#]([A-Za-z0-9_-]{43,44})', u)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def api_req(payload):
    last = None
    for host in API_HOSTS:
        try:
            r = requests.post(f'https://{host}/cs?id=0',
                              data=json.dumps([payload]), headers=UA, timeout=30)
            j = r.json()
            if isinstance(j, list):
                return j[0]
            last = j
        except Exception as e:
            last = e
    raise RuntimeError(f'mega api fail: {last!r}')


def file_meta(handle, key_b64):
    resp = api_req({'a': 'g', 'p': handle})
    if isinstance(resp, int):
        raise RuntimeError(f'mega api err {resp} (-4 flood, -9 not found, etc.)')
    kw = struct.unpack('>8I', b64d(key_b64))          # a0..a7 big-endian u32
    aes_key = struct.pack('>4I', kw[0] ^ kw[4], kw[1] ^ kw[5],
                          kw[2] ^ kw[6], kw[3] ^ kw[7])
    name = f'mega_{handle}'
    try:
        raw = AES.new(aes_key, AES.MODE_CBC, b'\0' * 16).decrypt(b64d(resp['at']))
        raw = raw[:struct.unpack('<I', raw[:4])[0]]
        meta = json.loads(raw[4:].decode('utf-8', 'ignore'))
        name = meta.get('n') or name
    except Exception:
        pass
    return resp, aes_key, name


def download_mega(u):
    """Returns (status, size, fname)."""
    handle, key = parse_link(u)
    if not handle:
        return 'mega_bad_url', 0, ''
    try:
        resp, aes_key, name = file_meta(handle, key)
    except RuntimeError as e:
        return ('mega_not_found' if '-9' in str(e) else f'error:{type(e).__name__}'), 0, ''
    except Exception as e:
        return f'error:{type(e).__name__}', 0, ''
    url = resp.get('g')
    sz = int(resp.get('sz', 0))
    if not url:
        return 'mega_no_url', 0, ''
    try:
        r = requests.get(url, headers=UA, stream=True, timeout=60)
    except Exception as e:
        return f'error:{type(e).__name__}', 0, ''
    if r.status_code != 200:
        return f'error:http{r.status_code}', 0, ''
    nonce = struct.unpack('>Q', b64d(handle)[:8])[0]   # handle u64 big-endian
    ctr = Counter.new(128, initial_value=nonce << 64)
    cipher = AES.new(aes_key, AES.MODE_CTR, counter=ctr)
    path = os.path.join(DEST, re.sub(r'[\\/:*?"<>|]', '_', name)[:150])
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
                    f.write(cipher.decrypt(chunk))
                    n += len(chunk)
                    if time.time() - T0 > BUDGET - 8:
                        raise TimeoutError('budget out mid-download')
    except TimeoutError:
        if os.path.exists(path):
            os.unlink(path)
        return 'budget_out', n, ''
    except Exception as e:
        return f'error:{type(e).__name__}', n, ''
    if sz and n != sz:
        return 'mega_size_mismatch', n, os.path.basename(path)
    return 'done', n, os.path.basename(path)


def main():
    links = load_json(FL, {})
    dl = load_json(DL, {})
    if len(sys.argv) > 1:
        urls = [a for a in sys.argv[1:] if a.startswith('http')]
    else:
        urls = [u for u, v in dl.items() if v.get('status') == 'mega']
    print(f'MEGA_QUEUE {len(urls)}', flush=True)
    done = 0
    for u in urls:
        if budget_left() < 8:
            print('BUDGET_OUT', flush=True)
            break
        st, size, fname = download_mega(u)
        dl[u]['status'] = st
        dl[u]['size'] = size
        if fname:
            dl[u]['fname'] = fname
        if st == 'done':
            done += 1
        manifest({'url': u, 'status': st, 'size': size, 'fname': fname,
                  'handler': 'mega', 'ts': int(time.time())})
        print(f'MEGA {st:18s} {size:>10d}  {u[:80]}', flush=True)
        save_json(DL, dl)
    save_json(DL, dl)
    left = sum(1 for v in dl.values() if v.get('status') == 'mega')
    print(json.dumps({'mega_done_this_run': done, 'mega_left': left}), flush=True)


if __name__ == '__main__':
    main()
