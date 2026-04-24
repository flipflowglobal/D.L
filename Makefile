.PHONY: all build cython rust sol clean install test lint server bot frontend frontend-dev frontend-build

# ── Default ────────────────────────────────────────────────────────────────────
all: build

# ── Build ──────────────────────────────────────────────────────────────────────
build:
	python build.py

cython:
	python build.py --cython

rust:
	python build.py --rust

sol:
	python build.py --sol

clean:
	python build.py --clean

# ── Dependencies ───────────────────────────────────────────────────────────────
install:
	pip install -r requirements.txt

# ── Testing ────────────────────────────────────────────────────────────────────
test:
	pytest tests/ -q --tb=short

test-watch:
	pytest tests/test_watchdog.py -q --tb=short -v

# ── Lint ───────────────────────────────────────────────────────────────────────
lint:
	python -m pyflakes engine/ intelligence/ vault/ tests/ watchdog/ main.py trade.py config.py
	flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
	python scripts/lint_alignment.py

# ── Run ────────────────────────────────────────────────────────────────────────
server:
	uvicorn main:app --host 0.0.0.0 --port 8010 --reload

bot:
	python trade.py

bot-live:
	DRY_RUN=false python trade.py --live

flash:
	python trade.py --flash

# ── Contracts ──────────────────────────────────────────────────────────────────
compile-sol:
	python compiler.py --compile-only

deploy-sol:
	python compiler.py --deploy-only

# ── Docker ────────────────────────────────────────────────────────────────────
docker-api:
	docker build --target api -t aureon-api .

docker-bot:
	docker build --target bot -t aureon-bot .

docker-run-api:
	docker run -d --env-file .env -p 8010:8010 --name aureon-api aureon-api

docker-run-bot:
	docker run -d --env-file .env -e TRADE_MODE=paper --name aureon-bot aureon-bot

# ── Frontend ───────────────────────────────────────────────────────────────────
frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend: frontend-build

# ── Hot-swap (dev only — start server with file watching) ──────────────────────
server-hotswap:
	HOTSWAP=1 uvicorn main:app --host 0.0.0.0 --port 8010 --reload

# ── Full production stack ──────────────────────────────────────────────────────
prod: frontend build
	uvicorn main:app --host 0.0.0.0 --port 8010 --workers 4
