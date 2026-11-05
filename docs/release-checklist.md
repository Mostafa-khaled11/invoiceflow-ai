# Public Release Checklist

This checklist is intentionally separate from the application documentation so
the first public push can be reviewed without changing product behavior.

## Required before first push

- The MIT license is now present in LICENSE. Confirm that this remains the
  intended license before the first public push.
- Review the complete tracked-file set for secrets, local credentials, private
  invoice data, and personal paths. Keep .env ignored and publish only
  .env.example placeholders.
- Run the local quality gates in the root README and record the final counts.
- The first GitHub Actions run exposed a backend dataset-bootstrap and
  cross-platform storage-key failure. A subsequent hosted run must confirm the
  Milestone 8E.2 fix before release publication.
- Consider pinning GitHub Actions to verified commit SHAs before treating CI as
  hardened public infrastructure; current references use maintained major tags.

## Recommended badges after the repository URL exists

Add only badges with a real destination:

1. GitHub Actions CI status for the repository's actual workflow path.
2. Python and Node/React stack badges only if they help a reader understand
   supported versions.
3. A license badge only after a license is deliberately selected.

Do not add a repository-owner/name placeholder, coverage vanity badge, release
badge, or deployment badge before those artifacts exist.

## Release claims to preserve

Describe local test evidence as local evidence. Do not claim production
certification, exactly-once n8n delivery, remote CI success before the follow-up
run, or AI extraction accuracy that has not been measured against a governed
dataset.
