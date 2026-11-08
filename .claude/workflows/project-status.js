export const meta = {
  name: 'project-status',
  description: 'Evidence-backed project status: discover lanes from git/PRs/features.txt, read each lane, verify board claims, check plan vs actual, write a cited report',
  whenToUse: 'Any time you want a fresh, cited read on where the hackathon project stands. Optional args: {now: "Sat Nov 7, 5:10 PM PST", focus: "what to emphasize", maxAreas: 5, followups: 1}',
  phases: [
    { title: 'Scout', detail: 'snapshot git, PRs, features.txt, status blocks; choose lanes' },
    { title: 'Read', detail: 'one evidence-citing reader per lane' },
    { title: 'Check', detail: 'skeptic tries to refute claims; scheduler compares plan vs actual' },
    { title: 'Fill gaps', detail: 'extra reader(s) for anything the skeptic found uncovered' },
    { title: 'Synthesize', detail: 'cited status report' },
  ],
}

const A = args || {}
const NOW = A.now ? `The current time is ${A.now}.` : 'Run "date" to get the current time (PST).'
const MAX_AREAS = Math.max(2, Math.min(A.maxAreas || 5, 8))
const FOLLOWUPS = Math.max(0, Math.min(A.followups ?? 1, 3))
const FOCUS = A.focus ? `\nThe user especially wants to understand: ${A.focus}\n` : ''

const CONTEXT = `Project: prep and build for two hackathons running at once. (1) Naive Autonomous Company Hackathon (YC SF): hacking Sat Nov 7 11:30 AM -> Sun Nov 8 11:00 AM PST, presentations 1 PM Sun, every team must use the Naive SDK. (2) Hermes Agent Plugin Hackathon (Nous Research, online): due Wed Nov 11 5 PM EST. The product is a self-healing robotic-arm company (SO-101 arm, Pi 5, STM32 firmware, camera/ultrasonic) with "Romulus" as the cloud self-healing + fleet layer (Kent), a device-side healer (Ishaan), firmware/hardware (Olir, Andre), and bench Hermes + Naive console + stack hub (James). Kent's build plan with milestone due times is docs/cloud-platform-planning/cloud-platform-build-plan.md; decisions are in docs/cloud-platform-planning/cloud-platform-decisions.md; rules and prize tracks in docs/hackathons.md. Some work lives only on teammates' machines (e.g. Ishaan's edge/ folder): report it as "reported, not in repo" rather than guessing. ${NOW}`

const SAFETY = `Ground rules:
- READ-ONLY. Do not edit, create, commit, push, stash, pull, checkout or switch branches in the main working tree: another Claude session is working in it right now. Read other branches with "git show origin/<branch>:<path>", "git diff origin/main...origin/<branch>", "gh pr view <n> --json title,body,files,commits,reviews" and "gh pr diff <n>".
- Do not run "git fetch" (the scout already did). Do not run builds that write into the tree (e.g. npm run build). Never start services: no "make up", docker, docker compose or servers; prove state with tests and reading instead. Any shell command that changes directory must use "cd <dir> || exit 1" so a failed cd can never run the rest in the main tree.
- Never print or quote secrets (.env contents, API keys, tokens).
- Every fact cites its source: file path (line if useful), commit sha, PR number, or command output. Anything you worked out yourself is marked (inferred).
- The status blocks in CLAUDE.md, features.txt, and hub/src/content/*.ts are CLAIMS, not ground truth. Check them against code, commits and PRs.`

const STR = { type: 'string' }
const STRS = { type: 'array', items: STR }
const obj = (props, req) => ({ type: 'object', properties: props, required: req || Object.keys(props) })
const arr = (props, req) => ({ type: 'array', items: obj(props, req) })

const SCOUT_SCHEMA = obj({
  snapshot: obj({
    as_of: STR,
    head: STR,
    remote_branches: STRS,
    prs: arr({ number: { type: 'integer' }, title: STR, branch: STR, state: STR, author: STR }, ['number', 'title', 'state']),
    unmerged_branches: arr({ branch: STR, commits_ahead: { type: 'integer' }, last_commit: STR }, ['branch']),
    people: arr({ name: STR, role: STR, recent_activity: STR }, ['name']),
  }, ['head', 'prs', 'people']),
  lanes: { type: 'array', minItems: 1, items: obj({
    key: STR, title: STR, owners: STRS, paths: STRS, branches: STRS,
    prs: { type: 'array', items: { type: 'integer' } },
    feature_lines: STRS, claims: STRS, questions: STRS,
  }, ['key', 'title', 'paths', 'questions']) },
})

