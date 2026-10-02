FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Cloud Run injects PORT; uvicorn reads it (run via CMD below, not run_dev.py)
ENV TRADING_MODE=DEMO \
    LIVE_TRADING=false \
    MT5_MODE=MCP \
    AUTONOMOUS_MODE=true

EXPOSE 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
