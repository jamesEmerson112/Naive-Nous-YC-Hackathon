# Overnight run: runbook (flow F10, build plan M7)

The goal: fake fleet + chaos schedule run **≥ 6 h unattended**, launched by **~12:30 AM**.
In the morning, `make digest` counts what the agents and the platform did, and how many
human actions it took (target 0). Every command runs from the repo root.

## 1. Before launch (11:30 PM to 12:15 AM)

1. **Laptop:** plug in the power adapter. Keep the lid open, or close it only with an
   external display, keyboard and power attached. In Docker Desktop, turn on
   *Settings → General → Start Docker Desktop when you sign in*.
2. **The launch commit:** check it out and make sure the tests pass on it (`make test`).

## 2. Launch (by 12:30 AM), in this order

```bash
caffeinate -dims                     # its own terminal tab; leave it running all night

make down                            # stop whatever runs now, fake fleet included
make up && FORCE=1 make reset        # fresh database whose schema matches this commit
                                     # (FORCE=1 because an old .env may set OVERNIGHT_STARTED)
cp .env .env.before-overnight        # keep your keys
cp .env.overnight.example .env       # overnight values: NAIVE_MODE=fake, 1200 / 3600, ...
rm -f .env.overnight                 # no stale override file
#   now copy your secrets (NAIVE_API_KEY, LLM_API_KEY, ...) from .env.before-overnight into
#   .env, but keep every *_MODE / *_PROVIDER line from the example: all fake (K13)
make sim                             # db, api, worker + fake-devices, --chaos=overnight
make seed
make overnight-start BY=kent         # DB flag: reset refuses; the digest counts from here
make overnight-check                 # every line PASS (a WARN is a judgment call; FAIL is not)
```

`NAIVE_MODE` must be **fake** all night: the chaos plants hardware faults, and a live work
order could phone a real supplier at 3 AM (build plan K13). `make overnight-check` asks the
running api and worker for the mode they actually resolved. Even in live mode, an order for
a device that isn't registered as `real` is never posted (it waits for a person).

Then Kent, at the go/no-go: commit, tag `demo-overnight`, push the tag.

What the chaos does (`tools/fake_device/src/fake_device/chaos.py`, rates in `PROFILES`):
a software fault about every 10 min (3 in 10 are D1 code patches, about 1 in 5 arms a bad
candidate on the canary, `sim-2`), a park/hardware fault about every 2 h (**capped at 2 a
day**, distinct arms, so work orders stay under `ORDERS_PER_DAY_MAX=3`), a short offline
period about every 90 min (the outbox drains afterwards), an unlabeled episode per arm
about every 15 min, and a 30 min quiet stretch every few hours. Same seed, same night.
Change it with `CHAOS_SEED=1 make sim`; try a dense version with `CHAOS=demo CHAOS_SPEED=60 make sim`.

## 3. Optional 3 AM check

`make overnight-check` (it FAILs if a worker step failed in the last few minutes), and
`docker compose logs --since 1h fake-devices | grep "chaos so far"`. A restart of
fake-devices resumes the schedule from `data/fake_device/chaos.json`; it never replays it.
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

## 5. Stop, in this order

```bash
docker compose --profile sim stop fake-devices   # FIRST: no more chaos faults
make overnight-stop BY=kent                      # clears the DB flag
cp .env.before-overnight .env && make up         # back to your own settings
```

Stop the fake fleet before changing `.env`: with chaos still running, a park after the
switch would create a work order under your normal (possibly live) settings.
Keep the data: datasets (M6) are built from it on Sunday morning. `make reset` refuses
while `OVERNIGHT_STARTED=true` or the DB flag is set; `FORCE=1` overrides, and wipes the night.
