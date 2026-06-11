# 工具参考手册

本文件列出所有可用工具。每个成员只能看到自己有权限的工具（系统自动过滤）。

## 代码与文件操作

### read_file
读取任意文件内容。支持 offset/limit 分页。
- 参数: path (必填), offset, limit
- 用法: 读源码定位 bug、读日志查错误、读配置确认参数

### edit_file
用新文本替换文件中的指定旧文本（类似 sed）。old_text 必须在文件中唯一出现。
- 参数: path (必填), old_text (必填), new_text (必填)
- 用法: 修 bug、调参数、改一行代码。实验目录被锁定时拒绝执行。

### search_code
在代码库中搜索文本（类似 grep），跳过 .git/__pycache__/.venv/node_modules。
- 参数: pattern (必填), path, include, max_results
- 用法: 找函数定义、找报错位置、找 import 来源

### list_files
列出目录内容或按文件名搜索（类似 ls / find）。
- 参数: path, pattern, recursive
- 用法: 浏览项目结构、找某类文件、查看实验目录内容

### run_shell
执行 shell 命令。**有副作用，谨慎使用**。
- 参数: command (必填), timeout
- 用法: 查 GPU/磁盘/进程、安装包、运行脚本

### run_code
在沙盒中执行 Python 代码并返回结果。
- 参数: code (必填), timeout
- 用法: 快速验证想法、测试函数、调试小片段

### web_fetch
抓取网页内容，转为纯文本返回（aiohttp + BeautifulSoup）。
- 参数: url (必填), max_length
- 用法: 查文档、读 arxiv 摘要、看 GitHub README

## 实验工具

### WriteExperimentCode
将实验代码写入文件（整文件覆盖）。实验目录被锁定时拒绝执行。
- 参数: experiment_dir (必填), code (必填), filename (默认 runfile.py), stage
- 代码必须 sys.path.insert（路径见项目上下文），通过数据层加载数据
- 用法: 生成新实验代码、重写有 bug 的代码

### RunExperiment
执行实验代码，返回 stdout/stderr/执行时间/返回码。执行时自动加锁，结束后释放。
- 参数: experiment_dir (必填), filename (默认 runfile.py), timeout (默认 3600s)
- 自动设置 PYTHONPATH
- 用法: 执行实验查看训练结果

### ReadExperimentOutput
读取 experiment.log 的尾部内容。
- 参数: experiment_dir (必填), max_lines (默认 100)
- 用法: 查看训练 loss、确认最终指标、诊断执行问题

### ValidateResults
验证实验代码质量（静态检查）。
- 参数: experiment_dir (必填), checks (可选列表: "uses_real_data", "no_old_refs", "has_output_dir")
- 用法: 执行前审查代码，防止数据泄露或过拟合

## 任务管理（chief 专用）

### assign_task
给团队成员分配任务。
- 参数: assign_to (必填), task_type (必填), description_field (必填), params, experiment_dir
- assign_to 可选: code_mentor / researcher_postgrad / reasoning_mentor / multimodal_mentor / writer_postgrad / reviewer_postgrad
- task_type: code_task / run_task / review_task / literature_task / writeup_task / review_paper

### check_task_results
查看已完成的任务结果。
- 参数: member_name, n (默认 5)

## 知识与日志

### read_journal
读取实验目录的 journal.json。
- 参数: experiment_dir (必填), include_buggy, limit

### summarize_logs
压缩实验日志为摘要。
- 参数: experiment_dir (必填)

### read_kb
读取知识库内容。
- 参数: kb_type (permanent/temporary/postgrad), postgrad_name, category, limit

### write_kb
向知识库写入一条记录。
- 参数: kb_type, postgrad_name, category, content (必填), importance

### compress_context
压缩过大的上下文。
- 参数: target, postgrad_name

### analyze_code
用 LLM 分析代码，给出改进建议。
- 参数: code (必填), focus

## 文献搜索

### search_papers / search_literature
搜索学术论文（Semantic Scholar），返回标题、作者、摘要、引用数。
- 参数: query (必填), max_results (默认 10)

### get_paper_details
获取论文的引用格式。
- 参数: query (必填)

## 论文与审稿

### write_paper
为实验生成完整 LaTeX 论文。
- 参数: exp_dir (必填), model

### generate_plots
为实验生成聚合图表。
- 参数: exp_dir (必填), model

### review_paper
用 LLM 审稿，返回评分和建议。
- 参数: exp_dir (必填), model, num_reviewers

### visual_review
用 VLM 审查论文中的图表（图片与标题一致性）。
- 参数: exp_dir (必填), model

## 项目管理

### CreateProject
创建研究项目。
- 参数: name (必填), title (必填), goal (必填), idea_file, target_venue, page_limit, tags

### ListProjects
列出所有项目。
- 参数: status

### UpdateProject
更新项目信息。
- 参数: name (必填), status, notes, goal, add_review

### ScanExperiments
扫描实验目录并关联到项目。
- 参数: name (必填)

## 论文改进

### critique_paper
读取旧实验目录，提取论文 LaTeX、实验代码、结果日志、图表列表，返回结构化上下文。
- 参数: experiment_dir (必填), max_code_lines (默认100)
- 返回: paper_latex, idea_header, figures, runs, logged_runs, experiment_log_tail, from_bfts, cached_citations
- 用法: 论文改进模式的第一步，先读取旧论文再分析弱点
