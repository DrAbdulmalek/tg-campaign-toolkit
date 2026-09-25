#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_fix_truncated_links.py — repair links truncated by the OLD URL regex.

Background: the old scanner regex excluded ')' so mediafire URLs whose FILENAME
contains parentheses were cut short (e.g. .../File_(2nd_Ed).pdf/file became
.../File_(2nd). Those land in link_dl_state.json as 'mediafire_no_link'
(dead page -> no direct download link found).

Repair pass (re-rebuilt after env reset #3):
  1. collect urls with status mediafire_no_link (or *_no_link generally)
  2. for each, refetch the ORIGINAL message via file_links.json's src/mid
  3. re-extract with the fixed norm_url() (strip trailing ')' only when
     parens are unbalanced)
  4. pop the old key, write the fixed key, reset status
  5. urls whose ORIGINAL MESSAGE TEXT was already truncated stay bad
     (legit failures -> left as-is for the report)

Run:  python tg_fix_truncated_links.py
Env:  FIX_BUDGET (default 110s)
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

API = json.load(open(f'{SEC}/telegram_api.json'))
SESSION = open(f'{SEC}/tg_string_session.txt').read().strip()
BUDGET = float(os.environ.get('FIX_BUDGET', '110'))
T0 = time.time()

URL_RE = re.compile(
    r'(?:https?://|www\.|t\.me/|mega\.nz/|vk\.com/)[^\s<>"\'\[\]{}\\]+', re.I)


def norm_url(u: str) -> str:
    u = u.rstrip('.,;:')
    while u.endswith(')') and u.count('(') < u.count(')'):
        u = u[:-1].rstrip('.,;:')
    return u


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
    links = load_json(FL, {})
    dl = load_json(DL, {})
    bad = {u: v for u, v in dl.items() if v.get('status', '').endswith('_no_link')}
    print(f'CANDIDATES {len(bad)}', flush=True)

    by_src = {}
    for u in bad:
        meta = links.get(u, {})
        if meta.get('src') and meta.get('mid'):
            by_src.setdefault(meta['src'], []).append(u)

    client = TelegramClient(StringSession(SESSION), int(API['api_id']), API['api_hash'],
                            request_retries=2, retry_delay=1, connection_retries=2,
                            timeout=15, flood_sleep_threshold=0)
    await client.connect()
    if not await client.is_user_authorized():
        print('SESSION_INVALID')
        raise SystemExit(3)

    removed = fixed = still_bad = 0
    try:
        for src, urls in by_src.items():
            if time.time() - T0 > BUDGET - 10:
                print('BUDGET_OUT', flush=True)
                break
            try:
                ent = await resolve_source(client, src, src)
            except Exception as e:
                print(f'RESOLVE_FAIL {src}: {type(e).__name__}', flush=True)
                still_bad += len(urls)
                continue
            mids = [links[u]['mid'] for u in urls]
            try:
                msgs = await client.get_messages(ent, ids=mids)
            except errors.FloodWaitError as e:
                print(f'FLOOD_WAIT {e.seconds}', flush=True)
                break
            except Exception as e:
                print(f'GETMSG_FAIL {src}: {type(e).__name__}', flush=True)
                still_bad += len(urls)
                continue
            if not isinstance(msgs, list):
                msgs = [msgs]
            msg_by_id = {m.id: m for m in msgs if m}
            for u in urls:
                mid = links[u]['mid']
                m = msg_by_id.get(mid)
                if not m:
                    still_bad += 1
                    continue
                text = (m.message or '') + ' ' + \
                       ' '.join((getattr(e, 'url', '') or '')
                                for e in (m.entities or [])
                                if getattr(e, 'url', None))
                cands = [norm_url(x.group(0)) for x in URL_RE.finditer(text)]
                full = [c for c in cands if c.lower().startswith('http')]
                if u in full:
                    still_bad += 1  # original text really is truncated
                    continue
                best = None
                for c in full:
                    if c.startswith(u) and len(c) > len(u):
                        best = c if (best is None or len(c) > len(best)) else best
                if best:
                    dl.pop(u, None)
                    meta = links.pop(u)
                    links[best] = meta
                    dl[best] = {'status': 'pending', 'tries': 0,
                                'fixed_from': u}
                    fixed += 1
                else:
                    still_bad += 1
    finally:
        save_json(FL, links)
        save_json(DL, dl)
        try:
            await client.disconnect()
        except Exception:
            pass
    print(f'FIX_DONE candidates={len(bad)} fixed={fixed} still_bad={still_bad} '
          f'removed_keys={fixed}', flush=True)


if __name__ == '__main__':
    asyncio.run(amain())
