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

# Format specific file (recommended after editing)
ruff format <filename.py>

# Format all Python files
ruff format .

# Type checking
pyright
```



## Claude Code Instructions

**IMPORTANT**: When working on this project, Claude Code must ALWAYS follow these steps after making any code changes:

1. **Format the modified file** after editing (prevents many linting issues):
   ```bash
   ruff format <filename.py>
   ```

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
