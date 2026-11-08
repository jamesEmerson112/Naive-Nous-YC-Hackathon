# Overnight run: runbook (flow F10, build plan M7)

The goal: fake fleet + chaos schedule run **≥ 6 h unattended**, launched by **~12:30 AM**.
In the morning, `make digest` counts what the agents and the platform did, and how many
human actions it took (target 0). Every command runs from the repo root.

## 1. Before launch (11:30 PM to 12:15 AM)

1. **Laptop:** plug in the power adapter. Keep the lid open, or close it only with an
   external display, keyboard and power attached. In Docker Desktop, turn on
   *Settings → General → Start Docker Desktop when you sign in*.
2. **Overnight settings:** `cp .env.overnight.example .env.overnight`. This sets
   `NAIVE_MODE=fake` (**never live overnight**: the chaos makes hardware faults all night,
   and live mode could phone a real supplier at 3 AM; build plan K13), the
   `CANARY_WINDOW_S=1200` / `BATCH_INTERVAL_S=3600` timings, fake LLM, and
   `OVERNIGHT_STARTED=true`.
3. **Schema:** a database created before M7 lacks one column. Either `make reset && make seed`
   (fine before launch), or keep the data and add the column:
   `docker compose exec db psql -U romulus -d romulus -c "ALTER TABLE fleet_state ADD COLUMN IF NOT EXISTS overnight_started_at TIMESTAMPTZ;"`
4. **Tests green on the commit you launch:** `make test`.

## 2. Launch (by 12:30 AM)

```bash
caffeinate -dims          # in its own terminal tab; leave it running all night
make up                   # db, api, worker (restart: unless-stopped)
make seed                 # safe to repeat
make sim                  # + fake-devices with --chaos=overnight (4 arms, seed 0)
make overnight-start BY=kent   # DB flag: reset now refuses; the digest counts from here
make overnight-check      # every line PASS (a WARN is a judgment call; a FAIL is not)
```

Then Kent, at the go/no-go: commit, `git tag demo-overnight`, `git push origin demo-overnight`.

What the chaos does (`tools/fake_device/src/fake_device/chaos.py`, rates in `PROFILES`):
a software fault about every 10 min (3 in 10 are D1 code patches, about 1 in 5 arms a bad
candidate on the canary, `sim-2`), a park/hardware fault about every 2 h (**capped at 2 a
day**, distinct arms, so work orders stay under `ORDERS_PER_DAY_MAX=3`), a short offline
period about every 90 min (the outbox drains afterwards), an unlabeled episode per arm
about every 15 min, and a 30 min quiet stretch every few hours. Same seed, same night.
Change it with `CHAOS_SEED=1 make sim`; try a dense version with `CHAOS=demo CHAOS_SPEED=60 make sim`.

## 3. Optional 3 AM check

`make overnight-check`, and `docker compose logs --since 1h fake-devices | grep "chaos so far"`.
The worker deletes the oldest unlabeled episode blobs when the store passes
`BLOB_BUDGET_GB` (every 10 min); incident evidence is never deleted.

## 4. In the morning

```bash
make digest                     # since the overnight start (or SINCE=12h, SINCE=2026-11-08T00:30-08:00)
curl -s localhost:8000/v1/digest | jq .   # the same as JSON
```

The first two lines of `make digest` are written to paste onto the demo slide, e.g.
"In 7.0 h: 41 incidents, 37 healed on the device, … ; 1 human actions (…)". The one
human action is the launch itself (`make overnight-start`), listed underneath. Also look
at: "waiting for a person" (should be 0), the blob store line (under budget), the
timeline page at `http://localhost:8000/`, and `docker compose ps` (everything up;
`docker inspect -f '{{.RestartCount}}' <container>` shows any restarts).

## 5. Stop

```bash
make overnight-stop BY=kent     # clears the DB flag
rm .env.overnight && make up    # back to demo timings (or: make down)
```

Keep the data: datasets (M6) are built from it on Sunday morning. `make reset` refuses
while `OVERNIGHT_STARTED=true` or the DB flag is set; `FORCE=1` overrides, and wipes the night.
