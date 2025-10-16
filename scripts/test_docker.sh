#!/bin/bash
# Copyright 2025 t54 labs
# SPDX-License-Identifier: Apache-2.0

set -e

echo "🐳 Starting Docker-based integration tests..."

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Detect docker compose command
if command -v docker-compose &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
elif docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
else
    echo -e "${RED}❌ Neither 'docker-compose' nor 'docker compose' found. Please install Docker Compose.${NC}"
    exit 1
fi

echo "📦 Using Docker Compose command: $DOCKER_COMPOSE"

# Function to cleanup
cleanup() {
    echo "🧹 Cleaning up Docker containers..."
    $DOCKER_COMPOSE -f docker-compose.test.yml down -v
}

# Set trap to cleanup on exit
trap cleanup EXIT

# Build images
echo "🔨 Building Docker images..."
$DOCKER_COMPOSE -f docker-compose.test.yml build

# Start services
echo "🚀 Starting services..."
$DOCKER_COMPOSE -f docker-compose.test.yml up -d facilitator-proxy mock-facilitator

# Wait for services to be healthy
echo "⏳ Waiting for services to be ready..."
timeout=60
elapsed=0
while [ $elapsed -lt $timeout ]; do
    if $DOCKER_COMPOSE -f docker-compose.test.yml ps | grep -q "healthy"; then
        echo -e "${GREEN}✅ Services are healthy${NC}"
        break
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done

if [ $elapsed -ge $timeout ]; then
    echo -e "${RED}❌ Services failed to become healthy${NC}"
    $DOCKER_COMPOSE -f docker-compose.test.yml logs
    exit 1
fi

# Run tests
echo "🧪 Running integration tests..."
$DOCKER_COMPOSE -f docker-compose.test.yml run --rm test-runner

# Check exit code
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ All tests passed!${NC}"
else
    echo -e "${RED}❌ Tests failed${NC}"
    echo "📋 Container logs:"
    $DOCKER_COMPOSE -f docker-compose.test.yml logs
    exit 1
fi
