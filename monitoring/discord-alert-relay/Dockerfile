FROM python:3.12-slim

WORKDIR /app

COPY alert_relay.py .
COPY config.example.toml .

RUN useradd --system --uid 10001 relay
USER relay

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status==200 else 1)"

CMD ["python", "alert_relay.py"]
