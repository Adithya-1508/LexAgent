# LexAgent Developer Tooling Guide

This guide documents the local engineering tooling baseline established for LexAgent, including Git hook automation with Lefthook, optional diff viewing with Delta, dependency reproducibility considerations, and the OpenCodeReview evaluation protocol (local preprocessing with host-model review).

---

## 1. Git Hook Automation: Lefthook

LexAgent uses **Lefthook v2.2.2** for fast, deterministic local pre-commit and pre-push quality gates.

### 1.1 Quality Gates Architecture

- **Configuration File:** `lefthook.yml` (located in repository root).
- **Minimum Version Enforced:** `min_version: 2.2.2` ensures that if an older global Lefthook binary is executed from system PATH, it aborts execution immediately.
- **Fail-Closed Installation Check:** `assert_lefthook_installed: true` ensures that if the Lefthook binary cannot be found across all search locations, the generated Git hook wrapper exits with status code `1` rather than silently continuing.
- **Hook Self-Management:** `no_auto_install: true` prevents background Lefthook executions from silently mutating or synchronizing Git hook wrappers.
- **Glob Matching Engine:** `glob_matcher: doublestar` ensures that `**/*.py` matches Python files recursively across all directory depths.
- **Execution Isolation:** All hook commands run via `uv run --no-sync --offline`.
  - **Provisioning vs Runtime Distinction:** Dependencies are provisioned explicitly during environment setup (`uv sync ...`). Routine Git hook executions run strictly `--no-sync --offline`, ensuring that local commits and pushes never trigger package index lookups, dependency resolution, or uncommitted lockfile modifications.

#### Quality Gate Specifications:

1. **Pre-commit (`pre-commit`)**:
   - `ruff-lint`: Runs `uv run --no-sync --offline ruff check -- {staged_files}` on staged Python files.
   - `ruff-format`: Runs `uv run --no-sync --offline ruff format --check -- {staged_files}` on staged Python files.
   - *Option Terminator (`--`):* Ruff commands include `--` immediately before `{staged_files}` to ensure that filenames starting with a hyphen (e.g. `-test.py`) are strictly parsed as path arguments rather than command-line flags.
   - *Behavior on non-Python changes:* When a commit touches only non-Python files (e.g. `.md`, `.json`, `.yml`), Lefthook detects zero matching files and skips execution cleanly in 0.00s.
2. **Pre-push (`pre-push`)**:
   - `mypy`: Runs `uv run --no-sync --offline mypy --config-file {files} src/`.
   - `legal-safety`: Runs `uv run --no-sync --offline pytest -c {files} -q -m legal_safety`.
   - *Tracked Config Binding (`files: git ls-files -- pyproject.toml`):* By binding `{files}` to `pyproject.toml`, Lefthook executes reliably across normal branch pushes and up-to-date tracking pushes where `PushFiles` is empty, as verified by simulated pre-push tests.
   - *Authoritative CI gate:* Comprehensive multi-version Python matrices (3.11/3.12), full 145+ test integration suites, Bandit static security scans, and packaging wheel installations remain in GitHub Actions.

### 1.2 Inspection Before Activation & Chaining Strategy

> [!WARNING]
> **Pre-Existing Git Hook Inspection Required**: Before running `lefthook install`, you must inspect `.git/hooks/`.
> 
> When Lefthook installs a hook wrapper, it renames any pre-existing non-Lefthook hook to `<hook-name>.old`. **Git only executes `<hook-name>`, meaning `<hook-name>.old` will NOT be executed during subsequent Git operations.**
> 
> If an active non-Lefthook hook is discovered in `.git/hooks/`, **do not proceed with installation**. Stop and obtain repository owner approval for an explicit chaining strategy (such as integrating the pre-existing check as a custom command in `lefthook.yml`).

To inspect and activate Lefthook in a clean checkout:

```bash
# 1. Inspect existing hooks
ls -la .git/hooks

# 2. Provision dependencies
uv sync --extra dev

# 3. Install Lefthook hook wrappers into .git/hooks/
uv run lefthook install
```

### 1.3 Executable Precedence & Global Version Drift Mitigation

