#!/usr/bin/env bash
# res_step.sh — single-step state machine for the link-harvest resource pipeline.
#
# Rebuilt after env reset #3. Contract: EACH invocation performs AT MOST ONE
# step and always exits 0 — the outer loop (sequential, NEVER parallel with
# any other Telegram client) just keeps calling it until RES_ALL_DONE.
#
# File-marker driven stages (all under state/linkharvest/ unless noted):
#   STAGE1  fnscan   -> tg_dump_target_filenames.py
#                       done when state/forward/fnscan_offset.txt == '1'
#   STAGE2  rescan   -> tg_scan_file_links.py
#                       done when rescan_done.txt exists
#   STAGE3  download -> tg_download_links.py
#                       done when dl_done.txt exists
#                       (script writes it when downloaded_this_run == 0)
#   STAGE4  send     -> tg_send_harvest.py
#                       done when send_done.txt exists
#                       (script writes it when sent_this_round == 0)
#                       NOTE: stage 4 loops back to STAGE2 first — new links
#                       keep arriving in the sources, so harvesting is cyclic.
#   RES_ALL_DONE marker only when STAGE4 completes with nothing new to harvest.
#
# Budget watchdog pattern: each python step runs under
#   <X>_BUDGET=110 timeout -s KILL 130 python3 script.py
#
# IRON RULE: run this SEQUENTIALLY only. One TelegramClient process at a time
# or the auth key gets burned (AuthKeyDuplicatedError — learned the hard way).

BASE=/home/z/my-project
TS=${BASE}/state/linkharvest
STF=${BASE}/state/forward
mkdir -p "$TS" "$STF" "${BASE}/download/link_harvest"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ---------- STAGE 1: rebuild target-filename dedup state ----------
if [ ! -f "$STF/fnscan_offset.txt" ] || [ "$(cat "$STF/fnscan_offset.txt" 2>/dev/null)" != "1" ]; then
  log "STAGE1 fnscan"
  FN_BUDGET=${FN_BUDGET:-110} timeout -s KILL 130 \
    python3 "$BASE/scripts/tg_dump_target_filenames.py"
  exit 0
fi

# ---------- STAGE 2: scan sources for links ----------
if [ ! -f "$TS/rescan_done.txt" ]; then
  log "STAGE2 rescan"
  OUT=$(SCAN_BUDGET=${SCAN_BUDGET:-110} timeout -s KILL 130 \
    python3 "$BASE/scripts/tg_scan_file_links.py" 2>&1)
  echo "$OUT" | tail -3
  # completion = run tail does NOT end with BUDGET_OUT / FLOOD_WAIT / SESSION_INVALID
  if ! echo "$OUT" | grep -qE 'BUDGET_OUT|FLOOD_WAIT|SESSION_INVALID'; then
    touch "$TS/rescan_done.txt"
    log "STAGE2 complete"
  fi
  exit 0
fi

# ---------- STAGE 3: download links ----------
if [ ! -f "${BASE}/download/link_harvest/dl_done.txt" ]; then
  log "STAGE3 download"
  OUT=$(DL_BUDGET=${DL_BUDGET:-110} timeout -s KILL 130 \
    python3 "$BASE/scripts/tg_download_links.py" 2>&1)
  echo "$OUT" | tail -3
  if echo "$OUT" | grep -q '"downloaded_this_run": 0'; then
    touch "${BASE}/download/link_harvest/dl_done.txt"
    log "STAGE3 complete"
  fi
  exit 0
fi

# ---------- STAGE 4: send harvested files ----------
if [ ! -f "${BASE}/download/link_harvest/send_done.txt" ]; then
  log "STAGE4 send"
  OUT=$(SEND_BUDGET=${SEND_BUDGET:-110} timeout -s KILL 130 \
    python3 "$BASE/scripts/tg_send_harvest.py" 2>&1)
  echo "$OUT" | tail -3
  if echo "$OUT" | grep -q 'sent_this_round=0\|"sent_this_round": 0'; then
    touch "${BASE}/download/link_harvest/send_done.txt"
    # cycle: reopen STAGE2 so newly discovered links get harvested
    rm -f "$TS/rescan_done.txt" "${BASE}/download/link_harvest/dl_done.txt"
    log "STAGE4 complete -> cycle back to STAGE2"
  fi
  exit 0
fi

log "RES_ALL_DONE"
exit 0
