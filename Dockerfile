FROM python:3.11-slim

WORKDIR /app

# 扁平结构：所有文件直接拷贝到 /app
COPY main.py .
COPY requirements.txt .
COPY index.html .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

# shell 形式支持 $PORT 变量（Render 动态分配）
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
