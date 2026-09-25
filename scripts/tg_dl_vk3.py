#!/usr/bin/env python3
"""tg_dl_vk3.py — VK cookie-session doc downloader (post browser-login).

Auth: cookies exported from agent-browser session (.secrets/vk_browser_state.json).
Targets: all links in link_dl_state.json with status 'vk'/'vk_private' (65).
Filename: Content-Disposition header > doc{id}.pdf fallback.
Bonus: fetch owner 243798239 docs catalog via m.vk.ru/docs HTML (paginated).
Resumable: skips existing files. Env VK_BUDGET (default 110s).
"""
import os, sys, json, re, time, traceback
from urllib.parse import unquote
import requests

BASE = '/home/z/my-project'
SEC = f'{BASE}/.secrets'
ST = f'{BASE}/state/linkharvest'
HARV = f'{BASE}/download/link_harvest'
DLSTATE = f'{ST}/link_dl_state.json'
STATEF = f'{SEC}/vk_browser_state.json'
BUDGET = float(os.environ.get('VK_BUDGET', '110'))
DEADLINE = time.time() + BUDGET
UA = ('Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36')


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def build_session():
    st = json.load(open(STATEF))
    S = requests.Session()
    for c in st.get('cookies', []):
        # EXACT domain preservation (leading dot = suffix match; stripping breaks it)
        S.cookies.set(c['name'], c['value'], domain=c.get('domain', ''), path=c.get('path', '/'))
    S.headers.update({'User-Agent': UA, 'Accept-Language': 'en,ar;q=0.9'})
    return S


def session_live(S):
    # doc URL is the real test: 302->psv4 = live, 302->login = dead
    try:
        r = S.get('https://vk.ru/doc243798239_430188596', timeout=20, allow_redirects=False)
        loc = r.headers.get('location', '')
        return r.status_code in (200, 302) and 'login' not in loc
    except Exception:
        return False


def sanitize(name, maxlen=100):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', name or '')
    name = re.sub(r'\s+', ' ', name).strip(' .')
    return name[:maxlen] or ''


def decode_vk_name(fn):
    """Decode VK percent-name pattern: '1602__1589__1577' -> Arabic text.
    Groups of __-separated decimals are UTF-8 codepoints."""
    if not re.search(r'\d{2,7}(__\d{2,7})+', fn):
        return fn
    try:
        parts = fn.split('__')
        chars = ''.join(chr(int(p)) for p in parts if p.isdigit())
        if chars and sum(c.isprintable() for c in chars) / len(chars) > 0.8:
            return chars
    except Exception:
        pass
    return fn


def fname_from_headers(r, url, did):
    cd = r.headers.get('content-disposition', '')
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if m:
        fn = sanitize(unquote(m.group(1)))
        if fn:
            return fn
    m = re.search(r'/([^/?]+)\.(pdf|doc|docx|djvu|epub|zip|rar)', url)
    if m:
        return sanitize(decode_vk_name(unquote(m.group(1)))) + '.' + m.group(2)
    return f'vkdoc_{did}.pdf'


def dl_doc(S, owner, did, caption):
    url = f'https://vk.ru/doc{owner}_{did}'
    with S.get(url, timeout=(15, 120), allow_redirects=True, stream=True) as r:
        r.raise_for_status()
        ct = r.headers.get('content-type', '').lower()
        if 'text/html' in ct:
            return 'html', None
        fn = fname_from_headers(r, r.url, did)
        dest = os.path.join(HARV, fn)
        tmp = dest + '.part'
        n = 0
        with open(tmp, 'wb') as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                n += len(chunk)
        os.replace(tmp, dest)
        open(dest + '.caption.txt', 'w', encoding='utf-8').write(caption)
        return 'ok', dest


def try_catalog(S, limit_pages=12):
    """Scrape m.vk.ru/docs?oid=243798239 — owner 243798239 docs list."""
    cat, offset = [], 0
    while offset < limit_pages * 20 and time.time() < DEADLINE - 20:
        url = f'https://m.vk.ru/docs?oid=243798239&offset={offset}'
        try:
            r = S.get(url, timeout=20)
        except Exception as e:
            log('catalog err', e)
            break
        html = r.text
        items = re.findall(r'href="(/doc\d+_\d+[^"]*)"[^>]*>([^<]{2,200})<', html)
        if not items:
            log(f'catalog offset={offset}: 0 items (len={len(html)})')
            break
        new = 0
        for href, title in items:
            m = re.search(r'/doc(-?\d+)_(\d+)', href)
            if m:
                cat.append((int(m.group(1)), int(m.group(2)), sanitize(title)))
                new += 1
        log(f'catalog offset={offset}: +{new}')
        if new == 0:
            break
        offset += 20
    return cat


def main():
    os.makedirs(HARV, exist_ok=True)
    S = build_session()
    live = session_live(S)
    log(f'session live={live}')
    if not live:
        log('FATAL session dead — re-login needed')
        sys.exit(4)

    dl = json.load(open(DLSTATE))
    targets = {}
    for u, v in dl.items():
        stv = v if isinstance(v, str) else v.get('status', '')
        if stv not in ('vk', 'vk_private'):
            continue
        m = re.search(r'doc(-?\d+)_(\d+)', u)
        if m:
            targets[(int(m.group(1)), int(m.group(2)))] = u
    log(f'targets: {len(targets)}')

    ok = skip = err = 0
    updates = {}
    for (o, did), src in sorted(targets.items()):
        if time.time() > DEADLINE:
            log('BUDGET_OUT')
            break
        try:
            res, dest = dl_doc(S, o, did, src)
            if res == 'ok':
                ok += 1
                updates[src] = 'vk_done'
                log(f'OK doc{o}_{did} -> {os.path.basename(dest)} ({os.path.getsize(dest)//1024}KB)')
            else:
                err += 1
                updates[src] = f'vk_{res}'
                log(f'{res.upper()} doc{o}_{did}')
        except Exception as e:
            err += 1
            log(f'ERR doc{o}_{did}: {type(e).__name__} {e}')
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

    log(f'RESULT ok={ok} skip=0 err={err} remaining={len(targets)-ok-err}')
    # catalog only when targets finished within budget
    if ok + err >= len(targets) and time.time() < DEADLINE - 25:
        cat = try_catalog(S)
        json.dump(cat, open(f'{ST}/vk_owner_catalog.json', 'w'), ensure_ascii=False)
        log(f'catalog saved: {len(cat)} docs')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.exit(1)
