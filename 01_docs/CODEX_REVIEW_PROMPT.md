<!-- ZenWiFi Monitor version: 0.1.0 -->

# Independent review prompt for Codex

A second reviewer prompt, alongside `01_docs/INDEPENDENT_REVIEW_PROMPT.md`. That
one is written for a reviewer with the repository already in front of it; this
one is written to be pasted into an agent that starts from nothing and has to
fetch the code itself.

Replace `<TARGET_COMMIT>` with the exact commit before starting. The last
published commit that changed behaviour is `90c7af4`, and the last that changed
tests is `a6df698`; review `a6df698` unless something has landed since. Naming a
moving head here would be a claim that goes stale the moment this file is
committed.

Give the reviewer the commit and nothing else. It is not told what the changes
were meant to achieve, why any decision was taken, or what previous rounds
found, because a reviewer told the reasoning tends to check the reasoning
instead of the code. It is told which claims the range makes, so it has
something falsifiable to aim at.

---

```text
Perform an independent, read-only review of the ZenWiFi Monitor repository at
https://github.com/hagdahl/zenwifi-monitor, at exactly commit <TARGET_COMMIT>.
Clone it, check that commit out, and review that state. Do not review main if it
has moved.

WHAT THE PROJECT IS

A local-first Internet watchdog that can restart an ASUSWRT router. It runs
every five minutes from Windows Task Scheduler on the author's machine, and
carries a Debian deployment path using systemd services and timers that has
been installed under a real systemd exactly once, in a container, and never on
hardware.

The safety model is two independent gates. A router restart requires BOTH
`--execute` on the invocation AND `execution_mode: "execute"` in the local
configuration. The same two gates govern delivery to a Notion database. Nine
test suites live in 04_tests/ and are run with `python 04_tests/<name>.py`; they
need no framework and no network.

WHAT THIS COMMIT RANGE CLAIMS

Judge these claims. Do not take any of them on trust, and do not confine
yourself to them.

1. A restart requires observed evidence, not elapsed time: the oldest failed
   run of the current outage must be at least
   `monitor.failure_minutes_before_reboot` old AND at least
   `monitor.required_failed_runs` failed runs must fall inside
   `monitor.failure_window_minutes`.
2. Restarts are bounded: at most `monitor.max_restarts_per_window` decisions
   within `monitor.restart_window_hours`, counted since connectivity was last
   seen, after which the monitor records a warning and stops trying.
3. The router's TLS certificate fingerprint is recorded and compared before a
   restart, and a change is reported without stopping the run.
4. Neither activation gate can be opened by accident: a default Debian install
   leaves no `--execute` drop-in and a `dry-run` configuration, and
   `install.sh --disable-execution` closes gate one again.
5. Credentials come only from the operating system's protected store — Windows
   Credential Manager, a desktop Secret Service, or systemd's encrypted
   credentials — and the code fails closed on any other store.
6. The installed Debian code is owned by root and not writable by the account
   that ran the install.
7. Every test in 04_tests/ fails if the behaviour it names is removed.
8. A restart requires evidence from the CURRENT outage: both the elapsed
   duration and the failed-run count are taken from runs after the most recent
   successful one, and where no successful run has ever been recorded the
   recency window is the whole of the constraint.
9. Of sixty-six safety-relevant behaviours, sixty-four fail at least one suite
   when reverted. The two that do not are documented in
   01_docs/PIN_SWEEP_A17.md with the reason, and neither reason is "no test was
   written". Judge that document as a claim, not as evidence: rerun the
   reversions it lists that interest you most, especially the ones it says
   changed nothing at all.

WHAT TO LOOK FOR, roughly in order of how much it would cost to be wrong

1. Anything that could cause a router restart that the evidence does not
   support, or prevent one that it does.
2. Anything that could expose a credential, or weaken either gate.
3. Tests that cannot fail, assert their own fixture, or promise more in their
   name than their assertion delivers. Three previous rounds found several, and
   the most recent found that a whole class of them had never been checked, so
   treat every test as suspect until you have made it fail. The pins added since
   are the ones most worth attacking, because they are the newest and were
   written by the same hand that wrote the code.
4. Conditions that look independent and share a domain. The last round's Blocker
   was two clauses of one rule computed over different sets. Look for others.
5. Behaviour that is tested as a function and never checked to be *called*. One
   was found this way; the join between a correct function and the path that
   should reach it is where this project keeps failing.
6. Checks that observe the wrong moment — after the state they were meant to
   catch has already been overwritten, or before the thing that changes it has
   run. This project has produced that defect three separate times.
7. Checks implemented by searching source text, which match their own
   explanatory comments. Also produced three times.
8. Concurrency: two scheduled processes share one SQLite database and one
   exclusive run lease.
9. Documentation, comments and commit messages that assert behaviour the code
   does not have. Count this as a real finding, not a nicety.
10. Anything unsafe to publish: secrets, private addresses, personal data, local
    paths, machine names.

HOW TO WORK

Read the code. Run the suites; they are safe and touch no real machine state,
with one known exception you should find rather than be told. You may modify
files in your clone to test whether a test actually catches a reverted
behaviour — that is the most useful thing you can do here — as long as you
restore them and say what you did.

04_tests/test_deploy.py runs the real Debian installer inside a throwaway mount
namespace and needs root plus `unshare`. If you have them, let it run; check its
claim that nothing escapes the namespace rather than believing it.

MANDATORY CONSTRAINTS

- Read-only with respect to anything outside your own clone and a scratch
  directory. Do not commit, push, open issues, or publish.
- Never pass `--execute` to anything. Do not make a router call, a Notion call,
  or any state-changing network request.
- Do not print credentials, real local paths, host names, tokens or router
  addresses in your report.

BEFORE YOU REPORT ANYTHING

Try to refute each finding yourself. Write the strongest case that it is wrong
or already handled, and drop it if that case holds. For each finding you keep,
say whether you CONFIRMED it by executing something or whether it is a reading
of the code you could not execute, and say which. A confident wrong finding
costs this project more than a missed one, because it has been trained to act
on findings.

REPORT

1. The commit you reviewed and what you actually did — files read, suites run,
   experiments performed.
2. Findings, ordered Blocker / High / Medium / Low. For each: file and line,
   what is wrong, the concrete failure it produces, whether you confirmed it by
   execution, and a specific remediation.
3. The nine claims above, each marked upheld or not upheld, with the evidence
   that decided it.
4. What you checked and found sound — briefly, but specifically enough that a
   reader can tell what was actually covered.
5. What you could not establish, and why.
6. A verdict: READY, READY WITH CHANGES, or NOT READY, separately for public
   publication and for production use.

Write in prose. If you find nothing serious, say so plainly rather than
inflating minor observations into findings.
```

<!-- END-OF-FILE: CODEX_REVIEW_PROMPT.md -->
