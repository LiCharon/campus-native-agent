# CampusDesk 后端镜像（M6 Compose 一键起）
# python:3.14-slim（debian 基底，wheel 覆盖全；alpine musl 轮子有风险）
# 依赖安装用清华源（官方 PyPI 国内卡死，CLAUDE.md §9）
# ⚠️ requirements.txt 的 `-e git+...` 行必须过滤——容器里会重新 clone GitHub 且指向旧 commit

FROM python:3.14-slim

WORKDIR /app

# 先装依赖（利用层缓存：改代码不重装依赖）
COPY requirements.txt .
RUN grep -v '^-e git' requirements.txt > requirements.in \
    && pip install --no-cache-dir \
       --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
       -r requirements.in

# 再拷源码 + editable 安装（src layout + alembic.ini 可用；--no-deps 避免二次解析）
COPY . .
RUN pip install --no-cache-dir \
       --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
       --no-deps -e . \
    && chmod +x docker/entrypoint.sh

# 启动：迁移 + 种子（均幂等）→ uvicorn
# --workers 1 硬约束：多 worker = 多图注册表 + 多 checkpointer 连接冲突
ENTRYPOINT ["/app/docker/entrypoint.sh"]
