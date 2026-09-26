#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_scan_file_links.py — scan SOURCE channels for file-download links (Google
Drive / MediaFire / MEGA / VK / direct), rebuild file_links.json + link_dl_state.json.

Rebuilt after env reset #3. Key fixes carried from the lost original:

1) norm_url() — URL truncation fix. The old regex excluded ')' which truncated
   mediafire links whose filename contains parentheses
   (e.g. mediafire.com/file/.../File_(2nd_Ed).pdf/file). New policy:
   rstrip('.,;:') and strip a trailing ')' ONLY IF parens are unbalanced.

2) resolve_source() — triple-fallback entity resolution (new sessions have an
   empty dialog cache, so bare get_entity(username) can fail):
     a) get_entity(ref) try
        + optional cache warm: get_dialogs(limit=None) once (cache_warm flag)
     b) numeric id: positive -> PeerChannel(id);
        negative -> strip -100 prefix (a-10**12 if a>10**12 else a)
     c) username fallback get_entity(key)

State files (state/linkharvest/):
  file_links.json     {url: {"src": key, "mid": msg_id, "fname": guess, "ts": ...}}
  link_dl_state.json  {url: {"status": pending|done|404|html|host_blocked|
                             drive_no_id|mediafire_no_link|bookleaks|vk|mega, ...}}
  scan_state.txt      last-scanned msg id per source (resume cursor)
  rescan_done.txt     marker: link scan finished

Env: SCAN_BUDGET (default 110s), runs under external `timeout -s KILL` watchdog.
"""
import os, re, json, time, asyncio
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.types import PeerChannel

BASE = '/home/z/my-project'
SEC = f'{BASE}/.secrets'
STATE = f'{BASE}/state/linkharvest'
FL = f'{STATE}/file_links.json'
DL = f'{STATE}/link_dl_state.json'
SCAN = f'{STATE}/scan_state.json'
DONE = f'{STATE}/rescan_done.txt'

API = json.load(open(f'{SEC}/telegram_api.json'))
SESSION = open(f'{SEC}/tg_string_session.txt').read().strip()

TARGET = 'DrMalekDrive'
TARGET_RAW_ID = 3913901632
BUDGET = float(os.environ.get('SCAN_BUDGET', '110'))
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


def norm_url(u: str) -> str:
    """Trim punctuation tails WITHOUT breaking URLs whose filename has parens."""
    u = u.rstrip('.,;:')
    # strip a trailing ')' only when parens are unbalanced (link tail, not filename)
    while u.endswith(')') and u.count('(') < u.count(')'):
        u = u[:-1].rstrip('.,;:')
    return u


def classify(url: str) -> str:
    l = url.lower()
    if 'drive.google.com' in l or 'docs.google.com' in l:
        return 'drive_no_id' if ('/folders/' in l or '/forms/' in l) else 'pending'
    if 'mediafire.com' in l:
        return 'pending'
    if 'mega.nz' in l:
        return 'mega'
    if 'vk.com/doc' in l:
        return 'vk'
    if 'vk.com' in l:
        return 'pending'
    if 't.me/' in l:
        return 'tg_ref'
    if any(h in l for h in ('bookleaks', 'annas-archive', 'z-lib', 'libgen')):
        return 'bookleaks'
    return 'pending'


class BudgetExit(Exception):
    pass


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
    """Triple fallback entity resolution. Returns entity or raises."""
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
            a = a - 10**12 if a > 10**12 else a  # strip -100 prefix
        try:
            return await client.get_entity(PeerChannel(a))
        except Exception:
            pass
    return await client.get_entity(key)  # username fallback


async def scan_source(client, key, ref, prog, links, dlstate, cache_warm):
    cursor = prog.get(key, 0)
    start = cursor
    found = 0
    max_id = cursor
    ent = await resolve_source(client, key, ref, cache_warm)
    async for msg in client.iter_messages(ent, limit=None, reverse=True,
                                          min_id=cursor):
        max_id = msg.id
        # checkpoint cursor every 250 msgs so BUDGET_OUT resumes (dedup makes
        # re-scan idempotent anyway, but skipping known range saves API calls)
        if max_id % 250 == 0 and max_id != prog.get(key):
            prog[key] = max_id
            save_json(SCAN, prog)
        text = (msg.message or '') + ' ' + \
               ' '.join((getattr(e, 'url', '') or '')
                        for e in (msg.entities or [])
                        if getattr(e, 'url', None))
        for m in URL_RE.finditer(text):
            u = norm_url(m.group(0))
            if not u.startswith('http'):
                u = 'https://' + u.lstrip('/')
            if u in links:
                continue
            st = classify(u)
            links[u] = {'src': key, 'mid': msg.id,
                        'fname': '', 'ts': int(time.time())}
            dlstate[u] = {'status': st, 'tries': 0}
            found += 1
        # also capture filenames of documents posted inline (vk doc links etc.)
        if msg.document:
            for a in (msg.document.attributes or []):
                fn = getattr(a, 'file_name', None)
                if fn and msg.id:
                    links.setdefault(f'tgdoc:{key}:{msg.id}',
                                     {'src': key, 'mid': msg.id, 'fname': fn,
                                      'ts': int(time.time()), 'inline_doc': True})
                    found += 1
        if found and found % 25 == 0:
            prog[key] = max_id
            save_json(FL, links)
            save_json(DL, dlstate)
            save_json(SCAN, prog)
        if time.time() - T0 > BUDGET - 10:
            raise BudgetExit()
    prog[key] = max_id
    return found


async def amain():
    os.makedirs(STATE, exist_ok=True)
    links = load_json(FL, {})
    dlstate = load_json(DL, {})
    prog = load_json(SCAN, {})
    cache_warm = {'warm': False}
    n_before = len(links)
    client = TelegramClient(StringSession(SESSION), int(API['api_id']), API['api_hash'],
                            request_retries=2, retry_delay=1, connection_retries=2,
                            timeout=15, flood_sleep_threshold=25)
    await client.connect()
    if not await client.is_user_authorized():
        print('SESSION_INVALID')
        raise SystemExit(3)
    completed = True
    try:
        for key, ref in PRIMARY:
            if budget_left() < 8:
                print('BUDGET_OUT', flush=True)
                completed = False
                break
            try:
                n = await scan_source(client, key, ref, prog, links, dlstate, cache_warm)
                print(f'SRC {key} +{n} total={len(links)}', flush=True)
            except BudgetExit:
                print('BUDGET_OUT', flush=True)
                completed = False
                break
            except errors.FloodWaitError as e:
                print(f'FLOOD_WAIT {e.seconds}', flush=True)
                completed = False
                break
            except errors.ChannelPrivateError:
                print(f'PRIVATE {key}', flush=True)
                prog[key] = prog.get(key, -1)
            except Exception as e:
                print(f'ERR {key}: {type(e).__name__} {e!r}'[:160], flush=True)
    finally:
        save_json(FL, links)
        save_json(DL, dlstate)
        save_json(SCAN, prog)
        if completed:
            with open(DONE, 'w') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S'))
            print(f'RESCAN_DONE total={len(links)} (+{len(links)-n_before})', flush=True)
        else:
            print(f'RESCAN_PARTIAL total={len(links)} (+{len(links)-n_before})', flush=True)
        try:
            await client.disconnect()
        except Exception:
            pass


if __name__ == '__main__':
    asyncio.run(amain())
