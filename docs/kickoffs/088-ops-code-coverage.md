# 088 · ops · Code coverage report from CI

Kickoff for Devin. Start the session with: "Build docs/kickoffs/088-ops-code-coverage.md."

CI, `pyproject.toml`, the frontend's dev dependencies and Vitest config, one small script with its test, two doc lines. No application code and no behaviour changes. No spec section; `docs/CHANGES.md` entry [088]. No design. Issue: #259.

```text
[088] Code coverage report from CI. Goal: every pull request shows how
much of the backend (lines and branches) and the frontend its tests run,
as totals in the job summary and as HTML reports to download — measured
in CI only, with no minimum that fails the build yet.
Start from the latest main.

Read first:
1. AGENTS.md → Commands (CI runs the tests, not the laptop) and
   docs/README.md.
2. .github/workflows/ci.yml (jobs backend-test and frontend-check),
   the Makefile's test targets, pyproject.toml ([project.optional-
   dependencies] dev, [tool.pytest.ini_options]), frontend/package.json.
3. docs/CHANGES.md [088].

Move this kickoff to docs/kickoffs/archive/ with git mv in your PR.
Put "Closes #259" in the PR description. One PR.

1. Backend packages and config (pyproject.toml, uv.lock):
   - add pytest-cov to the dev extra; uv lock;
   - [tool.coverage.run]: branch = true, source = ["app"],
     relative_files = true;
   - [tool.coverage.report]: show_missing = true, skip_covered = true,
     exclude_also = ["if TYPE_CHECKING:", "raise NotImplementedError",
     "@overload", "\\.\\.\\."] — nothing else; no "pragma: no cover" added
     to application code in this PR.
   pytest runs from backend/ and coverage only reads a config in the
   current directory, so every call passes --cov-config=../pyproject.toml.

2. Backend in CI (job backend-test only). Extend its PYTEST_ADDOPTS:
     --junitxml=junit-backend.xml --cov=app --cov-config=../pyproject.toml
     --cov-report=xml:coverage.xml --cov-report=html:htmlcov
     --cov-report=term:skip-covered
   `make test-backend` itself does not change: without those options it
   measures nothing, so a local run costs what it costs today.
   Not measured: backend-slow and the perf tests (timing budgets; the
   tracer would skew them), and the e2e jobs.

3. Frontend (frontend/package.json, pnpm-lock.yaml, the Vitest config):
   - add @vitest/coverage-v8 at the same major version as vitest;
   - test.coverage: provider "v8", reporter ["text-summary", "html",
     "json-summary"], reportsDirectory "coverage", include
     ["src/**/*.{ts,tsx}"], exclude test files, src/main.tsx and type-only
     files; coverage stays off unless --coverage is passed;
   - Makefile: test-frontend passes $(VITEST_ARGS) through (empty by
     default); CI runs `make test-frontend VITEST_ARGS=--coverage`;
   - add frontend/coverage/ to .gitignore.

4. Summary. scripts/coverage_summary.py (standard library only):
     coverage_summary.py --backend backend/coverage.xml
                         --frontend frontend/coverage/coverage-summary.json
   prints Markdown: one table with lines % and branches % per side; for
   the backend one row per top-level package under app/ (engine, risk,
   data, …), sorted by name; then the 10 files with the lowest line
   coverage among files of 50 statements or more, each with its missed
   count. Either argument may be missing (it then prints that side as
   "no report"); it never exits non-zero for a missing file.
   Each job appends its side to $GITHUB_STEP_SUMMARY with `if: always()`,
   so a failed test run still shows what it measured.
   Unit test with two small fixture files (backend/tests/
   test_coverage_summary.py): totals, package rows, the 50-statement cut,
   a missing file.

5. Artifacts (actions/upload-artifact, the SHA already pinned in ci.yml,
   if: always(), retention-days: 7, if-no-files-found: ignore):
   - coverage-backend: backend/htmlcov/ and backend/coverage.xml;
   - coverage-frontend: frontend/coverage/.

6. Docs.
   - AGENTS.md → Commands, under the local commands, one line:
       cd backend && uv run pytest tests/test_<area>.py \
         --cov=app.<module> --cov-config=../pyproject.toml \
         --cov-report=term-missing        # coverage for one module
     and add "a full coverage run" to the "Never run locally" list.
   - README: where to find the report (PR → Checks → backend-test →
     Summary; artifacts coverage-backend and coverage-frontend).

Rules: no third-party coverage service and no new action (the summary and
the artifacts are enough); no coverage threshold, no --cov-fail-under;
no application code touched to raise a number; no network in tests.
Tests are proven in CI: open a draft PR early and read the checks; do
not run the full suite, with or without coverage, on the laptop.

Done when: all CI checks are green; the backend-test and frontend-check
summaries show the tables; both artifacts open to an index.html with
per-line detail; the PR names the four totals (backend lines, backend
branches, frontend lines, frontend branches) and the backend-test wall
time before and after (from the last green run on main). If it grew by
more than 30%, say so in the PR instead of tuning it away.
```

## Why

Marc, 12 November: "How can I generate a code coverage report?" There was none: `.gitignore` lists `.coverage` and `htmlcov/`, but no coverage package is installed and neither the Makefile nor CI has a coverage step. A coverage run is a full suite run, which item 087 keeps off the laptop, so CI makes the report.

## Assumptions

- pytest-cov combines the data of the xdist workers by itself; `make test-backend` keeps `-n auto --dist worksteal`.
- Tests that start a subprocess (12 test files import `subprocess`) are not measured inside that subprocess. The number for the code they start is therefore too low, never too high.
- Branch coverage costs more time than line coverage. The 20-minute timeout of `backend-test` is expected to hold; the PR reports the real difference.
- Coverage shows which lines ran, not which results were checked. It is a way to find untested files, not a quality score.

## Open questions for Marc

- A minimum that fails CI (for example on `app/risk` and `app/engine`)? Left out on purpose until the first numbers are known; it would be its own small item.
- Coverage of the Playwright e2e runs is left out (it needs an instrumented build). Say so if you want it.
