"""
postgraduates/prompts/ — 研究生角色 prompt 模板

每个研究生 Member 通过 prompt_file 字段指定自己的行为规则。
Member 初始化时从此目录加载 .md 文件作为 system prompt。

文件:
    researcher.md     实验研究生：做实验、调参、跑代码、分析结果
    writer.md         写作研究生：撰写论文、生成图表、排版
    reviewer.md       自审研究生：审查论文质量、检查图表一致性

自定义:
    1. 在此目录下新建 .md 文件
    2. 在 group_member.json 的 members[].prompt_file 中引用文件名
    3. member_type 设为 "postgrad"
"""
