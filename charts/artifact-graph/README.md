# Artifact Graph — установка через Helm

Chart запускает одну реплику сервиса с Bitbucket Data Center и TeamCity.
PostgreSQL используется внешний; Bitbucket, TeamCity и БД chart не устанавливает.
Миграции выполняет отдельный Job перед установкой и обновлением.

## Подготовка (macOS / Linux, Bash / zsh)

Нужны Helm 3, kubectl, доступ к кластеру и опубликованный образ приложения.
Команды выполняются из корня репозитория. Проверьте выбранный кластер:

```bash
kubectl config current-context
kubectl create namespace artifact-graph
mkdir -p "$HOME/.config/artifact-graph"
chmod 700 "$HOME/.config/artifact-graph"
cp charts/artifact-graph/values-production.example.yaml "$HOME/.config/artifact-graph/values.yaml"
```

Если namespace уже существует, используйте его. В приватном `values.yaml` заполните
`image.repository`, `image.digest`, адреса BB/TC и настройки Ingress.
URL Bitbucket должен включать context path (если есть), но не `/rest/api`.
Для приватного registry укажите существующий pull Secret:

```yaml
imagePullSecrets:
  - name: registry-credentials
```

Создайте вне Git файлы `runtime.env` и `migration.env`, ограничив доступ к ним
через `chmod 600`. Содержимое `runtime.env`:

```dotenv
DATABASE_URL=postgresql+psycopg://graph:ENCODED_PASSWORD@postgres.company.ru:5432/artifact_graph?sslmode=verify-full&sslrootcert=/app/certs/company-ca.pem
BITBUCKET_TOKEN=READ_ONLY_TOKEN
TEAMCITY_TOKEN=READ_ONLY_TOKEN
WEB_PASSWORD=REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS
```

`migration.env` содержит только `DATABASE_URL` с правами на миграции в той же БД.
Специальные символы в пароле URL необходимо percent-encode.

```bash
kubectl -n artifact-graph create secret generic artifact-graph-runtime \
  --from-env-file="$HOME/.config/artifact-graph/runtime.env"
kubectl -n artifact-graph create secret generic artifact-graph-migrations \
  --from-env-file="$HOME/.config/artifact-graph/migration.env"
```

## Корпоративный CA

Загрузите публичный PEM bundle центра сертификации (без закрытых ключей):

```bash
kubectl -n artifact-graph create configmap artifact-graph-ca \
  --from-file=company-ca.pem=/absolute/path/on/your/mac/company-ca.pem
```

Настройки в `values.yaml`:

```yaml
caConfigMap: artifact-graph-ca
config:
  TLS_CA_FILE: /app/certs/company-ca.pem
```

Путь `/absolute/path/on/your/mac/...` используется только при загрузке файла.
`TLS_CA_FILE` и `sslrootcert` указывают на путь **в pod**: `/app/certs/company-ca.pem`.
Ключ ConfigMap `company-ca.pem` становится именем файла. Secret и ConfigMap
должны существовать до установки, поскольку migration Job запускается первым.
Если отдельный CA для BB/TC не нужен, задайте `caConfigMap: ""` и
`config.TLS_CA_FILE: ""`; доверие к PostgreSQL настройте отдельно, не оставляя
`sslrootcert` со ссылкой на отсутствующий файл.

## Первая установка

```bash
helm lint charts/artifact-graph -f "$HOME/.config/artifact-graph/values.yaml"
helm template artifact-graph charts/artifact-graph \
  -n artifact-graph -f "$HOME/.config/artifact-graph/values.yaml" \
  | kubectl -n artifact-graph apply --dry-run=server -f -
helm install artifact-graph charts/artifact-graph \
  -n artifact-graph -f "$HOME/.config/artifact-graph/values.yaml" \
  --wait --timeout 35m
kubectl -n artifact-graph get pods,jobs,svc,ingress
kubectl -n artifact-graph logs deployment/artifact-graph-graph --tail=100
```

В браузере откройте `https://<ingress.host>`. DNS, Ingress controller и TLS Secret
нужно подготовить в кластере. В production включены Secure cookies, поэтому
пользовательский вход требует HTTPS. Первый сбор может занять несколько минут.

Для диагностики готовности без Ingress:

```bash
kubectl -n artifact-graph port-forward service/artifact-graph-graph 18080:8080
# В другом терминале:
curl --fail http://localhost:18080/health/ready
```

Такой port-forward проверяет readiness; пользовательский HTTPS-доступ проверяйте
через Ingress. При ошибке миграций:

```bash
kubectl -n artifact-graph logs job/artifact-graph-graph-migrate
```

Порядок обновления с остановкой сборщика перед миграциями, резервные копии,
NetworkPolicy и локальный kind-тест описаны в [полном руководстве](../../docs/kubernetes.md).
Не увеличивайте число реплик: планировщик работает в каждом процессе.
