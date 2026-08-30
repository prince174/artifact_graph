# Production runbook для Artifact Graph

Этот документ описывает эксплуатацию сервиса Artifact Graph в production: подготовку окружения, безопасное развёртывание, миграции PostgreSQL, мониторинг, устранение сбоев, резервное копирование и ротацию секретов.

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

Встроенные `teamcity`, `teamcity-agent`, `registry` и профиль `datacenter` из базового `compose.yaml` предназначены для интеграционного стенда. В частности, TeamCity agent запускается с `privileged: true`, а порты 8111 и 5000 публикуются на хост. Не запускайте эти сервисы в production-контуре Artifact Graph, если они не являются осознанной частью отдельного защищённого стенда.

## 2. Обязательный preflight

### 2.1. Безопасность конфигурации

Базовый `compose.yaml` содержит удобные для разработки значения: `APP_MODE=demo`, `VERIFY_TLS=false`, `WEB_COOKIE_SECURE=false`, пароль PostgreSQL `graph` и публикацию порта приложения на всех интерфейсах. Это не production-конфигурация. До первого запуска подготовьте проверенный production override или эквивалентное описание сервиса со следующими свойствами:

- `APP_MODE=live`;
- `WEB_AUTH_ENABLED=true`;
- длинный случайный `WEB_PASSWORD` (рекомендуется не менее 32 случайных байт) и непустой `WEB_USERNAME`;
- `WEB_COOKIE_SECURE=true`, если пользовательский трафик приходит по HTTPS;
- `VERIFY_TLS=true` для Bitbucket, TeamCity и Registry/Nexus;
- уникальные учётные данные PostgreSQL вместо `graph:graph`;
- порт 8080 доступен только reverse proxy, например через bind на `127.0.0.1`, служебную Docker network или внутренний load balancer;
- PostgreSQL не публикует порт наружу;
- `/metrics`, `/health/live` и `/health/ready` закрыты сетевым ACL: эти маршруты намеренно не требуют web-сессии;
- `BITBUCKET_URL`, `TEAMCITY_URL`, `REGISTRY_URL` указывают на адреса, доступные из контейнера, а `TEAMCITY_PUBLIC_URL` и `REGISTRY_PUBLIC_URL` — на адреса, доступные пользователю;
- системные часы хоста, reverse proxy и webhook receiver синхронизированы по NTP.

Для reverse proxy включите TLS, HSTS, ограничение размера запросов и rate limit на `/login`. Встроенный лимитер входа хранится в памяти процесса, сбрасывается при рестарте и не заменяет сетевую защиту. Не публикуйте напрямую порты TeamCity, Registry, PostgreSQL и Docker daemon.

### 2.2. Учётные записи и секреты

Используйте отдельные runtime-учётные записи с минимальными правами:

- Bitbucket Cloud/Data Center — чтение проектов, репозиториев, веток и файлов;
- TeamCity — чтение проектов, конфигураций, последних билдов, логов и артефактов;
- Docker Registry/Nexus — только чтение manifest-ов;
- PostgreSQL — доступ только к базе Artifact Graph; права создания/удаления баз выдаются отдельному backup-оператору, если это возможно.

`BB_BOOTSTRAP_TOKEN`, `TC_ADMIN_TOKEN` и любые bootstrap/admin-токены не должны попадать в `.env` контейнера `graph`. После первоначальной настройки их нужно удалить с хоста или хранить отдельно в менеджере секретов.

Сервис сейчас получает секреты через переменные окружения. Это означает, что пользователь с доступом к Docker daemon может увидеть их через метаданные контейнера. Ограничьте членство в группе `docker` и доступ к сокету Docker как привилегированный доступ к хосту.

На хосте создавайте `.env` с закрытыми правами:

```bash
umask 077
install -m 600 /dev/null /opt/artefact-graph/.env
chown root:root /opt/artefact-graph/.env
```

