# Backend image: serves both the API and the Celery worker (command decides which).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System deps:
# - ffmpeg: server-side frame-extraction FALLBACK only (primary path is the browser)
# - libgl1 / libglib2.0-0: OpenCV + PaddleOCR runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the whole project, then install editable so the editable finder sees every
# package/submodule (empty stubs first would leave submodules unimportable).
COPY . .

# Install the base app. OCR extras (paddle) are installed separately so a paddle
# build failure doesn't block the whole image; toggle with build arg.
ARG INSTALL_OCR=true
# Base app + dev extras (pytest/ruff) so `docker compose run api pytest` works.
RUN pip install --upgrade pip && pip install -e ".[dev]"
RUN if [ "$INSTALL_OCR" = "true" ]; then pip install -e ".[ocr]" || \
        echo "WARNING: OCR extras failed to install; run with OCR_ENGINE=stub"; fi

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
