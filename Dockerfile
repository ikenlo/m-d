FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MDS_TIMEZONE=Europe/Kyiv \
    MDS_ENV=production

WORKDIR /app/server
COPY server/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY server/ ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN useradd --system --uid 10001 --home-dir /app mds \
    && mkdir -p /app/server/data \
    && chown -R mds:mds /app/server/data \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "mds", "start"]
