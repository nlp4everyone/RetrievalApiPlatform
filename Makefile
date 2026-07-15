COMPOSE = sudo docker compose --env-file .env -f docker/compose_db.yml -f docker/compose_web.yml -f docker/compose_tracking.yml

.PHONY: up down logs ps restart

up:
	$(COMPOSE) up --build -d --remove-orphans

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f web

ps:
	$(COMPOSE) ps

restart:
	$(COMPOSE) restart