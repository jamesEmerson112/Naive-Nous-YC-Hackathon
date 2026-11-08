# Build guide: landing the Sunlit Workshop

_This is the contract for every PR in the redesign. It sits on top of [`README.md`](README.md) (what changes, and why), [`DESIGN.md`](DESIGN.md) (the tokens) and [`mock.html`](mock.html) (the look). Read all four before you touch code, and follow [`cloud/CLAUDE.md`](../../cloud/CLAUDE.md)._

## Lanes
Each lane is one branch, one PR, and its own files. The coordinator session merges, in the order below.

| # | Branch | Owns | Starts after |
|---|---|---|---|
| 1 | `ui/foundation` | <ul><li>`romulus.css`: tokens, aliases, shared atoms and the shell, plus **empty page sections** for the other lanes</li><li>`base.html`, `partials/killswitch.html`</li><li>a new `partials/ui.html` (macros) and `partials/icons.html` (SVG sprite)</li><li>`api/web.py`: the `you` tone and the filters</li></ul> | now |
| 2 | `ui/technician` | `technician.html`, `partials/technician_case.html`, and `partials/tc_style.html` if it exists on main | now (rebase onto lane 1 before you preview) |
| 3 | `ui/fleet` | `timeline.html`; `partials/overview.html`, `fleet.html`, `incidents.html`, `chain.html`; the `page: fleet` section of `romulus.css` | lane 1 merged |
| 4 | `ui/incident` | `incident.html`, `agent_run.html`, `data.html`; `partials/story.html`, `run.html`, `run_page.html`; the `page: incident`, `page: run` and `page: data` sections | lane 1 merged |
| 5 | `ui/learning` | `learning.html`, `partials/learning.html`, the `page: learning` section | lanes 1–2 and `learn/page` merged |
| 6 | `ui/polish` | anything, as small fixes only, after a cross-page audit | lanes 1–5 |

**Rules for working in lanes:**
- Edit only your own files and the `romulus.css` sections between `/* ==== page: <name> ==== */` and `/* ==== end page: <name> ==== */`.
- If you need something shared, add it inside your section with a `/* shared? */` comment and say so in the PR. Lane 6 promotes it.
- If a test pins markup you change, update that assertion in the same commit.

## The shared vocabulary (lane 1 creates it, everyone uses it)
- **Tokens:** exactly the names in DESIGN.md and `mock.html`:
  - grounds and lines: `--ground --surface --well --line --line-strong`
  - ink: `--ink --ink-2 --muted`
  - the state colours, each with `-ink` and `-wash` variants: `--leaf`, `--sun`, `--sky`, `--clay`
  - also `--on-leaf --shadow --focus --ease --r-card --r-control`

  Day is on `:root` and `[data-theme="day"]`, Night on `[data-theme="night"]`. The old names (`--bg --text --good --bad --wait --info --accent --agent …`) stay as **aliases** onto the new ones, so pages that haven't been touched keep working.
- **Tones:** `tone-good|you|wait|bad|info`, plus the `chip-*` and `tone-edge-*` versions.
  - `wait` is now **sky** (working). `you` is new (**sun**: a person).
  - `api/web.py` maps `technician.diagnosed`, `needs_human` and `blocked_over_cap` to `you`. It does this where `story.status` is unpacked, and leaves the domain alone.
- **Atoms:**
  - `.card`, `.pill` (and `.status`, `.badge`, `.chip` all look like it), `.btn`, `.btn-primary`, `.btn-secondary`, `.icon`, `.section-h`
  - the heal line: `.line > li.st.{done|you|work|bad|todo} > .node + .lab`
  - the mini heal line: `.mini > i.{d|y|w|b|t}`, then `.mini-lab`
  - the your-turn card: `.turn`, `.turn-mark`, `.turn-t`, `.turn-s`, `.turn-cta`
  - `.is-changed`
- **Macros:** `{% from "partials/ui.html" import icon, heal_line %}`
  - `icon(name)` renders `<svg class="icon" aria-hidden="true"><use href="#i-NAME"/></svg>`. The names are `check x hand arrow out mail pause alert agent auto person chevron`, and the sprite sits once in `base.html`.
  - `heal_line(stations, mini=false)` takes `stations`, a list of `(label, state)` pairs, where state is one of `done you work bad todo`.
- **Filters:**
  - `shortid`: `wo_3d7cd6464e2e4980a69acd0a3e031264` becomes `wo_3d7cd6…`. Always put the full id in a `title`.
  - `kindword`: `joint_stall` becomes "Joint stall".
