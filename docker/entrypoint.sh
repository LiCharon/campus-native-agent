#!/bin/sh
# CampusDesk 后端启动脚本（M6）：迁移 + 种子（幂等）→ uvicorn
# ⚠️ 必须 LF 换行（CRLF 会让 exec 挂掉）
set -e

echo "[campus-desk] alembic upgrade head..."
alembic upgrade head

echo "[campus-desk] seed_db..."
python scripts/seed_db.py

echo "[campus-desk] starting uvicorn..."
exec uvicorn campus_desk.api.app:create_app --factory --host 0.0.0.0 --port 8000 --workers 1
