#!/usr/bin/env python3
"""手动生成改进论文"""
import sys
import os
import json

sys.path.insert(0, '/home/lk/ai_mentor')
from lib.llm import create_client, get_response_from_llm

EXPERIMENT_DIR = "experiments/2026-06-11_20-23-43_improve_2026-06-08_01-31-13_semantic_filtering_slam_attempt_0"

def load_results():
    with open(os.path.join(EXPERIMENT_DIR, "results.json")) as f:
        return json.load(f)

def load_code():
    with open(os.path.join(EXPERIMENT_DIR, "runfile.py")) as f:
        return f.read()

def generate_paper():
    results = load_results()
    code = load_code()
    
    prompt = f"""你是一个学术论文写作专家。请基于以下实验结果和代码，生成一篇有学术价值的论文。

## 实验结果
```json
{json.dumps(results, indent=2)}
```

## 实验代码
```python
{code[:5000]}  # 只取前5000字符
```

## 要求
1. 论文主题：语义分割在动态物体检测中的应用研究
2. 使用Cityscapes数据集
3. 比较LightweightUNet、ResUNet、PIDNetSmall三种模型
4. 包含消融实验（AblationNoBN）
5. 论文格式：ICML 2025格式
6. 包含以下部分：
   - Abstract
   - Introduction
   - Related Work
   - Method
   - Experiments
   - Results
   - Discussion
   - Conclusion
7. 论文应该有学术价值，能够发表在学术会议上
8. 使用中文撰写

请生成完整的LaTeX代码。"""

    client, model = create_client("custom/mimo-v2.5-pro")
    response, _ = get_response_from_llm(
        prompt=prompt,
        client=client,
        model=model,
        system_message="你是一个学术论文写作专家。请生成有学术价值的论文。",
        temperature=0.7,
        max_tokens=50000,
    )
    
    # 保存论文
    paper_path = os.path.join(EXPERIMENT_DIR, "latex", "improved_paper.tex")
    os.makedirs(os.path.dirname(paper_path), exist_ok=True)
    
    # 提取LaTeX代码
    if "```latex" in response:
        latex = response.split("```latex")[1].split("```")[0]
    elif "\\documentclass" in response:
        latex = response
    else:
        latex = response
    
    with open(paper_path, "w") as f:
        f.write(latex)
    
    print(f"论文已生成：{paper_path}")
    return paper_path

if __name__ == "__main__":
    generate_paper()
