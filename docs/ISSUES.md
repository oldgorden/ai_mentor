# ai_mentor 框架已知问题

> 最近一次审计: 2026-06-10
> 修复进度: 35/40 已修复 (截至 2026-06-10)

---

## Critical (5) — 全部已修复 ✅

### C1. `PostgradState` 未定义 — import 即崩 ✅
- **文件**: `agents/context.py` (新增 `PostgradState` dataclass)

### C2. `ctx.get_watcher()` 方法不存在 ✅
- **文件**: `agents/context.py` (新增 `get_watcher()` / `set_watcher()`)

### C3. 实验锁 TOCTOU 竞态条件 ✅
- **文件**: `agents/experiment_lock.py` (改用 `O_CREAT | O_EXCL` 原子创建)
- **现象**: `acquire_lock()` 先 `is_locked()` 再写文件，两步之间非原子
- **影响**: 两个并发调用可能同时通过检查，都获取锁
- **修复**: 用 `os.open()` + `O_EXCL | O_CREAT` 原子创建

### C4. 同步 IO 堵塞事件循环 ✅
- **文件**: `agents/tools/writing.py`, `agents/tools/process.py` (改用 `asyncio.create_subprocess_exec`)

### C5. `shared_state.json` 写入不原子 ✅
- **文件**: `agents/context.py` (改用 tempfile + `os.replace` 原子写入)

---

## Medium (20) — 18 已修复, 2 遗留

### M1. Token 追踪基本无效 ✅
- **修复**: `api/registry.py` 的 `call_completion` 中自动调用 `token_tracker.add_tokens`

### M2. `tiktoken` 导入未使用 ✅
- **修复**: 已删除

### M3. `experiment.py` 未使用的导入 ✅
- **修复**: 已删除 `json`, `traceback`, `subprocess`

### M3. `experiment.py` 未使用的导入 ✅
- **修复**: 已删除 `json`, `traceback`, `subprocess`

### M4. `WriteExperimentCode` 硬编码绝对路径 ✅
- **修复**: 改为 `<ROOT>` 占位符，execute 时动态替换

### M5. `.venv` 路径硬编码 ✅
- **修复**: 改为 `sys.executable`

### M6. EventBus 吞掉所有 handler 异常 ✅
- **修复**: 改为 `logging.exception()` 记录

### M7. `EventBus.stop()` 无法终止迭代 ✅
- **修复**: 新增 `stop()` 方法发布 sentinel 解除 `__aiter__` 阻塞

### M8. `route_event` fallback 逻辑错误 ✅
- **修复**: 显式 fallback 到 `chief_mentor` 并打印警告

### M9. `_fake_assign_action` 死代码 ✅
- **修复**: 已删除

### M10. `prepare_vlm_prompt` 是空函数 ✅
- **修复**: 已删除

### M11. `AssignTask` 参数名 `description_field` 误导 ✅
- **修复**: description 中添加显式提示参数名必须用 `description_field`

### M12. 工具命名不一致 ✅ (部分)
- **修复**: 给所有工具补上显式 `name` 属性。PascalCase 名称因 LLM prompt 引用暂不改动

### M13. `RunShell` 和 `RunExperiment` 三次 decode ✅
- **修复**: decode 一次存变量

### M14. `_log_action` 无 IO 异常处理 ✅
- **修复**: 加 try/except

### M15. `experiment_failed` 事件未注册路由 ✅
- **修复**: 显式添加到 routing 表

### M16. `_summarize_chief_research` 直接访问私有 `_msg_history` ⏳
- **现象**: 破坏封装，需在 Member 上提供公开接口
- **优先级**: 低，暂不影响功能

### M17. `WriteKB` 无增长上限 ✅
- **修复**: 加 `MAX_KB_ENTRIES=200` 上限 + 按重要性淘汰

### M18. `AnalyzeCode` 是空壳工具 ✅
- **修复**: 已删除

### M19. `CompressContext` 是空壳工具 ✅
- **修复**: 已删除

### M20. 零测试
- **优先级**: 中，需要后续补充

---

## Low (15) — 10 已修复, 5 遗留

### L1. `extract_content` fallback 无安全检查 ✅
- **修复**: 加 try/except IndexError

### L2. `NativeOpenAIProvider.handles()` 子串匹配过宽
- **优先级**: 极低，实际不会误匹配

### L3. `_is_text_file` 用 `errors="ignore"` 读文件
- **优先级**: 极低

### L4. `get_messages()` 消费式读取，无文档说明
- **优先级**: 低，但需在 docstring 中说明

### L5. `_msg_history` 被外部直接读写 ⏳
- **现象**: `group.py` 多处直接 `chief._msg_history = []`
- **修复**: 需在 Member 上提供 `clear_history()` / `get_history()` 方法

### L6. `os.killpg` 无 SIGKILL 兜底 ✅
- **修复**: SIGTERM 后超时发 SIGKILL

### L7. `token_tracker` sync_wrapper 元组检查不可达 ✅
- **修复**: 已删除死代码

### L8. `logging.info` 参数格式错误 ✅
- **修复**: 改为 `logging.info("args: %s", args)`

### L9. 硬编码模型名散落各处 ✅ (部分)
- **修复**: 工具中的 `.venv` 路径已改 `sys.executable`，`experiment.py` 用 `<ROOT>` 占位符
- **遗留**: `context.py` 默认模型、`run_agent.py` 默认模型仍硬编码（合理默认值）

### L10. `extract_json_between_markers` 重复定义 ✅
- **修复**: 删除 `vlm.py` 中的副本

### L11. `_html_to_text` regex fallback 不处理 HTML 实体
- **优先级**: 极低

### L12. `SearchCode` 同步文件 IO 堵塞事件循环
- **优先级**: 低，通常搜索文件不大

### L13. `WritePaper`/`GeneratePlots` 立即返回成功
- **优先级**: 低，子进程管理模式设计如此

### L14. `Member.init_client()` 失败后无限重试 ✅
- **修复**: 加 try/except，失败返回 None 而非异常

### L15. `AnthropicProvider` 忽略 `n` 参数 ✅
- **修复**: 加 warning 日志提醒
