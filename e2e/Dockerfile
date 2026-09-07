FROM python:3.12-slim

ARG PLAYWRIGHT_VERSION=1.55.0
RUN python -m pip install --disable-pip-version-check --no-cache-dir \
      pip==26.2.1 pytest==9.1.1 pytest-playwright==0.9.0 playwright==${PLAYWRIGHT_VERSION} \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
