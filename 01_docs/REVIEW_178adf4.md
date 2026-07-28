<!-- ZenWiFi Monitor version: 0.1.0 -->

# Independent review, fourth round — commit `178adf4`

## 1. Reviewed commit and scope

Reviewed commit: `178adf4`, "Add first-time configuration setup for either platform",
as published, obtained by cloning the public repository rather than by reading
the working tree it was built from.

The review was conducted by five independent reviewers with no knowledge of the
implementation reasoning, each given one lens and told not to duplicate the
others: safety controls, security and public-release hygiene, documentation
accuracy, test quality, and concurrency and persistence. The test-quality
reviewer worked by mutation, applying forty deliberate defects to a copy and
recording which ones the suites failed to catch.

Scope follows `01_docs/INDEPENDENT_REVIEW_PROMPT.md`, extended to cover the
surfaces added since that prompt was written: the platform seam, the run lease,
the Debian deployment path, and the configuration tool.

Findings marked **reproduced** were re-verified by executing the failure, with
the reproduction described. Findings marked **read** were established by reading
code and configuration and are stated without a reproduction. The distinction
matters: a reproduced finding is a fact, a read finding is an argument.

## 2. Findings

Numbering continues section 9 of `01_docs/IMPROVEMENT_PLAN.md`, which carries
F1 to F6 from earlier rounds.

### F7. The restart decision does not require an observed outage (Blocker for production) — reproduced

`src/watchdog.py:403,413,416,418`. `first_failure_utc` is written once when a
run first finds the Internet unreachable, and is cleared only by a later run
that finds it reachable. The restart decision is then `utc_now() -
first_failure_utc >= failure_minutes_before_reboot`. Nothing requires that any
monitoring run happened in between.

Any gap in monitoring therefore converts a stale marker into an immediate
restart decision on the **first** failing probe cycle after the gap. Gaps are
ordinary, not exotic: the Windows task is registered with an interactive token
and does not run while the user is logged off or the machine is asleep; the
Debian timer is deliberately not `Persistent`, so a machine that was off
produces the same gap; a run that declines the lease returns without probing; a
crashed run leaves no trace.

Reproduction: a marker aged nine hours, then one failed probe cycle, in
dry-run. The decision is reached immediately and the event recorded is
`('Offline', 'Dry-run', 'Restart condition met; no external action in dry-run.')`.
In execute mode that is a router restart on evidence of approximately zero
minutes of outage, while the persisted event detail and the on-screen notice
both assert that the Internet was unavailable for at least fifteen minutes.

The same wall-clock reasoning governs `last_reboot_attempt_utc` at `:421`, so a
gap or a forward clock step also makes the cooldown read as elapsed.

Remediation: require evidence of continuous failure rather than the age of a
marker — for example, ignore a marker older than a small multiple of the run
interval, or count consecutive failed runs and require the count as well as the
elapsed time. Whichever is chosen, the event detail and the notice must state
what was actually observed rather than a constant.

### F8. A partial failure during a recovery run arms the restart (High) — reproduced

`src/watchdog.py:404-413`. In the online branch the "Restored" event is
committed at `:410`, `deliver_outbox` runs at `:412`, and `first_failure_utc` is
cleared only at `:413`. An exception in delivery — a locked database, a network
failure, a killed process — leaves the recovery recorded and the marker still
set. Combined with F7, one transient probe failure at any later point then
restarts the router with both gates open.

Remediation: clear the marker before any work that can fail, in the same
transaction as the recovery event.

### F9. Enabling execution in configuration alone makes the outbox grow without bound (High) — reproduced

`src/watchdog.py:411` versus `:324`. Delivery requires both gates.
`delivery_is_configured`, which decides retention, reads the configuration only.
The two gates are opened by different commands on both platforms, so opening
gate two alone is a natural half-step. In that state nothing is ever delivered,
every event keeps `delivery_attempts = 0`, retention takes the branch that only
removes events which have exhausted their attempts, and the health monitor
reports a stalled queue for ever.

