# POKE ONE 单容器镜像：前端构建产物由 FastAPI 托管（部署取舍见 README）

# ---------- 阶段 1：构建前端 ----------
FROM node:20-alpine AS fe
WORKDIR /fe
COPY frontend/package.json ./
RUN npm install --registry=https://registry.npmmirror.com
COPY frontend/ ./
RUN npm run build

# ---------- 阶段 2：运行后端 ----------
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY backend/ ./
COPY data/ ./data/
# main.py 以 parent.parent.parent / frontend/dist 解析静态目录，容器内即 /frontend/dist
COPY --from=fe /fe/dist /frontend/dist
# pipeline 默认数据目录按 REPO_ROOT(=容器根)/data 解析，覆盖到 CSV 实际所在 /app/data
ENV APP_DATA_DIR=/app/data
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
# 启动时若数据库缺失会自动跑清洗管线（幂等，由 data/*.csv 重建）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