- **The live-change helper:** an element with `data-live="<key>"` gets `.is-changed` once, after an htmx swap, whenever its text differs from the last render. Use it only for the Approve moment and for status changes. Never use it on entrance animations.

## Design rules (the craft floor, short form)
- **Colours:** tokens only. No hex, rgb or hsl outside the token block, and no violet anywhere.
- **Type:** only the scale steps: 0.833, 1, 1.2, 1.44, 1.728, 2.074 and 2.488rem. Weights 400–700. The verdict stays the largest text on its page.
- **Never use:**
  - a tracked-caps label above a heading (kicker or eyebrow): put the meta after the heading, or make it the heading
  - a coloured `border-left` or `border-right` wider than 1px (side stripes)
  - gradients or gradient text, except inside a heal-line track
  - glass or backdrop blur
  - a card inside a card
- **Status is never colour alone.** A word or an icon always goes with it.
- **Icons** come from `icon()`, not Unicode glyphs. Where a test pins a glyph string, update the test.
- **One primary button per screen.** Freeze stays a quiet outline.
- **Motion:**
  - `transition: … 150ms|250ms var(--ease)`, with no bounce and no load sequences.
  - The only loop allowed is the waiting sun station.
  - Under `prefers-reduced-motion`, motion gets shorter, not deleted.
- **Copy:** prose first. Use readable kinds and short ids, and put raw JSON behind a `<details>`.

## Keep, because tests and the demo depend on it
- Routes, polling intervals, the htmx attributes and ids (`#tech-case`, `#story`, `#overview`, `#fleet`, `#incidents`, `#killswitch`, `#stale`), and every `data-*` hook.
- **In the Approve handler,** `window.open` runs before `await fetch` and before the resolve POST body.
- **On the Technician page:**
  - the header text "Naïve agent · technician · live session", or the replay badge
  - four `tc-rec-tag`s on a replay
  - the stage labels, "human click(s)", and the four gate labels
  - `<li class="ok"` on the ticks
  - the verdict is the largest text: 42 px at 100% zoom
- **The nav** keeps the exact string `<a class="nav" href="/technician">Technician</a>`.
- **The domain stays put:** don't change `domain/story.py` statuses or their tests.
- **Offline:** no webfonts, no CDN, no new scripts.

## Preview your branch (your own stack, never `tech` or `learn`)
```bash
# in your worktree; pick your lane's port: 1→8010 2→8011 3→8012 4→8013 5→8014 6→8015
printf 'TECHNICIAN_MODE=fake\nAPI_PORT=8012\n' > .env        # fake mode: no real email, no Naïve calls
export COMPOSE_PROJECT_NAME=ui-fleet API_PORT=8012
docker compose -f compose.yaml -f compose.dev.yaml --profile sim up -d --build && make seed   # dev mounts the fixtures; plain `make sim` drops them
python3 cloud/scripts/technician_demo.py fake --base http://localhost:8012 --no-open   # a case page
```
- **Screenshots:** headless Chrome. The narrowest layout it renders is 500 px.
  ```
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars --user-data-dir=<tmp> --window-size=1440,900 --virtual-time-budget=4000 --screenshot=<file>.png "<url>?theme=day"
  ```
  For Night, use `?theme=night`. Look at every screenshot yourself before you call it done.
- **Clean up:** `docker compose -p ui-<lane> down -v` when you finish.
- **Never touch these:**
  - the `tech` stack (:8000) or the `.claude/worktrees/tech-demo` worktree, which is pinned for the 1 PM demo
  - the `learn` stack (:8001) or `.claude/worktrees/learn-stack`
  - `make reset`, on any shared stack

## PR checklist
- [ ] **Targeted tests, run once before the PR:** `uv run pytest -q -x -p no:randomly cloud/tests/unit/web` plus the flow tests that fetch your pages (see `grep -rln '"/"\|/partials/' cloud/tests`), then `make lint`. Don't run the full `make test` (8 min); the coordinator runs it once on main after the last UI merge.
- [ ] Screenshots of your pages at 1440×900, Day and Night, viewed and listed in the PR.
- [ ] Nothing on the "Never use" list in your diff. Run `git diff origin/main | grep -nE '#[0-9a-fA-F]{3,6}\b|gradient|backdrop|border-left: *[2-9]|text-transform: *uppercase'` and justify every hit.
- [ ] Commits: small, with a one-sentence message and **no AI co-author lines** (repo rule). Run `git pull --rebase origin main` before you push.
- [ ] The PR body says what changed, cites the README section it implements, lists the screenshots and tests, and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- [ ] **Don't merge.** Report the PR number. The coordinator reviews, then merges.
