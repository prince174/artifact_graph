FROM node:22-alpine AS web-dependencies
WORKDIR /web-dependencies
RUN npm init -y && npm install --ignore-scripts --no-audit --no-fund --save-exact cytoscape@3.30.4

FROM python:3.12-slim
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA org.opencontainers.image.version=0.2.0
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app
RUN mkdir -p /app/app/static
COPY --from=web-dependencies /web-dependencies/node_modules/cytoscape/dist/cytoscape.min.js /app/app/static/cytoscape.min.js
COPY --from=web-dependencies /web-dependencies/node_modules/cytoscape/LICENSE /app/app/static/cytoscape.LICENSE
COPY alembic.ini ./
COPY migrations ./migrations
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
