#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_fetch_inline_docs.py — download documents posted INLINE in source channels.

Many sources (e.g. the dictionaries channel behind vk.com/doc243798239_*) post
files directly to Telegram instead of hosting links. VK private docs can't be
fetched anonymously, so the practical path is grabbing the same files as
inline Telegram documents.

Flow:
  1. read state/linkharvest/file_links.json entries with inline_doc=true
     ({src, mid, fname})
  2. for each: fetch the message, download_media into download/link_harvest/
     (skipping names already in state/forward/books_sent.txt and files already
     on disk; fingerprints in target_media_ids.json checked at send time)
  3. mark entry in link_dl_state.json: inline_done | inline_missing | error:*
  4. STAGE4 (tg_send_harvest.py) uploads everything afterwards

Resume: entries with status inline_done are skipped; re-runs cheap.
!!! TG CLIENT — run SEQUENTIALLY, one client at a time (iron rule) !!!
Env: FETCH_BUDGET (default 110s).
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
DEST = f'{BASE}/download/link_harvest'
SENT = f'{BASE}/state/forward/books_sent.txt'

API = json.load(open(f'{SEC}/telegram_api.json'))
SESSION = open(f'{SEC}/tg_string_session.txt').read().strip()
BUDGET = float(os.environ.get('FETCH_BUDGET', '110'))
T0 = time.time()


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


def sent_names():
    try:
        return {l.rstrip('\n') for l in open(SENT, encoding='utf-8',
                                             errors='ignore') if l.strip()}
    except FileNotFoundError:
        return set()


def uniq_path(fname):
    fname = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', fname)[:150] or 'unnamed'
    path = os.path.join(DEST, fname)
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(path):
        path = f'{base}_{i}{ext}'
        i += 1
    return path


async def resolve_source(client, key, ref):
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


async def amain():
    os.makedirs(DEST, exist_ok=True)
    links = load_json(FL, {})
    dl = load_json(DL, {})
    known = sent_names()
    items = [(u, m) for u, m in links.items()
             if m.get('inline_doc')
             and dl.get(u, {}).get('status') not in ('inline_done',)]
    print(f'INLINE_QUEUE {len(items)}', flush=True)
    client = TelegramClient(StringSession(SESSION), int(API['api_id']), API['api_hash'],
                            request_retries=2, retry_delay=1, connection_retries=2,
                            timeout=30, flood_sleep_threshold=0)
    await client.connect()
    if not await client.is_user_authorized():
        print('SESSION_INVALID')
        raise SystemExit(3)
    ent_cache = {}
    got = 0
    try:
        for u, meta in items:
            if budget_left() < 12:
                print('BUDGET_OUT', flush=True)
                break
            fname = meta.get('fname') or f'inline_{meta["mid"]}'
            if fname in known or os.path.exists(os.path.join(DEST, fname)):
                dl[u] = {'status': 'inline_done', 'note': 'already present'}
                continue
            src = meta['src']
            if src not in ent_cache:
                try:
                    ent_cache[src] = await resolve_source(client, src, src)
                except Exception as e:
                    dl[u] = {'status': f'error:resolve_{type(e).__name__}'}
                    continue
            try:
                msg = await client.get_messages(ent_cache[src], ids=[meta['mid']])
                msg = msg[0] if isinstance(msg, list) else msg
            except errors.FloodWaitError as e:
                print(f'FLOOD_WAIT {e.seconds}', flush=True)
                break
            except Exception as e:
                dl[u] = {'status': f'error:getmsg_{type(e).__name__}'}
                continue
            if not msg or not msg.document:
                dl[u] = {'status': 'inline_missing'}
                continue
            path = uniq_path(fname)
            try:
                await client.download_media(msg, file=path)
                got += 1
                dl[u] = {'status': 'inline_done',
                         'size': os.path.getsize(path)}
                print(f'GOT {fname[:70]} ({dl[u]["size"]}B)', flush=True)
                await asyncio.sleep(2.0)
            except errors.FloodWaitError as e:
                print(f'FLOOD_WAIT {e.seconds}', flush=True)
                break
            except Exception as e:
                dl[u] = {'status': f'error:dl_{type(e).__name__}'}
                if os.path.exists(path):
                    os.unlink(path)
    finally:
        save_json(DL, dl)
        try:
            await client.disconnect()
        except Exception:
            pass
    left = sum(1 for u, m in links.items() if m.get('inline_doc')
               and dl.get(u, {}).get('status') not in ('inline_done', 'inline_missing'))
    print(json.dumps({'downloaded_this_run': got, 'left': left}), flush=True)


if __name__ == '__main__':
    asyncio.run(amain())
