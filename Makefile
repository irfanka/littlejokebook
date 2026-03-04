.PHONY: start stop restart logs build refresh

COMPOSE := docker compose -f docker-compose.dev.yml

start:
	$(COMPOSE) up -d

stop:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f

build:
	$(COMPOSE) up -d --build

refresh:
	$(COMPOSE) restart web
