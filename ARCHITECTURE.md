# AI Mentor — 架构文档

面向接手本项目的开发者。读完本文档应能理解所有设计决策和代码组织。

## 1. 设计哲学

**一句话**：导师系统直接管理实验全流程，每个关键节点由导师审查决策。

### 核心决策记录

| 决策 | 原因 |
|------|------|
| 砍掉 BFTS 框架 | BFTS 的 62 个决策点中 18 个无验证，LLM 生成代码不可控 |
| 事件驱动（asyncio） | 不轮询，响应快，天然支持多成员并发 |
| 所有行为 = Tool 调用 | AI 作为决策者只调工具，工具负责副作用 |
| 三层 prompt 分离 | 角色 prompt 永久不变，工具参考加工具时改，项目上下文换项目时改 |
| 数据层抽象（experiment_env） | LLM 生成的代码 import 它，永远不可能加载错误数据 |
| 实验目录锁 | 防止 RunExperiment 执行时其他成员 edit_file 改代码 |
| 工具结果摘要 | 防止大返回值（24K LaTeX）污染对话历史 |
| 方法论重申 | 防止长对话中 LLM 忘记科学严谨性要求 |

## 2. 核心流程

### 2.1 初始化

```
run_agent.py
  → build_group(group_member.json)
    → Group.__init__: 创建 EventBus, TaskQueue, SharedContext
    → 遍历 members: Member.__init__(加载 prompt + tools_reference + project_context)
    → 遍历 ALL_TOOLS: group.register_tool(tool)
  → group.run()
    → 加载 ideas_file → 加载 shared_state
    → 初始化所有成员的 LLM client
    → 进入事件循环
```

### 2.2 事件循环

```python
# group.py:run() 简化版
async for event in event_bus:
    if event.type == "start_experiment":
        # 创建实验目录 → run_experiment_loop(chief)
    elif event.type == "improve_paper":
        # run_improve_loop(chief)
    else:
        # route_event → member.decide → execute_action
```

### 2.3 成员决策

```python
# member.py:decide() 简化版
messages = [
    {"role": "system", "content": system_prompt},   # 角色 + 工具参考 + 项目上下文
    *msg_history,                                     # 历史（摘要化）
    {"role": "user", "content": user_msg},            # 当前状态 + 事件 + 可用工具
]
response = llm.call(messages)
action = parse_action(response)  # {"thought": "...", "tool": "xxx", "params": {...}}
```

### 2.4 任务分配流程

```
chief.decide() → Action(assign_task, {assign_to: "code_mentor", ...})
  → group.execute_action()
    → AssignTask.execute()
      → task_queue.submit(task, "code_mentor")  # 放入 asyncio.Queue
```

被分配的成员通过 `task_queue.next_task()` 取任务。

## 3. 文件职责

### agents/group.py — 团队地基

- 管理成员、工具、事件路由、任务队列、实验锁
- `run()`: 主事件循环
- `run_experiment_loop()`: 新实验的多步迭代循环
- `run_improve_loop()`: 论文改进的多步迭代循环
- `execute_action()`: 执行工具 + 记录决策 + 注入结果
- `consensus()`: 低置信度时请求 chief 批准

### agents/member.py — AI 决策者

- `__init__()`: 加载三层 prompt（角色 + 工具参考 + 项目上下文）
- `decide()`: 调用 LLM 返回 Action
- `inject_tool_result()`: 将工具执行结果注入历史（自动摘要化）
- `_trim_history()`: 双重截断（条数 + 字符总数）
- `_summarize_tool_data()`: 大结果自动压缩

### agents/event.py — 异步事件总线

- `EventBus`: async iterator，`publish()` / `__aiter__`
- `Event`: type + data dict

### agents/context.py — 共享状态

- `SharedContext`: member_decisions、messages、ideas、config
- `summary_for_prompt()`: 为 LLM 生成状态摘要

### agents/task_queue.py — 任务队列

- `TaskQueue`: dict[str, asyncio.Queue]
- `Task` / `TaskResult` 数据类
- `submit(task, member_name)` / `next_task(member_name)`

### agents/experiment_lock.py — 竞态锁

- `acquire_lock(exp_dir)` / `release_lock(exp_dir)` / `is_locked(exp_dir)`
- 用 `.experiment_running` 文件存 PID，检查进程存活自动清理

### agents/tool.py — 工具基类

- `Tool`: name / description / parameters / permission / confidence_required
- `ToolResult`: success / data / error
- `get_schema()`: 返回 JSON Schema（LLM function calling 格式）

### agents/tools/ — 31 个工具

