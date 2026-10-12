FROM python:3.11.12-slim-bookworm
WORKDIR /workspace
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
# Web services do not install Spark or Java.
RUN sed '/^pyspark==/d' requirements.txt > /tmp/web.txt && pip install --no-cache-dir -r /tmp/web.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
