<!-- ZenWiFi Monitor version: 0.1.0 -->

# Remediation plan for the fourth review round

Proposed responses to the findings in `01_docs/REVIEW_178adf4.md`, agreed with
the project owner. This document is the design; the code is the record of what
was actually done, and where the two disagree the code is right and this file is
stale.

Ordering is by dependency, not by severity. F15 sits second because several of
its uncaught mutations would hide a regression in the fixes that follow it.

## F7 and F8 — what makes a restart legitimate

The owner proposed three or more failures within thirty minutes, which is six
probe intervals. That is the right instinct and it closes the staleness
directly: after a nine-hour gap there is exactly one observation in the window,
so nothing fires.

On its own, though, it would fire after ten minutes — failures at T, T+5 and
T+10 — which is *sooner* than the fifteen minutes the project promises in its
event text and in its notice. Fixing the staleness by quietly weakening the
stated guarantee is not a fix. The proposal is therefore two conditions, both
required:

**Duration.** The most recent successful run is at least
`failure_minutes_before_reboot` old. This preserves the documented promise and
is what breaks on flapping: a single successful run in between moves the
reference point forward, which is correct, because flapping is not an outage.

**Evidence.** At least `required_failed_runs` failed runs fall within the last
`failure_window_minutes`. This is what kills the gap: a machine that was asleep
contributes no observations, so the count cannot be met by history alone.

Both are computed from the `runs` table, which already records every run with
its connectivity result before the decision is taken. No new state is needed,
and `first_failure_utc` stops being a decision input — it survives only to
decide whether the start of an outage has already been announced. That is what
makes **F8 dissolve rather than need its own fix**: a marker left behind by a
partial failure can then cause at most a duplicate log line, never a restart.

Two defaults in `src/_defaults.py`, both configurable and both validated with a
floor: a thirty-minute window and three failures. `required_failed_runs` is
floored at two, so no configuration can restore the single-observation
behaviour by accident.

The event detail and the notice should state what was observed — how long, over
how many checks — rather than the constant "15 minutes" they assert today. That
also closes one of the F17 items.

## F15 — tests that assert their own fixture

The dominant defect is that the offline tests seed `first_failure_utc` by hand
and then assert on the transition that key drives, so they confirm the fixture
rather than the code. Driving `main()` twice — once to establish the state, once
to consume it — with the probes and the restart substituted closes four uncaught
mutations at once.

Beyond that: the cooldown test probes at zero and forty-five minutes, which fall
the same side of both the cooldown and the failure window, so it cannot tell
them apart; a mid-point is needed. `probe` and `internet_available` have no
coverage at all, and `any` becoming `all` is not caught, which would make one
CDN's bad minute an outage. The installer's activation gate is checked by
scanning the script's text rather than by running it against a temporary prefix.
The two `test_health.py` substring scans should be replaced by something
structural: a health monitor that spawns the watchdog with `--execute` currently
passes the check named "the health monitor cannot restart the router", because
it does not contain the word "reboot".

## F10 — the health monitor's own write failures

A reordering, not a redesign. The health row, the closing state and the
notification stamp are persisted while `state` still says healthy, and only then
is it flipped. The stored memory therefore disagrees with the run, so every
subsequent run sees a fresh transition and escalates again, bypassing the notice
cooldown entirely.

Compute the findings, decide the state, and only then persist. The write-failure
conditions belong in `codes` before the persistence block so they travel through
the same cooldown as every other condition. The self-contradictory detail —
"All local health checks passed. The health log could not be written." — and the
severity that stays at zero while the state is unhealthy both fall out of the
same change.

## F9 — an outbox that grows without bound

The tempting fix is to pass the full delivery gate into `purge_events`. That
recreates F1 mirrored: a manual dry-run against a production install would then
purge events the scheduled run was going to deliver.

Instead, make the retention window an **absolute** bound on how long any event
may sit, whatever the reason it has not been delivered, and log one aggregated
event when events are dropped undelivered so the loss is visible rather than
silent. This has no dependency on either gate and bounds growth in every
configuration.

Separately, the health monitor should distinguish a queue waiting because
delivery is not currently possible from one waiting although it is. Only the
second is a fault an operator can act on.

## F12 — the one error that leaves the machine

`sanitize_error` exists and is applied only to a field that is never
transmitted. The restart-failure message, which is both persisted and delivered
to Notion, is the raw exception from a call constructed with the router
credentials.

Apply the sanitizer there. While doing so, make it redact the actual secret
values, which the run has already fetched, rather than only a `Bearer` prefix: a
router password has no recognisable shape, so pattern matching cannot find it.

## F11 — the bootstrap log on Debian

Derive the path from `$LOGS_DIRECTORY` when systemd sets it, falling back to
`%LOCALAPPDATA%` and then the home directory. Two things belong with it: the
installer should verify the location is writable and stop if it is not, and the
top-level handler must not let a failing `append_log` throw over the original
exception, which is what turns this from inconvenient into invisible. The
Debian guide's troubleshooting section needs to name the path that will exist.

## F13 and F14 — the Debian install surface

`cp -a` preserves the ownership of a user-owned clone, so the installed code
ends up writable by an ordinary account while systemd runs it with the router
credentials. Use `install -m 0644` per file, or chown and strip group and other
write bits afterwards, and assert root ownership in the suite.

The credential directory is accepted on the sole test that it is a directory.
Require it to be owned by the run's own uid with mode 0700, and preferably to
live under `/run/credentials/`. The branch is only reached when systemd set the
variable, so the check costs nothing elsewhere.

## Sequence

1. F7 and F8 together, as one rewrite of the restart decision.
2. F15, so the fixes that follow cannot regress invisibly.
3. F10, F9, F12 — each small and self-contained.
4. F11, F13, F14 — all on the Debian path, verifiable in one pass.

F16 and F17 are documentation and small hardening, best done last so they
describe the code as it ends up rather than as it was proposed.

<!-- END-OF-FILE: REMEDIATION.md -->
