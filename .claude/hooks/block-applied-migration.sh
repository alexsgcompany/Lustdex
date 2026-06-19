#!/usr/bin/env bash
# PreToolUse hook: deny edits to applied (git-committed) migrations.
# FOUNDATION #4 — migrations are append-only; never edit an applied one.
# "Applied" is approximated by "tracked in git": a committed migration has
# been run on at least one DB and is immutable. A brand-new, uncommitted
# migration file is still editable.

input=$(cat)

# Extract file_path from the tool input JSON (compact, single-line).
file_path=$(printf '%s' "$input" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

case "$file_path" in
  */db/migrations/*.sql)
    if git ls-files --error-unmatch "$file_path" >/dev/null 2>&1; then
      echo "BLOCKED: $file_path is a committed (applied) migration." >&2
      echo "FOUNDATION #4: migrations are append-only — never edit an applied one." >&2
      echo "Create a NEW numbered migration instead." >&2
      exit 2
    fi
    ;;
esac

exit 0
