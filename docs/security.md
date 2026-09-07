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

## Remaining upstream advisory (2026-09-08)

Docker Scout reports CVE-2026-85091 for Debian's zlib package. Debian currently
lists no fixed package. The reported trigger involves non-blocking gzwrite and
gzprintf; exploitability through this application has not been demonstrated.
Do not treat this as a clean image scan or silently suppress it. Rebuild and
rescan after Debian publishes an update:
https://security-tracker.debian.org/tracker/CVE-2026-85091
