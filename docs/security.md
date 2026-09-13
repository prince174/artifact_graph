# Deployment security

The default Compose binds the graph, TeamCity and lab registry to loopback only.
For remote access, use an HTTPS reverse proxy and set WEB_COOKIE_SECURE=true.
Keep VERIFY_TLS=true; install your internal CA instead of disabling validation.

The graph runs as UID 10001, with a read-only root filesystem, no Linux
capabilities, and no privilege escalation. PostgreSQL data stays in its volume.

The optional `build-lab` profile starts a privileged Docker-in-Docker TeamCity
agent. Run this profile only on a disposable, isolated build host with trusted
repositories. The graph collector does not need an agent running. The bundled
registry is an unauthenticated local fixture; do not expose it to other hosts.
Use an authenticated HTTPS registry/Nexus for production.

Use separate tokens for administration, Git checkout and collection.
`BB_CHECKOUT_TOKEN` must have only repository read permission and differ from
`BB_BOOTSTRAP_TOKEN`. Re-running bootstrap updates existing fixture VCS roots;
rotate/revoke the previous bootstrap token only after verifying checkout.
Scope restrictions must be verified in Bitbucket; string comparison cannot
verify token permissions. Never put administrator tokens in runtime containers.
In TeamCity, inspect inherited group roles too: a Project Viewer that belongs
to All Users with global Project Developer is not read-only. Keep the operator's
explicit System Administrator role and remove write grants from shared reader
groups before using the collector token.

Protect `.env` with owner-only permissions. The Linux deployment script sets
mode 0600. On Windows, restrict its ACL to the operator account.

Before deployment, run unit tests, browser E2E and dependency vulnerability
scanning. A passing test suite alone does not prove security.

GitHub Actions also runs `.github/workflows/scheduled-security-scan.yml` every
day at 03:17 UTC and on demand. It builds the application image from the current
default branch and retains two Trivy reports for 30 days:

- `trivy-all-high-critical.txt` is the complete High/Critical inventory,
  including findings without an upstream fix;
- `trivy-fixable-high-critical.txt` (legacy filename) now fails the workflow for
  any High/Critical issue, including findings without an available fix.

Both reports include unfixable findings; these are no longer excluded from the gate.
A red scheduled workflow means maintainers must
rebuild or update the affected dependency and rerun the workflow. GitHub sends
failure notifications according to each maintainer's repository notification
settings. Use **Actions → scheduled-security-scan → Run workflow** to verify a
base-image or dependency update immediately instead of waiting for the next run.

## Проверка от 2026-09-13

Runtime переведён с Debian slim на Python 3.12 Alpine. В старом образе Trivy
обнаруживал 56 High/Critical записей; новый образ проверен Trivy 0.74.0 по всем
уровням серьёзности: известных уязвимостей не найдено. Это результат конкретного
сканирования, а не гарантия отсутствия будущих CVE. При обновлении образа повторяйте
сборку, сканирование, миграции и browser E2E. Данные PostgreSQL от смены образа
приложения не меняют формат.

SBOM и исходники BB читаются потоком с лимитом 2 MB; слишком большой источник
не анализируется как полный файл. Сжатые ответы на эти запросы отклоняются.
Внешний PostgreSQL проверяется до создания engine и миграций, требует verify-ca
или verify-full. Метрики доступны по веб-сессии либо отдельному METRICS_TOKEN.

Во вспомогательном тестовом контейнере pip обновлён до 26.2.1; повторный pip-audit
не нашёл известных уязвимостей. При ручном создании тестового окружения сначала
обновляйте pip до версии из CI. Runtime-образ удаляет pip после установки зависимостей.
