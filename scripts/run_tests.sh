#!/bin/bash
# Run tests in Docker container
# Usage: ./scripts/run_tests.sh [pytest args...]
# Example: ./scripts/run_tests.sh tests/unit/ -v -x

set -e

# Default to running all tests if no args provided
PYTEST_ARGS="${@:-tests/ -v}"

echo "Running tests with: pytest $PYTEST_ARGS"

# Build the test image if needed and run tests
docker compose run --rm \
    -e APP_ENV=testing \
    -e DATABASE_URL=sqlite+aiosqlite:///:memory: \
    -e SECRET_KEY=test-secret-key-for-testing-only \
    -e JWT_SECRET_KEY=test-jwt-secret-key-for-testing \
    -e TWITTER_CLIENT_ID=test-client-id \
    -e TWITTER_CLIENT_SECRET=test-client-secret \
    -e TWITTER_REDIRECT_URI=http://localhost:8000/twitter/callback \
    -e ENCRYPTION_KEY=dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcw== \
    web pytest $PYTEST_ARGS
