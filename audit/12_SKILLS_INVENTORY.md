# 12 — Skills inventory

| Scope | Skills found | Notes |
|---|---|---|
| `ikenlo/m-d` repo (`.claude/skills`, `.claude/commands`, `.claude/agents`) | **none** | |
| `ikenlo/md-managment` repo | **none** | |
| Container user (`/root/.claude/skills`) | `session-start-hook`, `synced/` | harness-provided, not project-specific |
| Session-provided (this run) | generic skills (code-review, simplify, security-review, run, artifact/doc tools, pdf/xlsx/docx/pptx, etc.) | none tailored to MDS |

Recommendation: no project skills are needed for correctness. Optional later:
- `canon-check` (command): runs the forbidden-symbol greps and the canon example tests.
- `release` (command): bumps the version in both files, CHANGELOG header, `test_version`.
Both are small and should be added only if the director wants them (no new features outside the plan).
