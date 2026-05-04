FROM python:3.11-slim

# Build deps for psycopg2-binary / reportlab wheels are already in slim;
# add gcc only if a sdist sneaks in.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cache the dependency layer separately from app source.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# App source — keep the same layout as the repo so PROJECT_ROOT
# (= parent of backend/) still resolves index.html and assets/.
COPY backend/ ./backend/
COPY index.html lab.html ./
COPY assets/ ./assets/

WORKDIR /app/backend

EXPOSE 8000

CMD ["python", "main.py"]
