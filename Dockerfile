# Single image: API + background worker + ffmpeg. Lean by default (Whisper via CTranslate2, no
# torch). Set INSTALL_ML=1 at build to add CLIP/detection (torch). Targets HF Spaces (port 7860).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    DATA_DIR=/app/data \
    DB_PATH=/app/data/app.db \
    TEMP_DIR=/app/data/tmp \
    HF_HOME=/app/data/.cache/huggingface \
    XDG_CACHE_HOME=/app/data/.cache \
    MPLCONFIGDIR=/app/data/.cache/mpl

# System deps: ffmpeg (media) + libGL/glib (OpenCV runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libgl1 libglib2.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-ml.txt ./
ARG INSTALL_ML=0
RUN pip install -r requirements.txt && \
    if [ "$INSTALL_ML" = "1" ]; then pip install -r requirements-ml.txt; fi

COPY app ./app

# HF Spaces writes to a non-root user; ensure the data dir is writable.
RUN mkdir -p /app/data/tmp && chmod -R 777 /app/data

EXPOSE 7860
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
