#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tg_deep_links.py — DEEP scan of source channels for file/dictionary links.

New extraction angles beyond tg_scan_file_links.py:
  A) reply_markup BUTTON URLs (never scanned before)
  B) dictionary keyword search in message text (قاموس/قواميس/معجم/dictionary/…)
  C) file-extension + file-host detection in URL OR text
  D) incremental messages after the old scan cursor

State (state/linkharvest/):
  deep_links.json   {url: {src, mid, file, dict, fname, ts, dl, dl_file, dl_size, sent}}
  deep_cursor.json  {src: last_scanned_msg_id}   (ascending, reverse=True)
  deep_report_sent.txt  marker: dictionary-links report uploaded

Phases in one round (budget-gated): SCAN -> DL(direct) -> SEND
Host links (mediafire/mega/vk/drive/yandex/dropbox/bookleaks) are deferred to
their dedicated pipelines; only direct-extension URLs are auto-downloaded.
Env: DEEP_BUDGET (default 110s) — run under external `timeout -s KILL` watchdog.
"""
import os, re, json, time, asyncio
from urllib.parse import unquote
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel

BASE = '/home/z/my-project'
SEC = f'{BASE}/.secrets'
ST = f'{BASE}/state/linkharvest'
OUT = f'{BASE}/download/link_files'
DEEP = f'{ST}/deep_links.json'
CURS = f'{ST}/deep_cursor.json'
REPORT_FLAG = f'{ST}/deep_report_sent.txt'
os.makedirs(OUT, exist_ok=True)

API = json.load(open(f'{SEC}/telegram_api.json'))
SESSION = open(f'{SEC}/tg_string_session.txt').read().strip()
TARGET = 'DrMalekDrive'
BUDGET = float(os.environ.get('DEEP_BUDGET', '110'))
T0 = time.time()

PRIMARY = [
    ('keymiftah_79', 'keymiftah_79'),
    ('tarjamatbybasel', 'tarjamatbybasel'),
    ('xesarth', 'xesarth'),
    ('translationzf', 'TranslationZF'),
    ('translationve', 'translationve'),
    ('pttranslators', 'pttranslators'),
    ('transskylanguagesolutions', 'TransSkylanguagesolutions'),
    ('targma_amely', 'Targma_amely'),
    ('translationpolice', 'translationpolice'),
    ('maqhaalmutarjim_group', 'maqhaalmutarjim_group'),
    ('nahwfortrans', 'NahwForTrans'),
    ('anggalizy1', 'Angalizy1'),
    ('bonjourtranslation', 'BonjourTranslation'),
    ('translatorguide1', 'translatorguide1'),
]

URL_RE = re.compile(
    r'(?:https?://|www\.|t\.me/|mega\.nz/|vk\.com/)[^\s<>"\'\[\]{}\\]+', re.I)

FILE_EXT_RE = re.compile(
    r'\.(zip|rar|7z|tar|gz|tgz|bz2|xz|pdf|epub|mobi|azw3|djvu|docx?|xlsx?'
    r'|pptx?|exe|msi|iso|apk|dmg|pkg|deb|torrent|bgl|ld2|dsl|cab|jar)([?#)\]]|$)',
    re.I)

FILE_HOSTS = ('mediafire.com', 'mega.nz', 'drive.google.com/file', 'uc?id',
              'yadi.sk', 'disk.yandex', 'dropbox.com/s', 'archive.org/download',
              'github.com' , '/releases/', '4shared.com', 'sendspace.com',
              'katfile.com', 'nitroflare.com', 'rapidgator.net', 'uploaded.net',
              'filefactory.com', 'solidfiles.com', 'anonfiles.com', 'gofile.io',
              'pixeldrain.com', 'workupload.com', 'catbox.moe', 'tmpsend.com')

DEFER_HOSTS = ('mediafire.com', 'mega.nz', 'drive.google.com', 'docs.google.com',
               'yadi.sk', 'disk.yandex', 'dropbox.com', 'bookleaks',
               'annas-archive', 'z-lib', 'libgen', 'vk.com')

DICT_KW_RE = re.compile(
    r'قاموس|قواميس|معجم|معاجم|dictionary|dictionaries|glossar|thesaurus'
    r'|stardict|babylon|lingvo|\bdict\b|لونغمان|أوكسفورد|اكسفورد|كولنز'
    r'|longman|merriam|oxford|cambridge|collins|macmillan|مفردات|المعاني', re.I)

UAH = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'}


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def norm_url(u: str) -> str:
    u = u.rstrip('.,;:')
    while u.endswith(')') and u.count('(') < u.count(')'):
        u = u[:-1].rstrip('.,;:')
    if not u.startswith('http'):
        u = 'https://' + u.lstrip('/')
    return u


def btn_urls(rm):
    out = []
    try:
        for row in (getattr(rm, 'rows', None) or []):
            for b in (getattr(row, 'buttons', None) or []):
                u = getattr(b, 'url', None)
                if u:
                    out.append(u)
    except Exception:
        pass
    return out


def guess_name(u: str) -> str:
    try:
        tail = unquote(u.split('?')[0].rstrip('/').split('/')[-1])
        return tail[:100]
    except Exception:
        return ''


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


async def resolve_source(client, key, ref, cache_warm):
    try:
        return await client.get_entity(ref)
    except Exception:
        pass
    if not cache_warm.get('warm'):
        try:
            async for _ in client.get_dialogs(limit=None):
                pass
            cache_warm['warm'] = True
        except Exception:
            pass
        try:
            return await client.get_entity(ref)
        except Exception:
            pass
    if isinstance(ref, int) or (isinstance(ref, str) and ref.lstrip('-').isdigit()):
        a = int(ref)
        if a < 0:
            a = a - 10**12 if a > 10**12 else a
        try:
            return await client.get_entity(PeerChannel(a))
        except Exception:
            pass
    return await client.get_entity(key)


def classify(u, text):
    is_file = bool(FILE_EXT_RE.search(u)) or any(h in u.lower() for h in FILE_HOSTS)
    is_dict = bool(DICT_KW_RE.search(u) or DICT_KW_RE.search(text))
    return is_file, is_dict


async def scan_phase(client, links, curs, warm, known):
    scanned = 0
    for key, username in PRIMARY:
        if budget_left() < BUDGET * 0.42:
            break
        try:
            ent = await resolve_source(client, key, username, warm)
        except Exception as e:
            log(f'SCAN skip {key}: {type(e).__name__}')
            continue
        cur = curs.get(key, 0)
        newf = 0
        last_id = 0
        try:
            async for msg in client.iter_messages(ent, limit=None,
                                                  reverse=True, min_id=cur):
                if budget_left() < BUDGET * 0.40:
                    break
                text = msg.text or ''
                urls = set()
                for m in URL_RE.finditer(text):
                    urls.add(norm_url(m.group(0)))
                for bu in btn_urls(msg.reply_markup):
                    urls.add(norm_url(bu.strip()))
                for u in urls:
                    is_file, is_dict = classify(u, text)
                    if not is_file and not is_dict:
                        continue
                    if u in known:
                        continue  # old pipeline owns it (file_links.json)
                    if u in links:
                        # known: upgrade flags only
                        if is_file and not links[u].get('file'):
                            links[u]['file'] = True
                        if is_dict and not links[u].get('dict'):
                            links[u]['dict'] = True
                        continue
                    links[u] = {'src': key, 'mid': msg.id, 'file': is_file,
                                'dict': is_dict, 'fname': guess_name(u),
                                'ts': int(time.time()), 'dl': '', 'sent': False}
                    newf += 1
                msg_id = msg.id
                last_id = msg_id
                scanned += 1
                if msg_id % 250 == 0 and msg_id != curs.get(key):
                    curs[key] = msg_id
                    save_json(CURS, curs)
                    save_json(DEEP, links)
                if scanned and scanned % 400 == 0:
                    log(f'SCAN …{key}@{msg_id} +{newf} new')
        except Exception as e:
            log(f'SCAN err {key}: {type(e).__name__} {str(e)[:80]}')
        if last_id:
            curs[key] = max(curs.get(key, 0), last_id)
        if newf:
            log(f'SCAN {key}: +{newf} links (cursor {curs[key]})')
    save_json(CURS, curs)
    save_json(DEEP, links)
    return scanned


def try_download(u, path):
    try:
        h = requests.head(u, timeout=15, headers=UAH, allow_redirects=True)
        ct = (h.headers.get('content-type') or '').lower()
        cl = int(h.headers.get('content-length') or 0)
        if 'text/html' in ct and 'download' not in u:
            return 'html', 0
        if cl > 600 * 1024 * 1024:
            return 'too_big', 0
        r = requests.get(u, timeout=(15, 90), headers=UAH, stream=True)
        r.raise_for_status()
        it = r.iter_content(65536)
        first = next(it, b'')
        low = first[:512].lower()
        if b'<html' in low or b'<!doctype html' in low:
            return 'html', 0
        n = len(first)
        with open(path, 'wb') as f:
            f.write(first)
            for chunk in it:
                f.write(chunk)
                n += len(chunk)
                if n > 600 * 1024 * 1024:
                    return 'too_big_during', n
        return 'done', n
    except Exception as e:
        return f'fail:{type(e).__name__}', 0


def uniq_path(out_dir, name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', name or 'file').strip(' ._') or 'file'
    p = os.path.join(out_dir, name)
    if not os.path.exists(p):
        return p
    base, ext = os.path.splitext(name)
    i = 2
    while os.path.exists(os.path.join(out_dir, f'{base}_{i}{ext}')):
        i += 1
    return os.path.join(out_dir, f'{base}_{i}{ext}')


def dl_phase(links):
    got = 0
    for u, m in links.items():
        if budget_left() < BUDGET * 0.18:
            break
        if not m.get('file') or m.get('dl') in ('done', 'sent') \
                or str(m.get('dl', '')).startswith('fail') or m.get('dl') == 'html':
            continue
        low = u.lower()
        if any(h in low for h in DEFER_HOSTS):
            m['dl'] = 'deferred'
            continue
        if not FILE_EXT_RE.search(u):
            m['dl'] = 'no_ext'
            continue
        path = uniq_path(OUT, m.get('fname') or guess_name(u) or 'file.bin')
        st, n = try_download(u, path)
        m['dl'] = st if st == 'done' else st
        if st == 'done':
            m['dl_file'] = path
            m['dl_size'] = n
            got += 1
            log(f'DL ok {os.path.basename(path)} ({n//1024}KB)')
        else:
            log(f'DL {st} {u[:70]}')
        save_json(DEEP, links)
    save_json(DEEP, links)
    return got


def build_dict_report(links):
    fl = load_json(f'{ST}/file_links.json', {})
    old_dict = [u for u in fl if DICT_KW_RE.search(u)]
    new_dict = [u for u, m in links.items() if m.get('dict')]
    merged = sorted(set(old_dict) | set(new_dict))
    files = [u for u, m in links.items()
             if m.get('file') and not any(h in u.lower() for h in DEFER_HOSTS)]
    lines = ['# 📚 قائمة القواميس والروابط المكتشفة من القنوات', '',
             f'**قواميس أونلاين: {len(merged)} رابط**', '']
    for u in merged[:400]:
        src = (links.get(u) or fl.get(u) or {}).get('src', '')
        lines.append(f'- {u}  (المصدر: @{src})')
    lines += ['', f'**روابط ملفات مباشرة: {len(files)}**', '']
    for u in files[:200]:
        lines.append(f'- {u}')
    rep = f'{OUT}/قائمة_القواميس_اونلاين.md'
    with open(rep, 'w') as f:
        f.write('\n'.join(lines))
    return rep, len(merged)


async def send_phase(client, links, target, warm):
    sent = 0
    for u, m in links.items():
        if budget_left() < 12:
            break
        if m.get('sent') or m.get('dl') != 'done' or not m.get('dl_file'):
            continue
        p = m['dl_file']
        if not os.path.exists(p):
            m['sent'] = True
            continue
        tags = '#ملف #DrMalekDrive'
        if m.get('dict'):
            tags = '#قاموس #ملف #DrMalekDrive'
        cap = f"📦 {os.path.basename(p)}\n🗂️ المصدر: @{m.get('src','')}\n{tags}"
        try:
            await client.send_file(target, p, force_document=True, caption=cap)
            m['sent'] = True
            sent += 1
            log(f'SENT {os.path.basename(p)}')
        except Exception as e:
            log(f'SEND err: {type(e).__name__} {str(e)[:60]}')
            if 'FloodWait' in type(e).__name__:
                break
        if sent % 3 == 0:
            save_json(DEEP, links)
    # dictionary report (once)
    if budget_left() > 20 and not os.path.exists(REPORT_FLAG):
        n_dict = sum(1 for m in links.values() if m.get('dict'))
        if n_dict or True:
            rep, merged = build_dict_report(links)
            try:
                await client.send_file(
                    target, rep, force_document=True,
                    caption=f'📚 قائمة القواميس والروابط المكتشفة ({merged} قاموس)\n'
                            f'#قاموس #قائمة #DrMalekDrive')
                open(REPORT_FLAG, 'w').write(time.strftime('%F %T'))
                log(f'REPORT sent ({merged} dict links)')
            except Exception as e:
                log(f'REPORT err: {type(e).__name__}')
    save_json(DEEP, links)
    return sent


async def main():
    links = load_json(DEEP, {})
    curs = load_json(CURS, {})
    fl = load_json(f'{ST}/file_links.json', {})
    known = set(fl.keys()) | set(
        f'tgdoc:{k}' for k in fl if k.startswith('tgdoc:'))
    warm = {'warm': False}
    async with TelegramClient(StringSession(SESSION), API['api_id'],
                              API['api_hash'],
                              flood_sleep_threshold=25) as client:
        target = TARGET
        try:
            await resolve_source(client, TARGET, TARGET, warm)
        except Exception as e:
            log(f'TARGET resolve fail: {e}')
        n_scanned = await scan_phase(client, links, curs, warm, known)
        n_dl = dl_phase(links)
        n_sent = await send_phase(client, links, target, warm)
    log(f'RESULT scanned={n_scanned} new_dl={n_dl} sent={n_sent} '
        f'total_links={len(links)}')


if __name__ == '__main__':
    asyncio.run(main())
