# The overnight soak (James's Mac)

An order every 10 minutes, about half with a planted fault the card doesn't mention. **Hermes does each job on the real Naïve board**: James chose that as the overnight default. Each order gets one line in `runs/soak.jsonl`; an hourly check-in fixes what broke and logs it at the end of [`MORNING.md`](MORNING.md). The code is `armsim/soak.py`, and its docstring has every option.

**Latest test (Sun 12:05 and 12:12 PM UTC, two runs, Linux sim host, scripted commander, file board):** each run placed 5 orders 30 s apart, 4 with faults. Both ended 5 of 5 done, with 4 of 4 healed.
- Every fault tripped during the job, at `approach`, and was healed there.
- `pod_image02` (twice a run): podd flagged `clock_skew`, and the pod's rollback healed it (47–48 s from order to done).
- `pod_image07`: podd flagged `us_mismatch`, and the rollback healed it (38–39 s).
- `us_frozen`: a sensor restart healed it (28 s).
- The clean pick took 17–18 s.
- The pod faults landed 0.7–1.2 s after the soak saw the claim. The stuck sensor landed 2.3–2.9 s after it (2 s planned).
- The scripted commander is the no-LLM stand-in for Hermes (`armsim/dispatch.py`). Hermes on the real board hasn't run a soak yet.

**Known gap: the block.** A pick that fails between `grip` and `release` loses the block for the rest of the night. A park while holding the block turns the torque off, so the jaws open and the block drops by the rest pose. Every later pick then ends `BLOCKED: grip: the gripper isn't holding the block`.
- **What happens:** the soak warns loudly after two clean orders in a row end blocked. It calls the host's `reset_scene` after a failed pick, but the host doesn't have that op yet.
- **The fix until then:** restart the host between orders (`demo/down.sh && demo/up.sh`). Earlier runs had pod faults trip anywhere from `go_home` to `release`, so expect this at least once overnight.

## Start it
Run everything from `prototypes/arm-sim`. Keep the Mac plugged in with the lid open, and run `caffeinate -ims` in a spare tab so it doesn't sleep (Ctrl-C there ends it).

1. **The sim:** `demo/up.sh` starts Romulus, the arm host with a virtual pod and `podd`, and the edge bridge.
2. **Once, before the real board:** `PYTHONPATH=. .venv/bin/python -m armsim.board smoke --board naive`. Only `add` has been run against the real CLI so far.
3. **The soak, with Hermes on the real board** (the overnight default; it spends LLM tokens on every order):
   ```
   PYTHONPATH=. .venv/bin/python -u -m armsim.soak run --board naive --with-dispatcher hermes --every 600 --hours 4 2>&1 | tee -i -a runs/logs/soak.log
   ```
   - **`--with-dispatcher hermes`** starts `python -m armsim.dispatch --board naive --exec hermes` as the soak's child, and restarts it if it dies.
   - **`--every 600`** is the default: an order every 10 minutes, counted from the start.
   - **`tee -i`** keeps the log going through Ctrl-C, so the soak can say how it stopped.
   - **Dispatcher log:** `runs/logs/soak-dispatch.log`.
   - **Hermes's output:** one file per card, `runs/commander/<card>.log`.
   - **Already running that dispatcher** in its own terminal? Leave out `--with-dispatcher` and keep `--board naive`. The soak warns if no dispatcher holds the board.
4. **No tokens, or Hermes is down:** the scripted commander works the same cards with the same healing pattern, on the local file board:
   ```
   PYTHONPATH=. .venv/bin/python -u -m armsim.soak run --with-dispatcher scripted --every 600 --hours 4 2>&1 | tee -i -a runs/logs/soak.log
   ```
5. **Faults:** the default is `us_frozen,pod_image07,pod_image02`, and they take turns.
   - **A bad pod image** goes out as soon as the soak sees the claim, because podd needs about 10 s to flag it.
   - **A stuck sensor** goes in 0.2–2.5 s after the claim, if the card is still in doing.
   - **Each fault lands after the skill in progress**, because the host does one thing at a time. The `fault_planted` event gives the measured time.
   - `joint_stuck` (a dead elbow) tests the hand-off. The soak plays the person: it clears the fault and re-checks, and the order counts as `needs_human`. Leave it out overnight until the host has `reset_scene`: planted after `approach`, the sagging arm misses the grip and knocks the block away.
   - Leave `us_no_echo` out. In sim a sensor restart doesn't bring the echo back, so it always ends blocked.
6. **Other orders on the same board** (Mission Control, a teammate, `armsim.orders add`) make the soak skip its slot as `busy` while their card is in todo or doing. The soak never places an order on top of another job.

## Stop it
1. **Ctrl-C once** in the soak's terminal:
   - the order in flight is recorded as `stopped`;
   - its fault is cleared;
   - its dispatcher gets one SIGTERM, finishes its step and blocks the card;
   - the arm is checked at home.

   A second Ctrl-C quits at once, without the cleanup.
2. `demo/down.sh` stops the host and the bridge (`make down` from the repo root stops Romulus).

