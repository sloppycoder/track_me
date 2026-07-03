# Instructions for coding agents

The canonical guidance for this repo lives in **`CLAUDE.md`** — structure, the
`track-me` CLI, database schema, timeline recipe, testing, and commit rules. Read
it first. This file exists so tool-agnostic agents find the same instructions;
keep them in sync by editing `CLAUDE.md`.

Quick orientation:
- Local-first tool, **no Django / no ORM** — plain Python over SQLite. All code is
  one package under `src/track_me/`; local state lives under `userdata/`.
- Entry point is the `track-me` CLI (`src/track_me/cli.py`):
  `ingest · geocode · export · timeline · serve`.
- After any change run, in order: `ruff format <file>`, `ruff check . --fix`,
  `ruff check .`, `pytest`, `ty check .` (line length 98, 4-space indent).
