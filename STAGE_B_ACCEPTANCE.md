# Stage B acceptance record

Date: 2026-10-19. Target: corporate Windows PC. Input package SHA-256 is recorded below; no work invitation content or identifiers are included.

## Frozen inputs and current status

- Stage A base revision: `03af38e7f62aef84a2545c91e29c7940a310d02e`.
- `SPECIFICATION.md` SHA-256: `c4e871df8c506ca2a51aa5e06aafe9aea9a627acd21d43254123a9aca3d19b6b`.
- `HANDOFF.md` SHA-256: `466694a1feea6be42447b92a0ca8c0f16baeae04b88bc260490d0e306aa77d47`.
- Operator reported that corporate policy and calendar use were approved verbally with managers. Written scope/reference is not available here.
- `stage_b_accepted=false`; Outlook COM remains **UNVERIFIED** as an end-to-end adapter.
- Synthetic-only acceptance data was used for writes; no real invitation content or identifiers were copied into the project. A narrow cleanup removed only five previously marked sync copies and cleared their five matching state links; unmarked events were left untouched.

## Checks performed

| Check | Result |
| --- | --- |
| Python | 3.13.14 available |
| `python -m calendar_sync diagnose --config config.example.json` | Exit 0; fake mode, no real connections, COM UNVERIFIED |
| Outlook COM default folder | Accessible in the interactive user session; 426 folder items at the time of the check |
| `python tools/inspect_outlook_readonly.py` | Exit 0; 44 appointments in the rolling 30-day window; 41 recurring instances; 44 with readable UTC fields and GlobalAppointmentID; no titles, Body, or IDs emitted |
| `.venv\\Scripts\\python.exe -m pip check` | Exit 0; no broken requirements after installation through corporate Artifactory `python-all` |
| `.venv\\Scripts\\python.exe -m unittest discover -s tests -q` | 35 run, OK |
| Real adapter aggregate read | Exit 0; 44 appointments, 41 recurring instances, all 44 with readable UTC fields and GlobalAppointmentID |
| Google adapter read | Exit 0 after local OAuth authorization; own and agreed shared calendars were read without emitting event IDs |

## Synthetic-only acceptance evidence

- Outlook to Google: five synthetic source events, including an all-day event and a moved/deleted series instance. Three consecutive apply runs remained idempotent after the initial repair; subsequent runs reported five skips and zero creates, updates, or deletes.
- Google own calendar to Outlook: six synthetic source instances covering timed, all-day, DST, and a series with a moved and cancelled instance. Three consecutive apply runs reported six skips and zero creates, updates, or deletes after reconciliation.
- Google shared calendar to Outlook: one manually created synthetic source event from the agreed shared calendar. It was discovered locally and reconciled; subsequent apply runs reported one skip and zero writes.
- The acceptance helper passes only locally discovered synthetic IDs via repeated `--test-id`; no event ID is recorded in this document.
- Google copies preserve Body and use default visibility, busy transparency, and disabled reminders. Outlook copies use the specified private/busy/no-reminder policy and do not create recipients or attachments.
- Incoming Outlook meetings are eligible only when `MeetingStatus` identifies a received meeting and `ResponseStatus` is Accepted. Ordinary appointments and meetings owned by the user remain eligible; unaccepted incoming meetings are skipped. This rule has anonymized unit coverage.
- A narrow marked-copy reset and state-link reset was completed before the corrected import. No unmarked Google event was deleted.
- SQLite-loss test: `local/state.sqlite3` was temporarily moved aside, a synthetic-only apply completed with exit 0, no duplicate copies were created, and the original state file was restored afterwards.

## Remaining ordered acceptance work

1. Set up a Google Calendar API OAuth desktop client and local authorized-user token on the target PC; configure the read-only shared calendar ID. Keep all credentials outside Git.
2. Repeat read-only real-adapter checks, including Outlook locale, recurrence exceptions/cancellations, All-day/DST, and Google pagination/inventory. Verify incomplete reads fail closed.
3. Manually create agreed synthetic events only. Discover their normalized source IDs locally and pass them to `dry-run --test-id` and `apply --test-id`.
4. Prove both directions and Google sources, Body, Busy/privacy/reminders, series/exceptions, cancellations, moves, All-day/DST, SQLite loss, ambiguous create, manual copy deletion, and three unchanged apply runs with zero writes. Add sanitized regressions for any observed defects.
5. Repeat a full dry-run, inspect all planned deletions, and obtain separate explicit operator confirmation before full initial fill. Schedule only after full acceptance.

## Remaining ordered gates

- The real synthetic path still needs the explicit crash-after-create receipt and a final read-only full dry-run with deletion review.
- COM is therefore still **UNVERIFIED** and `stage_b_accepted=false`.
- Full population and scheduling remain disabled and require separate explicit operator confirmation after the complete dry-run.
