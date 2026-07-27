<!-- ZenWiFi Monitor version: 0.1.0 -->

# User guide

1. Run `Install.ps1 -InstallDependencies` to create the local Python runtime.
2. Run `Setup-LocalConfig.ps1`. Choose an absolute local data directory when prompted. The script creates a `logs` child directory and a local SQLite database path, then creates or updates the ignored `config.local.json` file. Choose whether Notion logging is enabled. The local SQLite log remains active in both cases.
3. For an earlier configuration, run `Migrate-LocalConfig.ps1` without switches to inspect the migration plan. Run it with `-WriteConfig` to back up and update the ignored local configuration. Add `-EnableNotion` only to retain or enable optional Notion logging. The migration never contacts the router or Notion and never activates restart execution.
4. Run `Setup-RouterConfig.ps1` to discover the default gateway. Review its output, then re-run it with `-WriteConfig` to update the local router host and model. HTTPS/TLS is required by default. Only if TLS is unavailable and cannot be repaired, run with `-WriteConfig -AllowInsecureHttp` and type the exact risk acknowledgement when prompted; this permits unencrypted router communication.
5. Run `Setup-Secrets.py` under the same Windows account as the scheduled task. Enter a value to update it; press Enter to retain an existing stored value. Choose the optional Notion-token prompt only when Notion is enabled.
6. Run QA and dry-run validation. Confirm the local SQLite `runs` table is updated and that no router restart is requested.
7. Verify read-only router authentication separately. Do not invoke any restart or configuration endpoint during this test.
8. If Notion is enabled, create a Notion internal integration, grant it access to the selected data source, then verify a minimized direct Notion write without router action.
9. Run `Install.ps1 -RegisterTask` only after the dry-run checks pass. Obtain a separate production deployment decision before enabling restart capability. Do not add `--execute` to the VBS wrapper during testing or dry-run.
10. To verify silent operation, run `wscript.exe scripts\RouterWatchdog.vbs` and confirm that no window appears and that the SQLite `runs` table gains one row. Add `--script=<path>` to launch another project entry point through the same silent wrapper.
11. Run `python scripts/check_versions.py` to confirm that every tracked file carries the current project version. It prints a per-file summary and exits 0 when green, 1 on drift and 2 when `VERSION` or Git is unavailable. Add `--json` for a machine-readable result.
12. If a run fails before SQLite opens, inspect `%LOCALAPPDATA%\ZenWiFiMonitor\bootstrap-errors.log`. The silent VBS wrapper does not display standard error output.
