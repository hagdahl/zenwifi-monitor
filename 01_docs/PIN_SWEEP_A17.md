<!-- ZenWiFi Monitor version: 0.1.0 -->

# A-17: what the suites actually catch

ADR-026 says a pin is not accepted until the behaviour has been reverted and
the test observed to fail. That rule was written on 28 July and has been applied
to every change since. It had never been applied to anything written before it.

The sixth review round showed what that was worth: the reviewer changed one
query to the form it believed correct and all nine suites stayed green. The rule
was in the decision log while the code it was meant to protect was unprotected.

This is the sweep. Sixty-six reversions, each applied to a pristine copy of the
tree, with all nine suites run against it and the first failure recorded. Not a
sample: every safety-relevant behaviour the project claims, taken from the
modules rather than from the tests, so that a behaviour nobody had thought to
test would show up as a gap rather than be absent from the list.

**Result: sixty-four of sixty-six reversions are caught. Five of those pins did
not exist before this sweep.** The two that survive are explained below, and
neither is a missing pin.

## What the sweep found

Five behaviours were unprotected. Each now has a test, and each test was
observed to fail against the reversion that motivated it.

**The router's identity was never checked to be checked.** `check_router_identity`
had thorough coverage as a function — trust on first use, a changed fingerprint,
an unreachable endpoint — and nothing asserted it was on the restart path.
Deleting its only call site left every suite green. That is a pin on a function
rather than on a behaviour, and it is the same shape as the defect the sixth
round found: the part everyone looks at was tested, the join was not. The suite
now drives a real restart with both the identity check and the reboot recorded,
and asserts the order.

**A secret could have come from the environment.** `src/_secrets.py` opens by
saying a credential lives in the operating system's protected store "not in a
file, not in an environment variable, not in the repository, not on a command
line". Adding an `os.environ` fallback to `get_secret` left all nine suites
green. The systemd path was covered and the keyring path — the one such a
fallback would sit on — was not.

**A changed bootstrap error log was never reported.** `check_bootstrap_log`
writes its stamp and compares it, and removing the comparison changed nothing
any suite could see. The stamp was being recorded and never read. This is the
check at the centre of A-10, where a week was spent establishing that it was
working correctly; nothing in the suite would have noticed if it had not been.

**The run that announces an outage could also decide on it.** Deleting the early
return let the announcing run fall through to the restart decision. The failure
count refuses it anyway, so this is defence in depth rather than the last line —
but a guard nothing checks is a guard that gets tidied away.

**Two concurrent runs could both take the lease.** Every lease case took the
lease from one process at a time, which is not the situation the lease exists
for. The suite now runs two real connections against one database with a
rendezvous placed where two runs genuinely interleave, and asserts that exactly
one is granted.

## What survives, and why neither is a gap

**M-18, `BEGIN IMMEDIATE` reduced to `BEGIN`.** No suite catches it, and the
sweep could not construct a case where it changes the observable outcome.
SQLite refuses a deferred read-to-write upgrade outright instead of honouring
`busy_timeout`, so the losing run still declines; the new concurrency case
passes either way, which was verified by running it against both forms. Writing
a test that "caught" this would mean asserting the source text contains the
word, which is the class of check this project has been caught by three times.
The line stays, because the equivalence is a property of the current pragmas
rather than of the function, and the code now says so at the point it matters.

**M-51, the launcher allowlist.** It survives on Linux because
`04_tests/test_wrapper.py` records its launcher checks as skipped there — the
launcher is a Windows Script Host file. Run on Windows, the reversion is caught
immediately, as is forwarding `--execute` from any argument. Worth stating
plainly: a green `test_wrapper.py` on Linux is not evidence about the launcher,
and the suite says so in its own output. CI runs both platforms, so the gap is
in local runs rather than in the pipeline.

## Two reversions that proved to be no change at all

Kept in the record because a mutation that changes nothing is a finding about
the code, not a hole in the sweep.

**A boolean accepted as `required_failed_runs`.** Removing the `isinstance(bool)`
guard changes nothing, because `True` equals one and one is already below the
floor of two. The guard is still right for `failure_window_minutes`, where the
floor is one and `True` would pass — that one is pinned, as M-56.

**Forcing `delivery_configured` true in `purge_events`.** Nothing consults it.
The parameter is genuinely unused, and so is `max_attempts`, and both look like
dead code. They are kept because the outbox suite passes the flag both ways and
asserts the same outcome, which is how "no event outlives the window, whatever
the reason it is still here" is stated. `src/watchdog.py` now says that where a
reader will meet it, rather than leaving the next reviewer to report it.

