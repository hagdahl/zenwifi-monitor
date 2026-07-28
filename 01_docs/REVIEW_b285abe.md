<!-- ZenWiFi Monitor version: 0.1.0 -->

# Fifth independent review — published commit b285abe

Reviewed by an agent given the four commits, the code and the mandatory
constraints, and no knowledge of the implementation reasoning. Every finding
below was checked adversarially by the reviewer before being reported, and the
ones marked confirmed were established by executing a reverted version and
watching what the suite did.

Commits under review, on the public mirror:

    11e0ff0  Own what the Debian install installs, and give it a test that runs it
    00c413a  Run the installer test as root in CI, where the finding actually lives
    2c7b56e  Make the suites fail when the behaviour they name is removed
    b285abe  Overlay /opt in the installer test instead of masking it

Verdict: **READY WITH CHANGES.**

## The claims, judged

**F13 — upheld.** Every file is placed with explicit `-o root -g root`,
`${PREFIX}/src` is removed first, and `cp -a` is gone. Confirmed behaviourally
against a real install in the namespace, and both halves fail the suite when
reverted.

**F11 — upheld.** `bootstrap_log_path` prefers `$LOGS_DIRECTORY` and splits on
the path separator, which matches `LogsDirectory=` in both units; the
notification stamp follows it automatically; both top-level handlers swallow an
`OSError` from recording; the installer refuses when the account cannot write
the directory. All four fail their suite when reverted.

**F14 — upheld.** The directory is trusted only when owned by the run's own uid
with no group or other bits, the gate raises rather than falling back, and
`get_secret` reads through the same predicate so the two cannot disagree within
a run.

**F15 — NOT upheld.** Eight of the nine sub-items are closed and six were
verified by reversion. The ninth is not closed, and the check that names it
cannot fail. See Finding 1.

## Findings

### 1. High — a plain `--install` that opens activation gate 1 passes the suite

Confirmed by execution. `04_tests/test_deploy.py` runs `--install` and then
`--enable-execution` in the same namespace and only probes afterwards, so
nothing ever observes the state that `--install` alone produces. Changing the
argument parser so that `--install` also writes the `--execute` drop-in left all
84 checks green.

The check named "`--enable-execution` writes the drop-in and nothing else does"
carries its second half with a substring scan of the script text — the exact
technique F15 exists to remove. This is also the F15 sub-item the fourth round
named verbatim, while F15 is now marked closed.

Gate 2 is unpinned at the installed artefact as well: nothing asserts that the
`config.json` written by `--install` says `dry-run`.

Remedy: run `--install` in its own namespace and assert the drop-in does not
exist and the installed configuration is `dry-run`; only then, in a second
phase, run `--enable-execution` and assert the drop-in appears. Delete the
substring scan once the behavioural check exists.

### 2. High — a real installation on the test host answers for the install under test

Confirmed by execution. The driver clears the project's own paths from the
overlay but not `/etc/systemd/system/zenwifi-monitor.service.d/`, so on a host
that already runs the monitor the host's own drop-in shows through the lower
layer and is read back as this run's output. Making `--enable-execution` a
complete no-op still passed; adding that directory to the removal list made the
same mutation fail. The service account leaks the same way, which is why the
docstring's claim that account work is exercised is conditional.

Remedy: add the unit files and the drop-in directory to the removal, and assert
inside the driver that none of the probed paths exist before the installer runs.

### 3. Medium — `find … -exec install … \;` swallows every failure

Confirmed by execution. GNU `find` exits 0 even when every `-exec` fails, and
`rm -rf "${PREFIX}/src"` has already run by then. A partial or total failure
therefore destroys a working install's code, builds the environment, installs
the units, enables both timers and prints "Installed. Production execution is
OFF." with the source directory empty. Safe for the router — the unit fails at
import — but the monitor is dead and the operator is told the opposite.

Remedy: `install -m 0644 -o root -g root "${SOURCE_DIR}"/src/*.py "${PREFIX}/src/"`,
or `find -print0 | xargs -0 -r` under `pipefail`. Either also fails loudly when
the source has no modules.

### 4. Medium — `00_admin/HANDOVER.md` still documents the pre-F11 location

Lines 23 and 25 state that the bootstrap log lives under `%LOCALAPPDATA%` on
Windows and under the home directory on POSIX hosts — the sentence F11 exists
to falsify. The Debian guide was updated in the same commit; the handover was
not, and the project's own standard requires it to move with the change group.

### 5. Medium — the verification claim is not true of every pin

The changelog and the improvement plan both say every pin added was verified by
reverting the behaviour and confirming a suite fails. Finding 1 falsifies that
for the installer activation gate. Since ADR-026 makes exactly that the
acceptance rule for this project, an untrue instance of the claim matters more
than an ordinary documentation slip.

### 6. Low — the installed-module check shares its rule with the code

Confirmed by execution. The installer copies `-maxdepth 1 -name '*.py'`; the
test compares against a top-level-only glob. A future subpackage under `src/`
would be silently omitted from the install with the check still green.

### 7. Low — the `test_deploy.py` docstring overstates the run

It says the installer's dependency work is exercised. The stub interpreter
fabricates `venv/bin/python` as a script that exits zero, so all three `pip`
commands — including the `--require-hashes` install this project treats as a
supply-chain control — are no-ops. A comment further down says the opposite, so
the file contradicts itself.

### 8. Low — the ownership assertion is vacuous from a root-owned checkout

Confirmed by execution. `cp -a` preserves the clone's ownership, so the check
only discriminates when the clone is not root-owned. CI is the good case; a
local `sudo python3 04_tests/test_deploy.py` from a root-owned clone is the weak
one. Remedy: copy the source to a scratch path inside the namespace, chown it to
a non-root uid, and install from there.

## What the reviewer checked and found sound

The activation model itself is untouched and still holds: one `ExecStart`
without `--execute` in the shipped unit, a drop-in that clears `ExecStart`
before setting it, a health unit with no credentials and no network address
family, and a template that ships `dry-run`. The post-restart tests genuinely
assert that a failed restart neither stamps a success nor arms the recovery
notice, and that the credential value does not reach the event detail.

Namespace containment was verified rather than believed: a before-and-after
snapshot of `/etc`, `/opt`, `/var/lib`, `/var/log`, `/var/mail` and the
passwd, group and shadow checksums showed no change across a full root run. The
overlay over `/opt` is the right fix and is necessary on a GitHub runner. When
an overlay cannot be mounted the driver exits 90 and the suite fails loudly; the
only silent skips are the honest ones.

Fifteen mutation experiments were run in all; twelve were caught. The three that
were not are Findings 1, 2 and 6.

## Limits of this review

Nothing has run under a real systemd, and this review did not change that. Three
things remain unestablished: that systemd presents `$CREDENTIALS_DIRECTORY` with
a mode satisfying the new test on the target's version — if it ever does not,
the watchdog fails closed and a warranted restart does not happen; that
`LogsDirectory=` leaves the installer's 0750 mode on an existing directory
rather than applying the default 0755; and that `pip install --require-hashes`
succeeds against the lock file, which the harness stubs out.

The PowerShell-parser section of `test_wrapper.py` could not execute in the
review container, so it was read but not mutated there. It runs on both CI jobs,
and its three reversions were checked separately on the Windows machine.

Nothing unsafe to publish was found in the changed files.

<!-- END-OF-FILE: REVIEW_b285abe.md -->
