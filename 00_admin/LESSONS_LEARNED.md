<!-- ZenWiFi Monitor version: 0.1.0 -->

# Lessons learned

Seven independent review rounds, four verification exercises and one mutation
sweep produced these. Each one is here because it cost something — a wrong
diagnosis, a false claim in a commit message, a defect that survived rounds of
review — and each is stated so that the cost does not have to be paid again.

They are ordered by how much they have cost, not by when they were learned.

## 1. A correct function whose call site nobody checks

**Five instances in this project, and it is the single recurring failure mode.**

`check_router_identity` had thorough coverage — trust on first use, a changed
fingerprint, an unreachable endpoint — and nothing asserted it was on the
restart path. Deleting its only call site left every suite green.
`require_persistent_secret_store` and `validate_config` were the same:
exhaustively tested as functions, never checked to be called from `main()`,
and every watchdog test stubbed the first one out before calling `main()` at
all. Two health checks returned the right codes and were never wired through
`evaluate()`. F18, the sixth round's Blocker, was the same shape one level
down: two clauses of one rule, each defensible, computed over different sets.

The pattern is that attention goes to the interesting part. The function is
where the thinking happened, so the function is what gets tested; the line that
calls it is boring, so nobody looks at it. That is exactly why it is where the
defects live.

**Rule: pin the call, not only the callee.** When a behaviour matters, write one
test that drives the real entry point and asserts the behaviour happened —
substituting the callee with a recorder if necessary — in addition to the tests
of the callee itself.

## 2. A pin is worth what it catches, not what it asserts

Recorded as ADR-026 after the fourth round found tests confirming their own
fixtures. **The rule was then applied only forward** — to every change made
after it was written, and to nothing written before it — which the sixth round
demonstrated by mutating one query to the correct form and watching all nine
suites stay green. The rule was in the decision log while the code it protected
was unprotected.

A-17 swept all sixty-six safety-relevant behaviours and found five with no pin
at all. Then the seventh round found three more that the sweep had not thought
to list, and showed that two rows of the sweep's own report overclaimed.

**Rule: revert the behaviour and observe the test fail, every time. And when a
rule like this is adopted, sweep what already exists — a rule applied only to
new work leaves the old work exactly as it was.**

Two corollaries, both paid for:

- **A mutation that breaks the code proves nothing.** A reversion that produces
  a syntax error, an unbound name or a SQL parameter mismatch fails for the
  wrong reason. Check that the mutant still runs.
- **A mutation that changes nothing is a finding about the code.** Two
  reversions in the sweep were equivalent mutants. One revealed a redundant
  guard; the other revealed two parameters nothing consults. Both are now stated
  in the code, so the next reviewer does not have to rediscover them.

## 3. A check that reads source text finds its own explanatory comment

Produced three times in one day. A scan for `--upgrade pip` matched the comment
saying why it had been removed. A scan for `open(` matches `urlopen(`. A scan
for `--execute` matched prose in a PowerShell help block.

**Rule: check the parsed or executed artefact.** Python through its abstract
syntax tree, PowerShell through `Parser::ParseFile`, unit files through
`systemd-analyze` or a directive parser, an installer by running it and
observing the resulting owners and modes. Where a source-text check is genuinely
the only option, strip comments first and say in the test why the weaker check
is being used.

## 4. Documentation that overstates the code is a defect, not a nicety

The changelog once claimed a finding was closed when it was not. ADR-023
asserted that history could not satisfy a count taken over a recent window,
which the implementation did not deliver — and that gap **was** F18. Four
documents later stated a *weaker* duration rule than the code implements, which
was more dangerous than it sounds: someone correcting the code to match the
documents would have reintroduced a restart on evidence gathered before a
monitoring gap.

**Rule: when a document and the code disagree, decide which is right before
changing either, and correct the record in place with the correction stated
rather than quietly rewriting it.** ADR-023 and ADR-029, and ADR-027's
correction from "decisions" to "attempts", are the model.

