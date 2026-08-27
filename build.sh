#!/bin/bash
set -euo pipefail

# Build the SkySQL MCP Server Docker image
# Usage: ./build.sh [version]
# Example: ./build.sh 0.1.0
#
# Override the target architecture with PLATFORM, e.g.
#   PLATFORM=linux/arm64 ./build.sh

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
IMAGE_NAME="skysql-mcp-server"

# Cloud Run runs amd64. Building on an Apple Silicon machine defaults to arm64,
# which pushes fine but fails to start once deployed, so pin the platform here.
PLATFORM="${PLATFORM:-linux/amd64}"

# Get version from argument or pyproject.toml
VERSION=${1:-$(grep 'version = ' "$SCRIPT_DIR/pyproject.toml" | head -1 | sed 's/.*version = "\(.*\)"/\1/')}

echo "Building ${IMAGE_NAME}:${VERSION} for ${PLATFORM} ..."

docker build \
    --platform "${PLATFORM}" \
    -t "${IMAGE_NAME}:${VERSION}" \
    -t "${IMAGE_NAME}:latest" \
    "$SCRIPT_DIR"

echo "Tagged: ${IMAGE_NAME}:${VERSION}, ${IMAGE_NAME}:latest (${PLATFORM})"
