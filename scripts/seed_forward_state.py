#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seed_forward_state.py — rebuild state/forward/ from session records after env rollback.

Facts recovered from session log (pre-rollback state):
  - translearners: last_id 49861 / max_id 71261, 4741 media forwarded (cumulative
    total_forwarded = 4741), status active, pace batch=5 sleep=5.
  - 10 discovered channel ids (first batch) restored verbatim; remaining ~44
    discovered ids will be REFOUND by tg_rediscover_sources.py (read-only sweep).
  - copy_state.txt: lost; NOT needed (sweep resumes at last_id=49861 with
    min_id=last_id -> no duplication risk). It rebuilds naturally going forward.
"""
import json, os

STATE = '/home/z/my-project/state/forward'
os.makedirs(STATE, exist_ok=True)

KNOWN_DISC = [1143784990, 1028728370, 1057914215, 1164862202, 1250893879,
              1186556006, 1038498535, 1262702452, 1396808321, 1071977878]

prog = {
    'sources': {
        'translearners': {'ref': -1001135889451, 'type': 'media', 'hop': 0,
                          'status': 'active', 'last_id': 49861, 'forwarded': 4741,
                          'errors': 0, 'title': 'Translearners', 'max_id': 71261}
    },
    'flood_until': 0,
    'total_forwarded': 4741,
    'pace': {'batch': 5, 'sleep': 5.0},
    'note': 'seeded from session records after env rollback 2026-09-25',
}
for name in ['nahwfortrans', 'anggalizy1', 'bonjourtranslation', 'translatorguide1',
             'translationzf', 'translationve', 'pttranslators',
             'transskylanguagesolutions', 'targma_amely', 'translationpolice',
             'maqhaalmutarjim_group']:
    prog['sources'][name] = {'ref': name, 'type': 'all', 'hop': 0, 'status': 'pending',
                             'last_id': 0, 'forwarded': 0, 'errors': 0, 'title': ''}

disc_path = f'{STATE}/discovered.jsonl'
if not os.path.exists(disc_path):
    with open(disc_path, 'w') as f:
        for cid in KNOWN_DISC:
            f.write(json.dumps({'channel_id': cid, 'hop': 1, 'via': 'translearners',
                                'ts': 0, 'title': ''}, ensure_ascii=False) + '\n')

tmp = f'{STATE}/progress.json.tmp'
with open(tmp, 'w') as f:
    json.dump(prog, f, ensure_ascii=False)
os.replace(tmp, f'{STATE}/progress.json')
print('SEEDED progress.json + discovered.jsonl (10 known disc ids)')
