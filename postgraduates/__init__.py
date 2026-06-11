"""
postgraduates/ — 研究生（干脏活累活的 AI Agent）

研究生是 Group 中的 Member，和导师一样在 group_member.json 中配置。
区别在于：
    - 导师(member_type="mentor")：出脑力，指导、审稿、决策
    - 研究生(member_type="postgrad")：干脏活，做实验、写论文、搜文献

目录说明:
    prompts/        研究生角色 prompt 模板
                      researcher.md     实验研究生（做实验、调参、跑代码）
                      writer.md         写作研究生（写论文、生成图表）
                      reviewer.md       自审研究生（自查论文质量）

配置方式:
    在 group_member.json 的 members 数组中添加:
    {
        "name": "postgrad_researcher_1",
        "role": "实验研究生",
        "member_type": "postgrad",
        "permissions": ["experiment:*", "literature:*", "writing:*", "journal:*"],
        "confidence": 0.7,
        "model": "custom/mimo-v2.5-pro",
        "prompt_file": "researcher.md"
    }
"""
