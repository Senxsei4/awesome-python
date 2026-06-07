FROM python:3.12-slim

WORKDIR /app

# Install deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# 2 workers is plenty for a control panel. Note: the in-memory queue/event
# buffer is per-worker — keep workers low, or move to Redis for multi-worker.
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8000", "app:app"]
