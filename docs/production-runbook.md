# Production runbook для Artifact Graph

Этот документ описывает эксплуатацию сервиса Artifact Graph в production: подготовку окружения, безопасное развёртывание, миграции PostgreSQL, мониторинг, устранение сбоев, резервное копирование и ротацию секретов.

Пошаговое первичное подключение и будущий переход с Cloud описаны в [README: Bitbucket Data Center в production](../README.md#bitbucket-data-center-production). Здесь рассматривается эксплуатация уже настроенного контура.

Все команды ниже выполняются на Linux-хосте из отдельного production checkout. Перед каждой новой операторской сессией определите функцию, которая явно выбирает файл, окружение и Compose project:

```bash
cd /opt/artefact-graph-prod
pcompose() {
  docker compose --project-name artefact-graph-prod \
    --env-file /opt/artefact-graph-prod/.env.prod \
    -f /opt/artefact-graph-prod/compose.prod.yaml \
    --project-directory /opt/artefact-graph-prod "$@"
}
```

Для существующего PostgreSQL в этой функции замените `compose.prod.yaml` на
`compose.prod.external-db.yaml`. Не определяйте обе версии одновременно в одной операторской сессии.

Если checkout расположен иначе, измените все три абсолютных пути. Не заменяйте `pcompose` обычным `docker compose`: тот может выбрать лабораторный `compose.yaml`, `.env` и другой volume. Скриптам backup/restore/deploy также обязательно передавайте `--prod`.

## 1. Эксплуатационные границы

Рабочий контур выглядит так:

```text
пользователь -> HTTPS reverse proxy -> graph:8080 -> PostgreSQL
                                      |          -> Bitbucket (read-only)
                                      |          -> TeamCity (read-only)
                                      |          -> Registry/Nexus (read-only)
                                      `----------> webhook receiver

Prometheus -> /metrics через отдельный ACL/служебную сеть
orchestrator -> /health/live и /health/ready
```

Запускайте ровно **один экземпляр приложения `graph` и один worker Uvicorn**. Планировщик сканирования и обработчик webhook outbox находятся внутри процесса. `refresh_lock` защищает только один процесс; несколько реплик будут параллельно обновлять общий граф и могут повторно отправить webhook. PostgreSQL может быть отказоустойчивым внешним сервисом, но приложение остаётся single-instance до появления распределённой блокировки и атомарного захвата outbox-записей.

`compose.prod.yaml` — самостоятельный файл с `graph` и выделенным `postgres`.
`compose.prod.external-db.yaml` содержит только `graph` и подключает существующий PostgreSQL через
обязательный `DATABASE_URL`. Bitbucket и TeamCity в обоих вариантах являются внешними серверами.
Не объединяйте эти файлы с лабораторным `compose.yaml` или друг с другом. Встроенные `teamcity`,
`registry`, профиль `datacenter` и привилегированный `teamcity-agent` профиля `build-lab`
предназначены только для интеграционного стенда.

Для перехода Cloud → Data Center или на другие серверы создавайте отдельный project/volume с пустой базой. Не переиспользуйте Cloud-базу: там остаются прежние графы, snapshots и история, которые при недоступности нового upstream могут показываться как stale. `artefact-graph-prod` отделён от лабораторного `artefact-graph`, но повторное переключение уже существующего production-контура требует новой изолированной установки. Не удаляйте старый volume — сохраните его для отката и аудита.

Обогащение manifest-ов Registry/Nexus в стандартном prod-compose отключено. Поиск `docker push`/`podman push` в логах и `sbom.json` в TeamCity artifacts продолжает работать без него. Для включения registry нужен отдельный проверенный production override с HTTPS и RO-доступом.

## 2. Обязательный preflight

### 2.1. Безопасность конфигурации

Не используйте лабораторный `compose.yaml` для production: несмотря на включённую по умолчанию проверку TLS и loopback-порты, он содержит demo-режим, лабораторные сервисы и пароль PostgreSQL `graph`. Подготовьте `.env.prod` по `.env.prod.example`. Отдельный `compose.prod.yaml` фиксирует безопасные настройки, а production preflight проверяет адреса, пароли и CA:

- `APP_MODE=live`, `DEPLOYMENT_MODE=production`;
- `WEB_AUTH_ENABLED=true`;
- длинный случайный `WEB_PASSWORD` (рекомендуется не менее 32 случайных байт) и непустой `WEB_USERNAME`;
- `WEB_COOKIE_SECURE=true`; пользовательский трафик обязательно приходит по HTTPS;
- `VERIFY_TLS=true` для Bitbucket, TeamCity и Registry/Nexus;
- уникальный `POSTGRES_PASSWORD` из минимум 32 URL-safe символов (`A-Z`, `a-z`, `0-9`, `_`, `-`), отличный от web-пароля; например, сгенерируйте два независимых значения `openssl rand -hex 32`;
- либо для внешней БД полный `postgresql+psycopg://` URL отдельной database и роли с
  `sslmode=verify-full`/`verify-ca`; при внутреннем CA путь `sslrootcert` должен находиться под `/app/certs`;
- порт 8080 доступен только reverse proxy, например через bind на `127.0.0.1`, служебную Docker network или внутренний load balancer;
- PostgreSQL не публикует порт наружу;
- `/metrics`, `/health/live` и `/health/ready` закрыты сетевым ACL: эти маршруты намеренно не требуют web-сессии;
- `BITBUCKET_URL`, `TEAMCITY_URL` указывают на HTTPS base URL, доступные из контейнера, а `TEAMCITY_PUBLIC_URL` — на HTTPS base URL, доступный пользователю; сохраняйте context path `/bitbucket` или `/teamcity`, но не добавляйте `/rest/api`, страницу репозитория, query или токен в URL;
- системные часы хоста, reverse proxy и webhook receiver синхронизированы по NTP.

Для reverse proxy включите TLS, HSTS, ограничение размера запросов и rate limit на `/login`. Встроенный лимитер входа хранится в памяти процесса, сбрасывается при рестарте и не заменяет сетевую защиту. Не публикуйте напрямую порты TeamCity, Registry, PostgreSQL и Docker daemon.

Если корпоративные серверы используют собственный CA, поместите PEM bundle в отдельный каталог хоста и задайте в `.env.prod`, например, `TLS_CA_HOST_DIR=/etc/artifact-graph/certs` и `TLS_CA_FILE=/app/certs/company-ca.pem`. Каталог монтируется read-only; сертификат должен читаться пользователем контейнера `10001:10001`. Монтируйте только публичные сертификаты CA, не private keys. Bundle добавляется к системному хранилищу доверия; пустой `TLS_CA_FILE` оставляет только системные CA. Отсутствующий/невалидный bundle останавливает запуск. Не отключайте `VERIFY_TLS` ради корпоративного сертификата. Для HTTPS smoke с таким CA отдельно задайте хостовый `SMOKE_CA_FILE`.

### 2.2. Учётные записи и секреты

Используйте отдельные runtime-учётные записи с минимальными правами:

- Bitbucket Cloud/Data Center — чтение проектов, репозиториев, веток и файлов;
- TeamCity — чтение проектов, конфигураций, последних билдов, логов и артефактов;
- Docker Registry/Nexus — только чтение manifest-ов;
- PostgreSQL — доступ только к базе Artifact Graph; права создания/удаления баз выдаются отдельному backup-оператору, если это возможно.

`BB_BOOTSTRAP_TOKEN`, `BB_CHECKOUT_TOKEN`, `TC_ADMIN_TOKEN` и любые bootstrap/admin-токены не нужны production-коллектору и не должны попадать в `.env.prod` или контейнер `graph`. Не запускайте bootstrap тестовых проектов на production-серверах. Для DC используйте PAT отдельной RO-учётки, а не Cloud API token; в TC проверьте и прямые права, и наследование через группы.

Сервис сейчас получает секреты через переменные окружения. Это означает, что пользователь с доступом к Docker daemon может увидеть их через метаданные контейнера. Ограничьте членство в группе `docker` и доступ к сокету Docker как привилегированный доступ к хосту.

На хосте создавайте `.env.prod` с закрытыми правами, только если файла ещё нет:

```bash
umask 077
test ! -e .env.prod && install -m 600 .env.prod.example .env.prod
```

Заполните файл через защищённый канал под учёткой deployment-оператора и проверьте, что он не попадает в backup исходников, артефакты CI и логи. Не запускайте `pcompose config` без `--quiet` в CI: полный вывод содержит подставленные секреты.

Если включены webhook-и:

- `WEBHOOK_URL` должен быть абсолютным HTTPS URL;
- `WEBHOOK_SECRET` должен содержать не менее 24 символов, рекомендуется 32 случайных байта;
- `WEBHOOK_VERIFY_TLS=true`;
- `WEBHOOK_ALLOW_HTTP=false`;
- receiver должен проверять подпись, временную метку и дедуплицировать `eventId`.

`WEBHOOK_ALLOW_HTTP=true` и отключение проверки TLS допустимы только во временном изолированном тестовом контуре.

### 2.3. Preflight релиза

Перед изменением production:

1. Убедитесь, что CI для выбранного commit/tag зелёный, а Docker image соответствует ожидаемому Git SHA. Релизные образы публикуются в GHCR по тегам `v*` и имеют provenance attestation.
2. Зафиксируйте текущие Git SHA, `/api/version`, Alembic revision и время последнего успешного скана в заявке на изменение.
3. Проверьте чистоту checkout: `scripts/deploy.sh` откажется работать поверх dirty tree.
4. Проверьте конфигурацию без печати значений: `pcompose config --quiet`.
5. Проверьте DNS, маршруты и цепочки доверия CA от контейнера до Bitbucket, TeamCity, Registry/Nexus и webhook receiver.
6. Проверьте файл из `MAPPING_RULES_HOST_PATH`; production-правила лучше хранить вне Git checkout. Файл монтируется read-only, но ошибочный YAML остановит обновление графа.
7. Создайте pre-deploy backup и проверьте его восстановлением во временную базу по процедуре ниже, не заменяя рабочую базу.
8. Отдельно проверьте новую миграцию на восстановленной копии production-базы или на staging с эквивалентным объёмом данных.

На первой установке `scripts/deploy.sh --prod` или `--prod-external-db` при отсутствии `.env.prod`
копирует шаблон `.env.prod.example`, выставляет mode `600` и **останавливается до запуска контейнеров**
с кодом `2`. Это ожидаемый запрос настройки: заполните URL, RO-токены, пароли и CA, затем повторите
команду. Не копируйте поверх уже заполненного `.env.prod`.

## 3. Развёртывание и миграции

### 3.1. Резервная копия перед релизом

Для встроенного PostgreSQL:

```bash
cd /opt/artefact-graph-prod
backup="backups/prod/predeploy-$(date -u +%Y%m%dT%H%M%SZ).dump"
bash scripts/backup.sh --prod "$backup"
bash scripts/verify-backup.sh --prod "$backup"
```

Не продолжайте, если checksum или тестовое восстановление завершились ошибкой. Для внешнего PostgreSQL используйте согласованную процедуру платформы и отдельную тестовую базу; текущие скрипты жёстко ориентированы на Compose-сервис `postgres`, пользователя и базу `graph`.

### 3.2. Проверка миграций

Контейнер `graph` выполняет `alembic upgrade head` **до** запуска API. Поэтому ошибка миграции видна как цикл рестартов контейнера, а `/health/live` не станет доступен.

Для диагностики production прочитайте текущую revision без изменения схемы:

```bash
pcompose exec -T graph alembic current
```

Репетицию `alembic heads`, `alembic history`, `alembic upgrade head` и `alembic current` выполняйте в отдельном staging checkout/project с собственными `.env`, PostgreSQL и volume. До `upgrade head` проверьте, что `DATABASE_URL` указывает именно на восстановленную тестовую базу; не используйте для репетиции функцию `pcompose` из этого документа. После миграции запустите целевую версию приложения на этой базе и выполните authenticated smoke и проверку поиска репозитория. Webhook-и в staging отключите, чтобы не отправлять production-события. Не запускайте старую и новую версии приложения одновременно во время миграции.

### 3.3. Deploy

`scripts/deploy.sh --prod` клонирует репозиторий при необходимости, требует чистый checkout, выполняет `git pull --ff-only`, проверяет production-конфигурацию, собирает образ, пересоздаёт только `postgres` и `graph` с сохранением volume и запускает authenticated smoke. Файл `compose.prod.yaml`, `.env.prod` и project `artefact-graph-prod` выбираются явно; переменные `COMPOSE_FILE`/`COMPOSE_PROJECT_NAME` не переключат скрипт на лабораторный контур. Скрипт обновляет текущую tracking-ветку, поэтому перед production deploy проверьте её и нужный SHA.

`scripts/deploy.sh --prod-external-db` использует `compose.prod.external-db.yaml`, проверяет схему URL
и TLS, пересоздаёт только `graph` и не выполняет lifecycle-операций над внешней БД. Alembic-миграции
всё равно применяются при старте приложения; backup и восстановление до этого момента выполняет DBA.

```bash
bash scripts/deploy.sh --prod \
  https://github.com/prince174/artifact_graph.git \
  /opt/artefact-graph-prod
```

Для внешней БД:

```bash
bash scripts/deploy.sh --prod-external-db \
  https://github.com/prince174/artifact_graph.git \
  /opt/artefact-graph-prod
```

Если код перенесён в отдельный Bitbucket-репозиторий, замените Git URL точным clone URL из его UI; runtime-токен коллектора не используйте для deployment-доступа к коду.

Во время запуска следите за миграцией и первым сканом:

```bash
pcompose ps
pcompose logs --since 15m --follow graph
```

Первый scan выполняется в startup lifecycle и может занять заметное время. Не прерывайте контейнер только потому, что readiness ещё не стала зелёной; сравнивайте длительность с обычной `artifact_graph_last_scan_duration_seconds` и установленным timeout окна изменения.

По умолчанию smoke имеет 180 секунд; для большого контура заранее задайте `SMOKE_TIMEOUT_SECONDS`, например `900`. При ошибке deploy/smoke скрипт возвращает ненулевой код и сохраняет контейнеры, checkout и данные для диагностики. **Автоматического отката кода или схемы нет**, поскольку миграция уже могла примениться. Порядок согласованного rollback описан в разделе 8. Не запускайте `down -v` и не удаляйте volumes для устранения ошибки.

### 3.4. Проверка после deploy

Проверки API должны выполняться с web-авторизацией. Smoke умеет читать web-учётные данные из защищённого env-файла без его выполнения как shell-кода. Проверка через HTTPS reverse proxy:

```bash
GRAPH_URL=https://artifact-graph.example.internal \
SMOKE_ENV_FILE=/opt/artefact-graph-prod/.env.prod \
SMOKE_TIMEOUT_SECONDS=900 \
bash scripts/smoke-linux.sh
```

Проверьте также:

- `/health/live` возвращает 200;
- `/health/ready` возвращает 200, а JSON-поле `degraded` равно `false`;
- `/api/version` соответствует развёрнутому релизу;
- `/api/status` показывает `mode=live` и новый `lastScan.status=success`;
- `/api/graph` содержит ненулевые `nodes` и `edges`;
- поиск известного production-репозитория возвращает его полный путь до TeamCity build-ов;
- `/api/coverage` не показывает неожиданного падения покрытия сопоставлений;
- ручной refresh из UI создаёт новый scan, а не 409/ошибку;
- Prometheus продолжает собирать метрики после смены контейнера.

Для собственного CA добавьте `SMOKE_CA_FILE=/etc/artifact-graph/certs/company-ca.pem` к окружению этой команды; `TLS_CA_FILE` относится к пути внутри контейнера, а не к host curl. Deploy также поддерживает `GRAPH_URL` и `SMOKE_CA_FILE`, поэтому его smoke можно направить через production proxy.

HTTP 200 от `/health/ready` сам по себе недостаточен: degraded scan со stale-данными считается пригодным для чтения, а при более позднем failed scan readiness может опираться на предыдущий usable scan. Smoke дополнительно требует свежий `success` scan и отсутствие stale-узлов; deploy требует scan не старше начала текущего развёртывания. Проверка ожидает непустой граф; только для намеренно пустого сервера допускается `SMOKE_MIN_NODES=0`. `scripts/validate_live.py` проверяет фиксированные лабораторные fixtures и не является production smoke.

## 4. Мониторинг и алерты

### 4.1. Контрольные точки

Собирайте:

- `GET /health/live` — процесс отвечает;
- `GET /health/ready` — PostgreSQL доступен и существует хотя бы один `success` или `degraded` scan;
- `GET /metrics` — Prometheus metrics, маршрут публичен на уровне приложения и должен быть закрыт сетью;
- `GET /api/status` — текущий режим, последний scan и безопасная диагностика upstream;
- `GET /api/scans?limit=20` — история scan-ов с длительностью и upstream details;
- логи контейнера `graph` и события рестартов;
- свободное место, latency, connection count и backup status PostgreSQL.

Полезные метрики:

| Метрика | Что означает | Рекомендуемая реакция |
|---|---|---|
| `artifact_graph_last_scan_age_seconds` | Время с завершения последней попытки scan | Warning после `2 * REFRESH_MINUTES`, critical после `3 * REFRESH_MINUTES` плюс обычная длительность scan |
| `artifact_graph_last_scan_duration_seconds` | Длительность последней попытки | Warning при устойчивом росте или превышении окна обновления |
| `artifact_graph_scans_total{status="failed"}` / `{status="degraded"}` | Число таких scan-ов в сохраняемой истории | Сопоставлять с `/api/status`, scan webhook и логами |
| `artifact_graph_provider_requests_total` | Запросы по provider/status | Базовая нагрузка и доля ошибок |
| `artifact_graph_provider_retries_total` | Повторы запросов по provider | Warning при росте за несколько scan-ов |
| `artifact_graph_provider_failures_total` | Ошибки provider | Critical при росте вместе с degraded/failed scan |
| `artifact_graph_provider_request_duration_seconds_total` | Суммарное время запросов provider | Использовать вместе с request count для средней latency |
| `artifact_graph_nodes`, `artifact_graph_edges` | Текущий размер графа | Alert на ноль или резкое отклонение от собственной baseline |
| `artifact_graph_webhook_deliveries{status="pending"}` | Накопившиеся доставки | Warning, если очередь не уменьшается более 5–10 минут |
| `artifact_graph_webhook_deliveries{status="dead"}` | Исчерпавшие попытки доставки | Critical при значении больше нуля |

`artifact_graph_scans_total` вычисляется по ограниченной `SCAN_HISTORY_LIMIT` истории, а provider counters находятся в памяти процесса и сбрасываются при рестарте. Не используйте их как бесконечно монотонные business-counter-ы. Основной сигнал перехода в degraded — webhook `scan.degraded` либо authenticated-проверка `/api/status`.

Если новый scan завис в состоянии `running`, `artifact_graph_last_scan_age_seconds` для него будет равна нулю, потому что `finished_at` ещё отсутствует. Отдельно проверяйте первый элемент `/api/scans`: alert должен срабатывать, когда `status=running` и время с `startedAt` превышает обычную длительность scan с запасом. Не перезапускайте процесс автоматически до сбора thread/network diagnostics: зависший provider-запрос и исчерпание ресурсов требуют разных действий.

### 4.2. Degraded и stale

При полном отказе upstream и наличии предыдущего графа сервис сохраняет карту доступной, помечает старые узлы `stale=true` и завершает scan со статусом `degraded`. При частичном отказе чтения build log/artifacts stale могут быть только затронутые build-ы. Ошибки сбора исходников и сопоставления также делают scan `degraded`, даже если предыдущей копии сущности ещё нет: проверяйте `collectionError`, `mappingUnavailable` и `incompleteEntities`. UI показывает stale banner и причину без токенов.

Учитывайте два важных свойства:

1. `/health/ready` может оставаться зелёным во время degraded-состояния.
2. `artifact_graph_last_scan_age_seconds` показывает возраст **попытки scan**, а не обязательно возраст данных upstream. Повторяющиеся degraded scan-ы обновляют эту метрику, продолжая отдавать старый граф.

Поэтому alert freshness должен одновременно проверять scan age, `/api/status.lastScan.status`, поле `degraded` readiness и наличие stale-узлов. Critical-сигнал: два последовательных degraded scan-а или один failed scan без usable графа. После восстановления ожидайте новый `success` scan и webhook `scan.recovered`.

## 5. Инциденты upstream provider

### 5.1. Первичная диагностика

1. Зафиксируйте время, release version и результат `/api/status`/`/api/scans`.
2. Определите provider из `lastScan.upstream.provider`: `bitbucket`, `teamcity`, `registry` или `unknown`.
3. Сверьте `httpStatus`, `errorType`, `endpoint`, число retries и provider metrics.
4. Проверьте рестарты и логи без вывода переменных окружения:

   ```bash
   pcompose ps graph
   pcompose logs --since 30m graph
   ```

5. Проверьте DNS, маршрут, proxy/firewall, срок сертификата и доверие CA из той же Docker network.
6. Проверьте состояние самого Bitbucket/TeamCity/Nexus по их штатным status page и аудит-логам.

Никогда не вставляйте токен в `curl -v`, incident chat или лог. Для проверки авторизации лучше создать временный read-only токен и передать его через защищённую environment/secret-инъекцию.

### 5.2. Типовые причины

- `401`/`403`: токен истёк или отозван, изменились scopes/permissions, заблокирована сервисная учётная запись. Выпустите новый read-only токен, проверьте scan, затем отзовите старый.
- `404`: изменился base URL, workspace/project/build type удалён либо endpoint скрыт reverse proxy. Сопоставьте endpoint из диагностики с конфигурацией.
- `429`: превышен rate limit. Не увеличивайте агрессивно retry; сократите частоту scan (`REFRESH_MINUTES`) или согласуйте лимит provider.
- `5xx`, timeout, connection reset: проверьте provider health, route, DNS и proxy. Сервис сам повторяет временные ошибки согласно `API_RETRY_ATTEMPTS` и backoff.
- TLS error: проверьте CA bundle, hostname/SAN и часы; для корпоративного CA проверьте `TLS_CA_HOST_DIR`, контейнерный `TLS_CA_FILE` и доступ к файлу для UID `10001`. Не устраняйте production-инцидент отключением `VERIFY_TLS`.
- Registry/Nexus: проверьте Registry API v2 endpoint, repository routing и read permission manifest-ов. Ошибка registry может проявляться как `registryStatus=unavailable` для образа, не обязательно как полный отказ карты.
- TeamCity artifacts/build log: затронутый build может сохранить предыдущее содержимое как stale. Проверьте права чтения артефактов и логов отдельно от прав чтения build configuration.

После устранения причины запустите refresh кнопкой UI (она корректно передаёт CSRF), дождитесь завершения scan и проверьте `success`, отсутствие stale banner и восстановление метрик. Не удаляйте PostgreSQL и не очищайте граф: stale copy нужна пользователям и для диагностики.

## 6. Инциденты webhook/outbox

Webhook-и создаются для `scan.degraded`, `scan.recovered` и `graph.outputs.changed`. Outbox хранится в PostgreSQL, отправляется раз в минуту партиями до 20 записей и не делает scan неуспешным при недоступности receiver.

Повторяются transport errors, HTTP `408`, `425`, `429` и `5xx`; задержка растёт экспоненциально до 60 минут. Остальные `4xx` сразу переводят запись в `dead`. После `WEBHOOK_MAX_ATTEMPTS` временная ошибка тоже становится `dead`.

Диагностика очереди:

```bash
pcompose exec -T postgres psql -U graph -d graph -v ON_ERROR_STOP=1 -c \
  "SELECT status, count(*) FROM webhook_deliveries GROUP BY status ORDER BY status;"

pcompose exec -T postgres psql -U graph -d graph -v ON_ERROR_STOP=1 -c \
  "SELECT id, event_type, event_key, attempts, next_attempt_at, last_status \
     FROM webhook_deliveries \
    WHERE status IN ('pending','dead') \
    ORDER BY id LIMIT 50;"
```

Для внешнего PostgreSQL используйте штатный защищённый SQL-клиент и operator credentials. Не публикуйте поле `payload` без необходимости.

Порядок восстановления:

1. Устраните DNS/TLS/firewall/receiver error или неверный secret.
2. Убедитесь, что receiver проверяет подпись исходного тела и допускает разумное отклонение часов.
3. Наблюдайте `pending`: scheduler автоматически повторит допустимые ошибки.
4. После исправления причины явно переоткройте только согласованные `dead` записи:

   ```sql
   UPDATE webhook_deliveries
      SET status = 'pending', attempts = 0,
          next_attempt_at = now(), last_status = ''
    WHERE id IN (<проверенные_id>) AND status = 'dead';
   ```

5. Проверьте уменьшение `pending`, отсутствие новых `dead` и дедупликацию receiver по `X-Artifact-Event-Id`.

Подпись имеет вид `sha256=HMAC-SHA256(secret, timestamp + "." + event_id + "." + raw_body)` и передаётся вместе с `X-Artifact-Timestamp`, `X-Artifact-Event-Id` и `X-Artifact-Event`. Receiver должен отклонять слишком старые timestamp и уже обработанные event ID.

Не очищайте outbox целиком. SQL requeue выполняйте после backup, с точным списком ID и записью в журнале изменения.

## 7. Backup, проверка и restore

### 7.1. Что резервировать

Обязательный набор:

- PostgreSQL database `graph` — узлы, связи, scan history, snapshots, persistent cache и webhook outbox;
- файл из `MAPPING_RULES_HOST_PATH`;
- выбранный `compose.prod.yaml` или `compose.prod.external-db.yaml`, дополнительные проверенные override
  (если есть) и идентификатор образа/Git SHA;
- секреты и `.env.prod` — отдельно, в зашифрованном secret backup, не рядом с database dump;
- CA certificates и конфигурация reverse proxy.

`scripts/backup.sh --prod` создаёт PostgreSQL custom-format dump, `<dump>.sha256` и `<dump>.json` с версией, временем, режимом и Compose project. Без явного пути production backup сохраняется в `backups/prod/`; существующий bundle не перезаписывается. SHA-256 защищает от случайного повреждения, но не от умышленной подмены; храните dump, checksum и manifest в защищённом immutable/off-host хранилище с шифрованием и контролем доступа. У outbox пока нет встроенной очистки отправленных/dead записей, поэтому следите за размером `webhook_deliveries` и выполняйте согласованную retention-очистку только после backup.

Определите RPO/RTO, частоту и retention политикой эксплуатации. Практический минимум — ежедневный backup, отдельный pre-deploy backup и регулярная копия вне Docker host. Следите, чтобы каталог `backups/` не был единственным местом хранения: он исключён из Git, но остаётся на том же диске.

### 7.2. Проверка backup

```bash
bash scripts/verify-backup.sh --prod backups/prod/artifact-graph-YYYYmmddTHHMMSSZ.dump
```

Скрипт проверяет checksum именно выбранного dump, восстанавливает его во временную базу `graph_verify_<random>_<pid>` на том же Compose PostgreSQL и выводит количество строк в основных таблицах; рабочая база `graph` не заменяется. Восстановление создаёт нагрузку на production PostgreSQL: планируйте его в допустимое окно. Скрипт не завершает проверку ошибкой при нулевых counts, поэтому оператор должен сравнить их с ожидаемой baseline. Для production этого недостаточно как единственной проверки. Регулярно выполняйте disaster-recovery rehearsal в изолированном PostgreSQL:

1. восстановите database dump;
2. восстановите mapping rules и production config без production-секретов;
3. запустите целевой release и `alembic upgrade head`;
4. выполните authenticated smoke, поиск известного репозитория и сравнение node/edge counts;
5. зафиксируйте фактический RTO и удалите тестовые секреты/данные по политике.

`verify-backup.sh` требует право `createdb/dropdb`; не выдавайте это право повседневному runtime-пользователю только ради проверки — запускайте проверку отдельной backup-учёткой или на изолированном сервере.

### 7.3. Destructive restore

`scripts/restore.sh` останавливает `graph`, удаляет и заново создаёт базу `graph`, восстанавливает dump и запускает приложение. Команда необратима для текущей базы и требует явного `--confirm`.

Перед restore:

1. объявите maintenance window и остановите внешние изменения;
2. сохраните аварийный dump текущей базы, если она читается;
3. проверьте `.sha256` и manifest, включая `mode`/`project` и источник данных: checksum не доказывает, что выбран backup нужного окружения;
4. подтвердите совместимость backup schema с выбранной версией приложения;
5. убедитесь, что отдельно доступны production `.env.prod`, mapping rules и CA, а выбран именно project `artefact-graph-prod`, не lab;
6. подтвердите, что потеря изменений после момента backup допустима. Restore заменяет данные текущей production-базы, а не создаёт дополнительную копию.

```bash
bash scripts/restore.sh --prod \
  backups/prod/artifact-graph-YYYYmmddTHHMMSSZ.dump \
  --confirm
```

После restore контейнер снова выполнит `alembic upgrade head`. Проверьте:

```bash
pcompose exec -T graph alembic current
pcompose ps graph postgres
pcompose logs --since 15m graph
```

Затем выполните полный authenticated smoke, проверьте `/api/status`, поиск репозитория, snapshots и webhook queue. Restore базы не восстанавливает `.env.prod`, mapping rules, reverse proxy или внешние Bitbucket/TeamCity/Registry.

## 8. Rollback и совместимость схемы

Автоматический rollback в `scripts/deploy.sh` отключён. Ошибка запуска или smoke оставляет текущий checkout, контейнеры и volume для диагностики: схема уже могла измениться. Перед ручным откатом определите фактически применённую Alembic revision и совместимость старого кода с ней; один лишь возврат прежнего образа не восстанавливает базу.

Перед релизом классифицируйте миграцию:

- additive и backward-compatible: новые nullable/default columns, новые таблицы/индексы без изменения старого контракта;
- incompatible: удаление/переименование колонок, изменение типа/семантики, обязательная data migration;
- irreversible: потеря данных при downgrade или невозможность безопасно восстановить старую семантику.

Предпочитайте forward-fix. Откатывайте приложение без базы только после теста старой версии на новой схеме. Для несовместимой схемы допустимы лишь заранее протестированные варианты:

1. явный `alembic downgrade <revision>` с одобренным планом сохранения данных; либо
2. восстановление pre-deploy dump и предыдущей версии приложения, принимая потерю данных после момента backup.

Никогда не запускайте downgrade вслепую в production. Все текущие migration-файлы имеют функцию `downgrade`, но наличие функции не гарантирует сохранение данных. Для возврата на конкретный проверенный релиз используйте согласованную процедуру с закреплённым образом/Git SHA, не обычный повторный deploy: он делает `git pull --ff-only` текущей ветки. Если ручной rollback оставил checkout в detached HEAD, до следующего штатного deploy восстановите нужную tracking-ветку без уничтожения локальных изменений.

## 9. Ротация секретов и сессий

### 9.1. Bitbucket, TeamCity и Registry/Nexus

Ротируйте по одному provider за раз:

1. выпустите новый read-only токен с теми же минимальными правами;
2. обновите защищённый `.env.prod`/secret source;
3. пересоздайте только `graph`:

   ```bash
   pcompose up -d --no-deps --force-recreate graph
   ```

4. дождитесь `success` scan и проверьте данные этого provider;
5. отзовите старый токен;
6. зафиксируйте дату следующей ротации.

Если новый токен не работает, верните предыдущий до его отзыва. Не используйте bootstrap/admin-токен как временный runtime fallback.

### 9.2. Web-доступ и принудительный logout

Web-сессия — stateless HMAC-cookie. Ключ подписи выводится из `WEB_PASSWORD`, серверного списка сессий нет.

- Изменение `WEB_PASSWORD` и пересоздание `graph` немедленно инвалидирует все существующие сессии.
- Изменение только `WEB_USERNAME` **не** инвалидирует уже выданные cookies.
- `/api/logout` удаляет cookie только в текущем браузере.
- Компрометация web-пароля требует ротации `WEB_PASSWORD`, рестарта `graph` и проверки логов входа.
- `WEB_SESSION_HOURS` задаёт максимальный срок cookie; уменьшите его для чувствительного контура.

После ротации проверьте вход, CSRF-защищённый manual refresh и `WEB_COOKIE_SECURE=true` через browser developer tools/reverse proxy logs без записи cookie value.

### 9.3. Webhook secret

Receiver желательно временно настроить на приём старого и нового secret:

1. добавить новый secret в receiver;
2. обновить `WEBHOOK_SECRET` и пересоздать `graph`;
3. дождаться успешной доставки и проверить подпись;
4. удалить старый secret из receiver.

Pending payload подписывается в момент каждой отправки текущим secret, поэтому после ротации он будет доставлен с новым ключом. Если receiver не умеет принимать два ключа, согласуйте короткое окно и внимательно контролируйте outbox. Отключение `WEBHOOK_ENABLED` прекращает и отправку, и создание новых webhook-событий — это может создать пробел в уведомлениях.

### 9.4. PostgreSQL

Ротация пароля PostgreSQL должна быть согласованной между database role и `POSTGRES_PASSWORD` в `.env.prod`, из которого Compose строит `DATABASE_URL`. Для существующего volume простое изменение env и пересоздание `postgres` **не меняет пароль роли в базе**: `POSTGRES_PASSWORD` используется образом PostgreSQL при первоначальной инициализации. Измените пароль роли защищённым административным SQL-каналом в согласованное окно, проверьте новый credential на отдельном соединении, затем обновите `.env.prod` и пересоздайте `graph`. Не удаляйте volume для смены пароля. При недоступности базы readiness станет 503, а приложение не сможет сохранять scan/outbox.

## 10. Завершение инцидента или изменения

Закрывайте работу только после того, как:

- развёрнутая версия и Alembic revision зафиксированы;
- последний scan имеет статус `success`, либо degraded явно принят с владельцем и сроком;
- stale banner отсутствует или документирован;
- node/edge counts и mapping coverage сравнимы с baseline;
- webhook `pending` уменьшается, `dead=0`;
- Prometheus scrape и alert routing работают;
- backup перенесён off-host и прошёл verification;
- временные токены, cookies, тестовые базы и incident-доступы удалены;
- обновлены журнал изменения, известные ограничения и следующий срок ротации.
