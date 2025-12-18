#!/bin/bash
# Deploy script for Tempus application
# Usage: ./deploy.sh

set -e

SERVER="tempus"
REMOTE_PATH="/opt/tempus"

echo "Deploying Tempus to production..."

# Sync all application files
echo "Syncing files..."
rsync -avz --delete \
    --exclude '.git' \
    --exclude '.env' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache' \
    --exclude '.venv' \
    --exclude 'venv' \
    --exclude '.DS_Store' \
    --exclude '*.egg-info' \
    ./ ${SERVER}:${REMOTE_PATH}/

# Fix permissions for Docker
echo "Fixing permissions..."
ssh ${SERVER} "cd ${REMOTE_PATH} && chown -R root:root app && chmod -R 755 app"

# Restart services
echo "Restarting services..."
ssh ${SERVER} "cd ${REMOTE_PATH} && docker compose restart web celery_worker celery_beat"

echo "Deployment complete!"
