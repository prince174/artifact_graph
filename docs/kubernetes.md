# Kubernetes: Helm, одна реплика, без HPA

Chart находится в `charts/artifact-graph`. В production он создаёт Deployment,
ClusterIP Service, ConfigMap настроек/правил, migration Job; опционально Ingress
и NetworkPolicy. BB Data Center, TeamCity и PostgreSQL остаются внешними.
Токены и пароли chart не создаёт: нужны предварительно подготовленные Secret.

## Перезапуск и сохранность данных

- При падении процесса kubelet перезапускает контейнер; после удаления pod Deployment
  создаёт новый. При потере node восстановление требует доступного другого node и
  времени обнаружения отказа. Один локальный node не обеспечивает отказоустойчивость хоста.
- Каждый старт приложения запускает новый сбор. Затем сбор повторяется по
  `REFRESH_MINUTES` (по умолчанию 60). Старый незавершённый сбор не продолжается с места остановки.
- Граф, snapshots, история сканов и кэш сохраняются в PostgreSQL. Pod не имеет PVC
  приложения, `/tmp` временный. Потеря pod не удаляет БД.
- Первый сбор входит в startup приложения: пока он не закончен, HTTP ещё не принимает
  запросы. Startup probe допускает по умолчанию 30 минут. Увеличьте окно для большого
  контура, измерив реальное время сбора; слишком короткое окно вызовет цикл рестартов.
- При доступной БД временная ошибка BB/TC после старта может дать `stale`-карту.
  Readiness проверяет БД и пригодный скан; liveness проверяет только процесс, чтобы
  внешняя авария не провоцировала лишние рестарты.
- `replicas: 1`, `strategy: Recreate`. HPA отсутствует. Не масштабируйте вручную:
  каждый процесс содержит собственный планировщик. Возможен перерыв во время обновления.
  Это не распределённая блокировка: не делайте force-delete pod на недоступном node,
  пока не подтверждено, что старый процесс остановлен.

## Подготовка production

Нужны Helm 3, kubectl, доступ к выбранному namespace и registry. Не требуется kind.
Адреса registry, namespace, домен и ingressClass заполняются владельцем контура:
production-пример намеренно не содержит рабочих адресов.

1. Соберите, просканируйте и опубликуйте образ из нужного коммита во внутренний registry.
   Используйте digest в `image.digest`; не применяйте `latest`.
2. DBA создаёт отдельную database и роли. Migration-роль должна создавать/изменять
   таблицы и индексы; runtime-роль — читать/изменять данные и использовать sequences.
   При разделении ролей настройте default privileges на новые объекты и проверьте
   права после миграций. Резервные копии/PITR обеспечивает внешний PostgreSQL.
3. Разрешите из pod DNS, HTTPS к BB/TC, TCP к PostgreSQL и при необходимости webhook.
   Registry скачивает node, а не приложение — его сетевой доступ настраивается отдельно.
4. Создайте namespace и Secret в нём. Пример для Bash (секретные файлы вне Git):

```bash
kubectl --context YOUR_CONTEXT create namespace YOUR_NAMESPACE
umask 077
# Подготовьте runtime.env локально защищённым редактором или через secret manager.
# DATABASE_URL=postgresql+psycopg://.../artifact_graph?sslmode=verify-full&sslrootcert=/app/certs/company-ca.pem
# BITBUCKET_TOKEN=...
# TEAMCITY_TOKEN=...
# WEB_PASSWORD=... минимум 32 случайных символа
# METRICS_TOKEN=... отдельный необязательный секрет
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE create secret generic artifact-graph-runtime \
  --from-env-file=/secure/path/runtime.env
# migration.env содержит только DATABASE_URL с migration-ролью, той же БД и TLS.
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE create secret generic artifact-graph-migrations \
  --from-env-file=/secure/path/migration.env
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE create configmap artifact-graph-ca \
  --from-file=company-ca.pem=/secure/path/company-ca.pem
```

Secret Kubernetes не шифруется одним лишь base64: используйте RBAC, шифрование etcd
и принятый в контуре способ доставки секретов. Chart не передаёт BB/TC-токены migration Job.
Если CA публичный, `caConfigMap` и `TLS_CA_FILE` можно оставить пустыми; настройте
корневое доверие PostgreSQL согласно инфраструктуре.

5. Для приватного registry создайте image pull Secret и укажите `imagePullSecrets`.
6. Создайте TLS Secret для Ingress (либо используйте действующую автоматизацию сертификатов).
7. Скопируйте `values-production.example.yaml` в приватный файл вне Git и заполните:
   `image.repository`, `image.digest`, имена Secret/CA, HTTPS URL BB/TC, Ingress host/class/TLS.
   Не помещайте пароли и токены в values: они сохраняются в истории Helm.

## Установка

```bash
helm lint charts/artifact-graph -f /secure/path/values-production.yaml
helm template artifact-graph charts/artifact-graph -f /secure/path/values-production.yaml \
  | kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE apply --dry-run=server -f -
helm upgrade --install artifact-graph charts/artifact-graph \
  --kube-context YOUR_CONTEXT --namespace YOUR_NAMESPACE \
  -f /secure/path/values-production.yaml --wait --timeout 35m
```

Helm выполняет `pre-install` migration Job и ждёт его успеха. Job проверяет
DATABASE_URL до создания engine и выполняет `alembic upgrade head`. При ошибке
Deployment не устанавливается. Secret и CA должны существовать заранее: обычные
ресурсы chart на этом этапе ещё не созданы. Успешный Job удаляется, ошибочный
сохраняется для диагностики до следующей попытки.

