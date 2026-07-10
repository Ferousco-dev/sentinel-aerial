# Contributing to Sentinel

This repository runs an **enterprise workflow** even with a single primary
developer. The rules below are non-negotiable; they keep history clean, reviews
meaningful, and releases traceable. The full process lives in
[`docs/ENGINEERING_WORKFLOW.md`](docs/ENGINEERING_WORKFLOW.md).

## The golden rules

1. **Every change starts with an Issue.** No issue, no branch.
2. **One issue → one branch → one Pull Request.** Never mix unrelated work.
3. **No direct commits to `main`.** `main` only advances through merged PRs.
4. **Every PR gets a code review** against the checklist before merge.
5. **Keep PRs small and reviewable** (aim < ~400 lines of diff).

## Branch naming

`<type>/<slug>` where `type` ∈ `feature | fix | refactor | perf | docs | test | chore`:

```
feature/detection
fix/rtsp-reconnect
refactor/frame-source
perf/enhance-latency
docs/setup-guide
test/enhance-controller
```

## Conventional Commits

Every commit and PR title:

```
<type>(<scope>): <imperative summary>

feat(detect): add YOLOv8n detector stage
fix(ingest): recover from mid-stream RTSP drop
refactor(enhance): extract adaptive controller
perf(enhance): reuse CLAHE operator across frames
docs(readme): document benchmark workflow
test(enhance): cover per-tier latency memory
chore(ci): add lint + self-test workflow
```

Scopes track the subsystem: `ingest`, `enhance`, `detect`, `log`, `dashboard`,
`alert`, `report`, `ci`, `docs`.

## Local workflow

```bash
# 1. Pick an issue, create its branch
git checkout main && git pull
git checkout -b feature/detection

# 2. Do the work in small commits (Conventional Commits)
# 3. Run the gates locally before pushing
ruff check sentinel
python -m compileall -q sentinel
python -m sentinel.enhance 640 480

# 4. Push and open a PR that "Closes #<issue>"
git push -u origin feature/detection
gh pr create --fill --base main
```

## Definition of Done

- [ ] Acceptance criteria in the issue are all met
- [ ] CI green (lint, compile, self-test)
- [ ] Code review checklist satisfied
- [ ] Docs/docstrings updated
- [ ] PR linked to its issue and milestone