Заполните файл через защищённый канал и проверьте, что он не попадает в backup исходников, артефакты CI и логи. Не запускайте `docker compose config` без `--quiet` в CI: полный вывод содержит подставленные секреты.

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
4. Проверьте конфигурацию без печати значений: `docker compose config --quiet`.
5. Проверьте DNS, маршруты и цепочки доверия CA от контейнера до Bitbucket, TeamCity, Registry/Nexus и webhook receiver.
6. Проверьте `config/mapping-rules.yaml`; файл монтируется read-only, но ошибочный YAML остановит обновление графа.
7. Создайте и восстановите pre-deploy backup по процедуре ниже.
8. Отдельно проверьте новую миграцию на восстановленной копии production-базы или на staging с эквивалентным объёмом данных.

На первой установке не полагайтесь на автоматическое копирование `.env.example` в `scripts/deploy.sh`: после копирования скрипт сразу запускает контейнеры, а example содержит placeholder-значения. Сначала клонируйте репозиторий, создайте production `.env` и production Compose override, затем выполняйте deploy.

## 3. Развёртывание и миграции

### 3.1. Резервная копия перед релизом

Для встроенного PostgreSQL:

```bash
cd /opt/artefact-graph
backup="backups/predeploy-$(date -u +%Y%m%dT%H%M%SZ).dump"
bash scripts/backup.sh "$backup"
bash scripts/verify-backup.sh "$backup"
```

Не продолжайте, если checksum или тестовое восстановление завершились ошибкой. Для внешнего PostgreSQL используйте согласованную процедуру платформы и отдельную тестовую базу; текущие скрипты жёстко ориентированы на Compose-сервис `postgres`, пользователя и базу `graph`.

### 3.2. Проверка миграций

Контейнер `graph` выполняет `alembic upgrade head` **до** запуска API. Поэтому ошибка миграции видна как цикл рестартов контейнера, а `/health/live` не станет доступен.

Минимальные проверки на staging-копии:

```bash
docker compose run --rm --no-deps graph alembic heads
docker compose run --rm --no-deps graph alembic history
docker compose run --rm graph alembic upgrade head
docker compose run --rm graph alembic current
```

Для `upgrade head` задайте `DATABASE_URL` тестовой восстановленной базы, а не production. После миграции запустите целевую версию приложения на этой базе и выполните authenticated smoke и проверку поиска репозитория. Не запускайте старую и новую версии приложения одновременно во время миграции.

### 3.3. Deploy

`scripts/deploy.sh` клонирует репозиторий при необходимости, выполняет `git pull --ff-only`, собирает образы, пересоздаёт контейнеры и запускает smoke. При ошибке он возвращает код приложения на предыдущий Git SHA, но **не откатывает схему базы**.

Базовый скрипт поднимает все сервисы без profile и явно использует только `compose.yaml`: дополнительный override он сейчас не подключает. Поэтому не запускайте его без изменений в минимальном production-контуре. Используйте отдельный версионированный production deploy wrapper с `-f compose.yaml -f compose.production.yaml` либо ручную процедуру с явным списком сервисов. Например, для встроенного PostgreSQL:

```bash
docker compose -f compose.yaml -f compose.production.yaml \
  up -d --build --force-recreate postgres graph
```

Если PostgreSQL внешний, запускайте только `graph` с корректным `DATABASE_URL` и не создавайте локальный `postgres`.

Пример запуска `scripts/deploy.sh` применим к изолированному all-in-one стенду или после того, как его Compose-вызов официально адаптирован под production-файл:

```bash
sudo bash scripts/deploy.sh \
  ssh://git@bitbucket.example/scm/tools/artefact-graph.git \
  /opt/artefact-graph
```

Во время запуска следите за миграцией и первым сканом:

```bash
docker compose ps
docker compose logs --since 15m --follow graph
```

Первый scan выполняется в startup lifecycle и может занять заметное время. Не прерывайте контейнер только потому, что readiness ещё не стала зелёной; сравнивайте длительность с обычной `artifact_graph_last_scan_duration_seconds` и установленным timeout окна изменения.

### 3.4. Проверка после deploy

Проверки API должны выполняться с web-авторизацией. Передавайте пароль через environment, а не аргумент командной строки или историю shell:

