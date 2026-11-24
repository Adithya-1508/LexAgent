# LexAgent Developer Tooling Guide

This guide documents the local engineering tooling baseline established for LexAgent, including Git hook automation with Lefthook, optional diff viewing with Delta, dependency reproducibility considerations, and the OpenCodeReview evaluation protocol (local preprocessing with host-model review).

---

## 1. Git Hook Automation: Lefthook

LexAgent uses **Lefthook v2.2.2** for fast, deterministic local pre-commit and pre-push quality gates.

### 1.1 Quality Gates Architecture

- **Configuration File:** `lefthook.yml` (located in repository root).
- **Minimum Version Enforced:** `min_version: 2.2.2` ensures that if an older global Lefthook binary is executed from system PATH, it aborts execution immediately.
- **Fail-Closed Installation Check:** `assert_lefthook_installed: true` ensures that if the Lefthook binary cannot be found across all search locations, the generated Git hook wrapper exits with status code `1` rather than silently continuing.
- **Fail-Closed Offline Launcher:** `lefthook: uv run --no-sync --offline python -m lefthook` directs the generated shell wrapper to invoke Lefthook through the project virtual environment via Python module execution.
  - *Fallback Unreachable:* Because `/bin/sh` tests this launcher string first (`elif test -n "uv run --no-sync --offline python -m lefthook"`), it executes this branch directly. Any generic `uv run lefthook` fallback located further down in the wrapper template is completely unreachable, eliminating the possibility of un-flagged `uv run` triggering dependency resolution or package-index lookups.
  - *Fail-Closed Execution:* If `uv` or Python is unavailable in the environment, `/bin/sh` aborts with exit code `127` (command not found) or `1`, failing closed rather than falling open.
- **Hook Self-Management:** `no_auto_install: true` prevents background Lefthook executions from silently mutating or synchronizing Git hook wrappers.
- **Glob Matching Engine:** `glob_matcher: doublestar` ensures that `**/*.py` matches Python files recursively across all directory depths.
- **Execution Isolation:** All hook commands run via `uv run --no-sync --offline`.
  - **Provisioning vs Runtime Distinction:** Dependencies are provisioned explicitly during environment setup (`uv sync ...`). Routine Git hook executions run strictly `--no-sync --offline`, ensuring that local commits and pushes never trigger package index lookups, dependency resolution, or uncommitted lockfile modifications.
  - **Worktree vs Commit Inspection Distinction:** Local pre-commit and pre-push hooks execute against the developer's checked-out working directory on disk (`src/`), providing rapid developer feedback. Authoritative GitHub Actions CI jobs execute against the submitted Git commit tree in clean, ephemeral virtual environments.

#### Quality Gate Specifications:

1. **Pre-commit (`pre-commit`)**:
   - `ruff-lint`: Runs `uv run --no-sync --offline ruff check -- {staged_files}` on staged Python files.
   - `ruff-format`: Runs `uv run --no-sync --offline ruff format --check -- {staged_files}` on staged Python files.
   - *Option Terminator (`--`):* Ruff commands include `--` immediately before `{staged_files}` to ensure that filenames starting with a hyphen (e.g. `-test.py`) are strictly parsed as path arguments rather than command-line flags.
   - *Behavior on non-Python changes:* When a commit touches only non-Python files (e.g. `.md`, `.json`, `.yml`), Lefthook detects zero matching files and skips execution cleanly in 0.00s.
2. **Pre-push (`pre-push`)**:
   - Implemented as a dedicated script job via `.lefthook/pre-push/quality_checks.py`.
   - Invokes `uv run --no-sync --offline mypy --config-file pyproject.toml src/` for static type checking across the entire package.
   - Invokes `uv run --no-sync --offline pytest -c pyproject.toml -q -m legal_safety` to enforce deterministic statutory invariants.
   - *Full Execution Guarantee:* Both checks execute to completion sequentially even if the first check encounters errors. Diagnostics for all failures are displayed before the script exits with a non-zero exit code.
   - *Literal Config Path Passing:* Both checks receive literal `pyproject.toml` paths directly, rather than relying on dynamic template expansion.
   - *Unconditional Execution (Index & Ref Independence):* Unlike command jobs in Lefthook that evaluate `PushFiles` or Git index queries (which silently skip when pushing up-to-date refs or using empty alternate indexes), script jobs are evaluated unconditionally. Whether pushing existing tracking branches, newly branched refs, operating with an empty alternate index, or staging deletions of tracked config, the script executes and fails visibly if invariants are violated.

