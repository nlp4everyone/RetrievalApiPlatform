#!/bin/bash

# Run Docker Containers for ChatEngine
# This script starts all services using the compose files in the docker/ directory
set -e  # Exit on any error

echo "Building Docker containers ..."

# Start all services using compose files from docker/ directory
sudo docker compose --env-file .env -f docker/compose_db.yml -f docker/compose_web.yml -f docker/compose_tracking.yml up --build -d --remove-orphans

echo "Services started successfully!"