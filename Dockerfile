FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    NODE_ENV=production

WORKDIR /app

# Python runtime, native build tools for sqlite3 if a prebuilt binary is unavailable,
# and tini for correct signal handling as PID 1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-venv python3-pip build-essential ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

# Install Node dependencies first to maximize Docker layer caching.
COPY package.json package-lock.json ./
RUN npm_config_build_from_source=true npm ci --omit=dev \
    && node -e "const sqlite3=require('sqlite3');const db=new sqlite3.Database(':memory:');db.get('SELECT 1 AS ok',(e,r)=>{if(e||r.ok!==1)process.exitCode=1;db.close()})" \
    && npm cache clean --force

# Install Python dependencies in an isolated venv.
COPY requirements.txt ./
RUN python3 -m venv /opt/venv \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source. Secrets and runtime data are excluded by .dockerignore.
COPY . .

RUN chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /app/data /app/logs

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "const http=require('http');const r=http.get('http://127.0.0.1:5000/readyz',x=>process.exit(x.statusCode===200?0:1));r.on('error',()=>process.exit(1));r.setTimeout(3000,()=>{r.destroy();process.exit(1)});"

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker-entrypoint.sh"]
