# 提示词体系

## 目录结构

```
agents/prompts/
  permanent/                跨项目不变的角色定义
    chief.md                   大导师
    code.md                    代码导师
    reasoning.md               推理导师
    multimodal.md              多模态导师
    tools_reference.md         工具手册（全员共享）
  temporary/                项目相关上下文（换项目改这里）
    project_info.md                          → 全员
    code,researcher.data_interface.md         → code + researcher
    code,researcher,reviewer.experiment_constraints.md → code + researcher + reviewer

postgraduates/prompts/     研究生角色定义（也是永久的）
  researcher.md
  writer.md
  reviewer.md
```

## 加载流程

`Member.__init__()` (`agents/member.py:72`) 按顺序拼接 system prompt：

1. **角色 prompt**：`permanent/{prompt_file}` 或 `postgraduates/prompts/{prompt_file}`
2. **工具手册**：`permanent/tools_reference.md`
3. **临时文件**：`temporary/*.md`，按文件名排序，按角色前缀过滤

## 临时文件命名规则

| 文件名 | 可见角色 |
|--------|---------|
| `project_info.md` | 全员（无前缀） |
| `code.data_interface.md` | 仅 `code_mentor` |
| `code,researcher.data_interface.md` | `code_mentor` + `researcher_postgrad` |

前缀取成员 `name` 的第一段（`_` 分割）：
- `code_mentor` → `code`
- `researcher_postgrad` → `researcher`
- `chief_mentor` → `chief`

## 管理责任

- **chief_mentor** 负责在新项目启动时更新 `temporary/` 下的文件
- `permanent/` 下的文件跨项目不变，除非修改角色定义
- 新增临时文件只需放入 `temporary/` 目录，按命名规则加前缀

## 换项目清单

1. 更新 `temporary/project_info.md`：项目根目录、Python 路径
2. 更新 `temporary/code,researcher.data_interface.md`：新数据集 API、格式
3. 更新 `temporary/code,researcher,reviewer.experiment_constraints.md`：实验规则
4. 如需角色专属上下文，新增带前缀的 .md 文件
