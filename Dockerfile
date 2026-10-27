FROM python:3.12-slim

RUN useradd -u 1000 -m appuser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY housetracker/ housetracker/
COPY templates/ templates/
COPY certs/ certs/
RUN mkdir -p data && chown -R appuser:appuser /app

USER appuser
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

CMD ["uvicorn", "housetracker.web:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
