#!/bin/bash
# 会在 /var/log 下记录各脚本的日志
# 启动时会记录 PID 到 /tmp 下，方便精准停止

source ./venv/bin/activate

scripts=("follow_bot_v3.py")
log_dir="/var/log"
pid_dir="/tmp"

start_scripts() {
    for script in "${scripts[@]}"; do
        log_file="$log_dir/${script%.py}.log"
        echo "Starting $script ..."
        nohup python -u "$script" --live > "$log_file" 2>&1 &
        echo $! > "$pid_dir/${script}.pid"
    done
    echo "All scripts started."
}

stop_scripts() {
    for script in "${scripts[@]}"; do
        pid_file="$pid_dir/${script}.pid"
        if [ -f "$pid_file" ]; then
            pid=$(cat "$pid_file")
            if ps -p $pid > /dev/null 2>&1; then
                echo "Stopping $script (PID: $pid)"
                kill -9 $pid
            else
                echo "$script is not running."
            fi
            rm -f "$pid_file"
        else
            echo "No PID file for $script."
        fi
    done
    echo "All scripts stopped."
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
        start_scripts
        ;;
    *)
        echo "Usage: $0 {start|stop|restart}"
        ;;
esac

