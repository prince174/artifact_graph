FROM python:3.12-slim
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA org.opencontainers.image.version=0.2.0
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