const AREA_SCHEMA = obj({
  key: STR, title: STR,
  state: { type: 'string', enum: ['not-started', 'planning', 'in-progress', 'working', 'done', 'unknown'] },
  progress_pct: { type: 'number', minimum: 0, maximum: 100 },
  summary: STR,
  done: arr({ item: STR, evidence: STR }),
  in_flight: arr({ item: STR, owner: STR, evidence: STR }, ['item', 'evidence']),
  not_started: arr({ item: STR, why: STR }, ['item']),
  blockers: arr({ blocker: STR, owner: STR, blocks: STR }, ['blocker', 'blocks']),
  risks: arr({ risk: STR, severity: { type: 'string', enum: ['low', 'medium', 'high'] } }),
  discrepancies: arr({ claim: STR, source: STR, reality: STR, evidence: STR }),
  checks_run: arr({ command: STR, result: STR }),
  answers: arr({ question: STR, answer: STR }),
  next_actions: STRS,
}, ['key', 'title', 'state', 'progress_pct', 'summary', 'done', 'in_flight', 'blockers', 'discrepancies', 'next_actions'])

const SKEPTIC_SCHEMA = obj({
  verdicts: arr({ lane: STR, claim: STR, verdict: { type: 'string', enum: ['confirmed', 'refuted', 'unverifiable'] }, evidence: STR }),
  missing: arr({ what: STR, why_it_matters: STR, paths: STRS }, ['what', 'why_it_matters']),
})

const SCHEDULE_SCHEMA = obj({
  as_of: STR,
  next_deadline: STR,
  milestones: arr({ id: STR, name: STR, due: STR,
    status: { type: 'string', enum: ['done', 'on-track', 'at-risk', 'late', 'not-started', 'unknown'] }, evidence: STR }),
  critical_path: STRS,
  requirements: arr({ requirement: STR, source: STR,
    status: { type: 'string', enum: ['met', 'in-progress', 'not-started', 'unknown'] }, evidence: STR }),
  cut_candidates: STRS,
  verdict: STR,
}, ['next_deadline', 'milestones', 'critical_path', 'requirements', 'verdict'])

// ---------- Scout ----------
phase('Scout')
const scout = await agent(`You are the scout for a project-status sweep of the git repo in the current directory. ${CONTEXT}
${FOCUS}
${SAFETY.replace('Do not run "git fetch" (the scout already did). ', '')}

Do this:
1. Run "git fetch --prune origin" once (you are the only agent allowed to fetch). Then gather: "git log origin/main --since='3 days ago' --format='%h %ad %an %s' --date=format:'%a %H:%M'", "git branch -r", "gh pr list --state all --limit 50 --json number,title,headRefName,state,author,mergedAt,createdAt", and for every remote branch other than main the commits not yet in main ("git log origin/main..origin/<b> --oneline").
2. Read features.txt and every status block in CLAUDE.md (team-status, romulus-sync, Ishaan's, and any other <name>-sync block). Skim the top-level tree (docs/, cloud/, packages/, tools/, prototypes/, hub/) and the file list of each unmerged branch.
3. Group all active or planned work into at most ${MAX_AREAS} lanes that together cover everything. A lane is a coherent slice one reader can assess in depth (by subsystem and owner). Prefer splitting where things are changing or at risk; fold quiet docs into the nearest lane. For each lane give: owners, paths and branches/PRs to read, the features.txt lines it covers, the status claims made about it (quote briefly with source), and 2-4 sharp questions a reader must answer to judge real progress.
Return the snapshot and lanes.`, { label: 'scout', phase: 'Scout', schema: SCOUT_SCHEMA, effort: 'medium' })

