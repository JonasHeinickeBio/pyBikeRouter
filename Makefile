# Everyday commands for pyBikeRouter. `make` (or `make help`) lists them.
#
# Tools come from the project's own environment (.venv, made by `make install`); inside an
# activated environment or `poetry shell` they are found on the PATH instead. Override anything on
# the command line, e.g. `make test-fast ARGS="tests/nodes -k geocode"` or `make run PORT=8765`.

BIN  := $(if $(wildcard .venv/bin/python),.venv/bin/,)
HOST ?= 127.0.0.1
PORT ?= 8000
ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help install hooks run lint format format-check typecheck test test-fast test-cov test-live \
        check ci precommit frontend-check completion deploy deploy-status deploy-logs clean

help: ## list the commands
	@awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z_-]+:.*## / { printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------------- set up

install: ## create .venv and install the dependencies (needs poetry)
	poetry install

hooks: ## install the git hooks (commit: lint + types; push: the whole suite)
	$(BIN)pre-commit install --hook-type pre-commit --hook-type pre-push

# ---------------------------------------------------------------------------------- run

run: ## start the API with auto-reload (HOST=127.0.0.1 PORT=8000)
	$(BIN)bike-router serve start --reload --host $(HOST) --port $(PORT)

# ---------------------------------------------------------------------------------- quality

lint: ## ruff check
	$(BIN)ruff check .

format: ## ruff format (rewrites files)
	$(BIN)ruff format .

format-check: ## ruff format, only checking (CI does not run it: a few old files are not formatted)
	$(BIN)ruff format --check .

typecheck: ## mypy on src
	$(BIN)mypy src

test: ## the whole suite, with coverage and its 80 % floor (for a part use test-fast ARGS=...)
	$(BIN)pytest $(ARGS)

test-fast: ## no coverage, stops at the first failure; ARGS="tests/nodes -k geocode" runs a part
	$(BIN)pytest -q --no-cov -x -p no:cacheprovider $(ARGS)

test-cov: ## the suite with a coverage report of the lines not covered
	$(BIN)pytest --cov-report=term-missing $(ARGS)

test-live: ## tests that call real external services (marked live)
	$(BIN)pytest -m live $(ARGS)

frontend-check: ## the web page's script tests (they run under node)
	$(BIN)pytest -q --no-cov -p no:cacheprovider tests/test_frontend_*.py

check: lint typecheck test-fast ## quick gate before a push: lint, types, tests without coverage

ci: lint typecheck ## what CI runs: lint, types, and the suite with coverage and its 80 % floor
	$(BIN)pytest --maxfail=1 --cov-report=xml $(ARGS)

precommit: ## run every pre-commit hook on all files (commit and push stages)
	$(BIN)pre-commit run --all-files
	$(BIN)pre-commit run --hook-stage pre-push --all-files

# ---------------------------------------------------------------------------------- CLI

completion: ## print how to switch on tab completion for bike-router
	@echo "bash:  echo 'source <(bike-router completion bash)' >> ~/.bashrc"
	@echo "zsh:   echo 'source <(bike-router completion zsh)' >> ~/.zshrc"
	@echo "fish:  mkdir -p ~/.config/fish/completions && bike-router completion fish > ~/.config/fish/completions/bike-router.fish"
	@echo "(docs/cli.md#tab-completion)"

# ---------------------------------------------------------------------------------- deploy

deploy: ## build and (re)start the local container from main (bike-router deploy up)
	$(BIN)bike-router deploy up --routing-provider brouter

deploy-status: ## is the container running, which commit, healthy
	$(BIN)bike-router deploy status

deploy-logs: ## the container's logs
	$(BIN)bike-router deploy logs

# ---------------------------------------------------------------------------------- housekeeping

clean: ## remove caches and coverage files (not .venv, not exports)
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage .coverage.* coverage.xml dist build
	find . -name __pycache__ -type d -not -path "./.venv/*" -prune -exec rm -rf {} +
