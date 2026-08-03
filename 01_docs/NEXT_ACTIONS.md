<!-- ZenWiFi Monitor version: 0.1.0 -->

# Proposed next actions

Every planned item in `01_docs/IMPROVEMENT_PLAN.md` is implemented and every
finding from five independent review rounds is closed, except F6, which the
project owner accepted rather than engineered away. What follows is not a
backlog of missing work; it is what a project in that state should do next.

Identifiers are `A-nn` and are permanent. They do not collide with the review
findings (`F1`–`F17`, which are defects) or the decision log (`ADR-001`
onwards). An action that is completed is marked done here and keeps its
identifier, so a later document can refer to it without ambiguity.

A-04 and A-05 are done. A-06 is running. A-10 to A-13 were not proposed by
anybody: they came out of A-04, which is what a verification is for. The rest is
a proposal for the owner to pick from, in whatever order he chooses.

## Summary

| ID | Action | Class | Depends on | State |
|---|---|---|---|---|
| A-01 | Sixth independent review of `69121ac` | Assurance | — | Proposed |
| A-02 | Upgrade `keyring` 25.6.0 → 25.7.0 | Maintenance | A-01 | Proposed |
| A-03 | Release 0.1.0 | Release | A-01, A-02 | Proposed |
| A-04 | Verify and record the deployed state on the monitoring host | Assurance | — | **Done 3 Aug** |
| A-05 | Restore the declared line endings on `scripts/Install.ps1` | Correction | — | **Done 3 Aug** |
| A-06 | Soak the Debian path under a real `systemd` | Evidence | — | **Part done 3 Aug**; credentials still unverified |
| A-14 | Make `install.sh` executable in the index | Correction | — | **Done 3 Aug** |
| A-15 | Set `StateDirectoryMode` and `LogsDirectoryMode` on both units | Defect | — | **Done 3 Aug** |
| A-16 | Do not enable the timers before credentials exist | Defect | — | Proposed |
| A-07 | Verify the desktop notice against a real session bus | Evidence | A-06 | Blocked on a session |
| A-08 | First real recovery verification | Evidence | — | Partly observed 29 Jul |
| A-09 | Decide whether to close F6 with an off-host observer | Decision | — | Proposed |
| A-10 | Find out why the bootstrap-log health check never fires | Not a defect | — | **Closed 3 Aug — the tool was wrong, not the code** |
| A-11 | Stop the launcher suite writing to a real path | Defect | — | Proposed, consequence corrected |
| A-12 | Record the router certificate at a calm moment, not before a restart | Design | — | Proposed |
| A-13 | Correct the claim that `validate_config` requires `router.model` | Correction | — | Proposed |

## A-01. Sixth independent review of `69121ac`

**Why.** Everything published since `b285abe` is unreviewed: the fifth round's
own remedies, F16, F17, the restart bound, the certificate comparison, the
stricter validation rules, ADR-027, ADR-028, and the dependency review tool and
workflow. That is roughly twelve hundred lines, and it touches the safety model
in three places — when a restart is allowed, when it stops being allowed, and
what the monitor will accept as the router.

The argument is the project's own history, not caution in the abstract. The
fifth round existed to check the fourth round's fixes and found three defects in
them, one of which was a false claim in this project's own changelog that a
finding had been closed. A round of fixes that has not been reviewed is not
evidence of anything.

**Scope.** `01_docs/INDEPENDENT_REVIEW_PROMPT.md` bound to `69121ac`, with the
reviewer told which findings the range claims to close and required to refute
each of its own findings before reporting it, and to say which it confirmed by
executing something rather than by reading.

**Done when.** A report exists in `01_docs/`, each finding is closed or
explicitly accepted in writing, and the improvement plan records the round.

**Effort.** One session. It may generate more.

## A-02. Upgrade `keyring` 25.6.0 → 25.7.0

**Why.** The dependency review's first real output, currently open as issue #1
on the public repository. Running it once end to end is also what proves the
loop built in `69121ac` actually works as a process rather than as a script.

**Steps.** Edit `requirements.in`; regenerate `requirements.lock.txt` with
`uv pip compile requirements.in --universal --generate-hashes --python-version
3.11 -o requirements.lock.txt`; reinstall with `--require-hashes` on both
platforms; run all nine suites on both; commit; close the issue.

