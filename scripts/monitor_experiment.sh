#!/bin/bash
# 实验监控脚本 - 每小时运行一次
# 用法: nohup bash scripts/monitor_experiment.sh &

PROJECT_DIR="/home/lk/ai_mentor"
LOG_FILE="$PROJECT_DIR/monitor.log"
IMPROVE_TARGET="experiments/2026-06-08_01-31-13_semantic_filtering_slam_attempt_0"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

check_and_restart() {
    # 检查agent进程
    AGENT_PID=$(pgrep -f "run_agent.py --improve" | head -1)
    if [ -z "$AGENT_PID" ]; then
        log "Agent进程不存在，正在重启..."
        cd "$PROJECT_DIR"
        setsid .venv/bin/python -u agents/run_agent.py --improve "$IMPROVE_TARGET/" >> /tmp/improve_monitor.log 2>&1 &
        log "Agent已重启，PID: $!"
    else
        log "Agent运行中，PID: $AGENT_PID"
    fi

    # 检查实验进程
    EXPERIMENT_PID=$(pgrep -f "runfile.py" | head -1)
    if [ -n "$EXPERIMENT_PID" ]; then
        log "实验进程运行中，PID: $EXPERIMENT_PID"
    else
        log "无实验进程"
    fi

    # 检查最新结果
    LATEST_RESULT=$(find "$PROJECT_DIR/experiments" -name "results.json" -newer "$PROJECT_DIR/.agent.lock" 2>/dev/null | sort -r | head -1)
    if [ -n "$LATEST_RESULT" ]; then
        log "最新结果: $LATEST_RESULT"
        cat "$LATEST_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  mIoU={d.get(\"final_miou\",\"N/A\")}, Acc={d.get(\"final_pixel_acc\",\"N/A\")}, Epochs={d.get(\"epochs\",\"N/A\")}')" >> "$LOG_FILE"
    fi

    # 检查论文生成
    PAPER_COUNT=$(find "$PROJECT_DIR/experiments" -name "*.tex" -newer "$PROJECT_DIR/.agent.lock" 2>/dev/null | wc -l)
    if [ "$PAPER_COUNT" -gt 0 ]; then
        log "发现 $PAPER_COUNT 个LaTeX文件"
    fi
}

# 主循环
log "=== 监控启动 ==="
while true; do
    check_and_restart
    sleep 3600  # 每小时检查一次
done