Deployment запускает только Uvicorn. Рестарт pod не повторяет migration Job.
После первого сбора readiness включает pod в Service. Ingress завершает TLS;
Secure cookie требует пользовательского HTTPS-доступа.

Проверки:

```bash
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE get pods,jobs,svc,ingress
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE rollout status deployment/artifact-graph-graph
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE logs deployment/artifact-graph-graph --tail=50
# При ошибке миграций:
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE logs job/artifact-graph-graph-migrate
```

В UI проверьте свежий `success`, непустую карту, поиск репозитория и push/SBOM у
контрольных билдов. Проверка health сама по себе не доказывает полноту прав BB/TC.
Ресурсы по умолчанию: request 100m CPU/256Mi, limit 2 CPU/1Gi; измерьте пик памяти
на своём объёме графа. Превышение memory limit даёт OOMKilled и повторный сбор.

## Обновление и откат

Pre-upgrade hook исполняется **до** изменения Deployment. Поэтому `Recreate` не
останавливает старую версию перед миграцией автоматически. Запланируйте окно:

```bash
# Сделайте/проверьте backup внешней БД, затем остановите сборщик.
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE scale deployment/artifact-graph-graph --replicas=0
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE wait --for=delete pod \
  -l app.kubernetes.io/instance=artifact-graph,app.kubernetes.io/component=web --timeout=120s
helm upgrade artifact-graph charts/artifact-graph \
  --kube-context YOUR_CONTEXT --namespace YOUR_NAMESPACE \
  -f /secure/path/values-production.yaml --wait --timeout 35m
```

Helm создаст Job для новых миграций и вернёт одну реплику. Если hook упал, приложение
останется остановленным: изучите логи, устраните причину, повторите upgrade.
Не включайте автоматический откат при несовместимых миграциях: Helm rollback меняет
манифесты/образ, но не восстанавливает БД. Старый образ можно вернуть только после
проверки совместимости схемы, иначе нужен согласованный restore БД.

После ротации runtime Secret или CA выполните `rollout restart deployment/...`:
переменные окружения не обновятся внутри уже запущенного процесса. Изменения config
и mapping rules через Helm меняют checksum Pod template и вызывают пересоздание.

## Метрики и NetworkPolicy

Prometheus должен передавать `Authorization: Bearer METRICS_TOKEN`; токен даёт доступ
только к `/metrics`, либо используется веб-сессия. Health endpoints публичные.
При необходимости добавьте ServiceMonitor средствами существующего monitoring stack.

NetworkPolicy опциональна и по умолчанию выключена, пока неизвестен CNI и адреса.
Включение с пустыми списками закрывает весь вход/выход выбранным pod, включая Job.
Задайте разрешения DNS, ingress controller/monitoring, BB/TC/DB и webhook. Стандартная
NetworkPolicy работает с CIDR/labels, не с DNS-именами; kind по умолчанию не является
проверкой реального enforcement этих правил. Для первой миграции до создания политики
chart примените базовые namespace-политики заранее, если этого требует контур.

## Локальная проверка через kind

kind создаёт Kubernetes-node в Docker-контейнере. Нужны Docker 28+ в Linux-container mode
(с поддержкой `docker image save --platform`),
kind 0.33.0, Helm 3.19.0, kubectl и Python 3.12+. Первое создание скачивает node image;
это больше нагрузки, чем один Compose-сервис. На загруженной машине оставьте несколько
GiB памяти под кластер. Существующий Compose-стенд скрипт не изменяет.

```bash
python scripts/k8s-local.py
```

Если инструменты вне PATH, задайте `AG_HELM`, `AG_KIND`, `AG_KUBECTL` полными путями.
Скрипт использует только кластер `artifact-graph-test`, namespace с тем же именем и
отдельный `.local-k8s/config`. Он не меняет пользовательский default kubeconfig.

Проверяются сборка/загрузка образа, установка chart, миграции в отдельной тестовой
PostgreSQL с PVC, авторизация, граф, метрики, пересоздание pod с повторным сбором и
сохранением истории, upgrade, блокировка установки при ошибке migration Job.
Пароли генерируются случайно и сохраняются только в Kubernetes Secret.
CI выполняет `helm lint`, восемь проверок рендеринга, Kubernetes server-side dry-run
и этот же сценарий на отдельном kind-кластере. Release зависит от успешного
Kubernetes job наряду с обычными тестами и browser E2E. CI удаляет только свой
временный кластер после проверки; локальный запуск оставляет его для просмотра.
Используются demo-данные; это **не проверка** production BB/TC, корпоративного TLS,
Ingress или NetworkPolicy. Их проверяют при пилоте в целевом контуре.

Кластер остаётся запущенным для просмотра:

```bash
kubectl --kubeconfig .local-k8s/config -n artifact-graph-test port-forward service/local-graph 18083:8080
# http://localhost:18083, пользователь root
# Получить пароль локального стенда (показывает секрет в терминале):
kubectl --kubeconfig .local-k8s/config -n artifact-graph-test get secret artifact-graph-local \
  -o jsonpath='{.data.WEB_PASSWORD}' | base64 --decode
```

В PowerShell декодирование пароля:

```powershell
$encoded = kubectl --kubeconfig .local-k8s/config -n artifact-graph-test get secret artifact-graph-local -o jsonpath='{.data.WEB_PASSWORD}'
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
```

Повторный запуск скрипта использует существующую тестовую БД и обновляет релиз.
Для удаления **всего локального теста вместе с его БД**:

```bash
kind delete cluster --name artifact-graph-test --kubeconfig .local-k8s/config
```

Документация инструментов: [kind](https://kind.sigs.k8s.io/docs/user/quick-start/),
[Helm hooks](https://helm.sh/docs/topics/charts_hooks/).
