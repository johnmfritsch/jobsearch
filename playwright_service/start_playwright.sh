#!/opt/bin/bash
# start_playwright.sh — Build and start the Playwright URL resolution container
# Auto-detects DEV (port 3001) vs PROD (port 3000) based on script path.
# Run with: sudo bash start_playwright.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$SCRIPT_DIR"
IMAGE="playwright-jobsearch"

if echo "$SCRIPT_DIR" | grep -q '/DEV'; then
    PORT=3001
    CONTAINER="playwright-jobsearch-dev"
else
    PORT=3000
    CONTAINER="playwright-jobsearch-prod"
fi

echo "=== Playwright microservice setup ==="
echo "  Build dir : $BUILD_DIR"
echo "  Image     : $IMAGE"
echo "  Container : $CONTAINER"
echo "  Port      : localhost:$PORT"
echo ""

echo "[1/4] Building Docker image (this may take several minutes on first run)..."
docker build -t "$IMAGE" "$BUILD_DIR"

echo "[2/4] Stopping any existing container named $CONTAINER..."
docker rm -f "$CONTAINER" 2>/dev/null && echo "  Removed old container." || echo "  No existing container to remove."

echo "[3/4] Starting container..."
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p 127.0.0.1:${PORT}:${PORT} \
  -e PLAYWRIGHT_PORT=${PORT} \
  "$IMAGE"

echo "[4/4] Waiting for service to be ready..."
sleep 5

# Health check
HEALTH=$(curl -sf http://127.0.0.1:${PORT}/health 2>/dev/null || echo "FAIL")
if echo "$HEALTH" | grep -q '"ok"'; then
    echo ""
    echo "Playwright service is running on localhost:${PORT}"
    echo "  Health: $HEALTH"
else
    echo ""
    echo "Service did not respond on port ${PORT}. Check logs with:"
    echo "  docker logs $CONTAINER"
    exit 1
fi
