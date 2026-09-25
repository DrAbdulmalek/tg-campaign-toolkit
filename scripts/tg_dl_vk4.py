#!/usr/bin/env python3
"""tg_dl_vk4.py — browser-assisted VK doc downloader.

The VK session is TLS-fingerprint-bound to the browser (requests jar dies).
But m.vk.ru/doc{id} navigation redirects the BROWSER to the public signed
psv4 URL, which we read from the URL bar and download cookie-less via requests.

Phases per round:
  A) resolve: for each unresolved target -> agent-browser open doc URL,
     read final psv4 URL -> vk_psv4_map.json
  B) download: fetch all resolved-but-missing files (public URLs) -> harvest/
State: link_dl_state.json statuses -> vk_done / vk_fail:<reason>.
Env: VK_BUDGET (default 115s), VK_RESOLVE_CAP (max resolves per round, default 12).
"""
import os, sys, json, re, time, subprocess, traceback
from urllib.parse import unquote
import requests

BASE = '/home/z/my-project'
SEC = f'{BASE}/.secrets'
ST = f'{BASE}/state/linkharvest'
HARV = f'{BASE}/download/link_harvest'
DLSTATE = f'{ST}/link_dl_state.json'
PSV4MAP = f'{ST}/vk_psv4_map.json'
BUDGET = float(os.environ.get('VK_BUDGET', '115'))
CAP = int(os.environ.get('VK_RESOLVE_CAP', '12'))
DEADLINE = time.time() + BUDGET
UA = ('Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36')
UAH = {'User-Agent': UA}


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def sh(*args, timeout=25):
    try:
        r = subprocess.run(['agent-browser', *args], capture_output=True, text=True,
                           timeout=timeout)
        return (r.stdout or '') + (r.stderr or '')
    except subprocess.TimeoutExpired:
        return '__TIMEOUT__'


def sanitize(name, maxlen=100):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', name or '')
    name = re.sub(r'\s+', ' ', name).strip(' .')
    return name[:maxlen] or ''


def decode_vk_name(fn):
    if not re.search(r'\d{2,7}(__\d{2,7})+', fn):
        return fn
    try:
        chars = ''.join(chr(int(p)) for p in fn.split('__') if p.isdigit())
        if chars and sum(c.isprintable() for c in chars) / len(chars) > 0.8:
            return chars
    except Exception:
        pass
    return fn


def resolve_one(owner, did):
    """Navigate browser to doc URL, return final psv4 URL or None."""
    out = sh('open', f'https://m.vk.ru/doc{owner}_{did}', timeout=20)
    # join ALL lines: long URLs wrap; regex-extract from the whole blob
    blob = out.replace('\n', ' ')
    m = re.search(r'https://psv4[^\s"]+|https://[^\s"]*userapi[^\s"]+', blob)
    url = m.group(0).rstrip('\\') if m else ''
    if url and '/s/v1/d/' in url:
        return url
    time.sleep(2)
    out2 = sh('get', 'url', timeout=12)
    blob2 = out2.replace('\n', ' ')
    m2 = re.search(r'https://psv4[^\s"]+|https://[^\s"]*userapi[^\s"]+', blob2)
    url2 = m2.group(0).rstrip('\\') if m2 else ''
    return url2 if url2 and '/s/v1/d/' in url2 else None


def fname_for(url, did):
    m = re.search(r'/([0-9_]+)/([^/?]+)\.(pdf|doc|docx|djvu|epub|zip|rar|ppt|pptx)', url)
    if m:
        return sanitize(decode_vk_name(unquote(m.group(2)))) + '.' + m.group(3)
    return f'vkdoc_{did}.pdf'


def dl_public(url, dest, caption):
    tmp = dest + '.part'
    with requests.get(url, headers=UAH, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        ct = r.headers.get('content-type', '').lower()
        if 'text/html' in ct:
            return 'html'
        with open(tmp, 'wb') as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    os.replace(tmp, dest)
    open(dest + '.caption.txt', 'w', encoding='utf-8').write(caption)
    return 'ok'


def main():
    os.makedirs(HARV, exist_ok=True)
    dl = json.load(open(DLSTATE))
    try:
        pmap = json.load(open(PSV4MAP))
    except Exception:
        pmap = {}

    targets = {}
    for u, v in dl.items():
        stv = v if isinstance(v, str) else v.get('status', '')
        if stv not in ('vk', 'vk_private'):
            continue
        m = re.search(r'doc(-?\d+)_(\d+)', u)
        if m:
            targets[(int(m.group(1)), int(m.group(2)))] = u
    log(f'targets={len(targets)} resolved={sum(1 for k in targets if k in pmap)}')

    # Phase A: resolve
    resolved = failed = 0
    for (o, did) in sorted(targets):
        if resolved >= CAP or time.time() > DEADLINE - 40:
            break
        if (o, did) in pmap:
            continue
        purl = resolve_one(o, did)
        if purl:
            pmap[(o, did)] = purl
            resolved += 1
            log(f'RESOLVED doc{o}_{did}')
        else:
            failed += 1
            log(f'UNRESOLVED doc{o}_{did}')
            pmap[(o, did)] = pmap.get((o, did)) or ''
        json.dump({f'{k[0]}_{k[1]}': v for k, v in pmap.items()}, open(PSV4MAP, 'w'))

    # Phase B: download resolved
    ok = skip = err = 0
    updates = {}
    for (o, did), src in sorted(targets.items()):
        if time.time() > DEADLINE:
            log('BUDGET_OUT')
            break
        purl = pmap.get((o, did))
        if not purl:
            continue
        dest = os.path.join(HARV, fname_for(purl, did))
        if os.path.exists(dest):
            skip += 1
            updates[src] = 'vk_done'
            continue
        try:
            res = dl_public(purl, dest, src)
            if res == 'ok':
                ok += 1
                updates[src] = 'vk_done'
                log(f'OK doc{o}_{did} -> {os.path.basename(dest)} ({os.path.getsize(dest)//1024}KB)')
            else:
                err += 1
                updates[src] = f'vk_{res}'
        except Exception as e:
            code = getattr(e.response, 'status_code', '?') if hasattr(e, 'response') and e.response is not None else type(e).__name__
            err += 1
            log(f'DLERR doc{o}_{did}: {code} url_len={len(purl)}')
            time.sleep(1)

    if updates:
        dl = json.load(open(DLSTATE))
        for u, stv in updates.items():
            if u in dl:
                if isinstance(dl[u], dict):
                    dl[u]['status'] = stv
                else:
                    dl[u] = stv
        json.dump(dl, open(DLSTATE, 'w'), ensure_ascii=False)

    remaining = sum(1 for k in targets if k not in pmap or not pmap[k]) - 0
    log(f'RESULT ok={ok} skip={skip} err={err} resolved_now={resolved} unresolved={failed}')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