## The full table

Every row was executed. "Caught by" is the first suite to fail, in the fixed
order watchdog, health, outbox, platform, configure, deploy, wrapper,
dependencies, versions.

### activation gate

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-01 | either gate alone opens execution | `test_watchdog` | AssertionError |
| M-02 | the invocation gate is ignored; configuration alone decides | `test_watchdog` | AssertionError |
| M-03 | the configuration gate is ignored; --execute alone decides | `test_watchdog` | AssertionError |
| M-04 | every run is an execute run | `test_watchdog` | AssertionError |
| M-45 | a default install opens gate one | `test_deploy` | a default install writes no execution drop-in at all |
| M-46 | --disable-execution leaves the drop-in in place | `test_deploy` | --disable-execution removes the drop-in |
| M-47 | the shipped unit carries --execute | `test_deploy` | the shipped watchdog unit does not carry --execute |

### bootstrap log

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-40 | the systemd log directory is no longer preferred | `test_outbox` | systemd's logs directory wins over every other candidate |

### configuration validation

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-22 | execution_mode is no longer constrained to the two known values | `test_watchdog` | validate_config accepted an invalid configuration |
| M-23 | one observed failure may again be enough to restart a router | `test_watchdog` | validate_config accepted an invalid configuration |
| M-25 | plain HTTP no longer needs an explicit acknowledgement | `test_watchdog` | validate_config accepted an invalid configuration |
| M-56 | a boolean is accepted as the failure window, meaning one minute | `test_watchdog` | validate_config accepted an invalid configuration |
| M-57 | the cooldown may be shorter than the outage threshold | `test_watchdog` | validate_config accepted an invalid configuration |
| M-59 | a threshold below one probe interval is accepted | `test_watchdog` | validate_config accepted an invalid configuration |

### credential hygiene

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-26 | an authorization header survives into the persisted error | `test_outbox` | no token is persisted in the error detail |

### debian hardening

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-48 | the state directory mode is unset again | `test_deploy` | the watchdog unit sets StateDirectoryMode=0750 |
| M-49 | the unit's umask is removed | `test_deploy` | the watchdog unit creates files private to the service account |
| M-50 | the health unit's log directory mode is unset | `test_deploy` | the health unit sets LogsDirectoryMode=0750 |

### debian install

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-41 | installed code is group and world writable | `test_deploy` | /opt/zenwifi-monitor/src/watchdog.py is not writable by group or other |
| M-42b | installed code preserves the checkout's ownership (the original F13) | `test_deploy` | /opt/zenwifi-monitor/src/watchdog.py is owned by root |
| M-43 | a module deleted upstream survives an upgrade | `test_deploy` | the installed modules are exactly the project's modules |
| M-44 | the dependency set is installed without hash verification | `test_deploy` | and every dependency install requires hashes |

### delivery gate

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-14 | Notion delivery no longer requires the configured execute mode | `test_outbox` | delivery readiness for mode=dry-run notion=True |
| M-15 | a dry-run invocation drains the outbox | `test_watchdog` | dry-run must not deliver, delivered ['Restored'] |

### dependency review

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-53 | a known vulnerability no longer outranks a clean result | `test_dependencies` | a known vulnerability outranks everything else |
| M-54 | an unreachable service is reported as a clean result | `test_dependencies` | a question that could not be answered outranks an available upgrade |

### health monitor

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-34 | the health monitor gains a restart path | `test_health` | nothing in the health monitor names the watchdog entry point |
| M-35 | the health monitor takes a third-party dependency | `test_health` | the health monitor imports only the standard library and its own seam |
| M-36 | a stale monitoring run is no longer reported | `test_health` | a stale run is reported |
| M-37 | a changed bootstrap error log is no longer reported | `test_health` | a changed bootstrap error log is reported |
| M-38 | a stalled outbox is no longer reported | `test_health` | a stalled outbox is reported |
| M-39 | a run lease held across many runs is no longer reported | `test_health` | repeated skipped runs are reported |
| M-63 | an unhealthy state notifies on every run, not on a transition | `test_health` | a steady unhealthy state does not renotify |
| M-65 | a recorded fingerprint mismatch is no longer a health finding | `test_health` | a changed certificate is reported |

### launcher allowlist

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-51 | the launcher accepts any path, not the two entry points | `**nothing**` | see the notes below |

### outage announcement

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-16 | the first failure of an outage reaches the decision point | `test_watchdog` | the run that announces an outage must not also decide on it |
| M-17 | recovery no longer clears the announcement marker | `test_watchdog` | recovery must clear the announcement marker |

