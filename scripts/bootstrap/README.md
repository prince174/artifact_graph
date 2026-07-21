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
python -m pip install httpx
python scripts/bootstrap/bootstrap_cloud.py
```

Скрипт создаст проект `DEMO`, 10 приватных репозиториев, типовое наполнение, TeamCity-проект,
VCS roots и 10 build configurations. Повторный запуск не перезаписывает существующие репозитории.

После bootstrap удалите временный токен. Для приложения создайте отдельную учётную запись и API
token только со scope `read:repository:bitbucket`; запишите email и токен в `.env`.

## Bitbucket Data Center (опционально)

Старый bootstrap находится в `bootstrap.py`. Он требует лицензированный Bitbucket Data Center,
`BB_ADMIN_TOKEN` и `TC_ADMIN_TOKEN`. Data Center контейнеры запускаются только профилем:

```bash
docker compose --profile datacenter up -d bitbucket-db bitbucket
```
