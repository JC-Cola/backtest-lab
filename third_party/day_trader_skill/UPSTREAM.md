# Upstream: day-trader-skill

- Source: https://github.com/Jakeschincariol/day-trader-skill (MIT, see LICENSE in this folder)
- Commit: `3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd` (cloned 2026-11-09)
- Vendored unmodified: `trader-scanner`, `trader-news`, `trader-entry`, `trader-exit`, `trader-timemachine`,
  `trader-budget`, `trader-risk` (the `.py` tools, their `SKILL.md`, and the budget example configs).
- Also vendored unmodified (2026-11-16, same commit, for the Congress shadow account): `trader-pelosi` (`ptr.py`, `SKILL.md`).
- Not vendored: `trader-broker`, `trader-alerts`, `trader-analyst`, the tests and fixtures.
  Upstream's 315 tests pass at that commit.
- The wiring lives in `src/system_dts.py`, `src/dts_*.py` (and `src/congress_data.py` for trader-pelosi); nothing in this folder is edited by us.
