#!/usr/bin/env bash
#
# Validate the branch name, and warn about the pull request title before the
# push rather than after CI has already rejected it.
#
# A pull request title cannot be checked at commit time -- it does not exist
# yet. What can be checked is the thing GitHub derives it from: when a branch
# has more than one commit, the default title is the humanised branch name
# ("fix/gitleaks-scan-scope" -> "Fix/gitleaks scan scope"), which drops the
# colon and capitalises the type, so it can never satisfy cz check. Catching
# that here turns a red validate-pr-title run into a note before pushing.

set -euo pipefail

# Kept in step with the commitizen pattern in pyproject.toml.
TYPES="build|bump|chore|ci|docs|feat|fix|perf|refactor|revert|style|test"

branch="$(git rev-parse --abbrev-ref HEAD)"

case "$branch" in
  main | master | HEAD) exit 0 ;;
esac

if ! printf '%s' "$branch" | grep -Eq "^(${TYPES})/[a-z0-9][a-z0-9._-]*$"; then
  cat >&2 <<MSG

  Branch name "$branch" does not match <type>/<slug>.

  <type> must be one of: ${TYPES//|/, }
  <slug> is lowercase, digits, dot, underscore or hyphen.

  The type has to match the Conventional Commits type of the work, because
  it is what the pull request title and the version bump are derived from.

  Rename it with:

      git branch -m ${branch} fix/some-slug

MSG
  exit 1
fi

# Resolve the base branch that a pull request would target.
base=""
for candidate in origin/main origin/master main master; do
  if git rev-parse --verify --quiet "$candidate" >/dev/null; then
    base="$candidate"
    break
  fi
done

[ -n "$base" ] || exit 0

ahead="$(git rev-list --count "${base}..HEAD" 2>/dev/null || echo 0)"

# One commit: GitHub uses that commit's subject, which the commit-msg hook has
# already validated. More than one: it falls back to the branch name.
if [ "$ahead" -gt 1 ]; then
  type="${branch%%/*}"
  slug="${branch#*/}"
  cat <<MSG

  Note: this branch has ${ahead} commits, so GitHub will default the pull
  request title to the branch name -- which fails validate-pr-title.

  Set the title explicitly when opening the pull request. For example:

      ${type}: ${slug//-/ }

MSG
fi

exit 0
