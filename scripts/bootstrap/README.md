# Bootstrap тестового стенда

## Bitbucket Cloud (основной вариант)

1. Создайте Bitbucket Cloud workspace.
2. Создайте временный API token со scopes `read:repository:bitbucket` и
   `write:repository:bitbucket`. Он нужен только для создания и наполнения репозиториев.
3. При готовом TeamCity создайте admin token для первоначальной конфигурации.

```bash
export BITBUCKET_WORKSPACE=my-workspace
export BB_BOOTSTRAP_EMAIL=admin@example.com
export BB_BOOTSTRAP_TOKEN=...
export TC_ADMIN_TOKEN=...
export TEAMCITY_BOOTSTRAP_URL=http://localhost:8111
export TEAMCITY_READER_USERNAME=artefact-graph-reader
python -m pip install httpx
python scripts/bootstrap/bootstrap_cloud.py
```

Скрипт создаст проект `DEMO`, 10 приватных репозиториев, типовое наполнение, TeamCity-проект,
VCS roots и 10 build configurations. Повторный запуск пропускает наполненные репозитории и
безопасно продолжает загрузку пустых, если предыдущий запуск был прерван.

После bootstrap удалите временный токен. Для приложения создайте отдельную учётную запись и API
token только со scope `read:repository:bitbucket`; запишите email и токен в `.env`.

`TC_ADMIN_TOKEN` также используется только bootstrap-скриптом. `TEAMCITY_TOKEN` — отдельный
постоянный токен пользователя TeamCity с ролью Project Viewer; административный токен нельзя
оставлять приложению после настройки.

`TEAMCITY_URL` — адрес из Docker-сети (`http://teamcity:8111`), а
`TEAMCITY_BOOTSTRAP_URL` — адрес, доступный локальному bootstrap-процессу
(`http://localhost:8111`). Пользователь `TEAMCITY_READER_USERNAME` должен уже существовать;
скрипт назначит ему роль Project Viewer только на проект `Demo`.

После авторизации build agent запустите по пять сборок и дождитесь завершения:

```bash
export TC_ADMIN_TOKEN=...
export TEAMCITY_BOOTSTRAP_URL=http://localhost:8111
python scripts/bootstrap/run_teamcity_builds.py --count 5 --timeout 1800
```

Скрипт идемпотентен: он ставит в очередь только недостающее число сборок для каждой конфигурации.

Для Git-аутентификации bootstrap и TeamCity используют поддерживаемое Bitbucket статическое имя
`x-bitbucket-api-token-auth`; искать персональный Bitbucket username не требуется.

## Bitbucket Data Center (опционально)

Старый bootstrap находится в `bootstrap.py`. Он требует лицензированный Bitbucket Data Center,
`BB_ADMIN_TOKEN` и `TC_ADMIN_TOKEN`. Data Center контейнеры запускаются только профилем:

```bash
docker compose --profile datacenter up -d bitbucket-db bitbucket
```