The installed shell wrapper in `.git/hooks/` resolves the Lefthook executable in the following strict order:

1. **`$LEFTHOOK_BIN` Override:** If the `LEFTHOOK_BIN` environment variable is defined, the wrapper executes this path directly.
2. **System PATH (`lefthook.exe` / `lefthook`):** The wrapper checks for a global binary in PATH.
3. **Local Virtual Environment:** If not in PATH, it executes the project virtual environment binary (`.venv/Lib/site-packages/lefthook/bin/.../lefthook.exe` on Windows or `.venv/bin/lefthook` on POSIX).
4. **Fallback Package Runners:** Checks `uv run lefthook`, `devbox`, `mise`, etc.
5. **Fail-Closed Abort:** If no executable is found, `assert_lefthook_installed: true` ensures the wrapper prints an error and terminates with `exit 1`.

#### Mitigating Global Version Conflicts:
Because system PATH is evaluated before the local `.venv` path, a different global Lefthook binary installed on a developer's machine could execute instead of the project-pinned version:
- **`min_version: 2.2.2` Guard:** Configured in `lefthook.yml` to prevent older global binaries from running unsupported configurations.
- **Deterministic Pinning:** If a global binary is present and conflicts with project tooling, export `LEFTHOOK_BIN`:
  - *Windows PowerShell:* `$env:LEFTHOOK_BIN = "$PWD\.venv\Lib\site-packages\lefthook\bin\lefthook-windows-x86_64\lefthook.exe"`
  - *POSIX / Git Bash:* `export LEFTHOOK_BIN="$(pwd)/.venv/bin/lefthook"`

### 1.4 Manual Execution & Testing

You can manually trigger hooks at any time without committing or pushing:

```bash
# Run pre-commit checks on currently staged files
uv run --no-sync --offline lefthook run pre-commit

# Run pre-push checks
uv run --no-sync --offline lefthook run pre-push
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
# Uninstalls only Lefthook-managed hook wrappers and restores any pre-existing .old hooks.
# Does NOT touch core.hooksPath, tracked repository files, or user agent skills.
uv run lefthook uninstall
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

After reverting, re-sync your local virtual environment using your preferred selection of optional extras (e.g. `uv sync --extra dev` or the extras you routinely use), preserving your existing dependency setup and unrelated working tree changes.

#### Note on User-Level Agent Skills:
User-level agent skills (e.g. `~/.gemini/config/skills/property-based-testing`) reside in the user's global agent configuration, outside the Git repository. They should **not** be deleted during routine repository rollback unless the user explicitly chooses to remove that capability globally.

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
> 1. **Package Registry Traffic (npm/npx) & CLI Update Checks:** Invoking OpenCodeReview through package runners (such as `npx @alibaba-group/open-code-review`) causes npm to contact `registry.npmjs.org` to check for updates and resolve package manifests. While invoking with `npx --no-install` prevents npm from downloading packages from the registry, it does **not** necessarily disable background update checks, telemetry, or network calls initiated by the OpenCodeReview CLI itself upon startup. To guarantee zero outbound network requests during local preprocessing, any future pilot must use a **pinned, standalone pre-installed binary** (downloaded directly from official GitHub releases) executed in a network-isolated environment or with telemetry/update checks explicitly disabled.
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
  2. Missing deterministic negative tests for queries requiring statutory provisions outside the ingested corpus.
- **Note on Ancestry:** At commit `8c4a1db`, the part-time gratuity formula had *already been fixed* in commit `5ac1a65`. A reviewer evaluating `8c4a1db` must **not** be evaluated on gratuity calculation, but strictly on provenance inspection and corpus boundaries.

#### 4-Phase Evaluation Workflow:
1. **Blind Prompting:** Present raw Git diff between base and target commit. Strictly withhold review findings, issue descriptions, and subsequent fix commits.
2. **Independent Logging:** Host agent records all identified defects, file lines, severity, and reproduction scenarios.
3. **Unblinding & Evaluation:** Compare findings against the ground truth defects present at that specific commit.
4. **Quantitative Scoring:** Record True Positives, False Positives, False Negatives, execution duration, and token usage.
