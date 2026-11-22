# LexAgent Developer Tooling Guide

This guide documents the local engineering tooling baseline established for LexAgent, including Git hook management with Lefthook, optional diff viewing with Delta, and the offline OpenCodeReview evaluation protocol.

---

## 1. Git Hook Automation: Lefthook

LexAgent uses **Lefthook v2.2.2** for fast, deterministic local pre-commit and pre-push quality gates.

### 1.1 Quality Gates Configuration

- **Pre-commit (`pre-commit`)**:
  - `ruff-lint`: Executes `uv run ruff check {staged_files}` on staged Python files (`**/*.py`).
  - `ruff-format`: Executes `uv run ruff format --check {staged_files}` on staged Python files.
  - *Behavior on non-Python changes:* Automatically skips cleanly when no `.py` files are staged.
- **Pre-push (`pre-push`)**:
  - `mypy`: Runs `uv run mypy src/` for static type safety.
  - `legal-safety`: Runs `uv run pytest -q -m legal_safety` to enforce deterministic statutory invariants.
  - *Authoritative CI gate:* Comprehensive multi-version Python matrices, full test suites, Bandit SAST, and packaging checks remain in GitHub Actions.

### 1.2 Activation

To install or sync Lefthook git hooks into `.git/hooks`:

```bash
# Ensure development dependencies are synced
uv sync --extra dev

# Install Lefthook hooks into .git/hooks/
uv run lefthook install
```

### 1.3 Manual Execution & Testing

You can manually trigger hooks at any time without committing or pushing:

```bash
# Run pre-commit checks on staged files
uv run lefthook run pre-commit

# Run pre-push checks
uv run lefthook run pre-push
```

### 1.4 Emergency Bypass

If an emergency commit or push must bypass local hooks:

```bash
# Bypass via git flag
git commit -m "commit message" --no-verify

# Or bypass via environment variable
LEFTHOOK=0 git commit -m "commit message"
```

### 1.5 Safe, Non-Destructive Rollback

To completely deactivate Lefthook and restore the pre-tooling state without affecting unrelated files:

1. **Deactivate Git hooks:**
   ```bash
   uv run lefthook uninstall
   ```
   *Note:* `lefthook uninstall` strictly removes Lefthook-managed hook wrappers (`.git/hooks/pre-commit`, `.git/hooks/pre-push`) and automatically restores any pre-existing `.old` hook files. It does not modify global or repository Git configuration (`core.hooksPath`).

2. **Remove specific tooling configuration files:**
   ```bash
   # On POSIX / Git Bash:
   rm -f lefthook.yml docs/dev/tooling_guide.md
   rmdir docs/dev 2>/dev/null || true

   # On Windows PowerShell:
   Remove-Item -Path lefthook.yml -ErrorAction SilentlyContinue
   Remove-Item -Path docs/dev/tooling_guide.md -ErrorAction SilentlyContinue
   ```
   *(Do not run broad destructive commands like `git clean -fd` or `git checkout .`, which would wipe unrelated uncommitted work).*

3. **Revert only the Lefthook dependency in `pyproject.toml`:**
   Remove the `"lefthook==2.2.2"` entry from `[project.optional-dependencies].dev` in `pyproject.toml` while preserving any other dependency modifications, then re-sync:
   ```bash
   uv sync --extra dev
   ```

4. **Remove user-level agent skill (if installed):**
   ```powershell
   Remove-Item -Recurse -Force "C:\Users\adith\.gemini\config\skills\property-based-testing"
   ```

---

## 2. Optional Git Diff Tool: Delta

[Delta](https://github.com/dandavison/delta) is an optional syntax-highlighting pager for Git diffs. It is **not** a project dependency.

### 2.1 Installation (Optional)

- **Windows (winget):** `winget install dandavison.delta`
- **Windows (scoop):** `scoop install delta`
- **macOS (brew):** `brew install git-delta`
- **Linux:** Download from [Delta GitHub Releases](https://github.com/dandavison/delta/releases).

### 2.2 Git Configuration

Add the following to your `~/.gitconfig` or `.git/config`:

```ini
[core]
    pager = delta

[interactive]
    diffFilter = delta --color-only

[delta]
    navigate = true
    light = false
    side-by-side = true
    line-numbers = true
```

---

## 3. OpenCodeReview Blind Pilot Protocol (Future Evaluation)

LexAgent supports an offline read-only evaluation pilot using [Alibaba OpenCodeReview](https://github.com/alibaba/open-code-review).

### 3.1 Data Transmission & Privacy Architecture

> [!IMPORTANT]
> **Host Model Transmission Notice**: In OpenCodeReview Delegation Mode (`open-code-review-delegate`), the `ocr` CLI executes locally without communicating with Alibaba servers, DashScope, or external OCR cloud APIs. However, the host coding agent (e.g. Codex or Gemini Antigravity) running the review **does transmit the diff and relevant code context to its own underlying host model provider** (OpenAI / Google). This mode eliminates third-party tool accounts and separate API costs, but is not an air-gapped review.

### 3.2 Four-Phase Blind Review Workflow

1. **Phase 1 (Blind Review Prompting):** The host agent is provided *only* with the raw Git diff and repository context of historical commits (e.g., PR #1 commits `bc47290` and `8c4a1db`). Prior review findings and human fix commits are strictly withheld.
2. **Phase 2 (Independent Logging):** The agent records all identified defects, line citations, severity levels, and proposed fixes in an immutable log artifact.
3. **Phase 3 (Unblinding & Evaluation):** The logged findings are unblinded and cross-referenced against ground-truth PR #1 issues (e.g., missing part-time gratuity proration, silent nationality assumption in `tool_node()`, manifest provenance confusion).
4. **Phase 4 (Quantitative Scoring):** The review is evaluated on True Positives, False Positives, False Negatives, signal-to-noise ratio, execution latency, and token consumption.