### outbox

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-55 | an event is marked delivered before the remote call succeeds | `test_outbox` | both events remain pending |
| M-61 | delivery no longer stops at the first failure | `test_outbox` | delivery stops on the first failure |
| M-62 | the per-event retry bound is removed | `test_outbox` | a poison event is retried only up to the bound |

### probe

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-58 | a non-HTTPS probe URL is accepted | `test_watchdog` | validate_config accepted an invalid configuration |

### restart bound

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-12 | the bound never stops a restart | `test_watchdog` | a third decision must be refused once the bound is reached |
| M-13 | the bound counts from the clock, not since connectivity was last seen | `test_watchdog` | a restored connection must clear the count, or one bad day disables th |

### restart cooldown

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-28 | the cooldown no longer suppresses a second decision | `test_watchdog` | 20 minutes is inside the 30-minute cooldown |

### restart decision

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-05 | either condition alone warrants a restart | `test_watchdog` | the decision must wait for the failure window |
| M-06 | the duration condition is dropped | `test_watchdog` | the decision must wait for the failure window |
| M-07 | the observed-failures condition is dropped | `test_watchdog` | a single observation after a monitoring gap must not restart anything |
| M-08 | the failed-run count is not scoped to the current episode (F18) | `test_watchdog` | no notice, because there is nothing to announce |
| M-09 | the recency window is dropped, episode scoping kept | `test_watchdog` | one observation after a gap must not be topped up by this episode's ow |
| M-10 | a host that never saw connectivity can gather no evidence | `test_watchdog` | the decision must be reached on real evidence |
| M-11 | the outage start is the oldest failure ever, not this episode's | `test_watchdog` | a successful run between failures must reset the duration |

### router identity

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-27 | the certificate fingerprint is never compared before a restart | `test_watchdog` | the router's identity must be checked, and checked first |
| M-64 | a changed fingerprint is recorded without being reported | `test_watchdog` | AssertionError |

### run lease

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-18 | the lease transaction is deferred rather than immediate | `**nothing**` | see the notes below |
| M-19 | release is not scoped to the owner | `test_watchdog` | release must be owner-scoped |
| M-20 | a lease timestamped in the future locks the monitor out | `test_watchdog` | a lease dated in the future must be reclaimed |
| M-21 | a stale lease is never taken over | `test_watchdog` | a stale lease must be reclaimed |
| M-60 | the lease duration is unbounded above | `test_watchdog` | validate_config accepted an invalid configuration |
| M-69 | an existing lease is not consulted at all | `test_watchdog` | a run must not proceed while another holds the lease |

### schema migration

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-67 | a migration that loses a race with the other schedule is fatal again | `test_watchdog` | duplicate column name |

### secret store

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-29 | any keyring backend is accepted, including a plaintext one | `test_platform` | a fallback backend is refused |
| M-30 | a credentials directory owned by another user is accepted | `test_platform` | a credentials directory owned by another user is refused |
| M-31 | a world-readable credentials directory is accepted | `test_platform` | a credentials directory with mode 750 is refused |
| M-32 | a declared but invalid credentials directory falls back to keyring | `test_platform` | a declared but missing credentials directory is refused |
| M-33 | a secret may come from the environment | `test_platform` | a secret is never taken from the environment |

### version gate

| ID | Reversion | Caught by | Assertion |
|---|---|---|---|
| M-52 | version drift is no longer reported | `test_check_versions` | stale marker is detected |

## Method

A throwaway harness in the session that produced this, not committed: it copies
the tree, applies one textual reversion whose anchor must match
exactly once, runs the suites, and records the first failure. A reversion that
fails for the wrong reason — a syntax error, an unbound name, a SQL parameter
mismatch — is reported separately rather than counted as caught, because a
mutation that breaks the code proves nothing about the pin. Three anchors failed
to match on the first attempt and were corrected rather than dropped; one
reversion (`cp` for `install -o root`) was too weak to be meaningful and was
replaced with the one that reproduces the original defect (`cp -a`, which
preserves the checkout's ownership) and is caught.

## What this does not establish

The sweep runs on Linux. The Windows half of `test_wrapper.py` was swept
separately on the monitoring host, two reversions, both caught. The Debian
installer half of `test_deploy.py` runs here under a real mount namespace as
root, so those rows are behavioural rather than static.

It says nothing about behaviours nobody listed. The list was built by reading
the modules, which is the same faculty that missed F18 for four days.

<!-- END-OF-FILE: PIN_SWEEP_A17.md -->
