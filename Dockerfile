# syntax=docker/dockerfile:1.6
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    NODE_MAJOR=20

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git bash ripgrep \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code \
 && ln -sf "$(node -e 'console.log(require("child_process").execSync("npm root -g").toString().trim())')/@anthropic-ai/claude-code/cli.js" /usr/local/bin/claude-fallback \
 && (command -v claude >/dev/null || ln -sf /usr/local/bin/claude-fallback /usr/local/bin/claude)

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge.py ./

ENV CLAUDE_BIN=/usr/local/bin/claude \
    CLAUDE_CWD=/workspace \
    HOME=/root

RUN mkdir -p /workspace /root/.claude

VOLUME ["/workspace", "/root/.claude"]

CMD ["python", "-u", "bridge.py"]
