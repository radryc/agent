.PHONY: up down logs build dev clean

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

dev:
	docker compose up --build

clean:
	docker compose down -v --rmi local

status:
	docker compose ps

restart:
	docker compose restart

llm-logs:
	docker compose logs -f llm

backend-logs:
	docker compose logs -f backend

frontend-logs:
	docker compose logs -f frontend
