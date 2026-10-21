FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
EXPOSE 8000

WORKDIR /app

COPY local/cache/wheelhouse/ /app/.wheelhouse/
RUN python -m venv /opt/venv \
  && /opt/venv/bin/pip install --no-index --find-links=/app/.wheelhouse \
    -r /app/.wheelhouse/requirements.txt \
  && /opt/venv/bin/pip install --no-index --no-deps /app/.wheelhouse/project/*.whl

COPY src/ /opt/venv/lib/python3.11/site-packages/

ENV PATH="/opt/venv/bin:$PATH"
RUN rm -rf /app/.wheelhouse

RUN useradd --create-home appuser && mkdir -p /data && chown -R appuser:appuser /app /data
USER appuser

ENV PERSON_AGENTS_DATA_DIR=/data \
    PERSON_AGENTS_SQLITE_PATH=/data/memory.db \
    PERSON_AGENTS_CHROMA_PATH=/data/chroma

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

ENTRYPOINT ["person-agents"]
CMD ["serve"]
