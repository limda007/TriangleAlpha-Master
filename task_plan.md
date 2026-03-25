# Task Plan: 补强已完成账号平台同步的退避重试与漏补

## Goal
为已完成账号的平台同步链路补上退避重试、自检漏补和取号字段兼容，避免请求限制/超时后恢复过慢，以及轮询空结果或字段差异导致本地状态遗漏更新。

## Current Phase
Phase 5

## Phases
### Phase 1: Requirements & Discovery
- [x] Understand user intent
- [x] Identify constraints and requirements
- [x] Document findings in findings.md
- **Status:** complete

### Phase 2: Planning & Structure
- [x] Define technical approach
- [x] Create/adjust tests first
- [x] Document decisions with rationale
- **Status:** complete

### Phase 3: Implementation
- [x] Add backoff retry and self-check for completed-account sync
- [x] Keep current upload/poll flow backward compatible
- [x] Test incrementally
- **Status:** complete

### Phase 4: Testing & Verification
- [x] Run targeted pytest cases
- [x] Run ruff on touched Python files
- [x] Run mypy on touched Python files
- **Status:** complete

### Phase 5: Delivery
- [x] Review modified files
- [x] Summarize risks and next steps
- [x] Deliver to user
- **Status:** complete

## Key Questions
1. 平台 429/超时/普通请求失败后，是否存在“后续不再同步”的路径？
2. 平台轮询返回空列表或不同字段名时，主控是否会漏掉自检与已取号回写？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 非认证类错误统一进入指数退避重试 | 平台限流、超时、瞬时网络故障都需要自动恢复 |
| 退避自检优先补传未上传完成账号，其次轮询取号状态 | 优先解决“已完成但没同步出去”的滞留问题 |
| 轮询字段兼容 `steam_account / username / account` | 平台回包字段变化时仍能回写本地“已取号” |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| 仓库根目录未找到额外 `AGENTS.md` | 1 | 继续按用户直接提供的项目说明执行 |
| 平台轮询空结果时不会回到主线程 | 1 | 改为 `poll_done([])` 也发射，让主线程执行漏补自检 |

## Notes
- 认证连续失败 3 次自动暂停的保护逻辑保持不变
- 修改 Python 后必须跑 `ruff` 与 `mypy`
