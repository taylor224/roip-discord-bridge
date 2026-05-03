FROM python:3.13-slim

# discord.py[voice] needs libopus + libsodium native libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libopus0 libsodium23 libffi8 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY radio_discord_bridge/ ./radio_discord_bridge/

RUN pip install --no-cache-dir .

# Run with --network host (Docker) or hostNetwork: true (K8s) — multicast IGMP
# joins generally do not work in default container networks.
ENTRYPOINT ["python", "-m", "radio_discord_bridge"]