## 5. Ask the observer that will actually be running

On this machine, a shell reaching in through an assistant integration and the
Windows scheduled tasks resolve `%LOCALAPPDATA%` to **different files** — on 3
August, 3458 bytes from 27 July for the task against 29661 bytes from 28 July
for the shell, at one path. That cost four wrong hypotheses about a scheduler
result in July, and most of an investigation in August in which a correct health
check was written up as a high-severity defect on the strength of what the wrong
observer could see.

**Rule: to learn what a scheduled task sees, ask a scheduled task.** Register a
temporary one that stats or copies the file, read its output, delete it. Never
alter the project's own jobs to find something out.

The general form: before trusting an observation, ask which process made it and
whether that process shares the namespace, user, working directory and clock of
the one under investigation.

## 6. Say what was measured, separately from what was inferred

Three claims in this project were nearly reported as findings and were wrong:
that the SQLite database was world-readable (read from a manual `runuser` call
where the unit's `UMask` does not apply); that `systemd`'s credential mechanism
had failed (it does not work in Docker at all, which is Docker's constraint and
not evidence against the code); and a decoding failure in the launcher suite
that could not be reproduced on the target host, where that path emits no bytes
at all.

**Rule: do not report the worst case an observation is compatible with.** State
the measurement, then the inference, and label which is which. A confident wrong
finding costs more than a missed one in a project that acts on findings.

## 7. A command that reports success is not evidence the effect happened

`git checkout -- <file>` exited zero and wrote nothing, because the working-tree
content normalised to the same blob the index held. A commit message claimed the
line endings had been restored. They had not, and `git status` agreed with the
false claim.

**Rule: check the artefact, not the exit code.** Size, hash, mode, content —
whatever the change was supposed to produce.

## 8. A stale working copy silently reverts fixes

Work happens in a cloud sandbox against a snapshot of the repository. A snapshot
taken before a device-only fix will, when delivered, undo that fix — which
happened once with a machine name that had been removed from a document, and was
caught only by reading the whole diff rather than the lines that were meant to
change.

**Rule: re-snapshot from the authoritative repository before each batch, verify
by hash, and read the entire diff on the device before committing.** File
transfer has also returned stale content while reporting success, which is the
same lesson from the other direction.

## 9. Bookkeeping is skipped exactly when it feels like bookkeeping

Three changes — a platform soak and two defect fixes — landed with no changelog
entry and had to be caught up afterwards. Two decision records were appended
with the two characters `\n` instead of newlines, so both rendered inside a
third record's table cell and the file ended without a trailing newline. That
survived a publication, a mirror, a leak scan and an independent review, because
every one of those reads content and none of them reads shape.

**Rule: the changelog, the decision log and the plan are part of the change, not
after it. And gate the shape of a structured document, not only its words.**

## 10. Publish through a scan that assumes you will leak something

A machine name reached a handover document and a sandbox path reached a review
report. Both were caught by the leak scan before the public mirror was updated,
which is the only reason neither was published.

**Rule: publish only through a history-free mirror with a gating scan, and
maintain the scan's list of deliberate hits** — this project's are a TEST-NET-1
address, an impossible drive letter used as a test fixture, and the author's own
name in the licence. Note that a naïve pattern for the machine name matches the
word "compliance" in the Apache licence text; anchor it.

## 11. Verification produces more findings than construction

Every planned item was implemented before any of this started. What has produced
defects since is checking: the deployment verification turned up four items
nobody had proposed, the platform soak two more, and the last two review rounds
one Blocker and seven further findings — including one that had survived every
round since the behaviour it concerns was written.

**Rule: budget for verification as work in its own right, and keep going after
"everything is implemented". The rounds are not a formality that ends; each one
so far has found something the previous round's fixes introduced or missed.**

<!-- END-OF-FILE: LESSONS_LEARNED.md -->
