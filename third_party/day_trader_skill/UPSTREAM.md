# Upstream: day-trader-skill

- Source: https://github.com/Jakeschincariol/day-trader-skill (MIT, see LICENSE in this folder)
- Commit: `3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd` (cloned 2026-11-09)
- Vendored unmodified: `trader-scanner`, `trader-news`, `trader-entry`, `trader-exit`, `trader-timemachine`,
  `trader-budget`, `trader-risk` (the `.py` tools, their `SKILL.md`, and the budget example configs).
- Not vendored (not used by System B): `trader-broker`, `trader-alerts`, `trader-analyst`, `trader-pelosi`, the tests and fixtures.
  Upstream's 315 tests pass at that commit.
- The wiring lives in `src/system_dts.py`, `src/dts_*.py`; nothing in this folder is edited by us.
