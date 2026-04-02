# syntax=docker/dockerfile:1

FROM python:3.11-slim

RUN groupadd --gid 1000 appuser \
 && useradd --uid 1000 --gid appuser --no-create-home appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/status')"

CMD ["python", "app.py"]
