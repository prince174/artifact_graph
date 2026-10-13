# HTTPS через Nginx в Compose

`compose.https.yaml` подключает Nginx к одному из базовых Compose-файлов.
Он публикует HTTPS на 443 и передаёт запросы сервису `graph:8080` в Docker-сети.
Порт 80 не публикуется; открывайте именно `https://ваш-домен/`.

1. Поместите сертификат домена с цепочкой в `fullchain.pem`, приватный ключ без
   пароля — в `privkey.pem`, в один каталог вне Git. `config/certs/` уже исключён из Git.
   Это серверный сертификат сайта; `TLS_CA_FILE` для доверия к Bitbucket/TeamCity задаётся отдельно.
2. В `.env.prod` укажите:

   ```dotenv
   HTTPS_CERT_DIR=./config/certs/https
   HTTPS_BIND=0.0.0.0
   HTTPS_PORT=443
   ```

   На Linux файлы должны читаться UID/GID `101:101` контейнера Nginx:
   например, каталог с группой 101 и правами 750, ключ с группой 101 и правами 640.
   Для указанного каталога (Linux, обычный rootful Docker/Podman без переназначения UID):

   ```bash
   sudo chown -R 101:101 config/certs/https
   sudo chmod 750 config/certs/https
   sudo chmod 640 config/certs/https/fullchain.pem
   sudo chmod 640 config/certs/https/privkey.pem
   ```

   `101:101` — UID/GID Nginx внутри контейнера, не пользователя, запускающего Compose.
   Запуск через `sudo` не меняет пользователя Nginx. Для rootless Podman права нужно
   согласовать с отображением UID контейнера в user namespace; команды выше не универсальны.
   Не делайте приватный ключ общедоступным. DNS домена должен указывать на Docker-хост.
3. Для внешней БД выполните из корня проекта:

   ```bash
   docker compose --env-file .env.prod -f compose.prod.external-db.yaml -f compose.https.yaml config --quiet
   docker compose --env-file .env.prod -f compose.prod.external-db.yaml -f compose.https.yaml run --rm --no-deps nginx -t
   docker compose --env-file .env.prod -f compose.prod.external-db.yaml -f compose.https.yaml up -d --build
   ```

   Для PostgreSQL из бандла замените `compose.prod.external-db.yaml` на `compose.prod.yaml`.
   Для локального режима используйте `--env-file .env -f compose.yaml -f compose.https.yaml`;
   добавьте HTTPS-переменные в `.env`, оставьте `GRAPH_BIND=127.0.0.1`.
   Во всех последующих командах обновления сохраняйте оба `-f`.
4. Откройте `https://ваш-домен/`. Пока идёт первичный сбор, возможен 502:
   смотрите логи `graph`. Healthcheck Nginx проверяет сам proxy, готовность приложения
   проверяется отдельно по `/health/ready`.

   ```bash
   curl --cacert /path/to/company-ca.pem https://ваш-домен/health/ready
   ```

Для публичного доверенного сертификата `--cacert` не нужен. Если 443 занят, задайте
`HTTPS_PORT=8443` и открывайте URL с `:8443`. Loopback HTTP-порт графа остаётся для
локальной диагностики. Secure-cookie включается автоматически overlay-файлом.

Сертификаты не выпускаются автоматически. После замены файлов в каталоге проверьте
`nginx -t`, затем выполните `docker compose` с теми же env/файлами и `exec nginx nginx -s reload`.
Существующий `scripts/deploy.sh` не подключает HTTPS overlay автоматически: для этого
варианта используйте приведённые Compose-команды. Kubernetes продолжает использовать Ingress.

## DNS при запуске через Podman

В `config/nginx/nginx.conf` адрес `resolver 127.0.0.11` относится только к встроенному
DNS Docker. Для используемой сети Podman замените строку на:

```nginx
resolver 10.89.0.1 valid=10s ipv6=off;
```

`10.89.0.1` — адрес DNS в этой сети Podman, а не универсальный адрес для всех установок.
Проверьте `nameserver` внутри своего контейнера; при другом адресе используйте его:

```bash
sudo podman exec <имя-контейнера-nginx> cat /etc/resolv.conf
```

Nginx и `graph` должны находиться в общей сети с DNS-разрешением имени `graph`.
После изменения конфигурации:

```bash
sudo podman exec <имя-контейнера-nginx> nginx -t
sudo podman exec <имя-контейнера-nginx> nginx -s reload
```

Для rootless Podman выполняйте команды без `sudo`, от пользователя, запустившего контейнеры.