if (!scout) throw new Error('Scout failed; nothing to read.')
const snap = scout.snapshot
const lanes = scout.lanes.slice(0, MAX_AREAS)
if (scout.lanes.length > MAX_AREAS) log(`Scout proposed ${scout.lanes.length} lanes; reading the first ${MAX_AREAS}. Dropped: ${scout.lanes.slice(MAX_AREAS).map(l => l.title).join(', ')}`)
log(`Lanes: ${lanes.map(l => l.title).join(' | ')}`)

const snapBrief = `Snapshot: head ${snap.head}${snap.as_of ? ` as of ${snap.as_of}` : ''}. PRs: ${(snap.prs || []).map(p => `#${p.number} ${p.state} ${p.branch || ''} "${p.title}"`).join('; ')}. Unmerged branches: ${(snap.unmerged_branches || []).map(b => `${b.branch} (+${b.commits_ahead ?? '?'})`).join(', ') || 'none'}.`

const readerPrompt = (lane) => `You are assessing one lane of the project: "${lane.title}". ${CONTEXT}
${FOCUS}
${SAFETY}

Lane brief from the scout:
- Owners: ${(lane.owners || []).join(', ') || 'unknown'}
- Paths: ${(lane.paths || []).join(', ')}
- Branches: ${(lane.branches || []).join(', ') || 'none'}; PRs: ${(lane.prs || []).map(n => '#' + n).join(', ') || 'none'}
- features.txt lines: ${(lane.feature_lines || []).join(' / ') || 'none listed'}
- Status claims to check: ${(lane.claims || []).join(' / ') || 'none listed'}
- Questions to answer: ${(lane.questions || []).map((q, i) => `(${i + 1}) ${q}`).join(' ')}

${snapBrief}

Do this:
1. Read the actual code, docs and branch diffs for this lane, not just summaries. For PRs read the body and the diff.
2. Where cheap (under ~2 minutes each), run checks that prove state, e.g. "uv run --frozen pytest -q <path>" or "uv run --frozen ruff check <path>" on main. For a PR central to this lane you may test it in a throwaway worktree: do it all in ONE shell command: "D=$(mktemp -d)/wt && git worktree add --detach \"$D\" origin/<branch> && cd \"$D\" || exit 1; uv run --frozen pytest -q; cd - && git worktree remove --force \"$D\"". Record each command and its result.
3. Compare what exists against the plan (build plan milestones, features.txt lines) and the status claims. Record every mismatch as a discrepancy (claim, where it was made, what is actually true, evidence).
4. Answer the scout's questions.
progress_pct is your estimate of how far this lane is toward what the plan needs from it by the demo (Sun 1 PM), marked as an estimate in the summary.
Be concrete and terse; cite evidence for every done and in-flight item.`

// ---------- Read ----------
phase('Read')
const rawReports = await parallel(lanes.map(lane => () =>
  agent(readerPrompt(lane), { label: `read:${lane.key}`, phase: 'Read', schema: AREA_SCHEMA })
    .then(r => r && { ...r, key: lane.key, title: lane.title })
))
const reports = rawReports.filter(Boolean)
const failedLanes = lanes.filter((l, i) => !rawReports[i]).map(l => l.title)
if (failedLanes.length) log(`No report for: ${failedLanes.join(', ')}`)