**Declining is a valid outcome.** If the release brings nothing this project
needs, the honest action is to say so in the issue and leave the pin alone. What
is not acceptable is letting it sit unanswered, because that is the failure mode
the whole review exists to prevent.

**Depends on A-01**, so a review does not have to be repeated against a moved
dependency set.

**Effort.** An hour, most of it running suites.

## A-03. Release 0.1.0

**Why.** `CHANGELOG.md` still says `0.1.0 - Unreleased` while every planned item
is delivered. A version that stays unreleased indefinitely stops meaning
anything, and the
next change would otherwise land in the same undifferentiated heading.

**Steps.** Decide whether the Debian path ships in 0.1.0 or waits for A-06;
replace the heading with a date; tag the source repository and the public
mirror; check that `VERSION`, the per-file markers and `config_version` agree,
which `scripts/check_versions.py` already enforces.

**Depends on A-01 and A-02**, because tagging before a review means tagging
twice.

**Effort.** Half an hour.

## A-04. Verify and record the deployed state on the monitoring host

**Why.** On Windows there is no separate install step: the scheduled tasks run
the working tree through the silent launcher, so whatever is checked out is what
runs every five minutes. That makes the deployment invisible — nobody has to do
anything for a commit to become live, which is convenient and is also why it
should be checked rather than assumed.

The code that runs changed at `e0b82fe`, which altered `validate_config`, added
the restart bound and put a certificate comparison into the restart path. The
live configuration was validated against the stricter rules before that commit,
but no scheduled run has been observed since.

**Steps.** Confirm `git status` is clean at the intended commit; confirm both
scheduled tasks are registered, point at this folder through `wscript.exe`, and
that only the watchdog task carries `--execute`; read the last twenty rows of
the `runs` table and confirm they are five minutes apart and recent; read the
`health` table for the same period; confirm the bootstrap error log has not
grown.

**Done when.** The result is written into `00_admin/HANDOVER.md` as the last
verified deployment, with the commit and the date, so the next session does not
have to infer it.

**Effort.** Ten minutes, and it needs a shell on the host.

**Done, 3 August, against `69121ac`.** The working tree is clean at that commit.
Both scheduled tasks are registered, both run `wscript.exe` against this folder,
only the watchdog carries `--execute`, and its last result was 0. The `runs`
table holds 1684 rows; the newest was three and a half minutes old when checked,
and today's are exactly five minutes apart with no gaps. The last twenty-four
hours hold 97 runs rather than the 288 a continuously running machine would
produce, which is the machine being off for two thirds of the day and not a
fault — the task does not replay missed runs, deliberately. Every run in that
period recorded `execute`/`execute`. Both events ever written have been
delivered to Notion, with a maximum of one delivery attempt. Nothing is pending
and nothing is exhausted.

**And it found four things nobody was looking for**, which are A-10 to A-13. The
one that matters is A-10: 434 health rows, every one of them healthy, and a
check that provably should have reported a change six days ago.

## A-05. Restore the declared line endings on `scripts/Install.ps1`

**Why.** `.gitattributes` declares `*.ps1 text eol=crlf`. The file was delivered
from the cloud sandbox with LF, and `git checkout --` did not restore it,
because git considered the working tree clean: the LF content normalises to the
same blob the index holds, so there was nothing for it to write. The commit
message for `69121ac` claims the file was restored. **That claim is wrong**, and
this entry exists so the correction is on the record rather than in a chat log.

**Impact.** Cosmetic. The committed and published content is correct, PowerShell
reads LF without complaint, and nothing in CI touches the file. It is listed
because a declared attribute that the working tree quietly contradicts is
exactly the kind of small untruth this project has spent two review rounds
removing.

**Steps.** Delete the working-tree file and check it out again, which forces the
line-ending filter to run; confirm the size is 5543 bytes rather than 5454;
apply the same to any `.vbs` or `.ps1` delivered from the sandbox in future.
The delivery routine in the project memory has been corrected to say so.

**Effort.** Two minutes.

## A-10. Why the bootstrap-log health check appeared never to fire — closed, no defect

**Closed on 3 August. The health monitor was right and the instrument was
wrong.** Recorded in full because the mistake is more useful than the
non-finding.

