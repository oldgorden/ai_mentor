"""学生监控器：导师审查学生方案的合理性"""
import os
import subprocess
import time
import json
import re
from pathlib import Path


class StudentMonitor:
    """导师审查学生的方案是否合理"""
    
    def __init__(self, workstation, logger=None):
        self.workstation = workstation
        self.logger = logger
    
    def review_approach(self, code: str, history_summary: str, mentor=None) -> dict:
        """导师深度审查方案（带推理链）"""
        if not mentor:
            return {"approved": True, "reason": "无导师，跳过审查"}
        
        prompt = f"""你是实验导师。学生提交了代码方案，请**逐步推理**后判断是否合理。

实验历史：
{history_summary}

学生代码（前2000字）：
{code[:2000]}

请按以下步骤思考：

**Step 1: 理解实验目标**
- 这个实验要验证什么假设？
- 成功的标准是什么？

**Step 2: 分析学生的方法**
- 学生用了什么方法？（RL？行为克隆？其他？）
- 这个方法的原理是什么？

**Step 3: 推理方法是否可行**
- 这个方法能达到实验目标吗？为什么？
- 有没有反例或边界情况？
- 参数设置是否合理？

**Step 4: 结论**
- APPROVED: true/false
- REASON: 原因
- FIX: 如果有问题，具体修改建议"""

        try:
            response = mentor._ask(prompt, max_tokens=32000, deep_think=True)
            
            # 默认通过，除非明确说不通过
            response_lower = response.lower()
            approved = True  # 默认通过
            
            # 只有明确说 "approved: false" 或 "不通过" 才判为不通过
            if "approved: false" in response_lower or "不通过" in response_lower:
                approved = False
            # 如果说 "approved: true" 或 "通过"，肯定通过
            if "approved: true" in response_lower or "通过" in response_lower:
                approved = True
            
            reason_match = re.search(r"REASON:\s*(.+?)(?:\n|$)", response)
            fix_match = re.search(r"FIX:\s*(.+?)(?:\n|$)", response, re.DOTALL)
            
            return {
                "approved": approved,
                "reason": reason_match.group(1).strip() if reason_match else "审查通过",
                "fix": fix_match.group(1).strip() if fix_match else "",
                "reasoning": response,
            }
        except Exception as e:
            return {"approved": True, "reason": f"审查失败: {e}", "fix": "", "reasoning": ""}
    
    def check_output(self, stdout: str) -> list[dict]:
        """检查运行输出 — 只在结果明确有问题时干预"""
        issues = []
        
        # 检查 TAL 和 Standard 是否一样
        if "TAL" in stdout and "Standard" in stdout:
            tal_match = re.search(r"TAL.*?Peak Temperature.*?(\d+\.?\d*)", stdout)
            std_match = re.search(r"Standard.*?Peak Temperature.*?(\d+\.?\d*)", stdout)
            if tal_match and std_match:
                tal_val = float(tal_match.group(1))
                std_val = float(std_match.group(1))
                if abs(tal_val - std_val) < 1.0:
                    issues.append({
                        "type": "identical_results",
                        "reason": f"TAL ({tal_val}°C) 和 Standard ({std_val}°C) 结果一样",
                    })
        
        # 检查运行时错误
        if "Traceback" in stdout and "Error" in stdout:
            error_match = re.search(r"(\w+Error: .+)", stdout)
            error_msg = error_match.group(1) if error_match else "未知错误"
            issues.append({
                "type": "runtime_error",
                "reason": f"运行出错: {error_msg[:100]}",
            })
        
        return issues
    
    def get_guidance_from_review(self, review: dict) -> str:
        """从审查结果生成指导"""
        parts = [f"导师审查意见：{review['reason']}"]
        if review.get("fix"):
            parts.append(f"修改建议：{review['fix']}")
        return "\n".join(parts)
