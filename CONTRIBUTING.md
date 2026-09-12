# Contributing to Korra

Korra is a personal AI agent: one agent core behind a CLI, a TUI, a web
dashboard, a desktop app and messaging gateways. This guide covers the
practical part of contributing: setting up a dev environment, running the
test suite, writing commits, and how pull requests are handled. Architecture
and coding rules live in [AGENTS.md](AGENTS.md); read it before touching core.

---

## Development setup

### Prerequisites

- Python 3.11–3.13 (see `requires-python` in `pyproject.toml`)
- [uv](https://docs.astral.sh/uv/) for dependency management
- Node.js 20+ only if you work on the TUI (`ui-tui/`), the web dashboard
  (`web/`) or the desktop app (`apps/desktop/`)

### Install

```bash
git clone <this repository> korra
cd korra

# Reproducible dev environment from the lock file, with test/lint extras.
uv sync --frozen --extra dev

# Configure. `.env` is git-ignored and is the only place for API keys.
cp .env.example .env
$EDITOR .env
```

`uv sync` creates `.venv/` in the checkout. Activate it or prefix commands
with `uv run`:

```bash
uv run korra --help
uv run korra chat --query 'Reply exactly ok'
```

`korra` is the canonical command name. The legacy `hermes` entry point maps to
the same `main` and is kept only for already-installed wrappers and units.

### Run tests

```bash
scripts/run_tests.sh                     # full suite, matches CI
scripts/run_tests.sh tests/agent/        # one directory
scripts/run_tests.sh tests/foo.py -k x   # single file, bare pytest flags pass through
```

`scripts/run_tests.sh` is the canonical test entry point: it runs every test
file in its own subprocess with a hermetic environment (`TZ=UTC`,
`PYTHONHASHSEED=0`, env vars blanked) and a temporary `HERMES_HOME`. A raw
`pytest` call is not a substitute: it shares module state between files and
can pass locally while CI fails.

Tests must not touch `~/.hermes/` or real credentials. Use the autouse
fixture in `tests/conftest.py` (it already redirects `HERMES_HOME`) and
`tmp_path`; see "Testing" in [AGENTS.md](AGENTS.md).

Lint before pushing:

```bash
uv run ruff check .
```

---

## Commits

- One logical change per commit. Keep refactors and behaviour changes apart.
- Message format is Conventional Commits with a scope, and the task ID at the
  end of the subject when the change belongs to a tracked task:

  ```
  feat(multiplex): agent tabs work on any contour right after install (K21-058)
  fix(image_gen): keep the finished image when the output list is empty (K21-056)
  test(web): answer the Google status call the connections page now makes
  ```

  Types in use: `feat`, `fix`, `test`, `docs`, `chore`, `refactor`, `perf`.
- The subject says what the user or operator gets, not which file changed.
- Do not commit generated artifacts (`web_dist/`, `node_modules/`, caches).

---

## Pull requests

- Open the PR against `main`. Fill in the template in
  `.github/PULL_REQUEST_TEMPLATE.md`: what changed, why, how it was verified.
- CI runs the Python test suite, Python lints, JS/TS checks and installer
  tests (`.github/workflows/ci.yaml`). **Pull requests
  from forks do not run CI automatically**: a maintainer reviews the diff and
  approves the workflow run manually. Expect that step before any review
  comment appears.
- A green CI is required, but not sufficient. Reviewers check that the change
  keeps prompt caching valid, does not hardcode `~/.hermes` paths (use
  `get_hermes_home()`), and works on Linux, macOS and Windows where the code
  path is platform-sensitive. These rules are spelled out in
  [AGENTS.md](AGENTS.md).
- Bug fixes come with a regression test that fails before the fix.
- New capabilities are usually **skills**, not tools; third-party product
  integrations and memory providers ship as standalone plugins, not in this
  tree. See "Plugins" in [AGENTS.md](AGENTS.md) before adding a directory.

---

## Secrets and security

- **Keys and tokens never enter the repository.** API keys live only in the
  git-ignored `.env`; `auth.json`, session databases and anything under a real
  `HERMES_HOME` stay out of commits, fixtures and screenshots.
- Do not paste real tokens into tests or docs, even as "examples". Use
  obviously fake placeholders.
- Security-sensitive changes (shell execution, path handling, gateway auth,
  approval flows) need an explicit note in the PR describing the threat model
  you considered. For reporting vulnerabilities see [SECURITY.md](SECURITY.md).

---

## Where to look next

- [AGENTS.md](AGENTS.md) — architecture, tool and skill conventions, profiles,
  cross-platform rules, testing rules.
- `docs/` — design notes, ADRs and operational documents.
- `docs/client-deploy/` — how a production contour is actually deployed.