**What was actually happening.** The scheduled task and the MCP PowerShell shell
used to inspect the machine see **two different files at the same path**. Proved
by asking a temporary scheduled task of its own to stat the file and comparing:

| | `%LOCALAPPDATA%\ZenWiFiMonitor\bootstrap-errors.log` |
|---|---|
| what the scheduled task sees | 3458 bytes, modified 27 July 21:39 |
| what the inspecting shell sees | 29661 bytes, modified 28 July 17:06 |

`health-notified.txt` diverges the same way, each view carrying its own copy.

So the health monitor has been comparing its stamp against *its* file, which has
genuinely not changed since 27 July, and correctly reporting nothing. The
"silence" was an artefact of comparing its stamp against a file only the
inspecting shell can see. Every health row saying healthy was true.

**What this costs.** Not the code — the tooling. Any observation of
`%LOCALAPPDATA%` made through that shell is not the scheduled jobs' view and
must not be treated as evidence about them. That includes parts of A-04. The
SQLite database is unaffected: it lives outside that path and both views agree
on it, which is why the scheduled runs' health rows are visible in the database
this session read.

**This project already knew.** The July incident with the relocated virtual
environment ended with exactly this lesson written down: the MCP shell and the
scheduled task have different filesystem views. It cost four wrong hypotheses
then, and most of an investigation now. The rule earned twice: **to learn what a
scheduled task sees, ask a scheduled task.**

**No action follows for the monitor.** What follows is for the operator's
documentation: `00_admin/HANDOVER.md` now says that this path cannot be
inspected from a remote shell and how to ask properly.

## A-11. Stop the launcher suite writing to a real path

**Severity: medium; the behaviour is real, the consequence was overstated.**
`04_tests/test_wrapper.py` drives the real `scripts/RouterWatchdog.vbs` with
fourteen arguments it must refuse, and the launcher records each refusal in the
bootstrap error log at `%LOCALAPPDATA%`. One of the nine suites therefore writes
to a real machine path, contradicting the principle the others state, and its
docstring does not say so.

**Corrected after A-10.** The 261 lines of test output were originally described
as polluting the operator's first troubleshooting location. They are not: they
landed in the inspecting shell's view of that path, and the file the scheduled
jobs actually read still holds 3458 bytes of real history from 27 July. The
suite has been writing to *a* real path, not to *the operator's* one — which is
luck, not design, and does not make it acceptable.

**Remedy.** Point the launcher at a temporary log for the duration of the suite,
or give the wrapper a documented way to be told where to write and use it from
the test. Then say in the docstring which real surfaces the suite touches, if
any remain.

## A-12. Record the router certificate at a calm moment, not before a restart

**Severity: medium.** `check_router_identity` is called from exactly one place:
immediately before the restart, which is where it belongs for *checking*. But it
is also where the baseline is first *recorded*, and on this host
`router_tls_fingerprint` is still unset after six days, because no restart has
happened.

Trust on first use is only worth anything if first use is a moment nobody chose.
Establishing the baseline during an outage, in the seconds before the monitor
sends the router password, is the least trustworthy moment available and the
easiest for anybody positioned to arrange.

`01_docs/USER_GUIDE.md` says an install that predates the feature "gains it
automatically: the first run that reaches the router records what it sees". That
is true and misleading, because the only run that reaches the router is one that
is about to restart it.

**Remedy.** Record the fingerprint on an ordinary successful run when none is
stored — cheap, on a LAN, and it happens while nothing is wrong — and keep the
comparison where it is. Then correct the guide.

## A-13. Correct the claim that `validate_config` requires `router.model`

**Severity: low.** `scripts/configure.py` refuses to write a configuration
without a router model and explains that "the monitor's own validator rejects a
configuration without one". It does not: `validate_config` requires
`paths.state_database` and `router.host` and nothing else by name. The live
configuration on the monitoring host has no `model` key at all and validates.

Either make the validator require it or stop saying it does. The comment is the
same class of small untruth as the F16 items.

## A-14. Make `install.sh` executable in the index

**Severity: low, but it is the first command in the guide.** `git ls-files -s`
records `deploy/debian/install.sh` as mode `100644`. `01_docs/DEBIAN.md` opens
with `sudo ./deploy/debian/install.sh`, which on a fresh clone fails with
Permission denied. Confirmed on a clean checkout inside the container.

