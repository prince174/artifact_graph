# Artefact Graph

Интерактивная схема компонентов и потоков данных: откройте
[`service-architecture.html`](service-architecture.html) локально в браузере.

Python-сервис строит интерактивный граф `Bitbucket project/repository → TeamCity project/build configuration → build`.
Он сопоставляет системы по нормализованному URL VCS root, находит `docker push` и `podman push` в
script steps и связанных исходниках (в том числе `pom.xml`), правило `**/sbom.json => artifacts`
и последние запуски каждой конфигурации (`TEAMCITY_BUILD_LIMIT`): 3 по умолчанию в lab,
5 для расширенной приёмочной матрицы и в prod.
Push и SBOM отражаются цветом и деталями соответствующего build, без отдельных узлов на карте.

Bitbucket подключается через общий контракт `RepositoryProvider`. Поддерживаются адаптеры
`cloud` и `datacenter`; формат графа и интерфейс от выбора адаптера не меняются.

Для перехода с Cloud на существующий корпоративный сервер см. [Bitbucket Data Center в production](#bitbucket-data-center-production).

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
git clone https://github.com/prince174/artifact_graph.git /opt/artefact-graph-prod
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
TEAMCITY_BUILD_LIMIT=5
REFRESH_MINUTES=60
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
`TLS_CA_HOST_DIR` ниже. Режим `--prod-external-db` отклоняет `sslmode=require`, незашифрованный,
неполный или не-psycopg URL. Пароль базы не передавайте в Git URL, командной строке или чат.

| Настройка | Как заполнить |
|---|---|
| `BITBUCKET_URL` | Базовый HTTPS URL сервера. Сохраните `/bitbucket`, если такой context path действительно есть; не добавляйте `/rest/api`, `/projects/...` или Cloud workspace URL. |
| `TEAMCITY_URL` | HTTPS URL, доступный контейнеру, с реальным context path при наличии. |
| `TEAMCITY_PUBLIC_URL` | HTTPS URL того же TC для ссылок в браузере; может иметь другой hostname/context path. |
| `BITBUCKET_EMAIL`, `BITBUCKET_WORKSPACE`, `BITBUCKET_AUTH` | Только Cloud; для DC первые два можно оставить пустыми, третий игнорируется. |
| `TEAMCITY_BUILD_LIMIT` | От 1 до 100, по умолчанию 5 в prod. Это общий лимит последних запусков с учётом очереди, а не 5 успешных плюс все остальные. |
| `POSTGRES_PASSWORD` | Отдельный случайный пароль, минимум 32 символа из `A-Z a-z 0-9 _ -`; hex из команды выше подходит для URL подключения к БД. |
| `DATABASE_URL` | Только для `--prod-external-db`: полный `postgresql+psycopg://` URL отдельной базы с `sslmode=verify-full` или `verify-ca`. |
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

Порт графа публикуется только на `127.0.0.1:18080`. Поставьте перед ним HTTPS reverse proxy
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
  https://github.com/prince174/artifact_graph.git /opt/artefact-graph-prod
```

Для существующего PostgreSQL используйте другой самостоятельный Compose и режим deploy:

```bash
cd /opt/artefact-graph-prod
docker compose --project-name artefact-graph-prod --env-file .env.prod \
  -f compose.prod.external-db.yaml config --quiet
SMOKE_TIMEOUT_SECONDS=900 bash scripts/deploy.sh --prod-external-db \
  https://github.com/prince174/artifact_graph.git /opt/artefact-graph-prod
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
- `GET /health/ready` checks PostgreSQL and requires at least one usable `success` or `degraded` scan.
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
and are built by `.github/workflows/ci.yml` for version tags.

Production preflight, deploy, monitoring, incident, backup/restore, rollback and
secret-rotation procedures are documented in [docs/production-runbook.md](docs/production-runbook.md).