```bash
read -rsp 'Artifact Graph password: ' WEB_PASSWORD; echo
export WEB_PASSWORD WEB_USERNAME=root
export GRAPH_URL=https://artifact-graph.example.internal
bash scripts/smoke-linux.sh
python scripts/validate_live.py \
  --url "$GRAPH_URL" \
  --repository java-maven-api
unset WEB_PASSWORD
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

HTTP 200 от `/health/ready` сам по себе недостаточен: degraded scan со stale-данными считается пригодным для чтения, а при более позднем failed scan readiness может опираться на предыдущий usable scan.

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

При полном отказе upstream и наличии предыдущего графа сервис сохраняет карту доступной, помечает старые узлы `stale=true` и завершает scan со статусом `degraded`. При частичном отказе чтения build log/artifacts stale могут быть только затронутые build-ы. UI показывает stale banner и причину без токенов.

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
   docker compose ps graph
   docker compose logs --since 30m graph
   ```

5. Проверьте DNS, маршрут, proxy/firewall, срок сертификата и доверие CA из той же Docker network.
6. Проверьте состояние самого Bitbucket/TeamCity/Nexus по их штатным status page и аудит-логам.

Никогда не вставляйте токен в `curl -v`, incident chat или лог. Для проверки авторизации лучше создать временный read-only токен и передать его через защищённую environment/secret-инъекцию.

### 5.2. Типовые причины

- `401`/`403`: токен истёк или отозван, изменились scopes/permissions, заблокирована сервисная учётная запись. Выпустите новый read-only токен, проверьте scan, затем отзовите старый.
- `404`: изменился base URL, workspace/project/build type удалён либо endpoint скрыт reverse proxy. Сопоставьте endpoint из диагностики с конфигурацией.
- `429`: превышен rate limit. Не увеличивайте агрессивно retry; сократите частоту scan (`REFRESH_MINUTES`) или согласуйте лимит provider.
- `5xx`, timeout, connection reset: проверьте provider health, route, DNS и proxy. Сервис сам повторяет временные ошибки согласно `API_RETRY_ATTEMPTS` и backoff.
- TLS error: проверьте CA bundle, hostname/SAN и часы. Не устраняйте production-инцидент постоянным `VERIFY_TLS=false`.
- Registry/Nexus: проверьте Registry API v2 endpoint, repository routing и read permission manifest-ов. Ошибка registry может проявляться как `registryStatus=unavailable` для образа, не обязательно как полный отказ карты.
- TeamCity artifacts/build log: затронутый build может сохранить предыдущее содержимое как stale. Проверьте права чтения артефактов и логов отдельно от прав чтения build configuration.

После устранения причины запустите refresh кнопкой UI (она корректно передаёт CSRF), дождитесь завершения scan и проверьте `success`, отсутствие stale banner и восстановление метрик. Не удаляйте PostgreSQL и не очищайте граф: stale copy нужна пользователям и для диагностики.

## 6. Инциденты webhook/outbox

Webhook-и создаются для `scan.degraded`, `scan.recovered` и `graph.outputs.changed`. Outbox хранится в PostgreSQL, отправляется раз в минуту партиями до 20 записей и не делает scan неуспешным при недоступности receiver.

Повторяются transport errors, HTTP `408`, `425`, `429` и `5xx`; задержка растёт экспоненциально до 60 минут. Остальные `4xx` сразу переводят запись в `dead`. После `WEBHOOK_MAX_ATTEMPTS` временная ошибка тоже становится `dead`.

Диагностика очереди:

