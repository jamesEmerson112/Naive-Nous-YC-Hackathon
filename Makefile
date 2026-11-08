# Romulus cloud platform commands (architecture §8).
# Run from the repo root. Compose reads .env if present; override API_PORT and
# COMPOSE_PROJECT_NAME to run several copies side by side.

COMPOSE     ?= docker compose
TESTDB      := romulus-testdb
TESTDB_PORT := 55432
CLI         := $(COMPOSE) exec -T api python -m romulus_cloud.cli

.PHONY: up dev sim coder-image coder-firmware-check worker-host down logs test flows testdb seed reset digest overnight-start \
	overnight-stop overnight-check export lint fmt

# A host worker (`make worker-host`) must not run beside the Compose worker: refuse unless FORCE=1.
HOST_PIDFILE := data/worker-host.pid
define no_host_worker
	@if [ -f $(HOST_PIDFILE) ] && kill -0 "$$(cat $(HOST_PIDFILE))" 2>/dev/null; then \
		echo "refused: a host worker is running (pid $$(cat $(HOST_PIDFILE)), make worker-host);" \
			"this would start the Compose worker beside it. Stop it first, or FORCE=1."; \
		[ "$(FORCE)" = "1" ] || exit 1; \
	fi
endef

up:  ## db, api, worker
	$(no_host_worker)
	$(COMPOSE) up --build -d db api worker

dev:  ## same, with source mounted and uvicorn --reload
	$(no_host_worker)
	$(COMPOSE) -f compose.yaml -f compose.dev.yaml up --build -d db api worker

sim:  ## also start the fake devices (profile sim)
	$(no_host_worker)
	$(COMPOSE) --profile sim up --build -d

coder-image:  ## build the coding-agent sandbox image, romulus-coder (M8), with the STM32 toolchain (B8)
	$(COMPOSE) --profile coder build coder

coder-firmware-check: coder-image  ## B8: build a copy of firmware/ in the coder image, offline
	@bash cloud/scripts/coder_firmware_check.sh

DB_PORT ?= 55433
worker-host:  ## the worker as a host process for CODER_PROVIDER=local_agent (M8b); never overnight
	$(COMPOSE) -f compose.yaml -f compose.host-worker.yaml up -d db
	$(COMPOSE) stop worker
	DATABASE_URL=postgresql+psycopg://romulus:romulus@127.0.0.1:$(DB_PORT)/romulus \
		BLOB_DIR=$(CURDIR)/data/blobs EXPORT_DIR=$(CURDIR)/data/exports FORCE=$(FORCE) \
		uv run python -m romulus_cloud.worker --host --pidfile $(CURDIR)/$(HOST_PIDFILE)

down:
	$(COMPOSE) --profile sim --profile tunnel down

logs:  ## follow the api and worker JSON logs
	$(COMPOSE) logs -f api worker

testdb:  ## make sure the shared test Postgres (romulus-testdb) exists and is running
	@docker inspect $(TESTDB) >/dev/null 2>&1 || docker run -d --name $(TESTDB) \
		-p $(TESTDB_PORT):5432 -e POSTGRES_USER=romulus -e POSTGRES_PASSWORD=romulus postgres:17
	@[ "$$(docker inspect -f '{{.State.Running}}' $(TESTDB))" = "true" ] || docker start $(TESTDB)
	@until docker exec $(TESTDB) pg_isready -U romulus >/dev/null 2>&1; do sleep 0.5; done

test: testdb  ## every test; each pytest session gets its own throwaway database
	uv run pytest

flows: testdb  ## only the flow tests, one pass/fail line per flow
	@uv run pytest cloud/tests/flows -v; s=$$?; \
		if [ $$s -eq 5 ]; then echo "no flow tests yet"; else exit $$s; fi

seed:  ## firmware v1, harness v1, arm-1 + sim-1..3 (M1)
	$(CLI) seed

reset:  ## drop and recreate the db, clear data/ (refused after overnight start unless FORCE=1)
	$(COMPOSE) exec -T -e FORCE=$(FORCE) api python -m romulus_cloud.cli reset

digest:  ## counts since SINCE (ISO or 1h / 12h; default: overnight start, else 12 h) (M7)
	$(CLI) digest $(if $(SINCE),--since $(SINCE))

overnight-start:  ## mark the overnight run as started in the db; reset then refuses (K7)
	$(CLI) overnight start --by $(or $(BY),$(USER),operator)

overnight-stop:  ## clear the overnight flag (reset allowed again)
	$(CLI) overnight stop --by $(or $(BY),$(USER),operator)

overnight-check:  ## preflight before leaving it overnight: PASS/FAIL per check (cloud/OVERNIGHT.md)
	@bash cloud/scripts/overnight_check.sh

export:  ## build requested dataset exports (M6)
	$(CLI) export

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .
