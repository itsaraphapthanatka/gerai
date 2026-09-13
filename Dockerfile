FROM python:3.12-slim

WORKDIR /app

# system deps for psycopg[binary] are bundled; keep image lean
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8095
# bind 0.0.0.0 inside the container; host only publishes to 127.0.0.1 (see compose)
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8095", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
