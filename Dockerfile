FROM python:3.11-slim

# Build deps for psycopg2-binary / reportlab wheels are already in slim;
# add gcc only if a sdist sneaks in. fonts-dejavu-core — кириллица в PDF:
# slim не содержит НИ ОДНОГО шрифта, и pdf_export падал в Helvetica
# (вся кириллица квадратами). DejaVuSans.ttf — первый Linux-кандидат
# в pdf_export._register_fonts().
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache the dependency layer separately from app source.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# App source — keep the same layout as the repo so PROJECT_ROOT
# (= parent of backend/) still resolves index.html and assets/.
COPY backend/ ./backend/
COPY index.html lab.html admin.html styles.css app.js sw.js manifest.json ./
COPY assets/ ./assets/
COPY marketing/ ./marketing/

WORKDIR /app/backend

EXPOSE 8000

CMD ["python", "main.py"]
