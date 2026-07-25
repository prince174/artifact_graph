# Artefact Graph

Python-сервис строит интерактивный граф `Bitbucket repository → TeamCity build configuration → build/image/SBOM`.
Он сопоставляет системы по нормализованному URL VCS root, находит `docker push` и `podman push` в
script steps, правило `**/sbom.json => artifacts` и последние три запуска каждой конфигурации.

Bitbucket подключается через общий контракт `RepositoryProvider`. Поддерживаются адаптеры
`cloud` (основной) и `datacenter` (опциональный).

## Быстрый старт demo

```bash
docker compose up -d --build postgres graph
```

Откройте `http://localhost:8080`. Demo содержит 10 репозиториев. Каждому продукту соответствует
отдельный TeamCity project с цепочкой `Test → Build, push and SBOM → Deploy` и
последними тремя запусками каждой стадии.
Кнопка обновления запускает скан вручную, плановый скан выполняется раз в 60 минут.

## Bitbucket Cloud

Создайте workspace в Bitbucket Cloud и отдельную учётную запись системы. Для неё создайте API token
со scope `read:repository:bitbucket`. Заполните `.env`:

```env
APP_MODE=live
BITBUCKET_PROVIDER=cloud
BITBUCKET_WORKSPACE=my-workspace
BITBUCKET_AUTH=api_token
BITBUCKET_EMAIL=reader@example.com
BITBUCKET_TOKEN=...
TEAMCITY_TOKEN=...
```

Для workspace/project access token используйте `BITBUCKET_AUTH=access_token` и оставьте
`BITBUCKET_EMAIL` пустым. Приложение выполняет только GET-запросы.

## Полный стенд с TeamCity

```bash
docker compose up -d --build postgres graph teamcity teamcity-agent registry
```

1. Завершите бесплатную настройку TeamCity Professional на `http://localhost:8111`.
2. Создайте Cloud workspace и тестовые репозитории.
3. Скопируйте `.env.example` в `.env`, запишите read-only токены, смените `APP_MODE=live` и перезапустите `graph`.

Локальный Bitbucket Data Center сохранён только как необязательный профиль и по умолчанию не запускается:

```bash
docker compose --profile datacenter up -d bitbucket-db bitbucket
```

Сервис использует только `GET` к Bitbucket и TeamCity. Токены bootstrap-администратора приложению
не передаются. Web UI пока не имеет пользовательской авторизации (единый доступ, как согласовано),
поэтому публиковать порт 8080 в недоверенную сеть не следует.

## Развёртывание на Linux

```bash
sudo bash scripts/deploy.sh ssh://git@bitbucket.example/scm/tools/artefact-graph.git /opt/artefact-graph
```

Скрипт клонирует репозиторий при первом запуске, затем делает `git pull --ff-only` и полностью
пересоздаёт сервисы. Данные хранятся в Docker volumes и при пересоздании контейнеров сохраняются.

## Проверка

```bash
python -m pip install -e '.[test]'
pytest
```

Pytest enforces at least 75% line coverage for the application package. To validate
the running ten-repository fixture, including node counts and repository search:

```bash
python scripts/validate_live.py --url http://localhost:18081 --repository java-maven-api
```

## Registry and Nexus

The scanner reads OCI manifests through the Docker Registry HTTP API v2 and records
the immutable digest on image and build nodes. The same client supports a Nexus
Docker hosted/proxy endpoint:

```env
REGISTRY_ENABLED=true
REGISTRY_PROVIDER=nexus
REGISTRY_URL=https://nexus.internal.example:5001
REGISTRY_PUBLIC_URL=https://nexus.example:5001
REGISTRY_USERNAME=artifact-graph-reader
REGISTRY_TOKEN=read-only-token
```

Use a read-only account. `REGISTRY_URL` is the address available inside the graph
container; `REGISTRY_PUBLIC_URL` is used for links shown in the browser.

## Operations

- `GET /health/live` checks that the process is alive.
- `GET /health/ready` checks PostgreSQL and requires at least one successful scan.
- `GET /metrics` exposes Prometheus scan, graph-size, duration, and age metrics.
- `GET /api/scans?limit=20` returns recent scan history. `SCAN_HISTORY_LIMIT` controls retention.

The runtime `BITBUCKET_TOKEN` and `TEAMCITY_TOKEN` must belong to dedicated read-only
accounts. Keep `BB_BOOTSTRAP_TOKEN` and `TC_ADMIN_TOKEN` outside the graph container;
they are only used by bootstrap scripts.

Back up PostgreSQL and restore it without exposing credentials:

```bash
docker compose exec -T postgres pg_dump -U graph graph > artifact-graph.sql
docker compose exec -T postgres psql -U graph graph < artifact-graph.sql
```

Back up the named volumes before a destructive recreation of TeamCity or the local
registry. A normal `docker compose up -d --build` preserves all named volumes.

Automated backup verification and schema migrations:

```bash
bash scripts/backup.sh
bash scripts/verify-backup.sh backups/artifact-graph-YYYYmmddTHHMMSSZ.dump
# Destructive restore requires an explicit flag:
bash scripts/restore.sh backups/artifact-graph-YYYYmmddTHHMMSSZ.dump --confirm
docker compose exec -T graph alembic current
```

The graph container runs `alembic upgrade head` before starting the API. Release
images expose `GET /api/version`, include the Git commit as an OCI image label,
and are built by `.github/workflows/ci.yml` for version tags.
