# Artefact Graph

Все отдельные схемы находятся в [`docs/diagrams`](docs/diagrams/README.md):
редактируемая draw.io и автономная интерактивная HTML-карта.
Описание схем и инструкции по просмотру — в README этой папки.

Python-сервис строит интерактивный граф `Bitbucket project/repository → TeamCity project/build configuration → build`.
Он сопоставляет системы по нормализованному URL VCS root, находит `docker push` и `podman push` в
script steps и связанных исходниках (в том числе `pom.xml`), правило `**/sbom.json => artifacts`
и запуски каждой конфигурации за последние 90 дней (`TEAMCITY_BUILD_LOOKBACK_DAYS`).
Дополнительный лимит количества (`TEAMCITY_BUILD_LIMIT`) по умолчанию отключён: `0`.
Push и SBOM отражаются цветом и деталями соответствующего build, без отдельных узлов на карте.

Bitbucket подключается через общий контракт `RepositoryProvider`. Поддерживаются адаптеры
`cloud` и `datacenter`; формат графа и интерфейс от выбора адаптера не меняются.

Для перехода с Cloud на существующий корпоративный сервер см. [Bitbucket Data Center в production](#bitbucket-data-center-production).

## Схема работы сервиса

Быстрая установка Helm chart с Bitbucket Data Center: [charts/artifact-graph/README.md](charts/artifact-graph/README.md).

Развёртывание в Kubernetes через Helm (одна реплика, без HPA), миграции отдельным
Job, обновление и локальный kind-тест: [docs/kubernetes.md](docs/kubernetes.md).

`/metrics` требует веб-сессию либо отдельный `Authorization: Bearer <METRICS_TOKEN>`.
Для Prometheus задайте в `.env.prod` случайный `METRICS_TOKEN` (минимум 32 символа)
и используйте `authorization.credentials_file` в scrape-конфигурации. Токен даёт доступ
только к метрикам; если он не задан, доступ возможен только через веб-сессию.

```mermaid
flowchart LR
    S[Планировщик: startup / ручной / раз в час]
    BB[Bitbucket Data Center REST 1.0]
    TC[TeamCity REST API]
    RP[RepositoryProvider]
    TCC[TeamCityCollector]
    SA[Анализ pom.xml, Gradle, npm и скриптов]
    M[Сопоставление по нормализованным clone/VCS URL]
    O[Признаки build: docker/podman push и sbom.json]
    DB[(Внешний PostgreSQL)]
    API[FastAPI]
    UI[Интерактивный Cytoscape-граф]

    S --> BB --> RP --> SA --> M
    S --> TC --> TCC --> M
    TCC -->|build logs + artifacts| O
    SA -->|объявленные команды| O
    M --> O --> DB --> API --> UI
    DB -->|snapshots, diff, stale fallback| API
```

1. При старте, вручную и затем ежедневно в 03:00 МСК (UTC+03:00) сервис выполняет read-only обход BB и TC.
2. Адаптер Data Center читает пользовательские проекты, репозитории, default branch, текущую ревизию
   и нужные файлы. TeamCity-адаптер читает проекты, VCS roots, конфигурации и последние билды.
3. Репозиторий связывается с build configuration по нормализованному clone URL и URL VCS root.
   Сходство названий не считается доказательством связи; исключения задаются mapping rules.
4. Конфигурации и исходники анализируются на `docker push`/`podman push`, включая вызванные скрипты
   и `pom.xml`. Фактически выполненный push подтверждается по логу конкретного TeamCity build.
5. `sbom.json` считается найденным для билда, только если TeamCity опубликовал его как artifact.
6. Текущий граф, история сканов, snapshots, кэш и webhook outbox атомарно сохраняются в PostgreSQL.
   При временном отказе BB/TC последняя пригодная карта остаётся доступна с признаком `stale`.
7. FastAPI отдаёт фильтрованный граф; браузер показывает цепочку
   `BB project → repository → TC project → build configuration → build`, поиск, impact и diff.

Интерактивная подробная схема без внешних CDN находится в
[`docs/diagrams/service-architecture.html`](docs/diagrams/service-architecture.html).

## Быстрый запуск production: внешние BB, TC и PostgreSQL

Профиль внешней БД фиксирует `DATABASE_MODE=external`: приложение проверяет TLS до создания
SQLAlchemy engine и Alembic-миграций, включая прямой запуск Compose. Допустимы `sslmode=verify-ca`
и `verify-full`, а также явно заданный `prefer`; параметры, переопределяющие сервер через libpq, запрещены. Режим `internal`
используется только профилем встроенного сервиса `postgres`.

Этот вариант запускает **только Artifact Graph**. Существующие Bitbucket Data Center, TeamCity и
PostgreSQL сервис не создаёт, не перезапускает и не изменяет, кроме Alembic-миграций в выделенной
для него базе. Перед запуском DBA должен создать отдельную database и login-role, разрешить соединение
с Docker-хоста по TLS и настроить backup/PITR.

На Linux-хосте:

```bash
sudo git clone https://bitbucket.isb/scm/infsec/artifact_graph.git /opt/artefact-graph-prod
sudo chown -R "$(id -u):$(id -g)" /opt/artefact-graph-prod
cd /opt/artefact-graph-prod
umask 077
cp .env.prod.example .env.prod
chmod 600 .env.prod
openssl rand -hex 32
```

Последнее значение сохраните как `WEB_PASSWORD`. Заполните `.env.prod` локально, не присылая секреты
в чат и не коммитя файл:

```env
DATABASE_URL=postgresql+psycopg://artifact_graph:<percent-encoded-password>@postgres.company.example:5432/artifact_graph?sslmode=verify-full

BITBUCKET_PROVIDER=datacenter
BITBUCKET_URL=https://bitbucket.company.example/bitbucket
BITBUCKET_TOKEN=<Bitbucket-Data-Center-RO-token>

TEAMCITY_URL=https://teamcity.company.example/teamcity
TEAMCITY_PUBLIC_URL=https://teamcity.company.example/teamcity
TEAMCITY_TOKEN=<TeamCity-RO-token>
TEAMCITY_BUILD_LIMIT=0

WEB_USERNAME=root
WEB_PASSWORD=<random-value-from-openssl>
GRAPH_PORT=18080
# Плановое обновление: ежедневно в 03:00 МСК
```

Если BB, TC или PostgreSQL используют внутренний CA, поместите публичный PEM bundle на хост и добавьте:

```env
TLS_CA_HOST_DIR=/etc/artifact-graph/certs
TLS_CA_FILE=/app/certs/company-ca.pem
```

Для PostgreSQL добавьте к `DATABASE_URL` параметр
`&sslrootcert=/app/certs/company-ca.pem`. Затем проверьте конфигурацию и запустите:

```bash
cd /opt/artefact-graph-prod
docker compose --project-name artefact-graph-prod --env-file .env.prod \
  -f compose.prod.external-db.yaml config --quiet

SMOKE_TIMEOUT_SECONDS=900 bash scripts/deploy.sh --prod-external-db \
  https://bitbucket.isb/scm/infsec/artifact_graph.git /opt/artefact-graph-prod
```

Проверка после запуска:

```bash
docker compose --project-name artefact-graph-prod --env-file .env.prod \
  -f compose.prod.external-db.yaml ps
docker compose --project-name artefact-graph-prod --env-file .env.prod \
  -f compose.prod.external-db.yaml logs --since 15m graph

GRAPH_URL=https://graph.company.example SMOKE_ENV_FILE=.env.prod \
  SMOKE_TIMEOUT_SECONDS=900 bash scripts/smoke-linux.sh
```

Пользовательский доступ должен идти через HTTPS reverse proxy к `127.0.0.1:18080`; production использует
Secure cookie. Успешный запуск означает `healthy`, свежий `lastScan.status=success`, непустой граф и
отсутствие неожиданных `stale`-узлов. Полная подготовка прав, CA, mapping rules и приёмка описаны ниже.

## Быстрый старт demo

```bash
cp .env.example .env
# Задайте свой WEB_PASSWORD в .env; при занятом 8080 смените GRAPH_PORT.
docker compose up -d --build postgres graph
```

Откройте `http://localhost:8080`. Demo содержит 10 репозиториев. Каждому продукту соответствует
отдельный TeamCity project с цепочкой `Test → Build, push and SBOM → Deploy` и
последними тремя запусками каждой стадии.
Кнопка обновления запускает скан вручную, плановый скан выполняется раз в 60 минут.

## Локальная отладка через Docker на Linux

Нужны Git, Docker Engine с запущенным daemon и Docker Compose v2 (`docker compose version`).
Работайте из корня клонированного репозитория. Этот режим запускает только приложение
и PostgreSQL с demo-данными: доступ к BB/TC не нужен, реальные сборки не запускаются.

Создайте отдельный файл `.env.debug` (не заменяйте существующий `.env`), ограничьте
его права через `chmod 600 .env.debug` и заполните:

```dotenv
APP_MODE=demo
GRAPH_BIND=127.0.0.1
GRAPH_PORT=18085
DATABASE_URL=postgresql+psycopg://graph:graph@postgres:5432/graph
WEB_AUTH_ENABLED=true
WEB_USERNAME=root
WEB_PASSWORD=replace-with-your-random-password
WEB_COOKIE_SECURE=false
REGISTRY_ENABLED=false
WEBHOOK_ENABLED=false
```

Замените пароль своим случайным значением (например, результатом `openssl rand -hex 24`).
Не коммитьте `.env.debug`. Переменные текущего shell имеют приоритет над env-файлом:
не используйте shell с экспортированными production-настройками для этого запуска.

```bash
# Проверка настроек без вывода секретов
docker compose -p artifact-graph-debug --env-file .env.debug -f compose.yaml config --quiet
# Сборка и запуск в отдельном Compose-проекте и с отдельным томом БД
docker compose -p artifact-graph-debug --env-file .env.debug -f compose.yaml up -d --build postgres graph
# Состояние и готовность
docker compose -p artifact-graph-debug --env-file .env.debug -f compose.yaml ps
curl --fail --show-error http://127.0.0.1:18085/health/ready
# Логи приложения (Ctrl+C завершает просмотр, но не контейнеры)
docker compose -p artifact-graph-debug --env-file .env.debug -f compose.yaml logs -f --tail 100 graph
```

Откройте `http://localhost:18085`, войдите как `root` с паролем из `.env.debug`.
Дождитесь готовности: при старте выполняются миграции и первый сбор demo-графа.
Если порт занят, измените `GRAPH_PORT` и используйте выбранный порт в URL и проверках.
Для удалённой Linux-машины оставьте привязку к loopback и откройте SSH-туннель
на своём компьютере: `ssh -N -L 18085:127.0.0.1:18085 user@linux-host`.

```bash
# После изменения исходников пересоберите приложение
docker compose -p artifact-graph-debug --env-file .env.debug -f compose.yaml up -d --build graph
# Остановить стенд, сохранив БД
docker compose -p artifact-graph-debug --env-file .env.debug -f compose.yaml down
```

Не добавляйте `--volumes` к `down`, если данные нужно сохранить.
Это отладка по логам: текущий Compose не включает hot reload и `debugpy`/IDE breakpoints.
Режим с реальными источниками (`APP_MODE=live`) требует отдельных RO-токенов и настроек
BB/TC из соответствующих разделов ниже; demo не проверяет доступность этих систем.

## Диагностика зависшего старта (Docker Compose)

Для предварительного обхода Bitbucket без миграций, запуска сервиса и записи графа:

```bash
docker compose -p artefact-graph-prod --env-file .env.prod -f compose.prod.yaml build graph
docker compose -p artefact-graph-prod --env-file .env.prod -f compose.prod.yaml run --rm --no-deps graph python -m app.diagnose --dry-run --expect-projects INFSEC --timeout 600 --max-files 1000
```

Для внешней БД замените файл на `compose.prod.external-db.yaml`. Этот режим не
подключается к БД: он обходит выбранные проекты, индекс файлов и читает скрипты/
build-файлы через API. Не проверяет рабочую директорию TeamCity и не доказывает
фактическое выполнение найденных скриптов. `--expect-projects` сверяет ожидаемый
список с настройкой внутри контейнера до сетевых запросов. Используйте заглавное
`BITBUCKET_PROJECT_KEYS=INFSEC` в env-файле Compose. В логе есть fingerprint кода:
разные значения указывают на разные версии диагностируемых модулей.
В dry-run `--timeout` ограничивает весь обход, а `--max-files` — число кандидатов
исходников. Ошибка API, неразрешённые ссылки, несовпадение проектов, превышение
лимита или таймаут дают код `1`, а не успешный результат частичной проверки.

`LOG_LEVEL=DEBUG` включён по умолчанию (Compose и Helm тоже передают его).
Dry-run всегда использует DEBUG. При обычном запуске логи показывают
`scan_start`, `bitbucket_project_start`, `bitbucket_repository_start/ready`,
страницы репозиториев, `Bitbucket file index`, `source_read`,
`source_analysis_start/complete`, `teamcity_configuration_start` и `scan_finish`.
В событиях указаны ключи проектов, репозитории, commit и пути; у ответов API —
статус и длительность. HTTP-заголовки, query-параметры и тела ответов не выводятся.
Секреты из настроек редактируются. Для снижения детализации задайте `LOG_LEVEL=INFO`
и пересоздайте контейнер. Эти логи позволяют определить текущий этап, но не дают
процент или оценку оставшегося времени всего сбора.

`Waiting for application startup` может означать первый обход BB/TC: HTTP-сервер
начинает принимать запросы после него. Для проверки зависимостей независимо от
текущего сбора запустите Python-диагностику. Команды для macOS/Linux (Bash/zsh):

```bash
# После обновления кода пересоберите образ; это перезапустит приложение и первый сбор.
docker compose -p artefact-graph-prod --env-file .env.prod -f compose.prod.yaml up -d --build graph
docker compose -p artefact-graph-prod --env-file .env.prod -f compose.prod.yaml exec -T graph python -m app.diagnose --timeout 15
```

Для внешней PostgreSQL замените `compose.prod.yaml` на
`compose.prod.external-db.yaml`. Имя проекта и env-файл должны совпадать с запуском.
Если контейнер падает и `exec` недоступен, после сборки образа используйте отдельный
контейнер с той же конфигурацией (без запуска Uvicorn и миграций):

```bash
docker compose -p artefact-graph-prod --env-file .env.prod -f compose.prod.yaml run --rm --no-deps graph python -m app.diagnose --timeout 15
```

Диагностика выполняет валидацию настроек, PostgreSQL `SELECT 1`, чтение одной страницы
репозиториев Bitbucket Data Center и TeamCity `/app/rest/server`. При заданном
`BITBUCKET_PROJECT_KEYS` проверяет каждый выбранный проект. Показывает начало, время
и результат этапа; не выводит токены, URL БД, тела ответов и тексты исключений.
Код выхода `0` означает успех всех проб, `1` — ошибку, `2` — неверный аргумент CLI.
`--timeout` ограничивает отдельные HTTP-операции, подключение и SQL-запрос, а не
суммарное время всех проверок. Проверки чтения не запускают сборки и не меняют граф.

- `401/403`: проверить токен и права; `3xx`: базовый URL, context path и SSO.
- `ConnectError/ConnectTimeout`: проверить DNS, VPN, firewall и доверие сертификатам.
- `ReadTimeout`: сервер не ответил вовремя; `FileNotFoundError`: проверить CA внутри контейнера.
- `HTTP 200 but not JSON`: возможно, вместо API отвечает страница входа.

Это проверка доступности отдельных API, а не процент текущего сбора и не проверка
всех прав на файлы/билды. Для нагрузки используйте `docker stats`, для логов —
`docker compose ... logs -f --timestamps graph`.

При анализе ссылок на исходники пути считаются относительно корня checkout:
`./lib/log.sh` и `ci/../lib/log.sh` нормализуются в `lib/log.sh`. Ссылки вроде
`../lib/log.sh`, выходящие за корень, пропускаются с предупреждением
`Skipping unresolved repository source path`, не прерывая сбор. Сборщик не исполняет
shell и не отслеживает `cd`/рабочую директорию; содержимое пропущенного файла остаётся
непроверенным. Защита API URL от traversal остаётся включённой.

Для Bitbucket Data Center перед первым чтением исходников строится индекс всех
файлов репозитория через `GET /rest/api/1.0/projects/{key}/repos/{slug}/files`.
Все страницы обходятся по `nextPageStart`, а параметр `at` фиксируется на commit,
полученном при обходе default branch. Индекс и `/raw/{path}` используют один commit.
Чтение `/raw` выполняется только при точном совпадении полного пути от корня
репозитория с индексом. Отсутствующий путь не запрашивается; поиск по basename,
суффиксу и подмена `../lib/log.sh` на `lib/log.sh` не выполняются.
Индекс переиспользуется для того же проекта/репозитория/commit в пределах сбора;
смена commit создаёт новый индекс. Ошибка доступа или повреждённая страница индекса
не заменяется пустым списком и не приводит к обходу без проверки путей.
Это добавляет запросы списка файлов, но устраняет запросы содержимого несуществующих
путей. Содержимое всех файлов подряд не скачивается. Старый кэш анализа исходников
не переиспользуется после перехода на индекс. Cloud сохраняет прежний способ чтения.
Контракт API: [Bitbucket Data Center Repository API](https://developer.atlassian.com/server/bitbucket/rest/v1000/api-group-repository/).

## Сбор только выбранных проектов Bitbucket Data Center

В `.env.prod` (либо env-файле локального запуска) укажите **ключи проектов**, не имена:

```dotenv
BITBUCKET_PROVIDER=datacenter
BITBUCKET_PROJECT_KEYS=CORE,PAY,INFRA
```

Пробелы вокруг ключей и повторы удаляются; регистр сохраняется — используйте точные
ключи Bitbucket. Пустое значение сохраняет обход всех доступных репозиториев.
С заданным списком сборщик запрашивает `/projects/{key}/repos` только для выбранных
проектов, включая все страницы. Неизвестный/недоступный проект приводит к ошибке
сбора, а не к незаметному переходу на обход всех проектов. Для Cloud настройка не применяется.

После изменения пересоздайте `graph` командой `up -d graph` с теми же Compose-файлом,
проектом и env-файлом. Для Helm добавьте в приватный values-файл:

```yaml
config:
  BITBUCKET_PROJECT_KEYS: "CORE,PAY,INFRA"
```

Обновляйте Helm по процедуре в `docs/kubernetes.md`. Ограничение относится к Bitbucket:
обход TeamCity настраивается отдельно через `TEAMCITY_BUILD_TYPE_IDS`. После успешного сбора
текущий граф перестраивается; история и snapshots сохраняются. При ошибке сбора
может остаться прежний граф с признаком `stale`, включая ранее выбранные проекты.

## Сбор только выбранных build configurations TeamCity

Задайте в env-файле Compose список **ID конфигураций**, а не их отображаемые имена
и не ID проектов TeamCity:

```dotenv
TEAMCITY_BUILD_TYPE_IDS=Infsec_Agent_Build,Infsec_Agent_Publish
```

Пустое значение использует фильтр проектов ниже; если оба списка пусты — все доступные конфигурации.
Пробелы вокруг ID и повторы удаляются, регистр сохраняется. При заданном списке
сборщик запрашивает каждую конфигурацию напрямую; общий список `/app/rest/buildTypes`
не обходится. Детали, билды, логи и артефакты собираются только для выбранных
конфигураций. Ошибка доступа или отсутствующий ID не переключают сбор на все
конфигурации: сбор завершится ошибкой, возможен прежний граф с признаком `stale`.
Связанные конфигурации зависимостей автоматически к списку не добавляются.

Для Helm:

```yaml
config:
  TEAMCITY_BUILD_TYPE_IDS: "Infsec_Agent_Build,Infsec_Agent_Publish"
```

После изменения пересоздайте контейнер (`dc up -d graph` с вашим Compose-проектом)
или обновите Helm по инструкции. На старте лог `teamcity_scope` показывает фактический
список либо `ALL`. `TEAMCITY_BUILD_LIMIT` задаёт дополнительный предел числа билдов каждой конфигурации (`0` — без ограничения)
и работает независимо от списка ID. Фильтр `BITBUCKET_PROJECT_KEYS` также независим:
для сужения обеих систем задайте оба списка. Dry-run Bitbucket не обходит TeamCity
и не проверяет доступность выбранных build configurations.

## Вход через LDAP / Active Directory

В `.env.prod` задайте (пример для AD):

```dotenv
WEB_AUTH_PROVIDER=ldap
LDAP_URL=ldaps://ad.company.example:636
LDAP_BIND_DN=CN=graph-reader,OU=Service Accounts,DC=company,DC=example
LDAP_BIND_PASSWORD=<пароль служебной учётной записи>
LDAP_USER_BASE_DN=OU=Users,DC=company,DC=example
LDAP_USER_FILTER=(&(objectClass=user)(sAMAccountName={username})(memberOf=CN=GraphUsers,OU=Groups,DC=company,DC=example))
LDAP_CA_FILE=/app/certs/company-ca.pem
LDAP_TIMEOUT_SECONDS=5
```

Служебной учётной записи достаточно прав чтения каталога. Для OpenLDAP можно использовать
`LDAP_USER_FILTER=(uid={username})`. Фильтр определяет, кому разрешён вход; пример AD
разрешает прямых участников GraphUsers. Все допущенные пользователи имеют одинаковые права
в приложении, включая ручное обновление. Поиск должен вернуть ровно одного пользователя,
затем пароль проверяется отдельным bind от его DN. Пустые пароли запрещены.
Значение логина экранируется перед подстановкой в LDAP-фильтр.

`ldaps://` использует TLS сразу, `ldap://` обязательно выполняет StartTLS до передачи пароля.
Проверка сертификата обязательна; CA смонтируйте через существующий `TLS_CA_HOST_DIR`.
При публичном CA `LDAP_CA_FILE` можно оставить пустым. LDAP-запросы ограничены таймаутом
и выполняются вне event loop. Пароли пользователей не сохраняются.

`WEB_PASSWORD` оставьте случайным секретом длиной от 32 символов: он подписывает сессии.
В LDAP-режиме локальный логин отключён, автоматического отката при отказе LDAP нет.
Чтобы вернуть локальный вход, явно задайте `WEB_AUTH_PROVIDER=local` и пересоздайте контейнер.
Смена режима аннулирует существующие сессии; изменения прав в LDAP применяются при следующем
входе, уже выданные сессии действуют до выхода или истечения `WEB_SESSION_HOURS`.
Для Helm обычные параметры задаются в `config`, `LDAP_BIND_PASSWORD` — ключом в `existingSecret`.
После обновления кода пересоберите образ; одних изменений env для установки LDAP-библиотеки недостаточно.

## PostgreSQL с sslmode=prefer

Допускается явно заданный URL:

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@db.company.example:5432/graph?sslmode=prefer
```

`prefer` сначала пробует TLS, но допускает соединение без шифрования. Для проверки
сертификата и имени сервера продолжайте использовать `verify-full`. Настройка относится
только к PostgreSQL и не отключает проверку TLS для LDAP, Bitbucket или TeamCity.

## Доступность веб-интерфейса при сборе

Во время скана в верхней части интерфейса отображаются этап, длительность, текущий
проект/репозиторий/конфигурация/билд и счётчики обработки. Обновление — раз в минуту.
При `BITBUCKET_PROJECT_KEYS` процент показывает долю проектов с завершённым обходом
репозиториев (это не процент последующего анализа исходников).
При `TEAMCITY_PROJECT_IDS` показывается процент обработанных конфигураций каждого
выбранного проекта вместе с подпроектами. При явном `TEAMCITY_BUILD_TYPE_IDS`
процент считается только по этому списку, проекты TeamCity игнорируются.
Количество конфигураций становится известно после получения списка из TeamCity.
Пустой выбранный проект отображается как 100% (0/0); обработанные с ошибкой
конфигурации учитываются как пройденные, итоговое качество отражается статусом скана.
Проценты измеряют количество объектов, а не оставшееся время. Сохранение графа — отдельный этап.

Первичный сбор выполняется в фоне. После проверки настроек веб-интерфейс принимает
запросы, не ожидая Bitbucket/TeamCity. При повторном запуске отображается сохранённый
граф; при первом — пустая карта со статусом сбора. Интерфейс проверяет состояние каждые
минуту; пока скан выполняется, повторно загружает доступный граф и списки проектов,
а после завершения — итоговые данные. Фильтры и положение карты сохраняются.
Сбор публикует новый граф после завершения: при первом скане карта до этого может
оставаться пустой, при повторном показывается предыдущий сохранённый граф.
Ошибка первичного сбора не останавливает веб: можно посмотреть статус и повторить сбор вручную.
Проверки настроек и миграции БД всё ещё выполняются до открытия HTTP.
`/health/ready` проверяет доступность БД, а `graphReady` сообщает о наличии пригодного скана.

## Расписание и глубина истории

Глубина истории TeamCity задаётся в `.env` / `.env.prod`:

```dotenv
TEAMCITY_BUILD_LOOKBACK_DAYS=90
TEAMCITY_BUILD_LIMIT=0
```

Глубина — положительное целое число дней назад от момента запроса, по времени старта билда;
для очереди используется время постановки. API фильтрует историю по дате, страницы обходятся последовательно
([TeamCity API](https://www.jetbrains.com/help/teamcity/rest/get-build-details.html)).
`TEAMCITY_BUILD_LIMIT=0` позволяет получить всю доступную историю в этом окне;
значение 1–100 дополнительно ограничивает число билдов каждой конфигурации, с приоритетом очереди.
В существующем env-файле замените прежний лимит 3/5 на 0, если нужна вся история за 90 дней.
Удалённые политикой хранения TeamCity билды восстановить через API нельзя.
Для Helm используются одноимённые ключи в `config`. При `LOG_LEVEL=DEBUG` событие
`teamcity_build_window` показывает конфигурацию, глубину, дату отсечения и лимит.

Плановый сбор выполняется раз в сутки в **03:00 МСК (UTC+03:00)** независимо
от часового пояса хоста или контейнера. Первичный сбор при старте и ручной запуск сохраняются.
Старая переменная `REFRESH_MINUTES` больше не используется; её можно удалить из своего env-файла.
В `/api/status` поле `refreshSchedule` содержит расписание и `nextRun` с часовым смещением;
`refreshMinutes=1440` сохранено для совместимости проверок свежести графа.
Лог `refresh_schedule` также показывает ближайший запуск.
Если сервис выключен в 03:00, при следующем старте выполняется первичный сбор,
а следующий плановый запуск назначается на ближайшие 03:00. Задержка работающего
планировщика до пяти минут допускается; более поздний пропущенный запуск не догоняется.

## Сбор выбранных проектов TeamCity

В `.env` / `.env.prod` задайте ID проектов, а не отображаемые названия:

```dotenv
TEAMCITY_PROJECT_IDS=Infsec,Platform
TEAMCITY_BUILD_TYPE_IDS=
```

Собираются конфигурации выбранных проектов и всех их подпроектов любой глубины.
Повторы при пересечении деревьев обрабатываются один раз. Пробелы вокруг ID
удаляются, регистр сохраняется. Используется серверный фильтр `affectedProject`
с пагинацией ([TeamCity API](https://www.jetbrains.com/help/teamcity/rest/manage-build-configuration-details.html)).
Недоступный или несуществующий выбранный проект приводит к ошибке без перехода к сбору всех проектов.

**Приоритет:** непустой `TEAMCITY_BUILD_TYPE_IDS` полностью переопределяет выбор проектов:
обрабатываются только перечисленные конфигурации, даже если они находятся вне `TEAMCITY_PROJECT_IDS`.
Если оба списка пусты, собираются все доступные конфигурации.
В Helm задайте `config.TEAMCITY_PROJECT_IDS: "Infsec,Platform"`.
После обновления кода пересоберите образ и пересоздайте контейнер (`dc up -d --build graph`).
Лог `teamcity_scope` показывает эффективный режим, `teamcity_project_selected` — выбранные
корневые проекты, а при `LOG_LEVEL=DEBUG` выводятся конфигурации, их непосредственные проекты и выбранный корень.
Bitbucket dry-run не проверяет TeamCity.

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
WEB_USERNAME=root
WEB_PASSWORD=use-a-long-random-password
```

Для workspace/project access token используйте `BITBUCKET_AUTH=access_token` и оставьте
`BITBUCKET_EMAIL` пустым. Приложение выполняет только GET-запросы.

## Полный стенд с TeamCity

```bash
docker compose --profile build-lab up -d --build postgres graph teamcity teamcity-agent registry
```

1. Завершите бесплатную настройку TeamCity Professional на `http://localhost:8111`.
2. Создайте Cloud workspace и тестовые репозитории.
3. Скопируйте `.env.example` в `.env`, запишите read-only токены, смените `APP_MODE=live` и перезапустите `graph`.

Для расширения уже существующего Cloud-стенда используйте [приёмочную матрицу lab](docs/lab-acceptance.md).
Она добавляет 10 BB-проектов, 11 репозиториев и 16 TC-конфигураций, не удаляя исходные данные:
реальный push из Maven `pom.xml`, два Docker-тега, нативный Podman, несколько SBOM,
общий TC-проект, multi-root checkout, отрицательные сценарии и границы поиска 10/10.
В инструкции отдельно описаны создание (`expand_lab.py`), запуск билдов (`run_lab.py`),
RO-проверка результата (`validate_lab.py`), временные running/queued/paused-состояния и браузерные тесты.
Изменяющие стенд команды требуют явного `--apply`; они не предназначены для production.
Ротация checkout-токена выполняется отдельно: изменение `.env` не заменяет секреты существующих
TC VCS roots; безопасная ручная процедура приведена в той же инструкции.

Локальный Bitbucket Data Center сохранён только как необязательный профиль и по умолчанию не запускается:

```bash
docker compose --profile datacenter up -d bitbucket-db bitbucket
```

Сервис использует только `GET` к Bitbucket и TeamCity. Токены bootstrap-администратора приложению
не передаются. Web UI защищён общей учётной записью и подписанной HttpOnly-cookie; порт 8080 всё
равно следует публиковать только через HTTPS reverse proxy или во внутренней сети.

<a id="bitbucket-data-center-production"></a>

## Bitbucket Data Center в production

### 1. Что переключаем

Это подключение **графа** к уже работающим Bitbucket Data Center и TeamCity, а не перенос
репозиториев из Cloud в DC и не установка/лицензирование этих систем.
Используйте отдельные production Compose, `.env.prod` и checkout `/opt/artefact-graph-prod`.
`compose.prod.yaml` запускает `graph` и выделенный PostgreSQL, а `compose.prod.external-db.yaml` —
только `graph` с подключением к существующему PostgreSQL. Лабораторные TC, агент, registry и BB
не запускаются. Не объединяйте production-файлы с `compose.yaml` или друг с другом через несколько `-f`.

Один экземпляр графа работает с одним BB provider и одним сервером TC одновременно. Для
Cloud-стенда и DC-prod нужны отдельные БД. Prod Compose использует проект `artefact-graph-prod`;
во встроенном режиме создаётся volume `artefact-graph-prod_graph-db`, а во внешнем — выделенная
database существующего PostgreSQL. Lab-данные не переиспользуются.
Не копируйте Cloud dump в новую DC-БД: история, snapshots и старый граф относятся к прежнему источнику.
При последующей смене самого сервера BB/TC также подготовьте отдельную БД/инсталляцию;
при отказе upstream приложение намеренно сохраняет предыдущую карту, а не удаляет её.

Для существующего PostgreSQL используйте только режим `--prod-external-db`. База должна быть
PostgreSQL 14+ (рекомендуется 16), отдельной от других приложений, с отдельными database и login-role.
Миграции Alembic автоматически выполняются ролью приложения при старте, поэтому ей нужны права
создания и изменения объектов в своей базе/schema. Администрирование, replication и доступ к чужим
базам не нужны. Backup, PITR, HA и восстановление внешней базы остаются ответственностью её владельца;
локальные `scripts/backup.sh` и `scripts/restore.sh` предназначены только для встроенного PostgreSQL.

Контракт DC REST API для ветки 8.19 покрыт автоматическими тестами: проекты/репозитории,
пагинация, default branch с именем не `main`, точная ревизия, чтение файлов, пустые репозитории,
ошибки авторизации и общий граф с TC. Это **не** подтверждение подключения к вашему prod:
его сеть, права, версия TC, плагины и реальные сборки требуют пилотной проверки из пункта 7.

### 2. Подготовьте Linux-хост и RO-учётные записи

Нужны Docker Engine с Compose v2, Git, Bash, Python 3.9+ для deploy/smoke, curl и OpenSSL.
Сам сервис использует Python 3.12 внутри контейнера. Пользователь деплоя должен иметь права
на рабочий каталог и Docker. Доступ к Docker фактически привилегированный: его нельзя выдавать
всем пользователям графа. Запускайте только один `graph` и один worker Uvicorn.

Из контейнера должны быть доступны DNS, маршруты/VPN и HTTPS до BB и TC. `localhost` внутри
контейнера — сам контейнер, а не корпоративный сервер. Для сборки образа также нужен доступ
к источникам Docker, Debian и Python-пакетов либо их корпоративным зеркалам.

Bitbucket Data Center:

1. Создайте отдельного пользователя, например `artifact-graph-reader`, без административных прав.
2. Выдайте ему чтение нужных проектов и репозиториев, включая будущие репозитории через проектные
   права/группы. Если требуется карта всего BB, чтение нужно на все пользовательские проекты.
3. Под этой учёткой откройте **Manage account → HTTP access tokens → Create token**.
4. Создайте отдельный токен интеграции с **Project read / Repository read**, без write/admin,
   и сроком действия по политике организации. Сохраните в `BITBUCKET_TOKEN` файла `.env.prod`.

DC-адаптер использует `Authorization: Bearer`. Cloud API token с `id.atlassian.com` не заменяет
токен вашего DC-сервера; email и workspace для DC не нужны.
Права токена не расширяют права пользователя. См. [HTTP access tokens Atlassian](https://confluence.atlassian.com/bitbucketserver/personal-access-tokens-939515499.html).

TeamCity:

1. Создайте отдельного пользователя без прав запуска/изменения билдов.
2. Включите per-project permissions, если сервер использует упрощённый режим, и назначьте
   `Project Viewer` на нужные проекты с наследованием на подпроекты.
3. Проверьте эффективные права, включая `All Users` и другие группы: роль Viewer не отменяет
   Developer/Admin, унаследованные отдельно. Нужны чтение настроек, VCS roots, билдов, логов и артефактов.
4. В профиле этого пользователя создайте access token и запишите в `TEAMCITY_TOKEN`.

При кастомных ролях сверяйте фактические разрешения с [документацией ролей TeamCity](https://www.jetbrains.com/help/teamcity/managing-roles-and-permissions.html).
Значение переменной с названием `TOKEN` само по себе не делает доступ read-only: это обеспечивают
права на стороне BB/TC. Runtime выполняет только GET к этим системам и не запускает сборки.
`BB_BOOTSTRAP_TOKEN`, `BB_CHECKOUT_TOKEN`, `TC_ADMIN_TOKEN` и bootstrap-скрипты для prod-графа не нужны.

### 3. Создайте отдельную конфигурацию

Команды ниже выполняются пользователем деплоя в заранее подготовленном каталоге `/opt`:

```bash
git clone https://bitbucket.isb/scm/infsec/artifact_graph.git /opt/artefact-graph-prod
cd /opt/artefact-graph-prod
umask 077
cp .env.prod.example .env.prod
chmod 600 .env.prod
openssl rand -hex 32
openssl rand -hex 32
```

Два разных результата OpenSSL сохраните соответственно как `POSTGRES_PASSWORD` и `WEB_PASSWORD`.
Не используйте примерные значения, не отправляйте пароли/токены в чат и не коммитьте заполненный файл.
`.env.prod`, другие `.env.*` с секретами и `config/certs/` исключены из Git и Docker build context.
Не выполняйте `docker compose config` без `--quiet`: полный вывод содержит секреты.

Основные значения в `.env.prod` (адреса замените реальными, секреты задайте локально):

```env
BITBUCKET_PROVIDER=datacenter
BITBUCKET_URL=https://bitbucket.company.example/bitbucket
BITBUCKET_TOKEN=<DC-read-only-token>
TEAMCITY_URL=https://teamcity.company.example/teamcity
TEAMCITY_PUBLIC_URL=https://teamcity.company.example/teamcity
TEAMCITY_TOKEN=<TC-read-only-token>
TEAMCITY_BUILD_LIMIT=0
# Плановое обновление: ежедневно в 03:00 МСК
GRAPH_PORT=18080
POSTGRES_PASSWORD=<first-random-hex-value>
WEB_USERNAME=root
WEB_PASSWORD=<second-random-hex-value>
```

Если используется существующий PostgreSQL, `POSTGRES_PASSWORD` не нужен. Добавьте URL с обязательной
проверкой TLS; зарезервированные символы в имени/пароле должны быть percent-encoded:

```env
DATABASE_URL=postgresql+psycopg://artifact_graph:<encoded-password>@postgres.company.example:5432/artifact_graph?sslmode=verify-full
```

При внутреннем CA добавьте в тот же URL `&sslrootcert=/app/certs/company-ca.pem` и настройте
`TLS_CA_HOST_DIR` ниже. Режим `--prod-external-db` отклоняет `sslmode=require`, `sslmode=disable`,
неполный или не-psycopg URL. Пароль базы не передавайте в Git URL, командной строке или чат.

| Настройка | Как заполнить |
|---|---|
| `BITBUCKET_URL` | Базовый HTTPS URL сервера. Сохраните `/bitbucket`, если такой context path действительно есть; не добавляйте `/rest/api`, `/projects/...` или Cloud workspace URL. |
| `TEAMCITY_URL` | HTTPS URL, доступный контейнеру, с реальным context path при наличии. |
| `TEAMCITY_PUBLIC_URL` | HTTPS URL того же TC для ссылок в браузере; может иметь другой hostname/context path. |
| `BITBUCKET_EMAIL`, `BITBUCKET_WORKSPACE`, `BITBUCKET_AUTH` | Только Cloud; для DC первые два можно оставить пустыми, третий игнорируется. |
| `TEAMCITY_BUILD_LIMIT` | По умолчанию 0 — все доступные билды в окне истории; 1–100 — дополнительный общий лимит с учётом очереди. |
| `TEAMCITY_BUILD_LOOKBACK_DAYS` | Положительное число дней истории, по умолчанию 90. |
| `POSTGRES_PASSWORD` | Отдельный случайный пароль, минимум 32 символа из `A-Z a-z 0-9 _ -`; hex из команды выше подходит для URL подключения к БД. |
| `DATABASE_URL` | Только для `--prod-external-db`: полный `postgresql+psycopg://` URL отдельной базы с `sslmode=verify-full`, `verify-ca` или явно выбранный `prefer`. |
| `WEB_PASSWORD` | Другой случайный пароль минимум 32 символа; рекомендуется hex, чтобы исключить интерполяцию `$` в Compose. |

Prod Compose принудительно задаёт `APP_MODE=live`, `DEPLOYMENT_MODE=production`,
`VERIFY_TLS=true`, `WEB_AUTH_ENABLED=true`, `WEB_COOKIE_SECURE=true`.
Приложение проверяет эти условия при старте. Переменные prod задаются через `--env-file .env.prod`;
обычный `.env` принадлежит lab и здесь не используется. Уберите из shell старые переменные BB/TC,
портов и паролей, если они экспортированы: environment shell имеет приоритет над env-файлом Compose.

### 4. Корпоративный CA и HTTPS для браузера

Если BB/TC используют публичные доверенные сертификаты, оставьте `TLS_CA_FILE` пустым.
Для внутреннего CA разместите PEM bundle доверенных сертификатов на хосте, например
`/etc/artifact-graph/certs/company-ca.pem`, и задайте:

```env
TLS_CA_HOST_DIR=/etc/artifact-graph/certs
TLS_CA_FILE=/app/certs/company-ca.pem
```

Каталог монтируется read-only. Он должен существовать; файлы должны читаться UID/GID `10001:10001`
(для публичных сертификатов обычно достаточно каталогов `0755` и файлов `0644`).
Не кладите туда закрытые ключи. CA добавляется к системным доверенным корням для BB/TC/registry;
ошибочный или отсутствующий bundle останавливает старт. Это не заменяет корректные SAN/hostname
и срок действия серверного сертификата. Не исправляйте TLS-ошибку через `VERIFY_TLS=false`.

Готовый Nginx входит в бандл как `compose.https.yaml`: [запуск HTTPS на 443](docs/https-compose.md).
Можно также использовать собственный reverse proxy:
порт графа публикуется только на `127.0.0.1:18080`. Поставьте перед ним HTTPS reverse proxy
на этом Linux-хосте. Минимальный фрагмент Nginx внутри уже настроенного TLS `server`:

```nginx
location / {
    proxy_pass http://127.0.0.1:18080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    client_max_body_size 8k;
}
```

TLS-сертификат, DNS, HSTS, login rate limit и ACL настройте по правилам своей инфраструктуры.
Предполагается публикация в корне hostname, например `https://graph.company.example/`,
а не под `/artifact-graph/`. Пользователи входят по общей учётке `root` из `.env.prod`.
При доступе по обычному HTTP на удалённом IP Secure cookie не будет работать.
`/metrics` и `/health/*` не требуют web-авторизации — ограничьте их ACL в proxy/firewall.
Если proxy сам в контейнере, его `127.0.0.1` не указывает на хост: потребуется отдельная
защищённая Docker-сеть и согласованная конфигурация proxy.

### 5. Проверьте сопоставление с TeamCity

Связь строится по clone URL репозитория и URL VCS root из TC, а не по сходству имён.
Поддерживаются HTTP(S)/SSH, суффикс `.git`, стандартные порты и DC-путь `/scm/PROJECT/repo`.
Нестандартные порты сохраняются, чтобы не спутать два разных Git-сервера.
Структура карты остаётся `BB project → BB repository → TC project → build configuration → build`;
workspace, TC root и отдельные узлы SBOM/образов на карту не выводятся.

Если TC пока делает checkout из Cloud, а граф читает DC, автоматического совпадения может не быть.
Сначала администратор должен настроить реальные VCS roots под DC либо подтвердить связь с зеркалом.
Граф сам не меняет checkout URLs, токены TC и конфигурации сборок.
Для известного зеркала создайте вне checkout, например `/etc/artifact-graph/mapping-rules.yaml`:

```yaml
version: 1
mappings:
  - id: payments-dc-mirror
    teamcity_build_type: Payments_Build
    mode: replace
    repositories: [repo:PAYMENTS/payments-api]
    reason: Confirmed DC mirror of the repository used by this VCS root
```

Укажите `MAPPING_RULES_HOST_PATH=/etc/artifact-graph/mapping-rules.yaml` в `.env.prod`.
DC ID имеет вид `repo:PROJECT_KEY/repository-slug`, Cloud ID — `repo:workspace/slug`.
Точный ID берите из технических деталей узла; `teamcity_build_type` — стабильный ID, не display name.
`replace` заменяет автоматические связи, `add` дополняет. Файл читается при каждом refresh;
он должен быть доступен UID 10001. Не редактируйте tracked-файл для prod: deploy отклоняет dirty tree.

### 6. Запуск и последующие обновления

После настройки `.env.prod` и proxy:

```bash
cd /opt/artefact-graph-prod
docker compose --project-name artefact-graph-prod --env-file .env.prod -f compose.prod.yaml config --quiet
SMOKE_TIMEOUT_SECONDS=900 bash scripts/deploy.sh --prod \
  https://bitbucket.isb/scm/infsec/artifact_graph.git /opt/artefact-graph-prod
```

Для существующего PostgreSQL используйте другой самостоятельный Compose и режим deploy:

```bash
cd /opt/artefact-graph-prod
docker compose --project-name artefact-graph-prod --env-file .env.prod \
  -f compose.prod.external-db.yaml config --quiet
SMOKE_TIMEOUT_SECONDS=900 bash scripts/deploy.sh --prod-external-db \
  https://bitbucket.isb/scm/infsec/artifact_graph.git /opt/artefact-graph-prod
```

Этот режим не создаёт, не перезапускает и не удаляет внешний PostgreSQL. Перед первым запуском DBA
создаёт пустую database и роль приложения, разрешает TLS-соединения с Docker-хоста и настраивает backup.
Контейнер `graph` применяет миграции к указанной базе, поэтому сначала проверьте hostname, SAN/CA,
firewall/`pg_hba.conf` и права роли на отдельном staging-подключении.

Тот же вызов используйте для следующих обновлений, **после pre-deploy backup**.
Скрипт клонирует отсутствующий checkout, затем делает `git pull --ff-only`, проверяет конфигурацию,
собирает и пересоздаёт только prod-контейнеры, выполняет миграции и smoke.
Если `.env.prod` отсутствует, копируется пустой шаблон и выполнение останавливается с кодом 2:
заполните его и повторите. Секреты не подставляются в Git URL; для приватного репозитория
используйте SSH/deploy key либо защищённый Git credential helper.

Для проверки ещё и внешнего HTTPS-маршрута задайте перед вызовом
`GRAPH_URL=https://graph.company.example`; при внутреннем CA самого proxy также задайте
`SMOKE_CA_FILE=/etc/artifact-graph/certs/company-ca.pem` — это путь **хоста**, не контейнера.
По умолчанию smoke проверяет loopback `http://localhost:<GRAPH_PORT>`; это не проверка внешнего TLS.

Скрипт не удаляет volumes и не откатывает автоматически код/БД при ошибке: миграции уже могли
примениться. После неуспеха исследуйте логи; откат несовместимой схемы требует проверенной резервной копии.
Не используйте `down -v` или lab-скрипты seed/bootstrap для этого контура.

### 7. Приёмка на ваших реальных данных

```bash
docker compose --project-name artefact-graph-prod --env-file .env.prod -f compose.prod.yaml ps
docker compose --project-name artefact-graph-prod --env-file .env.prod -f compose.prod.yaml logs --since 15m graph
GRAPH_URL=https://graph.company.example SMOKE_ENV_FILE=.env.prod \
  SMOKE_TIMEOUT_SECONDS=900 bash scripts/smoke-linux.sh
```

В варианте с внешней БД замените `compose.prod.yaml` на `compose.prod.external-db.yaml` во всех
командах просмотра `ps`/`logs`; smoke-команда остаётся той же.

Smoke проверяет readiness, вход, API версии, **свежий `lastScan.status=success`**, структуру
непустого графа, отсутствие stale на его видимой странице и метрики. Во время deploy дополнительно
требуется scan, завершившийся после начала деплоя. `degraded` не считается успехом,
даже если страница открывается. Для заведомо пустого сервера можно явно задать `SMOKE_MIN_NODES=0`.
`scripts/validate_live.py` проверяет фиксированный lab-набор, для произвольного prod не подходит.

В UI найдите известный репозиторий и сравните с TC:

1. Правильные BB project/repository, TC project и все ожидаемые build configurations.
2. Последние 5 доступных запусков (либо меньше, если их ещё нет), их статусы и ссылки.
3. У билда с реальным push — цвет и краткое подтверждение из лога; у билда с SBOM — опубликованный
   `sbom.json`. Наличие команды в конфигурации само по себе не подтверждает выполненный push.
4. `/api/coverage` и раздел проблем сопоставления не содержат неожиданных пропусков.
5. Ручной refresh создаёт новый успешный scan, затем штатно работает интервал 60 минут.

Начните пилот с нескольких продуктов, временно ограничив **RO-доступы** учёток этими проектами,
затем расширяйте до всех нужных проектов. Лимит 10 проектов × 10 репозиториев — только представление
карты; сборщик всё равно обходит все доступные сущности. Первый обход большого DC делает минимум
два дополнительных запроса на каждый репозиторий для ветки/ревизии и может потребовать больше 900 секунд.
Измерьте длительность и нагрузку; увеличьте smoke/healthcheck start-period осознанно.

Частые проблемы:

| Симптом | Что проверить |
|---|---|
| 401 / 403 | DC-токен вместо Cloud-токена, срок, RO-права пользователя и групп; права TC на settings/logs/artifacts. Не заменяйте токен на admin ради обхода ошибки. |
| 404 / HTML вместо JSON | Базовый URL и context path; SSO/proxy не должен перенаправлять REST в форму входа. |
| TLS / connection error | DNS и VPN именно из контейнера, SAN, цепочку CA, срок сертификата; не отключайте verify. |
| Репозиторий без связи | Реальные VCS clone URLs, custom port, зеркало, права на VCS roots; при необходимости подтверждённое manual rule. |
| `degraded` / stale | `/api/status`, `/api/scans`, логи; часть источников могла быть недоступна, карта сохраняется для чтения. |
| Вход снова просит пароль | Внешний HTTPS, Secure cookie, корректный proxy hostname, совпадение пароля env и контейнера. |
| После смены пароля БД приложение не подключается | `POSTGRES_PASSWORD` инициализирует только новый volume. Для существующей БД нужна согласованная ротация пароля роли, не просто замена env. |

### 8. Ограничения, которые важно учесть

- Источники скриптов читаются по зафиксированному commit текущей default branch. Это не полный
  checkout исходников каждого исторического билда. Фактические push/SBOM определяются по логам
  и опубликованным артефактам соответствующего запуска.
- Анализ Maven/Gradle/npm и ссылок на скрипты эвристический; произвольный Kotlin DSL не исполняется,
  весь граф многомодульного Maven/внешних include автоматически не разворачивается. Нет команды
  или успешного маркера в доступном логе — нет подтверждённого push.
- Междоменные redirects API/артефактов блокируются. Для внешнего хранилища артефактов TC, например
  S3 с прямой переадресацией, нужна выдача через same-origin TC/proxy; это нужно проверить в пилоте.
- В prod по умолчанию отключено дополнительное чтение registry manifest. Поиск push в логах и
  `sbom.json` в TC продолжает работать; доступ к Nexus не требуется. Для enrichment нужен отдельно
  проверенный HTTPS endpoint, RO-доступ и адаптация prod Compose (см. раздел Registry and Nexus).
- Read-only доступ не означает отсутствие чувствительных данных: граф хранит metadata, пути и
  фрагменты evidence. Ограничьте web-доступ и backup, следите за секретами в самих build logs.
- Это single-instance Compose без HA; перед вводом в эксплуатацию выполните backup/restore rehearsal,
  проверку образов и согласуйте известные ограничения из [security.md](docs/security.md).

### 9. Backup и возврат к прежнему контуру

```bash
cd /opt/artefact-graph-prod
bash scripts/backup.sh --prod
bash scripts/verify-backup.sh --prod backups/prod/artifact-graph-YYYYmmddTHHMMSSZ.dump
```

Эти команды не работают с `--prod-external-db`: используйте утверждённые процедуры DBA для backup,
PITR и восстановления выделенной базы `artifact_graph` и обязательно проведите restore rehearsal.

Храните dump/checksum/manifest вне Docker-хоста; `.env.prod`, mapping, CA, proxy и Git SHA —
отдельно с защитой секретов. Восстановление разрушительно и требует явного подтверждения:

```bash
# Только в согласованное окно восстановления: заменяет текущую prod-БД.
bash scripts/restore.sh --prod backups/prod/artifact-graph-YYYYmmddTHHMMSSZ.dump --confirm
```

Возврат к Cloud-стенду — это использование его прежнего checkout, `.env`, Compose и БД,
а не загрузка DC dump в Cloud. Сохранённый lab можно продолжать использовать независимо от prod.
Подробные процедуры: [production runbook](docs/production-runbook.md).

## Развёртывание lab на Linux

```bash
bash scripts/deploy.sh --lab ssh://git@bitbucket.example/scm/tools/artefact-graph.git /opt/artefact-graph
```

Скрипт клонирует репозиторий при первом запуске, затем делает `git pull --ff-only` и полностью
пересоздаёт lab-сервисы. При отсутствии `.env` создаёт шаблон и останавливается до заполнения.
Данные хранятся в Docker volumes и при пересоздании контейнеров сохраняются.

## Проверка

```bash
python -m pip install -e '.[test]'  # Python 3.12; для Linux script-тестов также Bash и curl
pytest
```

Pytest enforces at least 75% line coverage for the application package. To validate
the original ten-repository fixture, including node counts and repository search:

```bash
python scripts/validate_live.py --url http://localhost:18081 --repository java-maven-api
```

Для расширенного стенда вместо проверки фиксированного количества узлов исходного demo
запускайте `scripts/validate_lab.py`; подготовка данных, команды и критерии успеха приведены
в [docs/lab-acceptance.md](docs/lab-acceptance.md). Live-браузерные проверки включаются только
через `E2E_LAB_MATRIX=1`; обычный изолированный E2E-прогон не обращается к Cloud/TC.

При включённой web-авторизации передайте `WEB_USERNAME` и `WEB_PASSWORD` через environment.
Изолированный браузерный E2E-прогон использует Chromium и чистую временную PostgreSQL:

```bash
docker compose -f compose.e2e.yaml up --build --abort-on-container-exit --exit-code-from browser-e2e
docker compose -f compose.e2e.yaml down --volumes --remove-orphans
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

## Web authentication

The web UI and `/api/*` use a signed HttpOnly session cookie. Set `WEB_USERNAME`
and a long random `WEB_PASSWORD` only in `.env`; never commit the real password.
State-changing requests also require the session CSRF token. Health endpoints and
Prometheus metrics remain available for container orchestration and monitoring.

## Manual mapping rules

Non-standard VCS mirrors can be mapped in `config/mapping-rules.yaml`. Rules target
a stable TeamCity build type ID and repository IDs shown in technical details:

```yaml
version: 1
mappings:
  - id: payments-mirror
    teamcity_build_type: Payments_Build
    mode: replace
    repositories: [repo:artifact_graph/payments]
    reason: TeamCity uses an internal mirror URL
```

`add` combines manual and automatic matches; `replace` replaces automatic matches.
The file is validated with safe YAML loading on every refresh.

## Operations

Optional signed webhooks use a persistent outbox, so receiver failures never fail a
scan. Enable them with `WEBHOOK_ENABLED=true`, an HTTPS `WEBHOOK_URL`, and a random
`WEBHOOK_SECRET` of at least 24 characters. Events include `scan.degraded`,
`scan.recovered`, and `graph.outputs.changed`; deliveries are retried asynchronously.

- `GET /health/live` checks that the process is alive.
- `GET /health/ready` checks PostgreSQL; `graphReady` separately indicates whether a usable scan exists.
- `GET /metrics` exposes Prometheus scan, graph-size, duration, and age metrics.
- `GET /api/scans?limit=20` returns recent scan history. `SCAN_HISTORY_LIMIT` controls retention.

The runtime `BITBUCKET_TOKEN` and `TEAMCITY_TOKEN` must belong to dedicated read-only
accounts. Keep `BB_BOOTSTRAP_TOKEN` and `TC_ADMIN_TOKEN` outside the graph container;
they are only used by bootstrap scripts.

The following commands target the lab. For production always use the `--prod` scripts above.
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
and can be built with `bash scripts/ci.sh test`. Configure tag-based publishing
in the corporate CI as described in [docs/ci.md](docs/ci.md).

Production preflight, deploy, monitoring, incident, backup/restore, rollback and
secret-rotation procedures are documented in [docs/production-runbook.md](docs/production-runbook.md).
