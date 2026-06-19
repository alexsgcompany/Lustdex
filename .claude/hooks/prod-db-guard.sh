#!/usr/bin/env bash
# PreToolUse(Bash): when a command targets the PRODUCTION database, surface the
# prod-db-ops skill. NON-BLOCKING — it injects a reminder, never denies the command.
# Markers: prod host / ssh port / prod pg container / prod tunnel port.

input=$(cat)

# Fire only when an ACCESS VERB sits next to a prod marker — so a command that
# merely mentions a prod term in text (e.g. a git commit message) does not trip it.
if printf '%s' "$input" | grep -qE 'ssh[^"]*(21488|158\.69\.112\.39|sasha@)' \
   || printf '%s' "$input" | grep -qE 'docker exec[^"]*lustdex-db' \
   || printf '%s' "$input" | grep -qE 'DATABASE_URL=[^ ]*127\.0\.0\.1:5433'; then
  # PreToolUse additionalContext: reaches the model without blocking the tool.
  cat <<'JSON'
{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"PROD DB command detected — follow the prod-db-ops skill (.claude/skills/prod-db-ops/SKILL.md). Key reminders: prod pg container is lustdex-db (catalog-db is LOCAL); ssh user sasha; db/user catalog. Prod is NOT a copy of local (smaller, different catalogue) — never ship local rows referencing local video_ids. DERIVED data: rebuild ON prod; AUTHORED/LLM data: pg_dump-deploy from local. First load into an empty table = direct INSERT; refresh of a live table = shadow+swap, never TRUNCATE a live table. ANALYZE after bulk load. Verify rollups with the FULL projection rule, not a naive vertical filter. Confirm destructive ops with the user."}}
JSON
fi

exit 0
