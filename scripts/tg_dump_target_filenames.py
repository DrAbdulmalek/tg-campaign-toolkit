#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_dump_target_filenames.py — READ-ONLY sweep of the TARGET channel (@DrMalekDrive)
history to rebuild the dedup/audit state after env rollback:

  state/forward/target_media_ids.json  fingerprint index (fname+size+mime hashes)
                                       used by forwarder/sender for zero-dup
  state/forward/books_sent.txt         fname-per-line dedup log for send_books
  state/forward/target_files.json      audit array [{id, fname, size, mime, date}]

Resume protocol (file-marker driven):
  state/forward/fnscan_offset.txt   numeric offset_id of the sweep cursor;
                                    written as '1' when the BOTTOM is reached
                                    (= FN_SCAN_DONE sentinel).

NO forwarding, NO outbound writes -> cannot duplicate anything in the target.
Env: FN_BUDGET (default 110s), CHUNK (default 500 msgs per pass).
"""
import os, json, time, asyncio, hashlib
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

BASE = '/home/z/my-project'
SEC = f'{BASE}/.secrets'
STATE = f'{BASE}/state/forward'
FPF = f'{STATE}/target_media_ids.json'
SENT = f'{STATE}/books_sent.txt'
AUDIT = f'{STATE}/target_files.json'
OFFF = f'{STATE}/fnscan_offset.txt'

API = json.load(open(f'{SEC}/telegram_api.json'))
SESSION = open(f'{SEC}/tg_string_session.txt').read().strip()
TARGET = 'DrMalekDrive'

BUDGET = float(os.environ.get('FN_BUDGET', '110'))
CHUNK = int(os.environ.get('CHUNK', '500'))
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


def fingerprint(doc):
    """Stable per-file fingerprint: fname + size + mime -> sha1."""
    fname = ''
    for a in (doc.attributes or []):
        fn = getattr(a, 'file_name', None)
        if fn:
            fname = fn
            break
    mime = getattr(doc, 'mime_type', '') or ''
    size = getattr(doc, 'size', 0) or 0
    key = f'{fname}|{size}|{mime}'
    return hashlib.sha1(key.encode('utf-8', 'ignore')).hexdigest(), fname, size, mime


async def amain():
    os.makedirs(STATE, exist_ok=True)
    fps = load_json(FPF, {})
    audit = load_json(AUDIT, [])
    try:
        offset = int(open(OFFF).read().strip() or 0)
    except FileNotFoundError:
        offset = 0
    if offset == 1:
        print(f'FN_SCAN_DONE fingerprints={len(fps)} audit={len(audit)}', flush=True)
        return

    client = TelegramClient(StringSession(SESSION), int(API['api_id']), API['api_hash'],
                            request_retries=2, retry_delay=1, connection_retries=2,
                            timeout=15, flood_sleep_threshold=0)
    await client.connect()
    if not await client.is_user_authorized():
        print('SESSION_INVALID')
        raise SystemExit(3)
    ent = await client.get_entity(TARGET)

    seen_docs = 0
    added_fp = 0
    added_sent = 0
    completed = False
    known_names = set()
    if os.path.exists(SENT):
        known_names = {l.rstrip('\n') for l in open(SENT, encoding='utf-8',
                                                     errors='ignore')}
    try:
        while budget_left() > 8:
            msgs = []
            async for m in client.iter_messages(ent, limit=CHUNK, offset_id=offset):
                msgs.append(m)
            if not msgs:
                completed = True
                break
            for m in msgs:
                offset = m.id
                doc = m.document
                if not doc:
                    continue
                seen_docs += 1
                fp, fname, size, mime = fingerprint(doc)
                if fp not in fps:
                    fps[fp] = {'mid': m.id, 'fname': fname, 'size': size, 'mime': mime}
                    added_fp += 1
                if fname and fname not in known_names:
                    with open(SENT, 'a', encoding='utf-8') as f:
                        f.write(fname + '\n')
                    known_names.add(fname)
                    added_sent += 1
                audit.append({'id': m.id, 'fname': fname, 'size': size,
                              'mime': mime,
                              'date': m.date.isoformat() if m.date else ''})
            save_json(FPF, fps)
            with open(OFFF, 'w') as f:
                f.write(str(offset))
            print(f'FN_SCAN offset={offset} docs={seen_docs} '
                  f'fp={len(fps)}(+{added_fp}) names={len(known_names)}', flush=True)
    except errors.FloodWaitError as e:
        print(f'FLOOD_WAIT {e.seconds}', flush=True)
    finally:
        save_json(FPF, fps)
        save_json(AUDIT, audit)
        with open(OFFF, 'w') as f:
            f.write('1' if completed else str(offset))
        try:
            await client.disconnect()
        except Exception:
            pass
    if completed:
        print(f'FN_SCAN_DONE docs={seen_docs} fp={len(fps)}(+{added_fp}) '
              f'sent_names={len(known_names)}(+{added_sent}) audit={len(audit)}',
              flush=True)
    else:
        print(f'FN_SCAN_PARTIAL offset={offset} fp={len(fps)}(+{added_fp})',
              flush=True)


if __name__ == '__main__':
    asyncio.run(amain())
