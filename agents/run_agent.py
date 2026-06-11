#!/usr/bin/env python3
"""
AI Agent 入口

导师 + 研究生统一 Agent 框架。所有成员在 group_member.json 中配置。

用法:
    python agents/run_agent.py
    python agents/run_agent.py --group my_team.json
"""
import argparse
import asyncio
import atexit
import faulthandler
import json
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agents.event import Event, EventBus
from agents.member import Member
from agents.group import Group
from agents.context import SharedContext
from agents.tools import ALL_TOOLS


def load_json(path: str) -> dict:
    p = Path(path)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def setup_env():
    from api.credentials import load_credentials
    creds = load_credentials()
    for pname, pcfg in creds.items():
        if pname == "xfyun":
            os.environ["XFYUN_API_KEY"] = pcfg.get("api_key", "")
            os.environ["XFYUN_BASE_URL"] = pcfg.get("base_url", "")
        elif pname == "custom":
            os.environ["CUSTOM_OPENAI_API_KEY"] = pcfg.get("api_key", "")
            if pcfg.get("base_url"):
                os.environ["CUSTOM_OPENAI_BASE_URL"] = pcfg["base_url"]
        elif pname == "semantic_scholar":
            os.environ["S2_API_KEY"] = pcfg.get("api_key", "")
    os.environ["HF_ENDPOINT"] = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")


def build_group(group_cfg: dict) -> Group:
    group = Group(
        name=group_cfg.get("group_name", "research_team"),
        root=ROOT,
        config=group_cfg,
    )

    for event_type, member_name in group_cfg.get("event_routing", {}).items():
        group.set_event_routing(event_type, member_name)

    group.config["ideas_file"] = group_cfg.get("ideas_file", "")

    member_configs = group_cfg.get("members", [])
    if not member_configs:
        print("[warn] no members, using default chief_mentor")
        member_configs = [{
            "name": "chief_mentor", "role": "大导师", "member_type": "mentor",
            "permissions": ["*"], "confidence": 1.0,
            "model": "opencode-go/kimi-k2.6", "prompt_file": "chief.md"
        }]

    for mcfg in member_configs:
        member = Member(
            name=mcfg["name"],
            role=mcfg["role"],
            permissions=mcfg.get("permissions", ["experiment:read", "research:read"]),
            confidence=mcfg.get("confidence", 0.8),
            model=mcfg.get("model", "opencode-go/kimi-k2.6"),
            member_type=mcfg.get("member_type", "mentor"),
            prompt_file=mcfg.get("prompt_file", ""),
            root=ROOT,
            max_tokens=mcfg.get("max_tokens", 128000),
            temperature=mcfg.get("temperature", 0.7),
        )
        group.add_member(member)
        mtype = "导师" if member.member_type == "mentor" else "研究生"
        print(f"[setup] {mtype}: {member.name} ({member.role}) model={member.model} max_tokens={member.max_tokens} temp={member.temperature}")

    for tool in ALL_TOOLS:
        group.register_tool(tool)

    return group


async def run(group_cfg: dict, improve_dir: str = None):
    setup_env()
    group = build_group(group_cfg)

    mentors = sum(1 for m in group.members.values() if m.member_type == "mentor")
    postgrads = sum(1 for m in group.members.values() if m.member_type == "postgrad")
    print(f"[agent] group={group.name} mentors={mentors} postgrads={postgrads} tools={len(group.tools)}")

    group_task = asyncio.create_task(group.run())

    if improve_dir:
        print(f"[agent] improve mode: {improve_dir}")
        start_event = Event("improve_paper", {
            "old_experiment_dir": improve_dir,
            "instruction": f"请分析旧论文 {improve_dir} 的学术弱点，制定改进计划，然后执行改进实验。",
        })
    else:
        start_event = Event("start_experiment", {
            "idea": group_cfg.get("default_idea", ""),
            "instruction": group_cfg.get("default_instruction", ""),
        })

    await group.event_bus.publish(start_event)

    try:
        await asyncio.gather(group_task)
    except asyncio.CancelledError:
        await group.stop()
    except Exception as e:
        print(f"[error] group.run crashed: {e}")
        import traceback
        traceback.print_exc()
        await group.stop()


def main():
    lock_path = ROOT / ".agent.lock"
    if lock_path.exists():
        import psutil
        try:
            old_pid = int(lock_path.read_text().strip())
            if psutil.pid_exists(old_pid):
                print(f"[error] another agent process is running (PID {old_pid}), exiting")
                sys.exit(1)
        except (ValueError, ImportError):
            pass
    lock_path.write_text(str(os.getpid()))

    def _cleanup():
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass
    atexit.register(_cleanup)

    faulthandler.enable()
    faulthandler.dump_traceback_later(300, exit=False)

    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, write_through=True, errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, write_through=True, errors="replace")

    def _signal_handler(sig, frame):
        print(f"[signal] received signal {sig} ({signal.Signals(sig).name}), exiting...", flush=True)
        sys.exit(128 + sig)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    parser = argparse.ArgumentParser(description="AI Agent Framework — 导师 + 研究生")
    parser.add_argument("--group", type=str, default=str(ROOT / "group_member.json"),
                        help="团队配置文件路径")
    parser.add_argument("--improve", type=str, default=None,
                        help="旧实验目录路径，进入改进模式")
    args = parser.parse_args()

    group_cfg = load_json(args.group)
    if not group_cfg:
        print(f"[error] cannot load group config: {args.group}")
        sys.exit(1)

    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(message)s',
        handlers=[
            logging.FileHandler(ROOT / "agent.log", mode="a"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    print(f"[agent] group config: {args.group}")
    if args.improve:
        print(f"[agent] improve mode: {args.improve}")
    try:
        asyncio.run(run(group_cfg, improve_dir=args.improve))
    except KeyboardInterrupt:
        print("\n[agent] stopped by user")
    except BrokenPipeError:
        sys.stderr.write("[agent] broken pipe, exiting\n")
        sys.stderr.flush()


if __name__ == "__main__":
    main()
