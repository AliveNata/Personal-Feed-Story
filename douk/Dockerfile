# Mirrors upstream's Dockerfile, but clones the pinned tag in a build stage.
# A git URL as compose build context breaks on Docker Desktop for Windows (bake treats it as a path).

FROM python:3.12-bookworm AS builder
ARG DOUK_REF=5.8
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${DOUK_REF}" https://github.com/JoeanAmier/TikTokDownloader.git /src
RUN pip install --no-cache-dir --prefix=/install -r /src/requirements.txt

FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /install /usr/local
COPY --from=builder /src/src /app/src
COPY --from=builder /src/locale /app/locale
COPY --from=builder /src/static /app/static
COPY --from=builder /src/license /app/license
COPY --from=builder /src/main.py /app/main.py
EXPOSE 5555
VOLUME /app/Volume
CMD ["python", "main.py"]
