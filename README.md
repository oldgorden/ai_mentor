# AI Mentor — AI 科研 Agent 框架

AI 导师团队自主完成科研全流程：提出假设 → 设计实验 → 写代码 → 跑实验 → 写论文 → 审稿。

所有导师和研究生都是 AI Agent，通过事件驱动协作，每个关键节点由导师审查决策。

## 快速开始

```bash
# 安装
pip install -r requirements.txt

# 配置 API 凭证
cp mentor/config.json mentor/config.local.json
# 编辑 mentor/config.local.json

# 方式一：从头开始实验
python agents/run_agent.py

# 方式二：改进已有论文
python agents/run_agent.py --improve experiments/2026-06-08_xxx/

# 指定团队配置
python agents/run_agent.py --group my_team.json
```

## 架构

```
用户指令（--improve / start_experiment）
  ↓
Group（团队地基）→ 事件路由到 chief_mentor
  ↓
chief 用 LLM 决策 → assign_task 分配给团队成员
  ↓
┌─────────────────────────────────────────────────────┐
│  code_mentor → 写实验代码                              │
│  researcher_postgrad → 跑实验                          │
│  reasoning_mentor → 审查实验设计                        │
│  multimodal_mentor → 搜论文 / 分析图表                  │
│  writer_postgrad → 写 LaTeX 论文                       │
│  reviewer_postgrad → 审稿                              │
└─────────────────────────────────────────────────────┘
  ↓
chief 审查结果 → 不合格则迭代 → 合格则完成
```

### 核心机制

| 机制 | 说明 |
|------|------|
| 事件驱动 | 异步事件总线（EventBus），不轮询 |
| 任务队列 | chief 通过 assign_task 分配任务，成员从 asyncio.Queue 取任务 |
| 竞态锁 | RunExperiment 执行时加锁，WriteExperimentCode/EditFile 检查锁 |
| 权限控制 | fnmatch 通配符匹配，chief 有 `*`（全部权限） |
| 置信度 | 每个 action 有 confidence，低于工具阈值时请求 consensus |

## 三层 Prompt 架构

```
┌────────────────────────────────────┐
│ ① 角色 prompt（agents/prompts/*.md）│  ← 永久：方法论、角色定义、工作流
│    chief.md / code.md / ...        │     换项目不需要改
├────────────────────────────────────┤
│ ② 工具参考（tools_reference.md）    │  ← 半永久：工具说明
│    所有工具的参数、用法、权限         │     加工具时改这一个文件
├────────────────────────────────────┤
│ ③ 项目上下文（data/project_context.md）│ ← 每个项目不同
│    数据层 API / 数据集名 / 路径       │    换项目只改这一个文件
└────────────────────────────────────┘
```

运行时自动拼接为 system prompt，注入到每个成员的 LLM 调用中。

## 运行时防污染

| 保护层 | 机制 | 值 |
|--------|------|----|
| 工具结果摘要 | `_summarize_tool_data` — 超过阈值自动截断 | 2000 字符 |
| Token 预算 | `_trim_history` — 总字符超限时丢弃最旧消息 | 80000 字符 |
| 方法论重申 | 每 N 轮在 user_msg 末尾注入提醒 | 每 5 轮 |

## 目录结构

