#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE_NAME="${1:-xbot:test}"
CONTAINER_NAME="xbot-smoke-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"

cleanup() {
    docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run --detach \
    --name "$CONTAINER_NAME" \
    --env ENCRYPTION_KEY=ci-smoke-encryption-key-at-least-32-bytes \
    --env ADMIN_PASSWORD=ci-smoke-admin-password-not-for-production \
    --env NODE_ENV=test \
    "$IMAGE_NAME" >/dev/null

for _attempt in $(seq 1 60); do
    state="$(docker inspect --format '{{.State.Status}}' "$CONTAINER_NAME")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$CONTAINER_NAME")"
    if [[ "$state" == "exited" || "$state" == "dead" ]]; then
        docker logs "$CONTAINER_NAME"
        exit 1
    fi
    if [[ "$health" == "healthy" ]]; then
        docker exec "$CONTAINER_NAME" node -e \
            "Promise.all(['/healthz','/readyz'].map(async p=>{const r=await fetch('http://127.0.0.1:5000'+p);if(!r.ok)throw new Error(p+' returned '+r.status)})).catch(e=>{console.error(e.message);process.exit(1)})"
        exit 0
    fi
    sleep 2
done

docker logs "$CONTAINER_NAME"
exit 1