Reproduction: forty events aged one year with a thirty-day retention window
produce `{'delivered': 0, 'abandoned': 0}` and forty rows remaining.

This is the same false-alarm class F1 was intended to close, reached through the
other gate. Remediation: judge retention on whether delivery can actually
happen in this run, not on the configuration alone.

### F10. An unwritable health log notifies on every run, bypassing the cooldown (High) — reproduced

`src/health.py:300,320`. The health record, the closing state and the
notification stamp are all persisted **before** `state` and `escalating` are
set to unhealthy for a write failure. The stored memory therefore says healthy
while the run reports unhealthy, so the next run sees a transition and
escalates again. `escalating = True` is unconditional on this path, so the
notice cooldown, the announced-codes set and the returning-condition damping
never apply.

Reproduction: a log directory that is a file rather than a directory, with a
sixty-minute cooldown configured, produces four notices in four consecutive
runs. On the shipped fifteen-minute schedule that is roughly ninety-six modal
dialogs a day until the path is fixed. The reported detail is also
self-contradictory — "All local health checks passed. The health log could not
be written." — and `severity` stays zero while `state` is unhealthy.

This defect was introduced by the fix for F4 in the same review cycle. The
suite written for F4 asserts that exactly one notice is raised, and never runs a
second time, so it cannot see the storm.

Remediation: set the state before persisting it, and route these findings
through the same cooldown as every other condition.

### F11. On Debian the bootstrap error log can never be written (High) — read

`src/_logrotate.py:18` resolves the path to `$HOME/ZenWiFiMonitor/` on POSIX.
`deploy/debian/install.sh:90` creates the service account with
`--no-create-home`, and both units set `ProtectHome=yes` with `ReadWritePaths`
covering only `/var/lib` and `/var/log`. Three consequences, all silent:

- `append_log` raises inside the top-level exception handlers in
  `src/watchdog.py:442` and `src/health.py:338`, so the original failure is lost
  and the intended non-zero exit never happens. Every pre-database failure,
  including a refused credential store, disappears.
- `check_bootstrap_log` short-circuits on a file that is never created, so that
  health check is dead code on Debian. It is the only mechanism that would
  surface a watchdog crashing on every run *after* writing its run row.
- The notification stamp, which exists precisely as the fallback for an
  unreadable database, can never be written.

`01_docs/DEBIAN.md:144` sends the operator to `/var/log/zenwifi-monitor` for
this log. Remediation: derive the path from `$STATE_DIRECTORY`/`$LOGS_DIRECTORY`
when systemd sets them, and fail the install if the location is not writable.

### F12. The restart-failure message is the one error that is both persisted and delivered, and the only one not sanitized (High) — read

`src/watchdog.py:430` writes `str(error)` for a failed restart.
`sanitize_error` exists at `:36` and is applied only at `:225`, to
`last_delivery_error`, which is never transmitted. The unsanitized string is the
one that `deliver_outbox` ships to Notion. The exception originates in the
router library on a call constructed at `:140` with a username and a password.
ADR-014 accepts the router endpoint appearing in delivered text; it explicitly
does not accept credentials.

Remediation: sanitize at `:430`, and redact the known secret values rather than
only a `Bearer` prefix.

### F13. Installed code is left writable by the invoking user (High) — read

`deploy/debian/install.sh:99,107`. `cp -a` preserves ownership, and the
documented invocation is `sudo ./deploy/debian/install.sh --install` from a
user-owned clone, so `/opt/zenwifi-monitor/src` ends up owned by the ordinary
user. systemd then executes it as the service account with the router
credentials decrypted into the process. Every hardening directive in the unit
remains satisfied. Lines 103 to 106 do this correctly with `install`.

### F14. The credential directory is accepted on one weak test (High) — read

`src/_secrets.py:66-72,80-82`. `CREDENTIALS_DIRECTORY` is honoured on the sole
condition that it is a directory: no ownership check, no mode check, no check
that it is systemd's. Under systemd the real path is safe; outside it, a
directory of plaintext files satisfies the project's own fail-closed gate and
places credentials in exactly the location the module docstring says is
impossible. `04_tests/test_platform.py` demonstrates the gap by asserting that a
bare temporary directory is an acceptable store.

