#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_send_harvest.py — STAGE4 sender: upload harvested files to @DrMalekDrive.

Dedup (zero-duplicate contract):
  - state/forward/books_sent.txt  fname-per-line log (skip already-sent names)
  - state/forward/target_media_ids.json fingerprint index (sha1(fname|size|mime))
    populated by tg_dump_target_filenames.py from the target's real history

Termination contract with res_step.sh STAGE4:
  report line ends with sent_this_round=N; when N == 0 the step writes
  download/link_harvest/send_done.txt.

Caption policy: prefer a sidecar caption file <file>.caption.txt placed next to
the download; else fall back to the source URL from manifest.jsonl.

Env: SEND_BUDGET (default 110s), runs under `timeout -s KILL 130`.
!!! Run SEQUENTIALLY — never while another client uses the same auth key !!!
"""
import os, re, json, time, asyncio, hashlib
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

BASE = '/home/z/my-project'
SEC = f'{BASE}/.secrets'
STATE = f'{BASE}/state/forward'
FPF = f'{STATE}/target_media_ids.json'
SENT = f'{STATE}/books_sent.txt'
HARVEST = f'{BASE}/download/link_harvest'
SEND_DONE = f'{HARVEST}/send_done.txt'
MANIFEST = f'{HARVEST}/manifest.jsonl'

API = json.load(open(f'{SEC}/telegram_api.json'))
SESSION = open(f'{SEC}/tg_string_session.txt').read().strip()
TARGET = 'DrMalekDrive'
BUDGET = float(os.environ.get('SEND_BUDGET', '110'))
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


def fp_of(fname, size):
    return hashlib.sha1(f'{fname}|{size}||'.encode('utf-8', 'ignore')).hexdigest()


def url_for_file(fname):
    try:
        for line in open(MANIFEST, encoding='utf-8', errors='ignore'):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get('status') == 'done':
                tail = os.path.basename(rec.get('url', '')).split('?')[0]
                if tail and tail in fname:
                    return rec['url']
    except FileNotFoundError:
        pass
    return ''


async def amain():
    os.makedirs(HARVEST, exist_ok=True)
    fps = load_json(FPF, {})
    try:
        sent_names = {l.rstrip('\n') for l in open(SENT, encoding='utf-8',
                                                   errors='ignore') if l.strip()}
    except FileNotFoundError:
        sent_names = set()

    files = sorted(f for f in os.listdir(HARVEST)
                   if os.path.isfile(os.path.join(HARVEST, f))
                   and not f.endswith(('.caption.txt', '.txt', '.jsonl', '.part'))
                   and f != 'dl_done.txt')
    todo = []
    for f in files:
        if f in sent_names:
            continue
        size = os.path.getsize(os.path.join(HARVEST, f))
        if fp_of(f, size) in fps:
            sent_names.add(f)
            with open(SENT, 'a', encoding='utf-8') as fh:
                fh.write(f + '\n')
            continue
        todo.append(f)

    client = TelegramClient(StringSession(SESSION), int(API['api_id']), API['api_hash'],
                            request_retries=2, retry_delay=1, connection_retries=2,
                            timeout=30, flood_sleep_threshold=0)
    await client.connect()
    if not await client.is_user_authorized():
        print('SESSION_INVALID')
        raise SystemExit(3)
    target = await client.get_entity(TARGET)

    sent_this = 0
    skip_dup = len(files) - len(todo)
    try:
        for f in todo:
            if budget_left() < 15:
                print('BUDGET_OUT', flush=True)
                break
            path = os.path.join(HARVEST, f)
            capf = path.rsplit('.', 1)[0] + '.caption.txt'
            caption = ''
            if os.path.exists(capf):
                caption = open(capf, encoding='utf-8', errors='ignore').read()[:1000]
            else:
                u = url_for_file(f)
                caption = f'📚 {f}' + (f'\n🔗 {u}' if u else '')
            try:
                await client.send_file(target, path, force_document=True,
                                       caption=caption)
                sent_this += 1
                sent_names.add(f)
                with open(SENT, 'a', encoding='utf-8') as fh:
                    fh.write(f + '\n')
                print(f'SENT {f}', flush=True)
                await asyncio.sleep(4.0)
            except errors.FloodWaitError as e:
                print(f'FLOOD_WAIT {e.seconds}', flush=True)
                break
            except errors.ChatWriteForbiddenError:
                print('TARGET_WRITE_FORBIDDEN')
                raise SystemExit(4)
            except Exception as e:
                print(f'ERR {f}: {type(e).__name__}', flush=True)
                await asyncio.sleep(2.0)
    finally:
        save_json(FPF, fps)
        try:
            await client.disconnect()
        except Exception:
            pass
    print(json.dumps({'sent_this_round': sent_this,
                      'remaining': max(len(todo) - sent_this, 0),
                      'skip_dedup': skip_dup}), flush=True)
    if sent_this == 0:
        with open(SEND_DONE, 'w') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S'))
        print('SEND_DONE', flush=True)


if __name__ == '__main__':
    asyncio.run(amain())
