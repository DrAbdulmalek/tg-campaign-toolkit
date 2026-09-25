#!/usr/bin/env python3
"""tg_dl_vk2.py — VK authenticated doc downloader (user-provided credentials).

Flow:
  1. Auth: VK direct-auth (Android client_id 2274003) trying password variants.
     Token cached in .secrets/vk_token.json (~20 days).
  2. docs.get(owner_id=...) → list ALL docs visible to the account per owner.
  3. Download target docs (from link_dl_state 'vk'/'vk_private') + all extra docs
     of owner 243798239 (dictionaries channel) into download/link_harvest/.
  4. Update link_dl_state.json → 'vk_done'. Sidecar .caption.txt = source URL.

Env: VK_BUDGET (default 110s, sandbox watchdog friendly). Resumable: skips files
already on disk (by final name). One round per invocation; re-run to continue.
"""
import os, sys, json, re, time, traceback
import requests

BASE = '/home/z/my-project'
SEC = f'{BASE}/.secrets'
ST = f'{BASE}/state/linkharvest'
HARV = f'{BASE}/download/link_harvest'
BUDGET = int(os.environ.get('VK_BUDGET', '110'))
DEADLINE = time.time() + BUDGET
TOKF = f'{SEC}/vk_token.json'
DLSTATE = f'{ST}/link_dl_state.json'

EMAIL = 'abdulmalek.husseini@gmail.com'
PWS = ['Tohu19561956', '19561956', 'tohu19561956']
UA = ('Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36')

S = requests.Session()
S.headers.update({'User-Agent': UA, 'Accept-Language': 'en,ar;q=0.9'})


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def left():
    return DEADLINE - time.time()


# ---------------------------------------------------------------- auth
def load_cached_token():
    try:
        d = json.load(open(TOKF))
        if time.time() - d.get('ts', 0) < 86400 * 20 and d.get('token'):
            log('AUTH cached token ok')
            return d['token']
    except Exception:
        pass
    return None


def direct_auth():
    for i, pw in enumerate(PWS, 1):
        if left() < 30:
            return None
        try:
            r = S.post('https://oauth.vk.com/token', data={
                'grant_type': 'password', 'client_id': '2274003',
                'client_secret': 'hHbZxrka2uZ6jB1inYsW',
                'username': EMAIL, 'password': pw,
                'v': '5.131', '2fa_supported': '1',
            }, timeout=25)
            j = r.json()
            if 'access_token' in j:
                json.dump({'token': j['access_token'], 'ts': time.time(),
                           'user_id': j.get('user_id')}, open(TOKF, 'w'))
                log(f'AUTH pw#{i}: OK user={j.get("user_id")}')
                return j['access_token']
            err = j.get('error')
            desc = j.get('error_description', '') or j.get('error_msg', '')
            log(f'AUTH pw#{i}: {err} {desc}')
            if str(err) == 'need_captcha':
                # captcha blocks further password tries; stop to avoid lockout
                log('CAPTCHA required — stopping password attempts')
                return None
        except Exception as e:
            log(f'AUTH pw#{i}: exc {e}')
        time.sleep(2)
    return None


def api(token, method, **params):
    p = {'access_token': token, 'v': '5.131'}
    p.update(params)
    r = S.post(f'https://api.vk.com/method/{method}', data=p, timeout=25)
    return r.json()


# ---------------------------------------------------------------- helpers
def sanitize(name, maxlen=100):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', name or 'doc')
    name = re.sub(r'\s+', ' ', name).strip(' .')
    return name[:maxlen] or 'doc'


def fpath(owner, did, title, ext):
    return os.path.join(HARV, f'{title}.{ext}' if title else f'doc{owner}_{did}.{ext}')


def dl_file(url, dest, caption):
    tmp = dest + '.part'
    with S.get(url, stream=True, timeout=(15, 90)) as r:
        r.raise_for_status()
        ct = r.headers.get('content-type', '')
        if 'text/html' in ct.lower():
            return 'html'
        n = 0
        with open(tmp, 'wb') as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                n += len(chunk)
                if n > 3 << 30:
                    return 'too_big'
    os.replace(tmp, dest)
    open(dest + '.caption.txt', 'w').write(caption)
    return 'ok'


def main():
    os.makedirs(HARV, exist_ok=True)
    token = load_cached_token() or direct_auth()
    if not token:
        log('FATAL no token')
        sys.exit(3)

    dl = json.load(open(DLSTATE))
    # collect vk targets from link state
    targets = {}
    for u, v in dl.items():
        st = v if isinstance(v, str) else v.get('status', '')
        if st not in ('vk', 'vk_private'):
            continue
        m = re.search(r'doc(-?\d+)_(\d+)', u)
        if m:
            targets[(int(m.group(1)), int(m.group(2)))] = u
    log(f'targets: {len(targets)} vk links')

    # docs.get per owner → full catalog visible to the logged account
    owners = sorted({o for (o, _) in targets})
    cat = {}   # (owner, id) -> {title, ext, size, url}
    for o in owners:
        if left() < 20:
            break
        j = api(token, 'docs.get', owner_id=o, count=2000, offset=0)
        resp = j.get('response') or {}
        items = resp.get('items') or []
        log(f'docs.get owner={o}: count={resp.get("count")} items={len(items)} err={j.get("error")}')
        for it in items:
            cat[(o, it.get('id'))] = it

    # extra docs of the dictionaries channel owner (not in targets) → queue too
    extra = [k for k in cat if k not in targets and k[0] == 243798239]
    log(f'extra docs from owner 243798239: {len(extra)}')

    queue = [(k, targets.get(k, ''), 'target') for k in sorted(targets)]
    queue += [(k, '', 'extra') for k in sorted(extra)]

    ok = miss = skip = err = 0
    stat_updates = {}
    for (o, did), src, kind in queue:
        if left() < 15:
            log('BUDGET_OUT')
            break
        info = cat.get((o, did))
        if not info:
            miss += 1
            if kind == 'target':
                log(f'MISS doc{o}_{did} (not in owner catalog)')
            continue
        title = sanitize(info.get('title') or f'doc{o}_{did}')
        ext = info.get('ext') or 'bin'
        dest = fpath(o, did, title, ext)
        if os.path.exists(dest):
            skip += 1
            if kind == 'target' and src:
                stat_updates[src] = 'vk_done'
            continue
        url = info.get('url')
        if not url:
            miss += 1
            continue
        try:
            cap = src or f'https://vk.com/doc{o}_{did}'
            res = dl_file(url, dest, cap)
            if res == 'ok':
                ok += 1
                mb = info.get('size', 0) / 1e6
                log(f'OK [{kind}] {title}.{ext} {mb:.1f}MB')
                if src:
                    stat_updates[src] = 'vk_done'
            else:
                err += 1
                log(f'{res.upper()} doc{o}_{did}')
        except Exception as e:
            err += 1
            log(f'ERR doc{o}_{did}: {type(e).__name__} {e}')
            time.sleep(1)

    # persist state updates
    try:
        dl = json.load(open(DLSTATE))
        for u, stv in stat_updates.items():
            if u in dl:
                if isinstance(dl[u], dict):
                    dl[u]['status'] = stv
                else:
                    dl[u] = stv
        json.dump(dl, open(DLSTATE, 'w'), ensure_ascii=False)
    except Exception as e:
        log('state save err', e)

    log(f'RESULT ok={ok} skip={skip} miss={miss} err={err} catalog={len(cat)}')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
