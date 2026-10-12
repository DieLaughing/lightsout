FROM python:3.13-slim
LABEL org.opencontainers.image.source="https://github.com/DieLaughing/lightsout" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LIGHTSOUT_DATA=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lightsout ./lightsout
COPY web ./web

EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz')"
CMD ["uvicorn", "lightsout.app:app", "--host", "0.0.0.0", "--port", "8787", "--proxy-headers", "--forwarded-allow-ips", "*"]
