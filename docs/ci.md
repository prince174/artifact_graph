# Проверки в корпоративном CI

Репозиторий: `https://bitbucket.isb/scm/infsec/artifact_graph.git`.
Bitbucket Data Center хранит код; запуск проверок настраивается в TeamCity.
Скрипты не регистрируют задания, расписание или уведомления на сервере автоматически.

На выделенном Linux-агенте нужны Git, Python 3.12+, Docker Engine/Compose,
curl, Helm 3.19.0, kind 0.33.0, kubectl и Trivy 0.74.0. Установите инструменты
из доверенного корпоративного зеркала с проверкой контрольных сумм.
Работайте в чистом checkout без локального `.env` и production-секретов.

```bash
python -m pip install pip==26.2.1
python -m pip install -e '.[test]' pip-audit==2.10.1
bash scripts/ci.sh all
```

Отдельные этапы: `test`, `browser`, `kubernetes`, `security`.
`test` проверяет Python, зависимости, Compose и собирает образ с OCI-меткой commit.
`browser` запускает E2E и удаляет только свой Compose-стенд с его томами.
`kubernetes` выполняет lint/render-тесты, затем локальный kind-сценарий миграций,
обновления и восстановления pod. Кластер `artifact-graph-test` остаётся доступным
для диагностики; удалите его после проверки на выделенном агенте:

```bash
kind delete cluster --name artifact-graph-test --kubeconfig .local-k8s/config
```

В TeamCity создайте цепочку этих проверок для push/PR и тегов `v*`. Для регулярного
сканирования настройте отдельное задание `bash scripts/security-scan.sh`, например
ежедневно в 03:17 UTC, и разрешите ручной запуск. Токен checkout — только для чтения.
Публикуйте `test-results/**` как артефакты, включая ошибочные запуски; срок хранения
отчётов безопасности — не менее 30 дней. Уведомления о неуспехе настройте в TeamCity.

Публикация образов в корпоративный registry и подпись/provenance настраиваются
отдельным release-заданием после успешных проверок. Скрипты сами образы не публикуют.
Укажите registry и credentials в защищённых параметрах CI, используйте immutable
tag/digest и проверяйте OCI-метку `org.opencontainers.image.revision`.
