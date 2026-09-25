# tg-campaign-toolkit

Telegram 图书搬运战役工具链 — 把多个翻译/学习频道的内容服务端转发到一个目标频道
（`@DrMalekDrive`），并把源频道里散落的下载链接（Google Drive / MediaFire / MEGA / VK）
收割、下载、以文件形式上传到同一目标频道。

> ⚠️ 本仓库在 **环境第 3 次被清空后** 重建。`RECONSTRUCTED` 标记的脚本是按会话记录
> 重建的（原始文件丢失），`SURVIVED` 标记的是本地幸存原件。逻辑保真，细节可能有出入。

## 目录

```
scripts/
  tg_relogin.py               SURVIVED       v1 登录（device session 引导）
  tg_relogin2.py              RECONSTRUCTED  v2 登录（修复 phone_code_hash 显式传入）
  tg_forward_to_channel.py    SURVIVED       主转发器 v2.1（异步、多源、服务端 copy）
  tg_rediscover_sources.py    SURVIVED       只读扫 Translearners 历史重建 discovered.jsonl
  seed_forward_state.py       SURVIVED       回滚后从会话记录播种 progress.json
  tg_dump_target_filenames.py RECONSTRUCTED  只读扫目标频道重建去重指纹库（STAGE1）
  tg_scan_file_links.py       RECONSTRUCTED  扫源频道收割下载链接（STAGE2）
  tg_download_links.py        RECONSTRUCTED  链接下载器 drive/mediafire/direct（STAGE3）
  tg_send_harvest.py          RECONSTRUCTED  收割文件上传目标频道（STAGE4）
  tg_fix_truncated_links.py   RECONSTRUCTED  修复被旧正则截断的 mediafire 链接
  res_step.sh                 RECONSTRUCTED  四阶段单步状态机（外层循环反复调用）
docs/
  STATE-SCHEMA.md             状态文件 schema 与恢复手册
```

## 铁律（血泪教训，先读这个）

### 1. 单客户端铁律 — AuthKeyDuplicatedError

StringSession 与设备会话共享**同一个 auth key**。任何时刻**只能有一个**
`TelegramClient` 进程使用它。两个并发客户端（如转发器 + 发送器并行）=
**密钥被 Telegram 永久烧毁**（`AuthKeyDuplicatedError`），出口 IP 轮换会加速触发。

- ✅ 正确：前台**顺序交替** — `fwd 一轮 → res_step 一步 → fwd 一轮`
- ❌ 错误：`fwd_loop.sh & res_pipeline.sh & wait`（这就是烧钥事故的根因）
- 烧钥后的恢复：归档 `*.burned` → `tg_relogin2.py send` 要新码 → 用户回码 →
  `tg_relogin2.py code <CODE>` → 导出 StringSession → 设备会话仅作引导

### 2. 沙箱限制

- `nohup` / `setsid` 启动的后台进程**在命令返回后被杀**（实测确认）——只能前台执行
- 单次工具调用内可用 `cmd1 & cmd2 & wait` 并行，但这正是烧钥诱因，禁止用于 TG 客户端
- 预算看门狗模式：`FWD_BUDGET=110 timeout -s KILL 130 python3 script.py`
  （软预算 110s 内自行收尾保存，130s 硬杀兜底）

### 3. 登录流程

```bash
python3 scripts/tg_relogin2.py send          # 发码 + 存 phone_code_hash
python3 scripts/tg_relogin2.py code 12345    # v2 修复：显式传 phone_code_hash
# 成功后 StringSession 写入 .secrets/tg_string_session.txt（从不打印）
```

v1 的 bug：`code` 模式没传 `phone_code_hash` → 失败表现为 `SessionPasswordNeeded`
假象。v2 从 `.secrets/tg_phone_code_hash.txt` 显式读取传入。

## 两条管线

### 管线 A — 转发（fwd）

`tg_forward_to_channel.py` 把 12 个主源（translearners 只转 media，其余全转）+
发现频道（fwd_from 头解析，hop≤2，标题关键词过滤）**服务端 copy** 到目标频道。

- `progress.json` 每批原子落盘 → 崩溃/回滚安全（last_id 游标续传）
- `FloodWaitError` → flood_until 持久化 + 降速 + 干净退出（batch 减半 / sleep 翻倍）
- 批量转发失败自动二分定位毒丸消息（`do_forward` 递归）
- 自适应节奏：连续 3 批全成 → sleep 递减至 5s 地板

### 管线 B — 资源收割（res_step 四阶段状态机）

```bash
bash scripts/res_step.sh   # 每次调用恰好一步，exit 0；外层循环反复调用直到 RES_ALL_DONE
```

```
STAGE1 fnscan    tg_dump_target_filenames.py   目标频道历史 → 去重指纹库
                 完成: state/forward/fnscan_offset.txt == '1'
STAGE2 rescan    tg_scan_file_links.py         源频道历史 → file_links.json
                 完成: rescan_done.txt 存在且运行尾无 BUDGET_OUT
STAGE3 download  tg_download_links.py          逐链接下载 → download/link_harvest/
                 完成: downloaded_this_run == 0 → dl_done.txt
STAGE4 send      tg_send_harvest.py            收割文件上传目标频道
                 完成: sent_this_round == 0 → send_done.txt
                 然后 rm 掉 rescan/dl 标记 → 循环回 STAGE2（新链接持续到达）
```

去重双保险：`books_sent.txt`（按文件名）+ `target_media_ids.json`
（sha1(fname|size|mime) 指纹，8,186+ 条）。

## 关键修复记录（重建时保留的知识）

| 问题 | 修复 |
|---|---|
| URL 截断：旧正则排除 `)` → 含括号文件名的 mediafire 链接被切 | `norm_url()`：`rstrip('.,;:')` + 仅当括号不平衡才剥尾 `)`；修复脚本按 `src/mid` 重取原消息重提取 |
| 新会话 `get_entity(username)` 失败（dialog 缓存空） | 三重回退：① 预热 `get_dialogs(limit=None)` ② 数字 id → `PeerChannel(id)`（负数剥 `-100` 前缀：`a-10**12 if a>10**12 else a`）③ 用户名回退 |
| Drive 文件夹/表单链接无单文件 id | 分类为 `drive_no_id` 合法失败，不重试 |
| MediaFire 页面过期 token | `mediafire_no_link` 合法失败（真死链） |
| 大文件 Drive 确认页 | confirm-token 重试 |

## 状态文件一览

见 [docs/STATE-SCHEMA.md](docs/STATE-SCHEMA.md)（含完整回滚恢复手册）。

## Roadmap（下次会话从这里继续）

- [ ] `tg_dl_vk.py` — VK doc 链接处理器（`vk.com/doc{uid}_{id}` 重定向下载）
- [ ] `tg_dl_mega.py` — MEGA `#!hash!key` 处理器
- [ ] 词典清单任务：24+ 条双语词典（VK 链接）下载上传（caption 用清单里的
      Edition/Pages/Size 元数据）
- [ ] 《البؤساء》双语版：英文 GDrive id `1-fBvbud5wNFzb8tPYNj-9lEzghZApYNZ` + 阿文 MEGA
- [ ] 全 Telegram 搜索：英阿词典 / 英书阿译 / 翻译教材 → 收割 → 上传
- [ ] 结束后提醒用户重新开 2FA

## 安全

- `.secrets/` 永不入库（api_id/api_hash、StringSession、phone_code_hash、GH token）
- 会话串泄露 = 账号被盗；token 泄露 = 立即 revoke
- 本仓库历史上出过仓库名 `workspace` 被删的情况——重建后第一时间推 GitHub
