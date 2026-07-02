# ============ Stage 1:构建前端 ============
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# vite root=src, outDir=../dist → 产物在 /build/dist
RUN npm run build

# ============ Stage 2:Python 运行时 ============
FROM python:3.12-slim AS runtime

# tzdata:living_state 的时段文案依赖本地时间,容器默认 UTC 会错乱
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app

# 先装依赖(层缓存:pyproject/uv.lock 不变就不重装)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

# 项目代码(uv sync 对根项目是 editable 安装,web.py 按相对位置找 /app/frontend/dist)
COPY mybuddy/ mybuddy/
COPY scripts/ scripts/
COPY config.example.yaml ./
RUN uv sync --frozen --no-dev

# 前端产物
COPY --from=frontend /build/dist frontend/dist

EXPOSE 8000
# config.yaml 由运行时挂载(见 docker-compose.yml),不进镜像
CMD ["uv", "run", "--no-sync", "mybuddy", "web", "--host", "0.0.0.0", "--port", "8000"]