### F15. Safety pins that test their own fixture (High) — reported by mutation

The test-quality review applied forty mutations. The lease is the best-pinned
mechanism in the tree: four of four mutations caught, and eight concurrent
processes produced exactly one winner in every trial. The following were **not**
caught, and each names a safety-relevant behaviour:

- Removing the line that stamps `first_failure_utc` at `:416`. Every offline
  test seeds that key by hand before calling `main()`, so the assertions confirm
  the fixture. With the mutation the monitor could never accumulate a failure
  window and would never restart anything.
- Removing the line that clears the marker on recovery at `:413`.
- Reading the failure-window value where the cooldown belongs at `:421`. The
  suite probes at zero and forty-five minutes, which fall the same side of both
  thresholds, so a silently halved cooldown passes.
- Removing the dry-run branch's cooldown stamp at `:423`.
- Dropping the Notion half of the delivery gate at `:411`; the fixture always
  has Notion enabled.
- Removing the per-run delivery bound from the query at `:213`.
- `any` to `all` in `internet_available`; `probe` and `internet_available` have
  no coverage at all, because every suite substitutes them.
- Adding a third-party import to the health monitor, and adding a spawn of the
  watchdog with `--execute` to it. Both checks are substring scans over source
  text, so the check named "the health monitor cannot restart the router" passes
  for a health monitor that restarts the router.
- The activation gate inside `install.sh --install`: the suite only ever runs
  the installer's dry-run branch, so a regression that writes the execution
  drop-in during a plain `--install` is invisible.

Remediation for the first four at once: drive `main()` twice — once to establish
the state, once to consume it — with the probes and the restart substituted,
rather than seeding rows by hand.

### F16. Documentation asserting things the code does not do (Medium) — read

Each verified against the code: the Debian guide's bootstrap log location
(F11); `01_docs/AUTHENTICATION.md:19,25`, which still states that secrets never
come from an environment variable or a text file and that the code refuses any
store but Windows Credential Manager, both of which the systemd path
contradicts; `00_admin/HANDOVER.md:15,17`, which still describes the
project-local environment that ADR-016 reversed; `00_admin/HANDOVER.md:14` and
`01_docs/USER_GUIDE.md:15`, which describe the launcher as taking an arbitrary
path when it accepts exactly two; `CHANGELOG.md:26`, naming a router method that
does not exist; the F6 remedy, recorded as applied to `ARCHITECTURE.md` and
`HANDOVER.md` and in fact applied to neither; ADR-015's "three source modules"
and its revisit trigger, already passed at six; ADR-018's claim that exactly two
behaviours branch on the platform, where `_logrotate.py` is a third;
`README.md:71`, describing Debian support as planned; and the `--init` command
in the Debian guide, which cannot succeed in the documented order because the
installer has already written the file that `--init` refuses to overwrite.

Two of these are this reviewer's own text: `CHANGELOG.md` claims "all nine
suites" twice, and there are eight.

### F17. Smaller items (Low) — read

- `validate_config` accepts `True` for the two safety thresholds, because
  `isinstance(True, int)` holds; the outbox loop excludes booleans, the
  safety-critical pair does not. There is no lower bound either, so `1` and `1`
  are accepted.
- There is no bound on consecutive restarts. During an upstream outage, which a
  router restart cannot fix, the router is restarted once per cooldown
  indefinitely.
- `run_lease_minutes` is not validated, so a large value stops monitoring for as
  long as it names.
- `install.sh --install` neither removes nor reports an existing execution
  drop-in, and prints that production execution is off regardless. There is no
  command to close gate one on Debian.
- `pip install --upgrade pip` runs unpinned and unhashed immediately before the
  hash-locked install it then enforces.
- `probe_tls` and its PowerShell counterpart disable certificate verification,
  which is defensible for a self-signed router, but the refusal message says
  "without verified TLS" when nothing was verified, and no fingerprint is
  recorded or compared, so even manual trust-on-first-use is impossible.
