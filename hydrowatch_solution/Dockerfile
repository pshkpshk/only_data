# syntax=docker/dockerfile:1
# Два целевых образа:
#   service   — аналитический сервис (REST API + карта), лёгкий, без torch. По умолчанию.
#   inference — полный инференс модели (torch CPU) для воспроизведения submission.csv.
#
#   docker compose up --build                     # сервис на http://localhost:8000
#   docker compose --profile inference run --rm inference   # пересчёт масок и submission

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Системные библиотеки, которых нет в slim-образе, но которые нужны бинарным колёсам
# rasterio/GDAL: на arm64 (Apple Silicon) колесо линкуется с системной libexpat.so.1.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# ---------------------------------------------------------------- service (default target)
FROM base AS service
COPY requirements-service.txt ./
RUN pip install -r requirements-service.txt
COPY src ./src
COPY web ./web
COPY configs ./configs
COPY predictions ./predictions
ENV PYTHONPATH=/app/src \
    HYDROWATCH_PREDICTIONS_DIR=/app/predictions \
    HYDROWATCH_DATA_ROOT=/data \
    HYDROWATCH_STATE_DIR=/app/outputs/service_state
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"
CMD ["python", "-m", "hydrowatch.service.app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- inference (torch CPU)
FROM base AS inference
COPY requirements-service.txt ./
RUN pip install -r requirements-service.txt \
 && pip install "torch>=2.5,<3" "torchvision>=0.20,<1" --index-url https://download.pytorch.org/whl/cpu \
 && pip install "tqdm>=4.66,<5"
COPY src ./src
COPY configs ./configs
COPY scripts ./scripts
ENV PYTHONPATH=/app/src
# данные и веса монтируются томами: /data (набор + выгруженные сцены), /app/weights, /app/outputs
ENTRYPOINT ["python", "-m", "hydrowatch.predict"]
CMD ["--root", "/data", "--checkpoint", "/app/weights/best.pt", "--output-dir", "/app/outputs/final_prediction", \
     "--thresholds", "0.95", "0.78", "0.95", "--patch-size", "384", "--stride", "256", "--batch-size", "2", "--device", "cpu"]
