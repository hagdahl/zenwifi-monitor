<!-- ZenWiFi Monitor version: 0.1.0 -->

# Seventh independent review — `a6df698`

Run on 3 August 2026 by an independent agent given
`01_docs/CODEX_REVIEW_PROMPT.md` and the commit, and nothing else: not what the
changes were meant to achieve, not what previous rounds found, not why any
decision was taken. It cloned the published repository into a scratch directory,
reviewed that state read-only, and returned the report below.

Two things about provenance, stated because they bear on how much the report is
worth. It was run inside the same session that wrote the code under review, so
it is independent of that reasoning but not of the environment. And it ran on
Linux as root with `unshare`, so `04_tests/test_deploy.py` executed its real
installer inside a mount namespace — 101 checks, no skips — while the Windows
launcher half of `test_wrapper.py` was skipped, which the report says plainly.

**Outcome: no Blocker. Three High, four Medium, five Low. Claims 1, 3, 8 and 9
not upheld.** Every finding was reproduced here before anything was changed, and
every remedy was revert-checked. The section after the report records what was
done with each.

---

## The report, as returned

### 1. What the reviewer did

Read in full: `src/watchdog.py`, `src/health.py`, `src/_secrets.py`,
`src/_platform.py`, `src/_defaults.py`, `src/_logrotate.py`,
`scripts/configure.py`, `scripts/RouterWatchdog.vbs`, `scripts/Setup-Secrets.py`,
`deploy/debian/install.sh`, all nine suites, `01_docs/PIN_SWEEP_A17.md`, the CI
workflow, and the decision log.

All nine suites run, all green. `test_deploy.py` executed its real installer
inside the mount namespace — 101 checks, zero skips, including
`systemd-analyze verify`. The containment claim was verified rather than
believed: `/etc/passwd` mtime, the unit files and `/opt` were snapshotted before
and after and diffed, with no host change.

Thirty-one mutations and three direct behavioural probes, all executed. It also
re-ran the four reversions `PIN_SWEEP_A17.md` singles out; all four behaved as
that document says.

### 2. Findings

**No Blocker.** Nothing found that can cause a router restart the evidence does
not support.

**HIGH — the restart bound does not hold when restarts fail.**
`restarts_this_episode` counted only `Router restarted` and `Dry-run`. A restart
that is attempted and fails is logged as `Restart failed` and counted by
nothing, leaving the cooldown as the only limit. Confirmed by execution: with
`max_restarts_per_window: 2` and a failing reboot, **eight** attempts, eight
`Restart failed` events, zero `Restart bound reached` events, zero notices.
During an outage where the router API rejects, the monitor submits the router
credentials once per cooldown for the entire outage, indefinitely, and the
operator is never told.

**HIGH — `main()` is never pinned to require the protected credential store.**
`require_persistent_secret_store` is exercised thoroughly as a function and
every watchdog test stubs it out before calling `main()`. Deleting the call from
`main()` leaves all nine suites green; a run would then proceed on any keyring
backend, including the plaintext and in-memory fallbacks the module exists to
refuse.

**HIGH — `main()` is never pinned to validate the configuration.** Deleting
`validate_config(cfg)` from `main()` leaves all nine suites green. Every floor
becomes inert on the only path that matters: a one-minute threshold, a
non-HTTPS probe list, and `use_tls: false` without acknowledgement all reach a
live run.

**MEDIUM — two health checks are pinned as functions, never as findings.** The
assertions named "a changed certificate is reported" and "repeated skipped runs
are reported" call the helpers directly. Removing either from `evaluate`'s tuple
leaves all nine suites green. The health monitor is the only thing that puts a
changed router certificate in front of an operator.

**MEDIUM — the documented duration rule is not the implemented one.**
`src/watchdog.py`, `src/_defaults.py`, ADR-023 and `01_docs/REMEDIATION.md` all
say the most recent successful run must be at least
`failure_minutes_before_reboot` old. The code measures from the oldest failed
run *after* that success, which is stricter whenever monitoring was interrupted.
Confirmed: last success 60 minutes ago, three failures at 10/6/2 minutes,
threshold 15 — the documented rule says restart, the code correctly refuses.
The code is the safer of the two; the risk is inverted, because someone
correcting the code to match the documents would reintroduce a restart on
evidence gathered before a gap.

**MEDIUM — `configure.py --init` can pre-authorise cleartext credentials.** The
`else` branch that writes `insecure_http_acknowledged` covers both "no TLS"
(intended) and "TLS present but no certificate bytes" (not intended). Confirmed
by execution. Flipping `use_tls` to false by hand then passes validation with no
typed confirmation, defeating that guard for the life of the install. The
reviewer was explicit that the natural trigger is close to unreachable in
production, and that what makes it worth fixing is that a test drives exactly
this path and looks only at the fingerprint key.

