# Instructions for coding agents

This project is being re-architected. See **REARCH_PLAN.md** (overall plan,
Phases 0–4) and **docs/PHASE_3_UI_PLAN.md** (the UI, not yet built). The new code
lives in the `library` and `places` apps; the legacy `myphoto` app still serves
the old Geo Tag / Footprints UI but is **being replaced and removed in Phase 3** —
do not build new features on it.

## Key Dependencies
- Python 3.12+
- UV package manager (`uv.lock` present) — use `uv sync`, `uv add`, `uv run`.

## Code Quality

```bash
ruff check .            # lint
ruff check . --fix      # lint + autofix
ruff format <file.py>   # format a file (recommended after editing)
ty check .              # type check (ty is the type checker; pyright is NOT used)
```

**Only run ruff/ty on Python (.py) files. DO NOT run them on HTML, CSS, JS, or
templates.**

After any code change, run, in order:
1. `ruff format <changed_file.py>`
2. `ruff check . --fix`
3. `ruff check .`
4. `pytest`
5. `ty check .`

Project rules: line length **98**, indent **4 spaces**, imports at top of file,
PEP8. Pre-commit runs ruff + ty (see `.pre-commit-config.yaml`).

## Database

Local-first **SQLite** at `data/track_me.db` (the `data/` dir holds the db and
thumbnails; auto-created at startup, gitignored). Set `DATABASE_URL` to override
with Postgres (e.g. Cloud Run). The fresh schema is re-derivable — recreate with
`python manage.py migrate`.

## Ingestion & geocoding pipeline (new)

```bash
# 1. Ingest a Google Takeout extract: parse sidecar JSON + EXIF, set taken_at for
#    every item, resolve location, store the Google Photos URL, cache thumbnails.
python manage.py ingest <takeout-dir>        # directory arg is REQUIRED

# 2. Reverse-geocode located items into place names (H3-batched, Google API).
python manage.py geocode [--resolution 9] [--max-api-calls N] [--recalculate]
```

Both commands are re-runnable/incremental (dedupe by `MediaItem.dedupe_key`) and
never overwrite manual edits. Key model: `library/models.py::MediaItem`
(`taken_at`, `latitude/longitude`, single `h3_cell`, `location_source`,
`time_source`, `google_photos_url`, `needs_review`, content-addressed thumbnails).

## API

django-ninja, mounted at `/api` (auto docs at `/api/docs`); root in
`track_me/api.py`. App routers are added as features land.

## Development Server

Always compile Tailwind CSS before serving (UI is broken without it):

```bash
python manage.py tailwind build       # compile once
python manage.py tailwind runserver   # compile + watch + runserver
```

## Testing

```bash
pytest                       # full suite (fast; SQLite in-memory)
pytest tests/test_xxx.py -v  # one file
pytest --cov                 # coverage
```

- Test settings: `tests/settings.py` (SQLite in-memory; WhiteNoise disabled).
- Real-world sidecar fixtures live in `tests/fixtures/` (anonymized) — add new
  Takeout quirks there as regression cases.
- **Playwright UI tests** are gated to macOS: mark them `@pytest.mark.playwright`
  and a `conftest.py` hook auto-skips them off macOS (CI / remote Linux). Install
  browsers with `playwright install chromium`. (No UI tests exist yet — Phase 3.)

## Git Commit Guidelines

Always include a summary in the commit message:

```bash
git commit -m "$(cat <<'EOF'
Brief description of changes

Summary of what changed:
- specific change 1
- specific change 2
EOF
)"
```

Explain what changed and why (if not obvious).
