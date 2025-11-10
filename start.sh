#!/bin/bash
# manage.sh - 启动和停止 4 个 Python 脚本
# 会在 /var/log 下记录各脚本的日志
# 启动时会记录 PID 到 /tmp 下，方便精准停止

source ./venv/bin/activate

#!/bin/bash
# ===============================
# follow_bot 管理脚本 + 自动守护
# ===============================

scripts=("follow_bot_v5.py")   # 可放多个脚本
log_dir="/var/log"
pid_dir="/tmp"
monitor_interval=60            # 每次检测间隔（秒）

mkdir -p "$log_dir" "$pid_dir"

start_scripts() {
    for script in "${scripts[@]}"; do
        log_file="$log_dir/${script%.py}.log"
        pid_file="$pid_dir/${script}.pid"

        # 检查旧进程
        if [ -f "$pid_file" ]; then
            old_pid=$(cat "$pid_file")
            if ps -p "$old_pid" > /dev/null 2>&1; then
                echo "⚠️ 检测到旧进程($old_pid)，先杀掉 $script ..."
                kill -9 "$old_pid" 2>/dev/null
                sleep 1
            fi
            rm -f "$pid_file"
        fi

        echo "🚀 启动 $script ..."
        nohup python -u "$script" --live >> "$log_file" 2>&1 &
        new_pid=$!
        echo "$new_pid" > "$pid_file"
        echo "$(date '+%F %T') ✅ $script 启动成功 (PID: $new_pid)" | tee -a "$log_file"
    done
}

stop_scripts() {
    for script in "${scripts[@]}"; do
        pid_file="$pid_dir/${script}.pid"
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            if ps -p "$pid" > /dev/null 2>&1; then
                echo "🛑 停止 $script (PID: $pid)..."
                kill "$pid" 2>/dev/null
                sleep 1
                if ps -p "$pid" > /dev/null 2>&1; then
                    echo "⚠️ 进程未退出，强制 kill -9 $pid"
                    kill -9 "$pid" 2>/dev/null
                fi
            else
                echo "ℹ️ 进程不存在，清理旧 PID 文件。"
            fi
            rm -f "$pid_file"
        else
            echo "ℹ️ 未找到 PID 文件: $pid_file"
        fi
    done
    echo "✅ 所有脚本已停止。"
}

status_scripts() {
    for script in "${scripts[@]}"; do
        pid_file="$pid_dir/${script}.pid"
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            if ps -p "$pid" > /dev/null 2>&1; then
                echo "🟢 $script 正在运行 (PID: $pid)"
            else
                echo "🔴 $script 的 PID 文件存在，但进程未运行。"
            fi
        else
            echo "⚪ $script 未运行。"
        fi
    done
}

monitor_scripts() {
    echo "🔍 启动自动监控守护进程，每 ${monitor_interval}s 检测一次..."
    while true; do
        for script in "${scripts[@]}"; do
            pid_file="$pid_dir/${script}.pid"
            log_file="$log_dir/${script%.py}.log"
            if [ -f "$pid_file" ]; then
                pid=$(cat "$pid_file")
                if ps -p "$pid" > /dev/null 2>&1; then
                    # 正常运行
                    :
                else
                    echo "$(date '+%F %T') ⚠️ 检测到 $script 已挂掉，正在自动重启..." | tee -a "$log_file"
                    nohup python -u "$script" --live >> "$log_file" 2>&1 &
                    echo $! > "$pid_file"
                    echo "$(date '+%F %T') ✅ $script 已重启 (PID: $(cat $pid_file))" | tee -a "$log_file"
                fi
            else
                echo "$(date '+%F %T') ⚠️ 未找到 PID 文件，自动重启 $script..." | tee -a "$log_file"
                nohup python -u "$script" --live >> "$log_file" 2>&1 &
                echo $! > "$pid_file"
                echo "$(date '+%F %T') ✅ $script 已重启 (PID: $(cat $pid_file))" | tee -a "$log_file"
            fi
        done
        sleep "$monitor_interval"
    done
}

case "$1" in
    start)
        start_scripts
        ;;
    stop)
        stop_scripts
        ;;
    restart)
        stop_scripts
        sleep 1
        start_scripts
        ;;
    status)
        status_scripts
        ;;
    monitor)
        monitor_scripts
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status|monitor}"
        ;;
esac