```bash
docker compose exec -T postgres psql -U graph -d graph -v ON_ERROR_STOP=1 -c \
  "SELECT status, count(*) FROM webhook_deliveries GROUP BY status ORDER BY status;"

docker compose exec -T postgres psql -U graph -d graph -v ON_ERROR_STOP=1 -c \
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
- `config/mapping-rules.yaml`;
- production Compose/override и идентификатор образа/Git SHA;
- секреты и `.env` — отдельно, в зашифрованном secret backup, не рядом с database dump;
- CA certificates и конфигурация reverse proxy.

`scripts/backup.sh` создаёт PostgreSQL custom-format dump, `<dump>.sha256` и `<dump>.json` с версией и временем. SHA-256 защищает от случайного повреждения, но не от умышленной подмены; храните dump, checksum и manifest в защищённом immutable/off-host хранилище с шифрованием и контролем доступа. У outbox пока нет встроенной очистки отправленных/dead записей, поэтому следите за размером `webhook_deliveries` и выполняйте согласованную retention-очистку только после backup.

Определите RPO/RTO, частоту и retention политикой эксплуатации. Практический минимум — ежедневный backup, отдельный pre-deploy backup и регулярная копия вне Docker host. Следите, чтобы каталог `backups/` не был единственным местом хранения: он исключён из Git, но остаётся на том же диске.

### 7.2. Проверка backup

```bash
bash scripts/verify-backup.sh backups/artifact-graph-YYYYmmddTHHMMSSZ.dump
```

Скрипт проверяет checksum, восстанавливает dump во временную базу `graph_verify_<pid>` на том же Compose PostgreSQL и выводит количество строк в основных таблицах. Он не завершает проверку ошибкой при нулевых counts, поэтому оператор должен сравнить их с ожидаемой baseline. Для production этого недостаточно как единственной проверки. Регулярно выполняйте disaster-recovery rehearsal в изолированном PostgreSQL:

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
3. проверьте `.sha256` и manifest;
4. подтвердите совместимость backup schema с выбранной версией приложения;
5. убедитесь, что отдельно доступны production `.env`, mapping rules и CA.

```bash
bash scripts/restore.sh \
  backups/artifact-graph-YYYYmmddTHHMMSSZ.dump \
  --confirm
```

После restore контейнер снова выполнит `alembic upgrade head`. Проверьте:

```bash
docker compose exec -T graph alembic current
docker compose ps graph postgres
docker compose logs --since 15m graph
```

Затем выполните полный authenticated smoke, проверьте `/api/status`, поиск репозитория, snapshots и webhook queue. Restore базы не восстанавливает `.env`, mapping rules, reverse proxy или внешние Bitbucket/TeamCity/Registry.

## 8. Rollback и совместимость схемы

Автоматический rollback в `scripts/deploy.sh` возвращает checkout и образ приложения на предыдущий commit, но оставляет базу на уже применённой Alembic revision. Поэтому успешный rollback кода не доказывает совместимость старого кода с новой схемой.

Перед релизом классифицируйте миграцию:

- additive и backward-compatible: новые nullable/default columns, новые таблицы/индексы без изменения старого контракта;
- incompatible: удаление/переименование колонок, изменение типа/семантики, обязательная data migration;
- irreversible: потеря данных при downgrade или невозможность безопасно восстановить старую семантику.

Предпочитайте forward-fix. Откатывайте приложение без базы только после теста старой версии на новой схеме. Для несовместимой схемы допустимы лишь заранее протестированные варианты:

1. явный `alembic downgrade <revision>` с одобренным планом сохранения данных; либо
2. восстановление pre-deploy dump и предыдущей версии приложения, принимая потерю данных после момента backup.

Никогда не запускайте downgrade вслепую в production. Все текущие migration-файлы имеют функцию `downgrade`, но наличие функции не гарантирует сохранение данных. После аварийного rollback восстановите нормальную ветку checkout: deploy script оставляет detached HEAD на предыдущем SHA.

## 9. Ротация секретов и сессий

### 9.1. Bitbucket, TeamCity и Registry/Nexus

Ротируйте по одному provider за раз:

1. выпустите новый read-only токен с теми же минимальными правами;
2. обновите защищённый `.env`/secret source;
3. пересоздайте только `graph`:

   ```bash
   docker compose up -d --no-deps --force-recreate graph
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

Ротация пароля PostgreSQL должна быть согласованной между database role и `DATABASE_URL`. Сначала проверьте новый credential на отдельном соединении, затем обновите приложение и пересоздайте `graph`; не оставляйте hardcoded `graph:graph`. При недоступности базы readiness станет 503, а приложение не сможет сохранять scan/outbox.

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
