#!/usr/bin/env bash
# 部署启动:gunicorn + gevent。
#
# 异步示例用后台线程干活,gevent 下线程就是协程,一个 worker 就能同时挂很多个。
# 后台活跑在 worker 进程里,进程重启时没干完的活会丢(卡片停在「进行中」);
# 正式服务请换成任务队列。
set -euo pipefail
cd "$(dirname "$0")"

# 读同目录的 .env(HOST / PORT / WORKERS 也可以写在里面)
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

exec gunicorn main:app \
  --bind "${HOST:-0.0.0.0}:${PORT:-3400}" \
  --workers "${WORKERS:-1}" \
  --worker-class gevent \
  --timeout 600 \
  --log-level info \
  --access-logfile - \
  --error-logfile -
