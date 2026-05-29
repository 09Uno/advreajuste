FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_MAX_UPLOAD_SIZE=2048 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    ADVREAJUSTE_DATA_DIR=/var/data \
    ADVREAJUSTE_OCR_CACHE_DIR=/var/data/cache/ocr \
    ADVREAJUSTE_PDF_WORKERS=2 \
    ADVREAJUSTE_OCR_WORKERS=1 \
    ADVREAJUSTE_OCR_DPI=150

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        tesseract-ocr \
        tesseract-ocr-por \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .
RUN mkdir -p /var/data/casos /var/data/cache /var/data/custody /var/data/indices

EXPOSE 8501

CMD ["sh", "-c", "streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT:-8501}"]