// ---------- Check (needs all lane reports: barrier) ----------
phase('Check')
const reportsJson = JSON.stringify(reports)
const [skeptic, schedule] = await parallel([
  () => agent(`You are the skeptic for a project-status sweep. ${CONTEXT}
${SAFETY}

Below are the lane assessments (JSON). Readers can be wrong, and status boards can be stale.
1. Pick the 10-15 most load-bearing claims across all lanes: "done", "merged", "tests pass", "blocked on X", "nobody has started Y", and every discrepancy. Try to REFUTE each with fresh evidence of your own (git, gh, reading files, running a quick test). If you cannot check a claim, mark it unverifiable. Do not take a reader's word.
2. List what no lane covered but matters for status: a remote branch nobody read, a claimed features.txt line nobody assessed, a plan dependency (build plan section 7) nobody checked, a hackathon requirement nobody tied to work. Order by importance.

${snapBrief}
Lane assessments:
${reportsJson}`, { label: 'skeptic', phase: 'Check', schema: SKEPTIC_SCHEMA, effort: 'high' }),

  () => agent(`You are the scheduler for a project-status sweep. ${CONTEXT}
${SAFETY}

Read docs/cloud-platform-planning/cloud-platform-build-plan.md (sections 2 Timeline, 3 Milestones, 4 Checkpoints, 6 Cut lines, 7 Dependencies) and docs/hackathons.md (rules, required deliverables, prize tracks), plus anything those cite that you need.
Using the lane assessments below as evidence (verify any you lean on), produce:
- every milestone with its due time and status right now (done / on-track / at-risk / late / not-started), with evidence;
- the critical path from now to the Sunday 1 PM demo, and separately to the Hermes plugin entry (Wed Nov 11);
- each hackathon requirement (e.g. uses the Naive SDK, demo, submission items) and whether current work meets it;
- if anything is late or at risk, which of the plan's own cut lines apply;
- a one-paragraph verdict.

${snapBrief}
Lane assessments:
${reportsJson}`, { label: 'scheduler', phase: 'Check', schema: SCHEDULE_SCHEMA }),
])

// ---------- Fill gaps ----------
const gaps = (skeptic?.missing || [])
const toFill = gaps.slice(0, FOLLOWUPS)
if (gaps.length > toFill.length) log(`Skeptic flagged ${gaps.length} uncovered items; filling ${toFill.length}. Not filled: ${gaps.slice(toFill.length).map(g => g.what).join('; ')}`)
let extra = []
if (toFill.length) {
  phase('Fill gaps')
  extra = (await parallel(toFill.map((g, i) => () =>
    agent(readerPrompt({
      key: `gap-${i + 1}`, title: g.what, owners: [], paths: g.paths || [], branches: [], prs: [],
      feature_lines: [], claims: [], questions: [g.why_it_matters, 'What is its real current state, with evidence?'],
    }), { label: `gap:${i + 1}`, phase: 'Fill gaps', schema: AREA_SCHEMA })
  ))).filter(Boolean)
}

// ---------- Synthesize ----------
phase('Synthesize')
const report = await agent(`Write the project status report for the team. ${CONTEXT}

Inputs (JSON) follow: snapshot, lane assessments, skeptic verdicts, schedule analysis, gap-fill assessments.
Rules:
- Use only facts present in the inputs and carry their citations (file path, commit sha, PR #). Mark your own inferences (inferred).
- A claim the skeptic refuted must not appear as fact: say what is actually true. Mark claims the skeptic could not verify "(unverified)".
- Plain, short sentences. No hype. Do not run tools unless a citation is ambiguous.
Structure (markdown):
# Project status: <as-of time>
**In one line:** where the project stands.
**Biggest risk:** one sentence.
**Do next:** the single most valuable action, and who.
## Lanes
Table: Lane | Owner | State | Progress (est.) | Key evidence | Next
## Plan vs actual
Table: Milestone | Due | Status | Evidence. Then the critical path to Sunday's demo and to the Hermes entry, and hackathon requirements met or not.
## Blockers
Who is waiting on whom: "<blocked> <- <owner>: <what>".
## Board vs reality
Verified mismatches between the status boards (CLAUDE.md blocks, features.txt, hub) and the repo.
## Risks
Top 5, with severity.
## Next 3 hours
Up to 6 concrete actions, ordered, each with an owner.
## Coverage
What this sweep read, what it ran, and what it did not cover${failedLanes.length ? ` (no report came back for: ${failedLanes.join(', ')})` : ''}${gaps.length > toFill.length ? ` (uncovered and not filled: ${gaps.slice(toFill.length).map(g => g.what).join('; ')})` : ''}.
Keep it under ~1500 words. Return only the markdown.

SNAPSHOT: ${JSON.stringify(snap)}
LANES: ${reportsJson}
SKEPTIC: ${JSON.stringify(skeptic)}
SCHEDULE: ${JSON.stringify(schedule)}
GAP_FILLS: ${JSON.stringify(extra)}`, { label: 'synthesize', phase: 'Synthesize' })

return { report, snapshot: snap, lanes: reports, gap_fills: extra, skeptic, schedule }
