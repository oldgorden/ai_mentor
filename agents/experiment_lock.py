"""
ExperimentLock — 实验目录锁

防止竞态条件：
- RunExperiment 执行时，实验目录被锁定，WriteExperimentCode/EditFile 被拒绝
- 锁通过 .running 文件实现，进程退出后自动清理（检查 PID 是否存活）
"""
import os
import time


LOCK_FILE = ".experiment_running"


def acquire_lock(experiment_dir: str, pid: int = None) -> bool:
    if is_locked(experiment_dir):
        return False
    lock_path = os.path.join(experiment_dir, LOCK_FILE)
    pid = pid or os.getpid()
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(fd, f"{pid}\n{time.time()}".encode())
    finally:
        os.close(fd)
    return True


def release_lock(experiment_dir: str):
    lock_path = os.path.join(experiment_dir, LOCK_FILE)
    if os.path.exists(lock_path):
        os.remove(lock_path)


def is_locked(experiment_dir: str) -> bool:
    lock_path = os.path.join(experiment_dir, LOCK_FILE)
    if not os.path.exists(lock_path):
        return False
    try:
        with open(lock_path) as f:
            parts = f.read().strip().split("\n")
        pid = int(parts[0])
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            release_lock(experiment_dir)
            return False
    except Exception:
        return False
