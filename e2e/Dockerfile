FROM python:3.12-slim

ARG PLAYWRIGHT_VERSION=1.55.0
RUN python -m pip install --disable-pip-version-check --no-cache-dir \
      pytest==8.4.1 pytest-playwright==0.7.1 playwright==${PLAYWRIGHT_VERSION} \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
