FROM python:3.12-slim

WORKDIR /app

# 系统依赖（akshare 需要）
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite 缓存目录
RUN mkdir -p /app/data

EXPOSE 5001

CMD ["python", "app/selector_app.py"]