`04_tests/test_deploy.py` cannot catch it: every invocation there is `bash
install.sh`, which works whatever the mode is.

**Remedy.** `git update-index --chmod=+x deploy/debian/install.sh`, and assert
in the suite that the file is executable as git records it.

## A-15. Set `StateDirectoryMode` and `LogsDirectoryMode` on both units

**Severity: medium, and confirmed by execution.** The installer creates
`/var/lib/zenwifi-monitor` and `/var/log/zenwifi-monitor` with
`install -d -m 0750`. Both units declare `StateDirectory=` and `LogsDirectory=`
and neither declares the matching `…Mode=`, so systemd applies its default 0755
every time a service starts.

Proved, not reasoned: the directories were set to 0750 by hand, the service was
started, and both came back 0755.

It compounds. SQLite creates `watchdog.sqlite3` with the default umask, so the
database is 0644 — observed. The directory is the only thing standing between a
local user and the router address, the whole run history and every event detail.
On a real Debian host that protection is removed by the first timer firing.

`04_tests/test_deploy.py` asserts the mode straight after the install, before
any service has run, so it passes. This is the same shape as the fifth review's
first finding: the check observes the wrong moment.

**Remedy.** `StateDirectoryMode=0750` and `LogsDirectoryMode=0750` in both units;
consider tightening the database file itself rather than relying on the
directory; and move the suite's assertion to after a service start.

## A-16. Do not enable the timers before credentials exist

**Severity: low.** `install.sh --install` ends with
`systemctl enable --now zenwifi-monitor.timer zenwifi-monitor-health.timer`,
and the guide's next step is `--set-credentials`. Between the two, every firing
of the watchdog fails with `243/CREDENTIALS`, because the unit declares three
`LoadCredentialEncrypted` entries that do not exist yet. Observed: the first
timer firing came 90 seconds after the install and failed exactly that way.

The failure itself is deliberate and documented — a declared credential that is
missing must not be run without. What is not deliberate is that the documented
order guarantees it, and a first-time operator meets a red unit before they have
done anything wrong.

**Remedy.** Enable the timers without `--now`, or leave enabling to a final step
once `--set-credentials` has run, and say in the guide which order avoids the
noise.

## A-06. Soak the Debian path under a real `systemd`

**Why.** The largest remaining gap in this project, and it is a gap in evidence
rather than in artefacts. No timer has ever fired on a real Debian host. The
install run substituted `systemctl`, because the environment used had no systemd
as process one. `pip install --require-hashes` is substituted in the installer
test, so only CI exercises it. `01_docs/DEBIAN.md` and the deploy suite's
docstring both say this; nothing closes it except doing it.

**Steps.** Install on a Debian machine with both gates closed. Leave it in
dry-run for a week. Read `journalctl` and the `runs` table. Confirm the timers
fire, the credentials decrypt, the bootstrap log lands in
`/var/log/zenwifi-monitor`, and the health timer reports. Only then consider
opening either gate.

**Done when.** The "not verified" paragraph in `01_docs/DEBIAN.md` can be
rewritten as verified, item by item, with what was observed.

### What was established on 3 August

Debian 12 in Docker with `systemd` 252 as process one, `--privileged` and
`--cgroupns=host`. The checkout was owned by an ordinary account, as the guide's
`sudo`-from-a-clone invocation implies.

Newly verified, none of it previously possible:

* The installer runs end to end under a real `systemd`. It created the service
  account, the directories, the environment and the units, and `systemctl enable
  --now` produced real symlinks under `timers.target.wants`.
* `pip install --require-hashes` actually installed the locked set — twenty-six
  distributions, hashes checked. Until now that control had only ever run in CI,
  never through the installer.
* **Both timers fired.** No timer had ever fired for this project before.
* F13 holds on a real install: `/opt/zenwifi-monitor/src` and every module are
  `root:root` 0644, `configure.py` is 0755 `root:root`, and
  `/etc/zenwifi-monitor/config.json` is 0640 `root:zenwifi`.
* Gate 1 is closed after a plain `--install`: the drop-in directory does not
  exist at all.
