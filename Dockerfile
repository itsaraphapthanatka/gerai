FROM python:3.12-slim

WORKDIR /app

# system deps for psycopg[binary] are bundled; keep image lean
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# แอปรันด้วยผู้ใช้ app (uid 10001) ไม่ใช่ root: ช่องโหว่ในแอปหรือไลบรารีจะแก้โค้ดใน /app ไม่ได้
# และถ้าหลุดออกจาก container ก็ไม่ได้เป็น root ของเครื่อง · entrypoint เริ่มเป็น root แค่เพื่อ
# โอนโฟลเดอร์ที่ mount มา (uploads, รูปที่สร้าง) ให้ app แล้วลดสิทธิ์ทันที — ดู deploy/docker-entrypoint.sh
RUN useradd --uid 10001 --create-home app \
 && install -m 755 deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["docker-entrypoint.sh"]

EXPOSE 8095
# bind 0.0.0.0 inside the container; host only publishes to 127.0.0.1 (see compose)
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8095", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