**MEDIUM — one unguarded timestamp read stops monitoring permanently.** The
cooldown's `datetime.fromisoformat` was the only timestamp read in the project
without a parse guard. Confirmed: a corrupt stamp makes `main()` raise on every
run, after `log_run` has already written a fresh row — so the watchdog exits 1
every cycle while the health monitor's stale-run check stays green, and the
failure looks like a healthy monitor doing nothing.

**LOW.** `configure.py` claims `validate_config` requires `router.model`; it
does not. `04_tests/test_configure.py`'s "requires a typed confirmation" is a
source-text search that cannot fail for the reason its name gives, though the
behaviour is pinned elsewhere by accident. The `runs` table has no retention,
the only unbounded table. Four suites print `"result": "green"` as a literal.
`check_router_identity` logs a certificate-changed event with no dedup.

### 3. The nine claims

1. **Not upheld as worded, upheld in substance** — the duration is measured
   from the first failed run of the episode, not the last successful run.
2. **Upheld.** Both figures are scoped to the current episode, with both
   fallbacks correct when nothing has ever succeeded.
3. **Not upheld.** Failed restart attempts are not counted at all.
4. **Upheld.** The identity check is immediately before the reboot, the order is
   pinned, a change is logged and the run continues.
5. **Upheld behaviourally**, on a real install in the namespace.
6. **Upheld for the code as written**, but the call that puts the gate on the
   run path is unpinned.
7. **Upheld.** Observed on a real install from a source tree owned by an
   unprivileged uid.
8. **Not upheld.** Three tests confirmed to pass with the behaviour they name
   removed.
9. **Upheld about the sixty-six listed rows; not upheld as a statement about
   coverage.** Three safety-relevant behaviours the sweep did not list are
   unpinned, all of the same shape it says it swept for.

### 4. Sound

Both activation gates and their four single-gate reversions; the delivery gate
in both halves; the episode-scoped count and the recency window with the
must-still-decide counter-case; the flapping reset; the never-been-online path;
the cooldown at three discriminating points; the restart bound's reset on
restored connectivity; the announce-then-return guard; the identity check's
position; the post-restart block for success, refusal and exception including
credential sanitisation; at-least-once delivery, the per-run limit, the retry
bound, retention as an absolute bound; probe semantics; the lease under a
genuine two-thread race, stale reclaim, future-clock reclaim, owner-scoped
release and skip counting; migration under a duplicate-column race; the secret
store's backend, ownership, mode and environment refusals; and the Debian
install's ownership, modes, hardening directives, hash-locked pip, module
pruning and bootstrap-log refusal — all on a real install, with containment
confirmed. Publication scan clean.

### 5. Not established

Windows: the launcher checks and the PowerShell parser checks did not run. No
running `systemd`, so the directory-mode directives are pinned as declarations
only. The namespace harness stubs the venv step, so `--require-hashes` is
covered by CI rather than there. No real router or Notion behaviour. The
reachability of the `--init` finding is reasoned, not measured.

### 6. Verdict

**Public publication: READY WITH CHANGES** — the changes being documentary.
**Production use: READY WITH CHANGES** — fix the restart bound, guard the
cooldown read, add the two missing wiring pins.

---

## What was done with each finding

Every one was reproduced here before anything changed, and every remedy was
reverted and observed to fail a named assertion.

| Finding | Verdict | Action |
|---|---|---|
| Restart bound ignores failed attempts | **Confirmed by execution** — 8 attempts against a bound of 2 reproduced exactly | Fixed: `Restart failed` counts. ADR-027 corrected from "decisions" to "attempts". Pinned; the reversion produces the same 8-for-2 and fails |
| `main()` unpinned for the credential store | **Confirmed** — deleting the call left all nine suites green | Pinned: a refusing store must stop the run before a row is written |
| `main()` unpinned for validation | **Confirmed** — same | Pinned: a rejected configuration must not even open its database |
| Two health checks unwired | **Confirmed** — removing either from `evaluate` left all suites green | Both assertions now also run through `evaluate()`. `PIN_SWEEP_A17.md` corrected, since two of its rows overclaimed |
| Documented duration ≠ implemented | **Confirmed** — the 60/10/6/2 shape refuses, as the code intends | Wording corrected in all four places. The stricter behaviour is pinned, so a future "correction" to the old wording fails |
| `--init` pre-authorises cleartext | **Confirmed** | Branch narrowed to `not use_tls`; the test now asserts the acknowledgement is absent from a TLS configuration |
| Unguarded cooldown timestamp | **Confirmed** — every run raises after the run row is written | Guarded, treating an unreadable stamp as an elapsed cooldown; pinned |
| `router.model` claim (A-13) | Correct | Comment and operator message corrected; A-13 closed |
| `test_configure` source-text check | Correct, and the behaviour is pinned elsewhere | Left, recorded; the name is misleading rather than the coverage missing |
| `runs` has no retention | Correct | Recorded as A-19, not fixed here |
| Literal `"result": "green"` | Correct | Recorded as A-20, not fixed here |
| Certificate event has no dedup | Correct, and materially reduced by the bound fix | Recorded as A-21 |

<!-- END-OF-FILE: REVIEW_a6df698.md -->
