#!/usr/bin/env python3
"""导师团队入口"""
import argparse
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from mentor.mentor import MentorTeam


def _daemonize_if_needed():
    """如果 stdin 连着终端（即被交互 shell 直接拉起），自动 re-exec 脱离会话"""
    if os.environ.get("_MENTOR_DAEMONIZED"):
        return
    if not sys.stdin.isatty():
        return

    env = os.environ.copy()
    env["_MENTOR_DAEMONIZED"] = "1"

    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / "mentor_terminal.log"

    stdin = open(os.devnull, "r")
    stdout = open(log_path, "a")
    stderr = open(log_path, "a")

    os.dup2(stdin.fileno(), 0)
    os.dup2(stdout.fileno(), 1)
    os.dup2(stderr.fileno(), 2)

    os.setsid()

    sys.argv = [sys.argv[0], "--config", _resolve_config()]
    if "--start-only" in sys.argv:
        sys.argv.append("--start-only")

    os.execvpe(sys.executable, [sys.executable] + sys.argv, env)


def _resolve_config():
    """优先使用 config.local.json（真实密钥），不存在则 fallback 到 config.json（模板）"""
    base = Path(__file__).parent
    local_cfg = base / "config.local.json"
    default_cfg = base / "config.json"
    if local_cfg.exists():
        return str(local_cfg)
    return str(default_cfg)


def main():
    _daemonize_if_needed()

    default_config = _resolve_config()
    parser = argparse.ArgumentParser(description="AI Mentor Team")
    parser.add_argument("--config", type=str, default=default_config)
    parser.add_argument("--start-only", action="store_true")
    args = parser.parse_args()

    team = MentorTeam(args.config)

    signal.signal(signal.SIGINT, lambda s, f: (team.stop_all(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda s, f: (team.stop_all(), sys.exit(0)))

    team.start_all()

    if args.start_only:
        print("Agent 已启动")
        return

    team.monitor_loop()


if __name__ == "__main__":
    main()
