<!-- ZenWiFi Monitor version: 0.1.0 -->

# Independent pre-publication review prompt

Use this prompt with an independent reviewer agent. Replace `<TARGET_COMMIT>`
with the exact commit supplied by the project owner before starting the review.

```text
Perform a read-only, independent pre-publication review of ZenWiFi Monitor at exact commit <TARGET_COMMIT>.

Purpose: decide whether this Windows 11, local-first ZenWiFi monitoring project is safe to publish as a public GitHub repository. The system monitors Internet reachability and can restart an ASUSWRT-compatible router only after explicit production activation; current deployment must remain dry-run.

Scope to inspect:
- Repository public-release hygiene: no secrets, private addresses, personal data, local paths, generated databases, logs, or credentials in tracked files or Git history.
- Licensing and attribution: project Apache-2.0 license, THIRD_PARTY_NOTICES.md, direct dependency licenses, copyright notices, and reference links.
- Safety controls: dry-run default, explicit execute boundary, no restart during tests, TLS default and explicit HTTP exception, reboot cooldown, persistent restart notification, and failure logging.
- Windows operation: VBS silent wrapper, Task Scheduler assumptions, same-user Windows Credential Manager access, local SQLite persistence, and local configuration migration.
- Optional Notion behaviour: local logging must work without Notion; no token or data source identifier must be logged.
- Documentation accuracy: README, setup scripts, architecture, handover, decisions, and changelog must match implementation.
- Dependency and test posture: pinned direct dependencies, reproducible setup instructions, and safe test coverage.

Mandatory constraints:
- Read-only review. Do not edit, commit, push, publish, install dependencies, alter Task Scheduler, write configuration, access Notion, or retrieve/display credentials.
- Do not use --execute. Do not invoke a router restart, router configuration change, or any state-changing network call.
- Safe static inspection and existing non-destructive tests are allowed only when they do not change router, Notion, configuration, or credentials.
- Treat any uncertainty, missing evidence, exposed personal/machine information, or unverified safety boundary as a blocker for publication.

Required report format:
1. Exact reviewed commit and review scope.
2. Findings ordered by severity: Blocker, High, Medium, Low. For each: evidence with file and line, impact, and a concrete remediation.
3. Explicit checks passed, with evidence.
4. Separate conclusions for (a) public GitHub publication and (b) local production activation.
5. Final verdict: READY, READY WITH CHANGES, or NOT READY.

Do not provide credentials, full local paths, database identifiers, tokens, router addresses, or configuration contents in the report. Use sanitized descriptions instead.
```
