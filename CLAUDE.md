# Instructions for coding agents


## Key Dependencies
- Python 3.12+
- UV package manager (uv.lock present)

### Code Quality
```bash
# Run linting
ruff check .

# Run linting with auto-fix
ruff check . --fix

# Format specific Python file (recommended after editing)
ruff format <filename.py>

# Format all Python files
ruff format .

# Type checking
pyright
```

**IMPORTANT**: Only run ruff on Python files (.py). **DO NOT** run ruff on HTML, CSS, JavaScript, or template files.



## Claude Code Instructions

**IMPORTANT**: When working on this project, Claude Code must ALWAYS follow these steps after making any code changes:

1. **Format the modified Python file** after editing (prevents many linting issues):
   ```bash
   ruff format <filename.py>
   ```

   **Note**: Only format Python (.py) files. DO NOT run ruff format on HTML, CSS, JavaScript, or template files.

2. **Run Ruff linting and auto-fix** after every code modification:
   ```bash
   ruff check . --fix
   ```


3. **Check for remaining linting issues**:
   ```bash
   ruff check .
   ```

4. **Project-specific linting rules**:
   - Line length limit: **90 characters** (configured in pyproject.toml)
   - Indent width: **4 spaces**
   - Always fix simple issues like line length, imports, spacing automatically
   - Follow PEP8 standards and project conventions
   - Import statements should ALWAYS be at the top of the file

5. **After fixing linting issues, run tests** to ensure nothing is broken:
   ```bash
   pytest
   ```

6. **Type checking** (optional but recommended):
   ```bash
   pyright .
   ```

**Never skip the ruff auto-fix step** - it's configured to handle most formatting issues automatically, including line length violations, import sorting, and spacing issues.

## Development Server

**IMPORTANT**: Before starting the Django development server, ALWAYS compile Tailwind CSS:

1. **Compile Tailwind CSS** (required before running server):
   ```bash
   python manage.py tailwind build
   ```

2. **Start development server with Tailwind watch mode**:
   ```bash
   python manage.py tailwind runserver
   ```

   This command automatically:
   - Compiles Tailwind CSS
   - Watches for CSS changes
   - Starts Django development server

**Never start the server without compiling Tailwind CSS first** - the UI will be broken without the compiled CSS file.

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_photo_processing.py

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov
```

### Playwright UI Tests

The project includes Playwright-based UI tests for the photo grid interface. These tests:
- Only run on macOS (skipped on other platforms for CI compatibility)
- Use Django's `live_server` fixture with test database
- Auto-populate test database with photos from `tests/test_photos/` directory
- Require `DJANGO_ALLOW_ASYNC_UNSAFE=true` for Playwright/Django compatibility

**Test Configuration:**
- **Test database**: SQLite in-memory (configured in `tests/settings.py`)
- **Test photos**: Placed in `tests/test_photos/` directory
- **Photos base directory**: Automatically set to `tests/test_photos/` in test settings
- **Fixture chain**: `processed_photos` (function-scoped) → `live_server` → `page_with_photos` → tests

**Running UI tests:**
```bash
# Run all UI tests
pytest tests/test_photo_grid_ui.py -v

# Run specific UI test
pytest tests/test_photo_grid_ui.py::test_photo_grid_display -v

# Run with headed browser (see browser window)
pytest tests/test_photo_grid_ui.py --headed
```

**UI Test Coverage:**
- Photo grid displays multiple photos per row (responsive grid)
- Photo selection updates map view
- Double-clicking photo opens modal with preview
- Complete workflow integration

**Important Notes:**
- Tests automatically skip on non-macOS platforms
- `processed_photos` fixture runs for each test function to populate test database
- Uses SQLite in-memory database for speed (no PostgreSQL required for tests)
- Test photos are automatically found in `tests/test_photos/` directory
- Playwright browsers installed via: `playwright install chromium`
- All 4 UI tests verify photo grid, selection, modal, and complete workflow

## Git Commit Guidelines

**IMPORTANT**: When committing code changes, ALWAYS include a summary of the changes in the commit message. Use the following format:

```bash
git commit -m "$(cat <<'EOF'
Brief description of changes

Summary of what was changed:
- List specific changes made
- Include any new features or fixes

EOF
)"
```

**Examples of good commit messages:**
- `Update citation system to use start_text/end_text format`
- `Simplify locate_citations logic by removing complex fuzzy matching`
- `Add support for portfolio manager extraction with improved prompts`

**Always include a summary section** that explains:
- What functionality was added/changed/removed
- The reason for the changes (if not obvious)