| 文件 | 工具 | 职责 |
|------|------|------|
| basic.py | ReadFile, SearchCode, ListFiles, EditFile, RunShell, WebFetch | 文件/代码/Shell 操作 |
| basic.py | AssignTask, CheckTaskResults | 任务管理（chief 专用） |
| experiment.py | WriteExperimentCode, RunExperiment, ValidateResults, ReadExperimentOutput | 实验全流程 |
| improve.py | CritiquePaper | 论文改进（读取旧论文上下文） |
| research.py | SearchPapers, RunCode, AnalyzeCode | 研究辅助 |
| literature.py | SearchLiterature, GetPaperDetails | 文献搜索 |
| writing.py | WritePaper, GeneratePlots | 论文写作 |
| review.py | ReviewPaper, VisualReview | 审稿 |
| journal.py | ReadJournal, SummarizeLogs | 实验日志 |
| knowledge.py | ReadKB, WriteKB, CompressContext | 知识库 |
| project.py | CreateProject, ListProjects, UpdateProject, ScanExperiments | 项目管理 |

## 4. 数据流

```
lib/experiment_env.py（数据层抽象）
  ↑ import
实验代码（runfile.py，由 code_mentor 用 WriteExperimentCode 写入）
  ↑ 执行
RunExperiment（异步子进程，自动注入 PYTHONPATH，加锁）
  ↑ stdout/stderr
ReadExperimentOutput → chief 审查指标
```

## 5. Prompt 架构

每个成员的 system prompt 由三部分自动拼接：

```
角色 prompt（agents/prompts/chief.md）
  + "\n\n---\n\n# 工具参考手册\n\n" + tools_reference.md
  + "\n\n---\n\n# 项目上下文\n\n" + data/project_context.md
```

### 拼接时机

`Member.__init__()` 时一次性读取并拼接，之后不再读取文件。

### 为什么不运行时改

prompt 文件在 `__init__` 时读入 `_system_prompt` 字符串，运行中无任何代码写回磁盘。即使文件被外部修改，当前运行的实例也不会受影响。

## 6. 运行时保护

### 6.1 工具结果摘要

`inject_tool_result()` 调用 `_summarize_tool_data()`：
- 字符串 > 2000 chars → 截断 + 标注 "[truncated, N total chars]"
- 列表过大 → 只保留前 5 项
- 字典过大 → 只保留前 10 个 key 的摘要

### 6.2 Token 预算管理

`_trim_history()` 双重保护：
- 条数上限：MAX_HISTORY_ROUNDS * 2 = 40 条消息
- 字符上限：MAX_HISTORY_CHARS = 80000 字符
- 超限时从头部丢弃最旧消息（FIFO）

### 6.3 方法论重申

`decide()` 中，每 5 轮（METHODOLOGY_REMINDER_INTERVAL）在 user_msg 末尾注入：
> ⚠️ 方法学提醒：遵守科学严谨性——基线对比、控制变量、统计显著性、消融实验、如实报告。

## 7. 事件路由

| 事件类型 | 路由到 | 触发场景 |
|----------|--------|---------|
| start_experiment | chief_mentor | `run_agent.py` 默认模式 |
| improve_paper | chief_mentor | `run_agent.py --improve` |
| experiment_results_ready | chief_mentor | RunExperiment 成功后 |
| experiment_retry | chief_mentor | 工具执行失败 |
| experiment_success | chief_mentor | 实验循环正常退出 |
| code_review | code_mentor | （预留） |
| logic_check | reasoning_mentor | （预留） |
| literature_search | multimodal_mentor | （预留） |

## 8. API 路由

模型名前缀决定走哪个 provider：

| 前缀 | Provider | 示例 |
|------|----------|------|
| `opencode-go/` | OpenAI 兼容（OpenRouter） | kimi-k2.6, deepseek-v4-pro |
| `custom/` | 自建 OpenAI 兼容（Mimo） | mimo-v2.5-pro, mimo-v2.5 |

配置在 `mentor/config.local.json` 的 `providers` 字段中。

## 9. 扩展指南

### 添加新成员
编辑 `group_member.json`，在 `members` 数组中加一项。prompt 放 `agents/prompts/`（导师）或 `postgraduates/prompts/`（研究生）。

### 添加新工具
1. `agents/tools/xxx.py` → 继承 `Tool`
2. `agents/tools/__init__.py` → 加入 `ALL_TOOLS`
3. `agents/prompts/tools_reference.md` → 加文档

### 换项目
1. 改 `data/project_context.md`（数据 API + 数据集 + 路径）
2. 如果需要不同模型，改 `group_member.json`
3. prompt 文件不需要改

### 添加新 LLM Provider
1. `api/providers/xxx.py` → 继承 `BaseProvider`
2. `api/providers/__init__.py` → 注册
3. `mentor/config.local.json` → 加凭证
