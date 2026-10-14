# muxroom — single entry point for development, tests and builds.

UV  ?= uv
NPM ?= npm
BACKEND  := backend
FRONTEND := frontend

.PHONY: help install check dev dev-backend dev-frontend test test-backend test-frontend \
        test-remote lint build run clean

help:
	@echo "make install       install backend + frontend dependencies"
	@echo "make check         verify required tools are available"
	@echo "make dev           backend (reload) + Vite dev server"
	@echo "make test          backend + frontend tests"
	@echo "make test-remote   opt-in tests against MUXROOM_REMOTE_HOST"
	@echo "make lint          ruff + oxlint + tsc"
	@echo "make build         build the frontend into frontend/dist"
	@echo "make run           serve the built app (production mode)"

install:
	cd $(BACKEND) && $(UV) sync
	cd $(FRONTEND) && $(NPM) install

check:
	@command -v $(UV)   >/dev/null || { echo "ERROR: uv not found";   exit 1; }
	@command -v $(NPM)  >/dev/null || { echo "ERROR: npm not found";  exit 1; }
	@command -v tmux    >/dev/null || { echo "ERROR: tmux not found"; exit 1; }
	@command -v ssh     >/dev/null || { echo "WARNING: ssh not found (openssh transport unavailable)"; }
	@echo "ok"

dev:
	@$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	cd $(BACKEND) && $(UV) run muxroom --dev --reload

dev-frontend:
	cd $(FRONTEND) && $(NPM) run dev

test: test-backend test-frontend

test-backend:
	cd $(BACKEND) && $(UV) run pytest

test-frontend:
	cd $(FRONTEND) && $(NPM) test

test-remote:
	cd $(BACKEND) && $(UV) run pytest -m remote

lint:
	cd $(BACKEND) && $(UV) run ruff check . && $(UV) run ruff format --check .
	cd $(FRONTEND) && $(NPM) run lint && npx tsc -b --noEmit

build:
	cd $(FRONTEND) && $(NPM) run build

run: build
	cd $(BACKEND) && $(UV) run muxroom

clean:
	rm -rf $(FRONTEND)/dist $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache
