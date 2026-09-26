# BACKUP MANIFEST — 2026-09-27 (after wipe #6)

## What this backup contains

| Path | Content |
|------|---------|
| `scripts/` (26 campaign scripts) | Full pipeline: forward / res_step / harvest / bilingual / corpora / VK / deep-scan / send |
| `tg_deep_links.py` (root → moved to scripts/) | Deep channel scanner (buttons + dict keywords + file ext) |
| `state-backup/forward/progress.json` | Campaign state: 13,770 forwards, translearners 69,860/71,261, pace, sources |
| `state-backup/forward/discovered.jsonl` | Discovered-source queue |
| `state-backup/WORKLOG.md` | Full multi-agent campaign history (all tasks, all wipes) |

## Wipe #6 impact (this session)

LOST locally (recovered from this repo unless noted):
- `.secrets/` — TG StringSession (needs user login code), gh_token (restored from chat), VK browser state (needs re-login)
- `state/linkharvest/` — 13,161 scanned links, 59 deep links, cursors, vk_psv4_map (rebuildable: re-run tg_scan_file_links.py + tg_deep_links.py after TG login)
- `state/send/` — bilingual_sent.json (579 uploads recorded; re-scan of channel can rebuild)
- `download/bilingual/`, `download/corpora/` (wikimatrix.zip ok on TG), `download/link_files/`, `download/link_harvest/`

SURVIVED: `state/forward/` (progress + discovered), `scripts/` pre-campaign era files, `worklog.md`.

## Delivered to @DrMalekDrive (on Telegram — safe)

1. **579 bilingual medical PDFs** (healthinfo 208 pairs + immunize 33 + medlineplus 387 pairs / 271AR+261EN+119EN-rules)
2. **قائمة_القواميس_اونلاين.md** — 271 dictionary links report
3. **العدد الخامس 2023.pdf** (12.4MB, Google Drive find)
4. **VK docs**: 21 downloaded pre-wipe (uploaded via harvest pipeline), +1 this session
5. **WikiMatrix 1M pairs** corpus (zip, on channel)
6. Campaign forwards: 13,770 files/messages

## Recovery procedure (proven, ~12 min)

1. Clone this repo → `cp scripts/* /home/z/my-project/scripts/`
2. `pip install telethon requests`
3. TG login: `python3 scripts/tg_relogin2.py send` → user code → `code <N>` → session restored
4. Re-seed state: `python3 scripts/seed_forward_state.py` (progress.json) + re-run
   `tg_scan_file_links.py` (rebuild file_links/link_dl_state) + `tg_deep_links.py` (deep state)
5. Resume alternation: `tg_forward_to_channel.py` ↔ `res_step.sh`; corpora: `corpus_dl.py` (Range-resume)

## Security notes

- GH token `ghp_eKp7...` shared in chat → **rotate after session**
- VK credentials shared in chat → **change password**
- Never commit `.secrets/` — sessions/tokens live locally only
