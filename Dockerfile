FROM node:22-alpine AS web-dependencies
WORKDIR /web-dependencies
RUN npm init -y && npm install --ignore-scripts --no-audit --no-fund --save-exact cytoscape@3.30.4

FROM python:3.12-slim
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA org.opencontainers.image.version=0.2.0
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip==26.2.1 && pip install --no-cache-dir . \
    && python -m pip uninstall -y pip
COPY app ./app
RUN mkdir -p /app/app/static
COPY --from=web-dependencies /web-dependencies/node_modules/cytoscape/dist/cytoscape.min.js /app/app/static/cytoscape.min.js
COPY --from=web-dependencies /web-dependencies/node_modules/cytoscape/LICENSE /app/app/static/cytoscape.LICENSE
COPY alembic.ini ./
COPY migrations ./migrations
RUN groupadd --gid 10001 graph && useradd --uid 10001 --gid graph --no-create-home graph \
    && chown graph:graph /app
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
