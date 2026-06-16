FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY gmail_scraper/ gmail_scraper/

# Volumes are mounted at runtime; create mount points so Docker knows where they go
RUN mkdir -p /config /data/raw /data/db /logs

CMD ["python", "-m", "gmail_scraper", "--help"]
