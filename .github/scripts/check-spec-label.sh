#!/usr/bin/env bash
# Spec label and changelog gate — runs from .github/workflows/spec-label.yml.
#
# Inputs (env vars set by the workflow):
#   BASE_SHA   merge-base side of the PR diff
#   HEAD_SHA   PR head
#   HAS_LABEL  "true" when the PR carries the "spec change" label
#
# Outputs:
#   step summary  — which spec-owned files the diff touches
#   ::error::     — GitHub annotation per violated rule (fails the job)
#   ::warning::   — annotation only; never fails the job
#   exit 0/1
#
# Rules enforced, derived from AGENTS.md and CLAUDE.md:
#   1. A diff touching a spec-owned path (patterns in
#      .github/spec-paths.txt) needs the "spec change" label.
#   2. The label on a PR that touches nothing spec-owned is stale —
#      warning, not an error: it doesn't block, but should be removed.
#   3. Changes to SPEC.md, the agent rules, prompts, config examples or
#      .env.example need an entry in docs/CHANGES.md; changes to design
#      files need one in design/CHANGES.md. Other spec-owned files
#      (README, kickoff, analysis, this gate itself) need the label only.
set -euo pipefail

# Missing pattern file must fail loudly — an empty pattern list would
# let every spec change through unlabelled (AGENTS.md rule 15).
test -f .github/spec-paths.txt || { echo "::error::.github/spec-paths.txt not found"; exit 1; }

mapfile -t patterns < <(grep -vE '^[[:space:]]*(#|$)' .github/spec-paths.txt)
# Three dots: changes introduced on the PR branch since the merge base.
# --no-renames reports a rename as delete + add, so a renamed spec file
# still trips the gate.
mapfile -t changed < <(git diff --name-only --no-renames "$BASE_SHA...$HEAD_SHA")

# Glob match a path against patterns. [[ == ]] pattern semantics let a
# single '*' cross '/', which is why 'design/*' covers design/screens/*.
matches() {  # file, then patterns
  local f=$1; shift
  for p in "$@"; do [[ $f == $p ]] && return 0; done
  return 1
}
# Exact (not glob) membership in the changed-file list — used for the
# changelog files themselves.
changed_has() { printf '%s\n' "${changed[@]}" | grep -qxF "$1"; }

# Spec-owned files this PR actually touches.
spec_files=()
for f in "${changed[@]}"; do
  if matches "$f" "${patterns[@]}"; then spec_files+=("$f"); fi
done

# Job summary — the verdict visible on the run page without opening logs.
{
  echo "## Spec label check"
  if ((${#spec_files[@]})); then
    echo "Spec files changed:"
    printf -- '- `%s`\n' "${spec_files[@]}"
  else
    echo "No spec files changed."
  fi
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"

# Rules 1 and 2: the label gate.
errors=0
if ((${#spec_files[@]})) && [[ $HAS_LABEL != true ]]; then
  echo "::error::This PR changes spec files but has no 'spec change' label: ${spec_files[*]}"
  errors=1
fi
if ((${#spec_files[@]} == 0)) && [[ $HAS_LABEL == true ]]; then
  echo "::warning::The 'spec change' label is set but no spec file changed. Remove the label."
fi

# Rule 3: the changelog gate. These are the spec-owned files whose change
# is a behaviour or design change and therefore needs a dated entry.
# Spec-owned files not listed here (docs/README.md, DEVIN_KICKOFF.md,
# analysis, CLAUDE.md, the gate's own files) need the label only.
needs_spec_log=(docs/SPEC.md AGENTS.md 'backend/prompts/*.md' backend/config.example.yaml .env.example)
needs_design_log=('design/screens/*' design/DESIGN.md design/tokens.css)
for f in "${spec_files[@]}"; do
  if matches "$f" "${needs_spec_log[@]}" && ! changed_has docs/CHANGES.md; then
    echo "::error file=$f::Spec change without an entry in docs/CHANGES.md"
    errors=1
  fi
  if matches "$f" "${needs_design_log[@]}" && ! changed_has design/CHANGES.md; then
    echo "::error file=$f::Design change without an entry in design/CHANGES.md"
    errors=1
  fi
done

exit "$errors"
