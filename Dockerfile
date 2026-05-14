FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8501

WORKDIR /app

COPY requirements.lock.txt ./requirements.lock.txt
RUN pip install --upgrade pip && pip install -r requirements.lock.txt

COPY . .

EXPOSE 8501

CMD ["python", "-m", "streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true", "--server.runOnSave", "false", "--server.fileWatcherType", "none"]