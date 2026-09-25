# State Schema & Recovery Runbook

所有路径相对于 `/home/z/my-project/`。

## 凭据（.secrets/，永不入库）

| 文件 | 内容 |
|---|---|
| `telegram_api.json` | `{api_id, api_hash, phone}` |
| `tg_phone_code_hash.txt` | 发码返回的 hash（`code` 模式必须显式传） |
| `tg_string_session.txt` | StringSession（主会话凭据） |
| `gh_token` | GitHub PAT（askpass 用） |

烧毁归档惯例：`*.burned`（如 `tg_string_session.burned`）——绝不删除，便于审计。

## state/forward/（管线 A 转发）

| 文件 | 格式 | 用途 |
|---|---|---|
| `progress.json` | `{sources: {key: {ref, type, hop, status, last_id, forwarded, errors, title, max_id}}, flood_until, total_forwarded, pace: {batch, sleep}}` | 转发游标与节奏；每批原子落盘 |
| `discovered.jsonl` | 每行 `{channel_id, hop, via, ts, title}` | 嵌套发现频道（去重保留最小 hop） |
| `errors.log` | 时间戳行 | 单条失败日志 |
| `target_media_ids.json` | `{sha1: {mid, fname, size, mime}}` | 目标频道文件指纹索引（去重核心） |
| `books_sent.txt` | 每行一个文件名 | 发送去重日志 |
| `target_files.json` | `[{id, fname, size, mime, date}]` | 目标频道审计数组 |
| `fnscan_offset.txt` | 数字 / `1` | STAGE1 游标；`1` = 到底（完成哨兵） |
| `scan_state.txt` | 数字 / `done` | tg_rediscover 游标 |

source status 生命周期：`pending → active → done`；
旁路：`unresolved`（解析失败）/ `filtered`（标题不相关）/ `dup_primary`（与主源重复）/
`restricted`（疑似重定向受限）。

## state/linkharvest/（管线 B 收割）

| 文件 | 格式 | 用途 |
|---|---|---|
| `file_links.json` | `{url: {src, mid, fname, ts, inline_doc?}}` | 收割到的链接（`src/mid` 可回溯原消息） |
| `link_dl_state.json` | `{url: {status, tries, size?, fixed_from?}}` | 每链接下载状态机 |
| `scan_state.json` | `{src_key: last_msg_id}` | STAGE2 游标 |
| `rescan_done.txt` | 时间戳 | STAGE2 完成标记 |

link status 枚举：`pending` / `done` / `404` / `html` / `host_blocked` /
`drive_no_id` / `mediafire_no_link` / `bookleaks` / `vk`（待处理器）/
`mega`（待处理器）/ `error:<Type>` / `budget_out`

## download/link_harvest/

| 文件 | 用途 |
|---|---|
| `manifest.jsonl` | 每行 `{url, status, size, ts}` |
| `dl_done.txt` | STAGE3 完成（downloaded_this_run==0 时写入） |
| `send_done.txt` | STAGE4 完成（sent_this_round==0 时写入） |
| `*.caption.txt` | 可选：同名文件的 caption 覆盖 |

## 回滚恢复手册（环境清空后按序执行）

1. `git clone https://github.com/DrAbdulmalek/tg-campaign-toolkit` → 拷 `scripts/` 回位
2. 重建 `.secrets/`（telegram_api.json + 新登录：`tg_relogin2.py send` → `code <码>`）
3. `python3 scripts/seed_forward_state.py`（从会话记录播种 progress.json）
4. `python3 scripts/tg_rediscover_sources.py` 循环跑完 → 重建 discovered.jsonl
5. `bash scripts/res_step.sh` 循环 → STAGE1 重建指纹库（去重零丢失）
6. 顺序交替：`fwd 一轮 → res_step 一步`，绝不并行
7. 预期规模参考（2026-09 时点）：指纹 ~8,186；链接 ~851；discovered ~152 频道；
   total_fwd 10,900+
