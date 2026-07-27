<!-- ZenWiFi Monitor version: 0.1.0 -->

# Public GitHub publication

The source repository's local Git history is private operational history and must never be pushed. Public release uses a separate, history-free `_public` staging tree.

1. Complete the independent review and address required changes.
2. Ensure the source repository is clean and run `Prepare-PublicStaging.ps1` without switches.
3. Run `Prepare-PublicStaging.ps1 -WriteStaging`. It refuses to overwrite an existing `_public` directory and copies only tracked source files.
4. Independently review `_public`, including a secret and environment-data scan.
5. In `_public`, initialize a new Git repository and configure the intended public commit identity before the first commit. Use a GitHub noreply address if the personal email address must not be public.
6. Create exactly one initial commit, verify that the new repository has only that commit, then add the public GitHub remote and push.
7. Enable GitHub private vulnerability reporting and update `SECURITY.md` with its resulting link before publishing.

Never copy or push the source `.git` directory. Never use `_public` for local configuration, logs, databases, credentials, or router testing.
