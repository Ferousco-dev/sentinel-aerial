<!--
  PR title MUST follow Conventional Commits, e.g.:
    feat(detect): add YOLOv8n detector stage
    fix(ingest): recover from mid-stream RTSP drop
  Keep this PR focused on ONE issue. Unrelated changes will be requested out.
-->

## Summary

<!-- What does this PR do and why? One or two paragraphs. -->

Closes #<!-- issue number -->

## Type of change

- [ ] `feat` — new capability
- [ ] `fix` — bug fix
- [ ] `refactor` — behaviour-preserving restructure
- [ ] `perf` — performance improvement
- [ ] `docs` — documentation only
- [ ] `test` — tests only
- [ ] `chore` / `ci` — tooling, build, or CI

## Changes

<!-- Bullet the notable changes so a reviewer can navigate the diff. -->
-

## Testing instructions

<!-- Exact commands a reviewer runs to verify. Include expected output. -->
```bash
python -m sentinel.enhance 640 480
python -m sentinel --screen --region 0,0,640,480 --enhance
```

## Screenshots / recordings

<!-- Required for any change that alters the preview, dashboard, or report UI.
     Attach a before/after. Write "N/A — no visual change" otherwise. -->

## Reviewer checklist

- [ ] Scope is a single issue; no unrelated changes
- [ ] Conventional-commit title and commit messages
- [ ] Architecture & SOLID: clear boundaries, no leaky abstractions
- [ ] Naming, readability, and comments match the surrounding code
- [ ] Error handling & edge cases covered (empty frames, dropped stream, etc.)
- [ ] Security: no secrets committed; inputs validated
- [ ] Performance: no obvious regressions in the frame loop
- [ ] Tests / self-tests updated and passing
- [ ] Docs (README / docstrings) updated where behaviour changed

## Risks

<!-- What could this break? Blast radius if it goes wrong. -->

## Rollback plan

<!-- How to revert safely. Default: `git revert <merge-sha>` — note any
     data/migration steps that revert does not undo. -->