### 1.2 Inspection, Activation & Upgrades

Lefthook installs hook wrappers into the repository's `.git/hooks/` directory. Note that custom Git hooks paths are not supported by default: if `core.hooksPath` is configured in local or global Git configuration, Lefthook installation refuses execution with an error (`Custom hooks paths are not supported by default`), rather than automatically respecting the custom path. Developers must unset `core.hooksPath` (e.g. `git config --unset-all core.hooksPath`), reset it with `lefthook install --reset-hooks-path`, or explicitly force installation into the custom path.

> [!WARNING]
> **Pre-Existing Hook & Backup Inspection Required**:
> Before running `lefthook install`, you must inspect the effective Git hooks directory (e.g. `.git/hooks/`).
> 
> 1. **Foreign Hooks:** When Lefthook installs a hook wrapper, it renames any pre-existing non-Lefthook hook to `<hook-name>.old`. Git executes `<hook-name>` only; `<hook-name>.old` will **never** be executed during subsequent Git operations. If an active non-Lefthook hook is discovered, **do not install**. Stop and obtain repository owner approval for an explicit chaining strategy.
> 2. **Pre-Existing Backups:** If a `<hook-name>.old` already exists from a previous installation, Lefthook halts installation to avoid clobbering existing backups. Developers must inspect and safeguard pre-existing `.old` files before proceeding.
> 3. **Native Tooling Side Effects (`.github/hooks/lefthook.json`):** Lefthook v2.2.2 includes an `ai.copilot` integration routine during both installation and uninstallation. When `ai.copilot` is omitted (as in LexAgent), Lefthook's installation routine actively removes any existing `.github/hooks/lefthook.json` file, as does `uninstall`. Developers must check for and preserve any existing `.github/hooks/lefthook.json` before running `lefthook install` or `lefthook uninstall` to prevent unintended deletion.

#### Initial Activation in a Clean Checkout:

```bash
# 1. Inspect effective hooks directory and check for existing .github/hooks/lefthook.json
ls -la .git/hooks
ls -la .github/hooks 2>/dev/null || true

# 2. Provision virtual environment dependencies
uv sync --extra dev --extra corpus

# 3. Install Lefthook hook wrappers
uv run --no-sync --offline python -m lefthook install
```

#### Upgrading Existing Installations:

Full pytest collection and the pre-push legal safety check require the corpus extra. The acquisition tests use `openssl` on `PATH` to generate short-lived certificates for loopback HTTPS.

To upgrade existing local wrappers to the verified fail-closed baseline:

```bash
# 1. Verify no foreign hooks or uninspected .old backups exist in .git/hooks/,
#    and preserve any existing .github/hooks/lefthook.json
ls -la .git/hooks
ls -la .github/hooks 2>/dev/null || true

# 2. Perform normal wrapper installation using the pinned offline launcher
uv run --no-sync --offline python -m lefthook install

# 3. Verify both generated wrappers contain assert_lefthook_installed and fail-closed launcher
head -n 25 .git/hooks/pre-commit
head -n 25 .git/hooks/pre-push

# 4. Demonstrate fail-closed nonzero exit status when the runner is unavailable
#    (both wrappers must return nonzero exit code 127/1 rather than succeeding):
(PATH="/nonexistent" .git/hooks/pre-commit); echo "Pre-commit exit status: $?"
(PATH="/nonexistent" .git/hooks/pre-push); echo "Pre-push exit status: $?"
```

> [!IMPORTANT]
> **Preserve Existing Backups**: Do not use `--force` (`-f`) when upgrading. Normal installation preserves existing hooks and `.old` backups. If a conflict is reported (`sync hooks: ❌`), stop immediately, inspect the conflicting `.old` file or hook, and resolve it manually rather than overwriting.

### 1.3 Executable Precedence & Global Version Drift Mitigation

The installed shell wrapper in `.git/hooks/` resolves the Lefthook executable in the following strict order:

1. **`$LEFTHOOK_BIN` Override:** If the `LEFTHOOK_BIN` environment variable is defined, the wrapper executes this path directly.
2. **Pinned Module Launcher:** The wrapper evaluates `elif test -n "uv run --no-sync --offline python -m lefthook"`, executing the project virtualenv's Python module directly without network access.
3. **System PATH (`lefthook.exe` / `lefthook`):** Evaluated only if the custom launcher is unset.
4. **Local Virtual Environment:** Direct binary lookup in `.venv`.
5. **Fallback Package Runners:** (Unreachable under the pinned launcher).
6. **Fail-Closed Abort:** If no executable is found, `assert_lefthook_installed: true` ensures the wrapper terminates with `exit 1`.

