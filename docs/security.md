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

Protect `.env` with owner-only permissions. The Linux deployment script sets
mode 0600. On Windows, restrict its ACL to the operator account.

Before deployment, run unit tests, browser E2E and dependency vulnerability
scanning. A passing test suite alone does not prove security.
