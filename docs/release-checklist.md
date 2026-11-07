# Public Release Checklist

This checklist is intentionally separate from the application documentation so
the v0.1.0 publication can be reviewed without changing product behavior.

## Required before publication

- Confirm that the MIT license in LICENSE remains the intended license.
- Review the complete tracked-file set for secrets, local credentials, private
  invoice data, and personal paths. Keep .env ignored and publish only
  .env.example placeholders.
- Run the local quality gates in the root README and record the final counts.
- The first GitHub Actions run exposed a backend dataset-bootstrap and
  cross-platform storage-key failure; GitHub-hosted CI Run #2 confirmed the
  Milestone 8E.2 fix.
- The current release-candidate commit must pass all six hosted CI jobs,
  including PostgreSQL integration and clean-database migration verification.
- GitHub Actions references are pinned to verified full commit SHAs with
  readable release comments.

## Repository presentation

Add only badges with a real destination:

1. GitHub Actions CI status for the repository's actual workflow path.
2. MIT License badge linked to LICENSE.

Do not add a coverage vanity badge, release badge, or deployment badge before
those artifacts exist.

## Release claims to preserve

Describe local test evidence as local evidence. Do not claim production
certification, exactly-once n8n delivery, remote CI success before the follow-up
run, or AI extraction accuracy that has not been measured against a governed
dataset.