* F11 holds. With `$LOGS_DIRECTORY` set the bootstrap path resolves to
  `/var/log/zenwifi-monitor/bootstrap-errors.log`, and a real
  `FileNotFoundError` from a missing configuration was written there. Without
  it, the path falls back to a home directory the service account does not have
  — which is the defect F11 fixed, now observed rather than argued.
* F14 accepts what it should: given a directory owned by the running user with
  mode 0700, the watchdog passed the credential gate as the service account.
* Three monitoring runs and one health run completed as the service account
  against the installed environment, writing run rows and a healthy health row.

### What this could NOT establish, and why it matters

**`systemd`'s credential mechanism does not work in Docker at all.** Not a
subtlety: with `LoadCredentialEncrypted` the directory appeared as `root:root`
0700 and its contents were unreadable **even by root**, and `systemd` logged
`Failed to set up credentials: Protocol error` on every start.

The watchdog therefore refused to run, with F14's message. **That refusal is
Docker's fault and is not evidence against F14** — the same setup was unreadable
by root, which cannot be what `systemd` intends. Saying otherwise would be
exactly the confident wrong finding this project has learned to fear.

So the production credential route — the one thing on Debian that has no Windows
equivalent and no other coverage — remains unverified. Closing it needs a real
kernel and a real `systemd` outside a container: a Debian WSL distribution with
`systemd=true`, or hardware. Docker is not sufficient for this part of A-06 and
this document should stop implying that any Linux will do.

**Also still unverified:** a timer firing through to a successful run, since the
firings that happened failed on credentials; and the desktop notice, which is
A-07.

Three defects came out of this: A-14, A-15 and A-16.

## A-07. Verify the desktop notice against a real session bus

**Why.** `_posix_notice` has been exercised only against a substituted surface.
On a headless host it correctly reports that there was nobody to notify; what
has never been seen is it succeeding.

**Depends on A-06**, and is cheap once that host exists: run the health entry
point with `--notice` from a desktop session and confirm a notification appears.

## A-08. First real recovery verification

**Why.** Section 7 of the improvement plan. After a genuine outage and recovery,
confirm the five pieces of local evidence: the offline event, the restart event
and cooldown state, the restored event and its queued delivery, the recovery
notification shown exactly once, and the health monitor returning to healthy.

**Do not force an outage to produce this.** The plan says so and it is right: a
manufactured outage tests the code against a situation it was not written for,
and the value of this check is precisely that the situation was real.

**Partly observed, 29 July.** A real outage ran from 18:54:21 to 19:14:07 UTC:
four consecutive failed runs, then recovery. The monitor recorded `Monitoring
started` and `Restored`, both delivered to Notion, and the health monitor stayed
healthy throughout. **No restart was attempted, and that was correct**: the code
measures an outage from the first *observed* failure, so at its last chance —
the run at 19:09:21 — it had witnessed 14.9995 minutes against a threshold of
fifteen. It declined by three hundredths of a second, and the connection came
back fourteen seconds before the next run.

That is the design working, and it is worth writing down precisely because it
looks like a near miss. Wall-clock unavailability was up to twenty-five minutes,
since the outage began somewhere between the last successful run at 18:49 and
the first failure at 18:54. The monitor deliberately does not assume that
interval, which is the whole point of F7: it acts on what it saw, not on what it
can infer.

**Still needed:** an outage long enough to reach a restart, so the remaining
three items — the restart event and cooldown, the recovery notification shown
exactly once, and the outbox behaviour around a restart — are observed rather
than reasoned about. Still **blocked on an event**, and still not to be forced.

## A-09. Decide whether to close F6 with an off-host observer

**Why.** F6 is accepted, not fixed. The health monitor runs on the same host,
under the same scheduler, from the same install as the thing it observes, so a
fault that stops both leaves nobody to report it. That has happened once. The
acceptance is recorded as ADR-021 and is now stated in `01_docs/ARCHITECTURE.md`
and `00_admin/HANDOVER.md`.

**The only thing that would close it** is an observer outside this host, which
is also the only thing that would notice the machine being off. That is a new
moving part with its own credentials, its own failure modes and its own
maintenance, for a monitor whose whole design premise is local-first. It may
well not be worth it.

**This is a decision, not an implementation.** Recording the choice again, with
whatever has been learned since ADR-021, is the action. Reaffirming the
acceptance is a complete answer.

<!-- END-OF-FILE: NEXT_ACTIONS.md -->
