"""
lib/ — 底层实现库（从 ai_scientist/ 迁移而来）

被 agents/tools/ 调用，提供 LLM 调用、论文撰写、文献搜索、代码执行等基础能力。

文件说明:
    llm.py              LLM 调用封装（get_response_from_llm 等，委托 api/）
    vlm.py              VLM 调用封装（get_response_from_vlm 等，委托 api/）
    token_tracker.py    Token 计数和费用追踪
    base_tool.py        旧版 Tool 基类（semantic_scholar 使用）
    semantic_scholar.py 论文搜索（Semantic Scholar API）
    interpreter.py      Python 沙盒执行器（多进程隔离，超时控制）
    llm_review.py       LLM 审稿（论文评分和建议）
    vlm_review.py       VLM 审稿（图表与标题一致性审查）
    writeup.py          LaTeX 论文撰写
    plotting.py         科学图表生成
    icbinb_writeup.py   ICBINB 格式论文撰写
"""
