FROM jetbrains/teamcity-agent:2025.03.3-linux-sudo
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    maven podman nodejs npm python3 uidmap \
    && rm -rf /var/lib/apt/lists/*
COPY config/lab-agent/storage.conf /etc/containers/storage.conf
USER buildagent
