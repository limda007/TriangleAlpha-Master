# Findings & Decisions

## Requirements
- 审计已完成账号的平台上传/取号回写链路
- 增加退避重试，避免 429/超时后恢复过慢
- 检查轮询阶段是否存在漏补和字段兼容遗漏

## Research Findings
- `PlatformSyncer` 原本有固定 120s 上传重试，但没有指数退避；普通请求失败会继续靠固定周期恢复，不够积极也不区分限流/超时。
- `PlatformSyncer._do_poll()` 原本只在 `taken` 非空时发 `poll_done`，空轮询不会回到主线程，导致无法顺手执行漏补自检。
- `PlatformSyncer._do_poll()` 原本只认 `steam_account` 字段，而现有 `tests/test_platform_client.py` 已表明平台查询结果可能使用 `username` 字段。
- 连续认证失败 3 次后自动暂停同步的保护仍然存在，这属于显式停机保护，不是遗漏。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 平台同步增加单独的 `_backoff_timer` | 退避与原有固定轮询/固定上传重试分层，避免互相覆盖 |
| 退避触发后先查本地 `已完成未上传`，再轮询平台 | 用户最关心的是已完成账号别滞留 |
| 轮询用户名提取做多字段兼容并去重 | 降低平台回包字段变动带来的漏同步风险 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| `AGENTS.md` 文件不在仓库根目录 | 使用用户消息中提供的项目级指令 |

## Resources
- `src/master/app/core/platform_syncer.py`
- `src/master/app/core/platform_client.py`
- `tests/test_master_fixes.py`
- `tests/test_platform_client.py`
- Context7: PyQt6 `QTimer` 与 HTTPX 异常/超时文档

## Visual/Browser Findings
- 用户要求补“退避重试”，并在“检查已提取”阶段如果没有结果时做漏补。
