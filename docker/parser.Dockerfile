FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/quorum
COPY pyproject.toml README.md ./
COPY quorum ./quorum
RUN pip install --no-cache-dir "pypdf==6.14.2" "Pillow==12.3.0" "pytesseract==0.3.13"
RUN pip install --no-cache-dir --no-deps .

USER 65534:65534
ENTRYPOINT ["python", "-I", "-m", "quorum.research.parser_worker"]
