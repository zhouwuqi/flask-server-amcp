#!/bin/bash


# 计算工作进程数（假设有4个CPU核心）
NUM_WORKERS=1

# 使用 Gunicorn 启动 Flask 应用，增加 timeout 和 workers 参数
gunicorn main:app \
    --bind 0.0.0.0:3400 \
    --workers $NUM_WORKERS \
    --worker-class gevent \
    --timeout 600 \
    --log-level info \
    --access-logfile - \
    --error-logfile -

# 如果需要，可以在脚本结束时取消激活虚拟环境
# deactivate