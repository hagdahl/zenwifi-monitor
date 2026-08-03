<!-- ZenWiFi Monitor version: 0.1.0 -->

# Independent review — `d1ab7c81b25d7ebaac5fa1bf02689977ac3c978b`

**Role:** independent reviewer.  
**Scope:** the public ZenWiFi Monitor state at the exact object named above.  
**Method:** read-only review of a detached scratch clone, except for this
authorised report. No commit, push, configuration change, router request,
Notion request, or `--execute` invocation was made.

## Object and evidence

The requested commit was checked out detached in a fresh scratch clone created
from the locally available clean public staging repository. A direct remote
verification was not possible because outbound access to the public remote was
unavailable in the review environment. The local public staging copy resolved
the requested full object ID and reported a clean working tree before cloning.

I read the watchdog decision, configuration validation, outbox, secret-store,
platform, health-monitor, Windows wrapper, Debian installer and units, relevant
documentation, and all nine test suites. I ran the suites without passing
`--execute`:

- `test_check_versions.py`, `test_configure.py`, `test_dependencies.py`,
  `test_health.py`, `test_outbox.py`, `test_platform.py`, and
  `test_watchdog.py` completed green under the available Python runtime.
- `test_deploy.py` completed its static checks. Its privileged namespace
  installer execution and systemd parser checks were skipped because this was
  not a POSIX/systemd host.
- `test_wrapper.py` did not complete; see M-1 below.
- The required Python 3.11 runtime was unavailable. The available Python 3.14
  was used only for safe, standard-library test paths. This is not equivalent
  to a supported-runtime validation.

I also performed an isolated SQLite experiment. It inserted two old failures,
then one successful run, then one new failure. With a 30-minute window, a
three-failure requirement and a 15-minute threshold, the implementation
reported 19 minutes, three failed checks and `restart_is_warranted=True`.
No network or router call was involved.

Finally, in the scratch clone only, I changed the failed-run query so that it
counted only failures after the most recent successful run. `test_watchdog.py`
still completed green. I restored the scratch clone and verified it clean
afterwards. This is a mutation experiment, not a proposed patch.

## Findings

### Blocker — failures from an earlier, ended episode can authorise a restart

**Location:** `src/watchdog.py:462-497`, reached by `src/watchdog.py:749-787`.
**Status:** confirmed by isolated execution and by mutation testing.

`outage_evidence()` counts every failed run inside `failure_window_minutes`,
but it separately finds the most recent successful run only after that count.
The failure count is never constrained to runs after that success.

Consequently, an earlier failed period can supply part of the required count
after connectivity has been observed again. Example: two failures, one success,
then one new failure more than the duration threshold ago. The current code
counts three failures and regards the restart condition as satisfied, even
though the current episode contains only one observed failed run. In execute
mode, the normal path then proceeds to the router restart call.

This contradicts the code's own stated meaning of a successful observation as
the boundary of the current outage, and defeats the claim that the failed-run
requirement makes a monitoring gap harmless. The existing flapping test only
checks the duration immediately after recovery; it does not test an old failed
count combined with a later, sufficiently old new failure.

**Remediation:** derive both the duration and the failed-run count from the
same current episode. After finding the latest successful run, count only failed
runs later than it (while retaining the failure-window lower bound). Add a
negative end-to-end test with the sequence above, and assert that neither a
dry-run decision nor a reboot call is reached. Retain a separate no-prior-success
case so a first-ever outage can still gather its required evidence.

### Medium — the Windows wrapper suite is not runnable in this Windows review environment

**Location:** `04_tests/test_wrapper.py:195-198`.
**Status:** confirmed by execution in the available runtime; unsupported Python
3.11 could not be used to establish whether the problem also occurs there.