#### Mitigating Global Version Conflicts:
- **`min_version: 2.2.2` Guard:** Configured in `lefthook.yml` to prevent older global binaries from running unsupported configurations.
- **Deterministic Pinning:** If an external launcher or global binary causes conflicts, export `LEFTHOOK_BIN`:
  - *Windows PowerShell:* `$env:LEFTHOOK_BIN = "$PWD\.venv\Lib\site-packages\lefthook\bin\lefthook-windows-x86_64\lefthook.exe"`
  - *POSIX / Git Bash:* `export LEFTHOOK_BIN="$(pwd)/.venv/bin/lefthook"`

### 1.4 Manual Execution & Testing

You can manually trigger hooks at any time without committing or pushing:

```bash
# Run pre-commit checks on currently staged files
uv run --no-sync --offline python -m lefthook run pre-commit

# Run pre-push quality checks
uv run --no-sync --offline python -m lefthook run pre-push
```

### 1.5 Emergency Bypass

If an emergency commit or push must temporarily bypass local hooks:

- **Git native bypass:**
  ```bash
  git commit -m "Emergency fix" --no-verify
  git push origin <branch> --no-verify
  ```
- **Environment variable bypass (POSIX / Git Bash):**
  ```bash
  LEFTHOOK=0 git commit -m "Emergency fix"
  LEFTHOOK=0 git push origin <branch>
  ```
- **Environment variable bypass (Windows PowerShell):**
  ```powershell
  $env:LEFTHOOK = "0"; git push origin <branch>; Remove-Item env:LEFTHOOK
  ```

> [!NOTE]
> **CI Quality Gate**: Bypassing local hooks affects only the local workstation. GitHub Actions CI operates as the authoritative quality gate on pull requests and protected branches according to repository ruleset and branch protection configuration.

### 1.6 Safe, Non-Destructive Rollback Procedures

Rollback is strictly partitioned between local developer hook deactivation and repository-wide reversal:

#### Procedure A: Local Developer Hook Deactivation (Non-Destructive)
If an individual contributor wishes to deactivate Lefthook hooks on their local checkout without affecting tracked repository files or other contributors:
```bash
# Check and preserve any existing .github/hooks/lefthook.json before uninstalling if needed:
# Lefthook uninstall removes .github/hooks/lefthook.json if present.
#
# Removes Lefthook-managed hook wrappers in .git/hooks/, cleans up .git/lefthook.checksum,
# and restores any pre-existing .old backup hooks.
# Does NOT touch core.hooksPath, tracked repository files, or user agent skills.
uv run --no-sync --offline python -m lefthook uninstall
```

#### Procedure B: Repository-Wide Reversal (After PR Merge)
If the team decides to revert the tooling milestone from `main` after merge, select the command corresponding to the GitHub merge strategy employed:

- **If merged via Merge Commit (default merge):**
  ```bash
  git revert -m 1 <merge_commit_sha>
  ```
- **If merged via Squash and Merge:**
  ```bash
  git revert <squash_commit_sha>
  ```
  *(No `-m 1` flag, as squash commits have a single parent).*
- **If merged via Rebase and Merge:**
  ```bash
  # In Git revision ranges, A..B excludes A. The caret (^) on the oldest commit
  # includes that commit in the range, ensuring every commit in the PR is reverted:
  git revert <oldest_commit_sha>^..<newest_commit_sha>
  ```

After reverting, dependency resynchronization is optional. Note that standard `uv sync --extra dev` removes unrequested extras; to preserve your existing extras selection, re-specify all active extras (e.g. `uv sync --extra dev --extra retrieval`) or supply `--inexact` (e.g. `uv sync --inexact`) to avoid pruning unrequested installed packages.

#### User-Level Agent Skills:
User-level agent skills (e.g. `~/.gemini/config/skills/property-based-testing`) reside in the user's global agent configuration, completely outside the Git repository. They must **never** be deleted during repository rollback unless the user explicitly chooses to remove that capability globally.

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

## 3. Dependency Reproducibility Assessment (`uv.lock`)

LexAgent currently manages dependencies through declarative specifications in `pyproject.toml` with version bounds and extras (`[project.optional-dependencies]`). 

