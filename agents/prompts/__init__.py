"""
agents/prompts/ — 角色 prompt 模板

分为 permanent（永久）和 temporary（临时）两类：

permanent/       跨项目不变的角色定义和工具手册
    chief.md         大导师：统筹决策，处理学生退出/卡住等关键事件
    code.md          代码导师：审查代码，分析实验结果，处理 new_node 事件
    reasoning.md     推理导师：逻辑分析，假设验证
    multimodal.md    多模态导师：文献检索，图表分析
    tools_reference.md  工具使用手册（所有成员共享）

temporary/       项目相关的上下文，换项目时改这个目录
    命名规则：
        - 无前缀（如 project_info.md）     → 所有成员都能看到
        - 角色前缀（如 code.data_interface.md） → 只有该角色能看到
        - 多角色前缀用逗号（如 code,researcher.data_interface.md）
        - 前缀取 member name 的第一段（如 code_mentor → code, researcher_postgrad → researcher）

    当前文件：
        project_info.md                          → 全员：项目路径、Python 环境
        code,researcher.data_interface.md         → code+researcher：数据集 API、数据格式
        code,researcher,reviewer.experiment_constraints.md → code+researcher+reviewer：实验规则

用法:
    1. 在 group_member.json 的 members[].prompt_file 中引用 permanent/ 下的文件名
    2. Member 初始化时自动加载 permanent/ 下的角色 prompt + tools_reference.md
    3. 然后按角色过滤拼接 temporary/ 下的 .md 文件（按文件名排序）
    4. 换项目：只改 temporary/ 目录
"""