- `router.use_tls` is never validated at runtime, so the typed insecure-HTTP
  confirmation guards one moment at `--init` only; hand-editing the value sends
  the router password in clear text with no warning and no health finding.
- A lease timestamp in the future locks the watchdog out for the whole skew, and
  a run killed by `TimeoutStartSec` leaks the lease for ten minutes; both exit
  zero and neither is visible to the health monitor's thresholds.
- One malformed timestamp in `runs` or `events` raises `ValueError`, which
  `evaluate` does not catch, killing the health monitor permanently and
  silently. The lease deliberately hardens against exactly this input; the
  observer does not.
- Concurrent schema migration can raise `duplicate column name`; concurrent log
  rotation can destroy a retained generation. Both are one-shot and
  self-healing.

## 3. Checks passed, with evidence

Both activation gates are genuinely enforced. `reboot_router` is defined once
and called from exactly one site, `src/watchdog.py:428`, confirmed by search
across every source type. That site is reachable only past the mode check at
`:422`, and `effective_mode` is computed once at `:396` from both the argument
and the configuration, which `validate_config` constrains to two literals. The
Debian unit declares one `ExecStart` without `--execute`, and the drop-in clears
`ExecStart=` before setting it, so systemd's list-append cannot produce a second
un-gated invocation. The Windows launcher allowlists exactly two entry points,
refuses any unrecognised argument, and refuses rather than launching a
non-existent interpreter.

Nothing in the tests, tools or setup scripts can trigger a real restart. The
restart function is replaced by an asserting stub before every `main()` call,
the launcher suite exercises refusal paths only, the configuration tool writes
dry-run unconditionally on `--init` and never changes an existing mode, and CI
passes `--execute` nowhere.

The cooldown is committed before the restart is attempted, so a killed process,
a hung call or a failed restart cannot lose it.

The run lease holds. Eight concurrent processes across four initial states —
no lease, a live lease, a stale lease, and an unparseable timestamp — produced
exactly one winner in every trial. Release is owner-scoped. Even a genuine
overlap cannot produce two restarts, because the cooldown is committed first.

No secret, token, private address, personal path or machine name exists in any
tracked file or anywhere in the thirty-three commit history. The only email is a
GitHub noreply address and the only name is the intended copyright holder.
Credentials never reach a command line or an environment variable. The backend
predicate correctly refuses keyring's plaintext, in-memory, null and chainer
fallbacks.

The supply chain is sound: twenty-seven pinned distributions with 806 hashes,
`--require-hashes` at every install site followed by `pip check`, both GitHub
Actions pinned to full commit hashes with the upstream tag recorded, workflow
permissions limited to read, no secrets referenced.

Every persisted timestamp is timezone-aware UTC. There is no naive datetime and
no DST-sensitive arithmetic. Events cannot be lost on any normal path: the local
commit precedes every network call, delivered is set only after a success, and
retention touches only delivered, exhausted, or genuinely aged rows. Delivery
order is preserved.

## 4. Conclusions

**Public GitHub publication.** No blocker. The repository carries no secret,
personal datum or private address, in either its files or its history, and its
licensing and attribution are in order. F12 should be fixed before the project
is used with a real Notion destination, since it is the one path that can carry
credential-bearing text off the machine, and the documentation inaccuracies in
F16 mislead anyone auditing the security posture from the files that exist to
describe it.

**Local production activation.** F7 is a blocker on its own terms: the property
the project documents, notifies about and records — fifteen minutes of observed
unavailability — is not the property the code enforces, and the gap between them
is filled by ordinary events like a machine sleeping. F8 compounds it. An
install running with both gates open can restart the router on evidence of no
outage at all.

The project owner has been informed of F7 and has chosen to leave production
execution enabled pending the fix. That is a deliberate acceptance of a stated
risk, recorded here so it is not mistaken for an oversight.

## 5. Verdict

**READY WITH CHANGES** for public GitHub publication.

**NOT READY** for local production activation until F7 and F8 are closed.

<!-- END-OF-FILE: REVIEW_178adf4.md -->
