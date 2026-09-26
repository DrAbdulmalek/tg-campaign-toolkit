#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dedup_manifest.py — keep best entry per doc id in bilingual manifests."""
import json, sys, os

for path in sys.argv[1:]:
    if not os.path.exists(path):
        continue
    best = {}
    order = []
    for line in open(path):
        try:
            j = json.loads(line)
        except Exception:
            continue
        k = j.get('id') or j.get('name') or j.get('title')
        if k not in order:
            order.append(k)
        cur = best.get(k)
        score = (1 if j.get('ar_file') else 0) + (1 if j.get('en_file') else 0)
        cscore = ((1 if cur.get('ar_file') else 0) + (1 if cur.get('en_file') else 0)) if cur else -1
        if score > cscore:
            best[k] = j
    with open(path, 'w') as f:
        for k in order:
            f.write(json.dumps(best[k], ensure_ascii=False) + '\n')
    full = sum(1 for j in best.values() if j.get('ar_file') and j.get('en_file'))
    one = sum(1 for j in best.values() if (j.get('ar_file') or j.get('en_file')) and not (j.get('ar_file') and j.get('en_file')))
    none = sum(1 for j in best.values() if not j.get('ar_file') and not j.get('en_file'))
    print(f'{os.path.basename(path)}: kept={len(best)} full={full} one_side={one} none={none}')
