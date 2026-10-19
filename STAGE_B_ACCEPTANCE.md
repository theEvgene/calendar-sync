# Stage B acceptance record

Date: 2026-10-19. Target: corporate Windows PC. Input package SHA-256 is recorded below; no work invitation content or identifiers are included.

## Frozen inputs and current status

- Stage A base revision: `03af38e7f62aef84a2545c91e29c7940a310d02e`.
- `SPECIFICATION.md` SHA-256: `c4e871df8c506ca2a51aa5e06aafe9aea9a627acd21d43254123a9aca3d19b6b`.
- `HANDOFF.md` SHA-256: `466694a1feea6be42447b92a0ca8c0f16baeae04b88bc260490d0e306aa77d47`.
- Operator reported that corporate policy and calendar use were approved verbally with managers. Written scope/reference is not available here.
- `stage_b_accepted=false`; Outlook COM remains **UNVERIFIED** as an end-to-end adapter.
- No calendar event was created, changed, or deleted by this stage B run. No Google OAuth token or working invitation data was copied into the project.

## Checks performed

| Check | Result |
| --- | --- |
| Python | 3.13.14 available |
| `python -m calendar_sync diagnose --config config.example.json` | Exit 0; fake mode, no real connections, COM UNVERIFIED |
| Outlook COM default folder | Accessible in the interactive user session; 426 folder items at the time of the check |
| `python tools/inspect_outlook_readonly.py` | Exit 0; 44 appointments in the rolling 30-day window; 41 recurring instances; 44 with readable UTC fields and GlobalAppointmentID; no titles, Body, or IDs emitted |
| `python -m unittest discover -s tests -q` | 31 run, 6 errors, all due to missing `tzdata`; local scratch path was set under worktree |
| Real adapter read | Blocked before event read: `ZoneInfoNotFoundError` for `Europe/Moscow` |
| Google adapter read | Not run: `google-api-python-client` and local OAuth credentials absent |

## Remaining ordered acceptance work

1. Obtain an approved Python package source and install the exact pinned dependencies under the target-PC project only, following the corporate two-confirmation rule for software installation.
2. Set up a Google Calendar API OAuth desktop client and local authorized-user token on the target PC; configure the read-only shared calendar ID. Keep all credentials outside Git.
3. Repeat read-only real-adapter checks, including Outlook locale, recurrence exceptions/cancellations, All-day/DST, and Google pagination/inventory. Verify incomplete reads fail closed.
4. Manually create agreed synthetic events only. Discover their normalized source IDs locally and pass them to `dry-run --test-id` and `apply --test-id`.
5. Prove both directions and Google sources, Body, Busy/privacy/reminders, series/exceptions, cancellations, moves, All-day/DST, SQLite loss, ambiguous create, manual copy deletion, and three unchanged apply runs with zero writes. Add sanitized regressions for any observed defects.
6. Repeat a full dry-run, inspect all planned deletions, and obtain separate explicit operator confirmation before full initial fill. Schedule only after full acceptance.

No full dry-run, real apply, initial fill, or scheduler enablement has occurred.
