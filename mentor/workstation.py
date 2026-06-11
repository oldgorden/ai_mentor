"""工作站：导师可以直接执行代码、查看结果、迭代修改"""
import os
import subprocess
import time
import json
import numpy as np
from pathlib import Path


class Workstation:
    """导师的工作站：可以直接跑代码、看结果、改代码"""
    
    def __init__(self, work_dir: Path, logger=None):
        self.work_dir = work_dir / "mentor_workbench"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        self.results_dir = self.work_dir / "results"
        self.results_dir.mkdir(exist_ok=True)
    
    def run_code(self, code: str, timeout: int = 300) -> dict:
        """执行代码，返回 {success, stdout, stderr, output_files}"""
        script_path = self.work_dir / "experiment.py"
        with open(script_path, "w") as f:
            f.write(code)
        
        try:
            result = subprocess.run(
                ["python", str(script_path)],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(self.work_dir),
            )
            output_files = list(self.work_dir.glob("*.png")) + list(self.work_dir.glob("*.npy"))
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "output_files": [str(f) for f in output_files],
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": "执行超时", "output_files": []}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e), "output_files": []}
    
    def test_thermal_params(self, thermal_mass=2.0, thermal_resistance=0.3, 
                            power_constant=0.08, resistance=0.8, 
                            t_threshold=60.0, torque=1.0, duration=10.0) -> dict:
        """快速测试热参数：给定参数，看温度变化"""
        code = f"""
import numpy as np

DT = 0.02
T_AMBIENT = 25.0
T_MAX = 100.0
THERMAL_MASS = {thermal_mass}
THERMAL_RESISTANCE = {thermal_resistance}
POWER_CONSTANT = {power_constant}
RESISTANCE = {resistance}

def torque_to_power(torque):
    return (torque / POWER_CONSTANT)**2 * RESISTANCE

def update_temp(temp, torque):
    power = torque_to_power(abs(torque))
    heat_loss = (temp - T_AMBIENT) / THERMAL_RESISTANCE
    return np.clip(temp + DT * (power - heat_loss) / THERMAL_MASS, T_AMBIENT, T_MAX)

# 恒定扭矩测试
temp = T_AMBIENT
steps = int({duration} / DT)
temps = [temp]
for i in range(steps):
    temp = update_temp(temp, {torque})
    temps.append(temp)

print(f"参数: M={thermal_mass}, R={thermal_resistance}, P={power_constant}, R_eff={resistance}")
print(f"扭矩: {torque} Nm, 时长: {duration}s")
print(f"起始温度: {{T_AMBIENT:.1f}}°C")
print(f"最终温度: {{temps[-1]:.1f}}°C")
print(f"温度变化: {{temps[-1] - T_AMBIENT:.1f}}°C")
print(f"峰值温度: {{max(temps):.1f}}°C")
print(f"达到阈值({t_threshold}°C): {{'是' if max(temps) > {t_threshold} else '否'}}")
"""
        return self.run_code(code, timeout=30)
    
    def iterate_until_works(self, base_code: str, max_attempts: int = 5) -> dict:
        """迭代修改代码直到能跑通"""
        current_code = base_code
        for attempt in range(max_attempts):
            if self.logger:
                self.logger.log(f"[工作站] 尝试 {attempt+1}/{max_attempts}")
            
            result = self.run_code(current_code)
            
            if result["success"]:
                if self.logger:
                    self.logger.log(f"[工作站] 成功！")
                return result
            
            # 如果失败，分析错误并修改代码
            error = result["stderr"][:500]
            if self.logger:
                self.logger.log(f"[工作站] 失败: {error[:100]}")
            
            # 简单修复：根据错误类型调整
            current_code = self._auto_fix(current_code, error)
        
        return result
    
    def _auto_fix(self, code: str, error: str) -> str:
        """简单的自动修复"""
        # 如果是 import error，尝试安装
        if "ModuleNotFoundError" in error:
            module = error.split("'")[1] if "'" in error else ""
            if module:
                subprocess.run(["pip", "install", module], capture_output=True)
        
        # 如果是 dimension error，尝试打印形状
        if "dimension" in error.lower() or "shape" in error.lower():
            code = "import torch; torch.autograd.set_detect_anomaly(True)\n" + code
        
        return code
    
    def save_result(self, name: str, code: str, result: dict):
        """保存实验结果"""
        save_path = self.results_dir / name
        save_path.mkdir(exist_ok=True)
        
        with open(save_path / "code.py", "w") as f:
            f.write(code)
        with open(save_path / "output.txt", "w") as f:
            f.write(result.get("stdout", ""))
        with open(save_path / "error.txt", "w") as f:
            f.write(result.get("stderr", ""))
        with open(save_path / "meta.json", "w") as f:
            json.dump({
                "success": result.get("success", False),
                "output_files": result.get("output_files", []),
            }, f)
