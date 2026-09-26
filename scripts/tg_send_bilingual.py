#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg_send_bilingual.py — send the bilingual EN<->AR medical corpus to @DrMalekDrive.

Pairs are sent ADJACENTLY (Arabic then English) with a matching caption so the
channel reads as a bilingual reference collection.

Job sources (resumable via state/send/bilingual_sent.json):
  download/bilingual/healthinfo/pairs.jsonl   (id/title/ar_file/en_file)
  download/bilingual/immunize_vis/pairs.jsonl (vaccine/ar_file/en_file)
  download/bilingual/immunize_az/pairs.jsonl  (name/ar_file/en_file)
  download/medlineplus/pairs.jsonl            (title/ar_file/en_file)

Env: SEND_BUDGET (default 110s) under `timeout -s KILL 130`.
!!! Run SEQUENTIALLY — never while another client uses the same auth key !!!
"""
import os, json, time, asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

BASE = '/home/z/my-project'
SEC = f'{BASE}/.secrets'
STF = f'{BASE}/state/send'
SENTF = f'{STF}/bilingual_sent.json'
BIL = f'{BASE}/download/bilingual'
MLP = f'{BASE}/download/medlineplus'

API = json.load(open(f'{SEC}/telegram_api.json'))
SESSION = open(f'{SEC}/tg_string_session.txt').read().strip()
TARGET = 'DrMalekDrive'

BUDGET = float(os.environ.get('SEND_BUDGET', '110'))
T0 = time.time()
def budget_left(): return BUDGET - (time.time() - T0)

TAG = '#طبي #ثنائي_اللغة #Medical #Bilingual'

def load_json(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d

def save_json(p, o):
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(o, f, ensure_ascii=False)
    os.replace(tmp, p)

def jobs():
    out = []
    # healthinfo: 208 full pairs + 9 AR-only
    p = f'{BIL}/healthinfo/pairs.jsonl'
    if os.path.exists(p):
        for line in open(p, encoding='utf-8', errors='ignore'):
            j = json.loads(line)
            t = (j.get('title') or f"doc {j.get('id')}").strip()[:90]
            if j.get('ar_file') and os.path.exists(j['ar_file']):
                out.append((j['ar_file'], f'{t} — العربية [{j.get("ar","")}]'))
            if j.get('en_file') and os.path.exists(j['en_file']):
                out.append((j['en_file'], f'{t} — English'))
    # immunize_vis
    p = f'{BIL}/immunize_vis/pairs.jsonl'
    if os.path.exists(p):
        for line in open(p, encoding='utf-8', errors='ignore'):
            j = json.loads(line)
            t = (j.get('title') or j.get('vaccine') or 'Vaccine VIS').strip()[:90]
            if j.get('ar_file') and os.path.exists(j['ar_file']):
                out.append((j['ar_file'], f'VIS {t} — العربية'))
            if j.get('en_file') and os.path.exists(j['en_file']):
                out.append((j['en_file'], f'VIS {t} — English'))
    # immunize_az
    p = f'{BIL}/immunize_az/pairs.jsonl'
    if os.path.exists(p):
        for line in open(p, encoding='utf-8', errors='ignore'):
            j = json.loads(line)
            n = (j.get('name') or '').replace('-ara', '').rsplit('.', 1)[0]
            if j.get('ar_file') and os.path.exists(j['ar_file']):
                out.append((j['ar_file'], f'Immunize handout {n} — العربية'))
            if j.get('en_file') and os.path.exists(j['en_file']):
                out.append((j['en_file'], f'Immunize handout {n} — English'))
    # medlineplus
    p = f'{MLP}/pairs.jsonl'
    if os.path.exists(p):
        for line in open(p, encoding='utf-8', errors='ignore'):
            j = json.loads(line)
            t = (j.get('title') or '').strip()[:90]
            if j.get('ar_file') and os.path.exists(j['ar_file']):
                out.append((j['ar_file'], f'{t} — العربية (MedlinePlus)'))
            if j.get('en_file') and os.path.exists(j['en_file']):
                out.append((j['en_file'], f'{t} — English (MedlinePlus)'))
    return out

async def amain():
    os.makedirs(STF, exist_ok=True)
    sent = load_json(SENTF, {})
    all_jobs = jobs()
    todo = [(f, c) for f, c in all_jobs if not sent.get(f)]
    print(f'JOBS total={len(all_jobs)} todo={len(todo)}', flush=True)
    if not todo:
        print('sent_this_round=0', flush=True)
        return
    client = TelegramClient(StringSession(SESSION), int(API['api_id']), API['api_hash'],
                            request_retries=2, retry_delay=1, connection_retries=2,
                            timeout=30, flood_sleep_threshold=25)
    await client.connect()
    if not await client.is_user_authorized():
        print('SESSION_INVALID')
        raise SystemExit(3)
    target = await client.get_entity(TARGET)
    n = 0
    try:
        for f, c in todo:
            if budget_left() < 15:
                print('BUDGET_OUT', flush=True)
                break
            try:
                await client.send_file(target, f, force_document=True,
                                       caption=f'🏥 {c}\n{TAG}')
                sent[f] = 1
                n += 1
                if n % 5 == 0:
                    save_json(SENTF, sent)
                    print(f'PROG sent={n} left={len(todo)-n} ({budget_left():.0f}s)', flush=True)
                await asyncio.sleep(1.0)
            except Exception as e:
                print(f'ERR {os.path.basename(f)[:40]}: {type(e).__name__}', flush=True)
    finally:
        save_json(SENTF, sent)
        await client.disconnect()
    print(f'sent_this_round={n} total_sent={len(sent)}', flush=True)

if __name__ == '__main__':
    asyncio.run(amain())