- **Current State:** `uv.lock` is untracked on `main`. Clean wheel builds, CI runners, and packaging smoke tests execute standard `pip install -e .[extras]`.
- **Reproducibility Consideration:** Committing `uv.lock` would enable lockfile reproducibility for local developers using `uv sync --frozen`, but would not affect CI reproducibility unless GitHub Actions workflows are also migrated from `pip install -e .` to `uv sync --frozen` across all matrix runners.
- **Policy Decision:** We keep the existing `pyproject.toml` dependency model and do not commit `uv.lock` in this tooling PR. A dedicated dependency management RFC will be submitted for repository owner approval should the project decide to standardize on a universal lockfile workflow.

---

## 4. OpenCodeReview Protocol: Local Preprocessing with Host-Model Review

LexAgent supports a future evaluation pilot using [Alibaba OpenCodeReview](https://github.com/alibaba/open-code-review) following a **local preprocessing with host-model review** architecture.

### 4.1 Data Transmission & Network Disclosures

> [!WARNING]
> **Network Activity & Model Transmission Notice**:
> 
> 1. **Package Registry Traffic (npm/npx) & CLI Update Checks:** Invoking OpenCodeReview through package runners (such as `npx @alibaba-group/open-code-review`) causes npm to contact `registry.npmjs.org` to check for updates and resolve package manifests. While invoking with `npx --no-install` prevents npm from downloading packages from the registry, it does **not** disable background update checks, telemetry, or network calls initiated by the OpenCodeReview CLI itself upon startup. Any future evaluation must use a **pinned, standalone pre-installed binary** executed within an enforced network sandbox (or with verified disabled telemetry) if strict network isolation is required. Do not treat local execution as an absolute zero-network guarantee unless enforced by an operating system network boundary.
> 2. **Host-Model Data Transmission:** In Delegation Mode (`open-code-review-delegate`), the `ocr` CLI performs deterministic file selection, bundle chunking, and rule matching locally on your machine without contacting Alibaba servers or DashScope APIs. **However, the host coding agent (Gemini Antigravity or Codex) transmits the resulting code diff, surrounding context, and review prompt to its own host model provider** (Google / OpenAI) to perform the actual review. Delegation mode eliminates third-party tool accounts and separate API fees, but is **not** an air-gapped review.

### 4.2 Verified Historical Review Ancestry & Blind Protocol

To eliminate confirmation bias and prevent scoring against already-remediated issues, pilot evaluations must evaluate specific historical commit ranges with verified defect presence:

#### Scenario 1: Statutory Calculation Invariant & Silent Assumptions
- **Pre-Fix Revision:** Commit `bc47290` (Head of Phase 5.1 review prior to remediation).
- **Base Revision:** Commit `9fa48b8` (Base of PR #1).
- **Verified Ground-Truth Defects Present at `bc47290`:**
  1. Part-time gratuity proration returned `total_gratuity=0.0` with `eligibility_status="part_time_prorated"` instead of computing statutory proration.
  2. `tool_node()` in `nodes.py` silently defaulted nationality to non-UAE national and work pattern to full-time when facts were unknown.
- **Reviewer Evaluation Criterion:** Identify both defects from the diff and prompt context without prior knowledge of the fix commits.

#### Scenario 2: Manifest Provenance & Invariant Coverage
- **Pre-Fix Revision:** Commit `8c4a1db` (Mid-remediation state of PR #1).
- **Base Revision:** Commit `9fa48b8`.
- **Verified Ground-Truth Defects Present at `8c4a1db`:**
  1. `src/uae_lexagent/evaluation/validate.py` validated benchmark test cases, but did not inspect `data/official_sources/manifest.json` for source ID consistency, file existence, SHA-256 hashes, or unretrieved upstream PDFs.
  2. Missing end-to-end legal-safety test coverage verifying fail-closed abstention for queries requiring statutory provisions or topics outside the ingested corpus (e.g., uningested statutory articles or out-of-domain disputes). *(Note: existing unit negative tests verified malformed syntax, but end-to-end invariant abstention tests were missing).*
- **Note on Ancestry:** At commit `8c4a1db`, the part-time gratuity formula had *already been fixed* in commit `5ac1a65`. A reviewer evaluating `8c4a1db` must **not** be evaluated on gratuity calculation, but strictly on provenance inspection and corpus boundaries.

#### 4-Phase Evaluation Workflow:
1. **Blind Prompting:** Present raw Git diff between base and target commit. Strictly withhold review findings, issue descriptions, and subsequent fix commits.
2. **Independent Logging:** Host agent records all identified defects, file lines, severity, and reproduction scenarios.
3. **Unblinding & Evaluation:** Compare findings against the ground truth defects present at that specific commit.
4. **Quantitative Scoring:** Record True Positives, False Positives, False Negatives, execution duration, and token usage.
