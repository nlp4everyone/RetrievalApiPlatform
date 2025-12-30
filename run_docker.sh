# Start all Docker services using the new directory structure
sudo docker compose \
  -f compose_db.yml \
  -f compose_serving.yml \
  -f compose_web.yml \
  -f compose_tracking.yml \
  up -d