The suite invokes `cscript.exe` with `capture_output=True, text=True` and no
explicit encoding or decoding-error policy. Its child output was decoded with a
Windows text codec that did not match the bytes produced by Windows Script
Host, raising `UnicodeDecodeError` in the subprocess reader thread. The test
then received exit code 1 rather than the expected refusal code and aborted at
its first launcher case.

This prevents the Windows activation-surface checks from providing a usable
local validation result on this host. It also means the claimed set of safe
test suites was not fully executable in the environment the project targets.

**Remediation:** make the wrapper-test subprocess encoding explicit and robust
for Windows Script Host output (or arrange an explicitly Unicode output mode),
then verify the expected refusal code and diagnostic independently of display
encoding. Re-run it under the documented Python 3.11 runtime.

## Claim assessment

1. **Not upheld.** The duration is measured from the current episode, but the
   failed-run count can include failures before its latest successful run. The
   confirmed sequence above can authorise a restart without the configured
   number of observed failures in the current outage.
2. **Upheld by reading and existing tests.** Restart decisions are counted
   since the last observed online run and bounded by the configured window;
   the code records and suppresses further decisions at the limit.
3. **Upheld by reading and existing tests.** The TLS fingerprint is collected
   immediately before the restart path, stored/compared, and a change is
   recorded without blocking the subsequent path. An unreachable TLS endpoint
   is intentionally not treated as an identity change.
4. **Upheld by reading and static deployment tests.** The shipped Debian unit
   has no `--execute`; the optional drop-in supplies it; the default
   configuration is dry-run; and the disable option removes the drop-in. A real
   systemd installation run could not be performed in this environment.
5. **Upheld by reading and platform tests.** The secret accessor accepts only
   the listed protected keyring backends or a private systemd credential
   directory, and refuses a declared-but-invalid directory or fallback backend.
6. **Upheld by reading and static deployment tests.** The installer uses
   root-owned install modes for the deployed code. The privileged installation
   test was not runnable here, so this remains unverified against a live Debian
   filesystem.
7. **Not upheld.** The watchdog suite remains green after the mutation that
   prevents pre-recovery failures being counted in the current episode. It
   therefore does not detect removal of the behaviour needed to close the
   blocker. The wrapper suite also did not complete in this environment.

## Sound checks

- Both activation gates are checked before a router action and before Notion
  delivery; no review command opened either gate.
- Configuration validation rejects absent/placeholder required values, a
  non-HTTPS probe list, unsafe TLS downgrade without acknowledgement, invalid
  thresholds and invalid execution mode.
- The SQLite run lease uses an immediate transaction, owner-scoped release and
  bounded stale-lease takeover.
- The outbox commits local events before remote delivery, retries in oldest
  order with bounds, sanitises delivery errors and keeps local logging primary.
- The health monitor has no router restart path and the Debian health unit has
  no credentials or network address families.
- Version markers and the changelog agreed at the reviewed commit.
- The source scan found no credential-shaped token. The only user-home style
  paths found were synthetic test fixtures; no private-address match was found.

## Limitations and known test side effect

- Public-remote freshness could not be independently verified because outbound
  remote access was unavailable. The report is bound to the resolved local
  public object, not to a fresh remote fetch.
- Python 3.11, a POSIX mount namespace, root, and systemd as process one were
  unavailable. The Debian installer execution claims therefore remain only
  statically reviewed here.
- `test_wrapper.py` intentionally invokes the real VBS refusal path on Windows.
  That path attempts to append a bootstrap-error record outside the scratch
  clone. This is the known test-side-effect exception described by the review
  prompt; it should be considered when running the suite on an operational
  Windows account.

## Verdict

- **Public publication: NOT READY.** The repository must not be presented as
  having its claimed current-episode restart evidence while the blocker remains.
- **Production use: NOT READY.** In execute mode, the blocker can lead to a
  router restart supported partly by evidence from a previous, already ended
  outage.

<!-- END-OF-FILE: CODEX_REVIEW_d1ab7c81b25d7ebaac5fa1bf02689977ac3c978b.md -->