```
agents/                        # AI Agent 核心
├── run_agent.py               #   入口（--improve / 默认模式）
├── group.py                   #   Group：团队地基（成员/工具/事件/任务队列/实验锁）
├── member.py                  #   Member：统一 AI 决策者（角色/权限/LLM 调用/历史管理）
├── event.py                   #   EventBus：异步事件总线
├── context.py                 #   SharedContext：共享状态（决策历史/消息/知识库/idea）
├── tool.py                    #   Tool 基类 + ToolResult
├── task_queue.py              #   TaskQueue：任务分配系统
├── experiment_lock.py         #   ExperimentLock：实验目录竞态锁
├── tools/                     #   31 个工具实现
│   ├── basic.py               #     ReadFile / SearchCode / ListFiles / EditFile / RunShell / WebFetch / AssignTask / CheckTaskResults
│   ├── experiment.py          #     WriteExperimentCode / RunExperiment / ValidateResults / ReadExperimentOutput
│   ├── improve.py             #     CritiquePaper（读取旧论文上下文）
│   ├── research.py            #     SearchPapers / RunCode / AnalyzeCode
│   ├── literature.py          #     SearchLiterature / GetPaperDetails
│   ├── writing.py             #     WritePaper / GeneratePlots
│   ├── review.py              #     ReviewPaper / VisualReview
│   ├── journal.py             #     ReadJournal / SummarizeLogs
│   ├── knowledge.py           #     ReadKB / WriteKB / CompressContext
│   └── project.py             #     CreateProject / ListProjects / UpdateProject / ScanExperiments
└── prompts/                   #   角色 prompt（领域无关）
    ├── chief.md               #     大导师：统筹决策、任务分配、结果审查
    ├── code.md                #     代码导师：写代码、调试
    ├── reasoning.md           #     推理导师：逻辑分析、假设验证
    ├── multimodal.md          #     多模态导师：文献检索、图表分析
    └── tools_reference.md     #     工具参考手册（所有工具的参数和用法）

postgraduates/
└── prompts/                   #   研究生 prompt（领域无关）
    ├── researcher.md          #     实验研究生：跑实验、调参、运行时调试
    ├── writer.md              #     写作研究生：LaTeX 论文、图表
    └── reviewer.md            #     自审研究生：审稿、图表一致性

data/
├── project_context.md         #   项目上下文（数据层 API / 数据集 / 路径）← 换项目改这个
└── ideas/                     #   研究想法 JSON

api/                           #   统一 LLM API 管理
├── credentials.py             #     凭证源
├── registry.py                #     Provider 路由
└── providers/                 #     各厂家 provider

lib/                           #   底层库（被 tools 调用）
├── experiment_env.py          #     数据层抽象（get_dataloaders / list_available）
├── semantic_scholar.py        #     论文搜索
├── interpreter.py             #     Python 沙盒
├── writeup.py                 #     LaTeX 论文生成
├── plotting.py                #     图表生成
├── llm_review.py              #     LLM 审稿
├── vlm_review.py              #     VLM 审稿
└── project.py                 #     项目管理

group_member.json              #   团队配置（7 成员 + 权限 + 模型）
```

## 团队配置

编辑 `group_member.json`：

```json
{
  "group_name": "research_team",
  "members": [
    {
      "name": "chief_mentor",
      "member_type": "mentor",
      "role": "大导师，统筹所有决策",
      "permissions": ["*"],
      "model": "opencode-go/kimi-k2.6",
      "prompt_file": "chief.md"
    }
  ]
}
```

### 权限体系

| 权限模式 | 覆盖范围 |
|----------|---------|
| `*` | 全部权限（chief） |
| `experiment:*` | 实验读写 |
| `research:*` | 代码/文件/搜索 |
| `literature:*` | 文献搜索 |
| `writing:*` | 论文写作 |
| `review:*` | 审稿 |
| `journal:*` | 实验记录 |
| `knowledge:*` | 知识库 |
| `project:*` | 项目管理 |
| `task:assign` | 分配任务（chief 专用） |

### 当前成员

| 成员 | 类型 | 模型 | 权限范围 | 职责 |
|------|------|------|---------|------|
| chief_mentor | mentor | kimi-k2.6 | * | 统筹、分配任务、审查 |
| code_mentor | mentor | mimo-v2.5-pro | experiment+research+knowledge | 写代码、调试 |
| reasoning_mentor | mentor | deepseek-v4-pro | research+knowledge+review | 逻辑分析、假设验证 |
| multimodal_mentor | mentor | mimo-v2.5 | research+literature+review | 文献、图表 |
| researcher_postgrad | postgrad | deepseek-v4-pro | experiment+literature+writing | 跑实验 |
| writer_postgrad | postgrad | mimo-v2.5-pro | writing+literature | 写论文 |
| reviewer_postgrad | postgrad | mimo-v2.5 | review+literature | 审稿 |

## 两种运行模式

### 模式一：从头开始实验

```bash
python agents/run_agent.py
```

触发 `start_experiment` 事件 → chief 分配 code_task → code_mentor 写代码 → researcher 跑 → chief 审查。

### 模式二：改进已有论文

```bash
python agents/run_agent.py --improve experiments/2026-06-08_xxx/
```

触发 `improve_paper` 事件 → chief 用 `critique_paper` 读取旧论文 → 分析学术弱点 → 制定改进计划 → 分配任务 → 迭代改进。

## 添加新工具

1. 在 `agents/tools/` 下新建 `.py` 文件，继承 `Tool`
2. 定义 `name`、`description`、`parameters`、`permission`、`confidence_required`
3. 实现 `async def execute(self, ctx, **kwargs) -> ToolResult`
4. 在 `agents/tools/__init__.py` 的 `ALL_TOOLS` 中注册
5. 在 `agents/prompts/tools_reference.md` 中添加说明

## 换项目

只需改两个文件：
1. `data/project_context.md` — 数据层 API、数据集名、路径
2. `group_member.json` — 如果需要不同的模型/成员

所有 prompt 文件不需要改（领域无关）。