## Read the report
`PYTHONPATH=. .venv/bin/python -m armsim.soak report` prints Markdown to paste under **Soak log** in MORNING.md. Add `--since 70m` for the last hour, or `--since 2026-11-08T03:00`.
- **Orders:** how many ended done, blocked, not claimed, busy, or needs a person.
- **Time to done:** from placing the order to the card reaching done, as a median and the worst order.
- **The fault table:** for each fault, how many were planted, how many healed, and how fast.
  - **Healed** means three things: the fault landed before the card was done, this card's job noticed it, and the card ended done. Noticed means an incident or heal after the plant, from the commander's events, the host's status or the host's incident history.
  - **"Planted, not noticed during the job"**: the card ended done, but nothing tripped before it did. For example, image 02 needs about 10 s to show, and a wave is over sooner.
  - **"Landed after the job ended"**: the inject reached the host after the card was done, so it tested nothing. These don't count in the Planted column.
- **No fault planted … tripped an incident anyway:** a false alarm, or the sim drifting (say, the block knocked out of place). A fault Mission Control's presenter planted during the order is recorded as `outside_faults` and isn't counted as a false alarm.
- **Busy:** slots skipped because another order's card was in todo or doing, with those card ids. A busy slot isn't a failure, but every slot busy all night means a card is stuck in doing.
- **Residue:** an incident or a bad pod image an order left behind. The soak cleans it up before the next order, and says when it couldn't.
- **Failures:** every order that didn't end done, with its order and card ids and the `BLOCKED:` reason. A run of "the gripper isn't holding the block" is the block gap above.
  - Follow one up with `grep <card> runs/events.jsonl`.
  - Then read `runs/logs/host.log`, `runs/logs/soak-dispatch.log` and, for Hermes, `runs/commander/<card>.log`.
- **A late claim:** a card claimed just as the soak gave up on it (120 s) is waited for, not counted as not claimed. Its line says `late_claim: true`.

## The hourly check-in
Set it up as a scheduled task in the Claude desktop app (every hour), or run `/loop 60m <prompt>` in Claude Code. The prompt:

> You are the hourly check-in for the overnight arm soak in Naive-Nous-YC-Hackathon, folder prototypes/arm-sim (read SOAK.md there). A soak places an arm order every 10 minutes; don't stop or restart the soak, the dispatcher or the arm host unless one of them is dead or you must reload a fix.
> 1. Run `git pull --rebase` on the current branch.
> 2. Run `PYTHONPATH=. .venv/bin/python -m armsim.soak report --since 70m`, then the same without `--since` for the whole night.
> 3. Check the pieces are alive:
>    - `pgrep -fl 'armsim\.(soak|dispatch|host)'`;
>    - `PYTHONPATH=. .venv/bin/python -c "from armsim.client import call; print(call('status'))"`;
>    - the last 40 lines of `runs/logs/host.log`, `runs/logs/soak-dispatch.log` and `runs/logs/soak.log`.
>
>    For each failure in the report, read `grep <card id> runs/events.jsonl` and, if Hermes ran it, `runs/commander/<card id>.log`.
> 4. If something broke (a failure, an order not claimed, residue not cleaned up, a dead process), find the cause.
>    - Clean orders failing with "the gripper isn't holding the block" mean the sim's block was knocked away. Restart the host between orders (`demo/down.sh && demo/up.sh`) and log it.
>    - Otherwise, fix it with the smallest change in prototypes/arm-sim.
>    - Run `PYTHONPATH=. .venv/bin/python tests/test_soak.py` and `tests/test_dispatch.py`.
>    - Re-run the failing case once, right after one of the soak's orders finishes (they take under a minute), against the running dispatcher, with the same board: `PYTHONPATH=. .venv/bin/python -m armsim.soak run --orders 1 --fault-rate 1 --faults <that fault> --board <same board> --file runs/soak-recheck.jsonl`.
>    - A fix to code a running process has loaded only takes effect after that process restarts. Restart only that one, between orders: `kill -INT <pid>` once, wait for it to exit, start it again the way SOAK.md says, and say so in the log.
> 5. Append a dated entry at the end of `prototypes/arm-sim/MORNING.md`, under a `## Soak log` heading (create the heading if it's missing).
>    - The entry starts with a heading like `### Sun Nov 8, 4:05 AM PST`, followed by 2–5 short bullets:
>      - the hour's numbers (orders, done, healed %);
>      - each failure with its order and card ids and the cause;
>      - what you changed (the file, in one line) and what the re-run showed.
>    - If nothing broke, write one bullet: "N orders, N done, nothing broke".
>    - Keep MORNING.md's style: plain, short sentences.
> 6. Commit and push.
>    - `git add` only MORNING.md and the files you changed.
>    - Commit as "Soak check-in <time>: <one line>".
>    - Run `git pull --rebase`, then `git push` to the current branch. If the push is rejected, pull --rebase once and retry.
>    - Never force-push. No AI co-author lines. Don't touch the team-status or *-sync blocks in CLAUDE.md.
