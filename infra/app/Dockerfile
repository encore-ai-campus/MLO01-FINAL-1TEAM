FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/apps \
    TZ=Asia/Seoul

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/rag_api ./apps/rag_api

RUN useradd --create-home --uid 10001 appuser

USER appuser

RUN python -c "from rag_api.main import app; print(app.title)"

EXPOSE 8000

CMD ["uvicorn", "rag_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
