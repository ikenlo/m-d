# MDS Connect — образ для хостингу (Railway та інші, що вміють Dockerfile)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MDS_TIMEZONE=Europe/Kyiv

WORKDIR /app/server
COPY server/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY server/ ./

# Дані (база, ключ, резервні копії) — на підключеному диску: Railway передає його шлях
# у RAILWAY_VOLUME_MOUNT_PATH; без диска — /app/server/data (зникне при оновленні!).
EXPOSE 8000
CMD ["python", "-m", "mds", "start"]
