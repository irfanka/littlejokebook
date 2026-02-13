.PHONY: start stop restart logs build

start:
	docker compose up -d

stop:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

build:
	docker compose up -d --build
