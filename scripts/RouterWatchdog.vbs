' ZenWiFi Monitor version: 0.1.0
' Silent launcher for every ZenWiFi Monitor scheduled job.
' Window mode 0 keeps the run invisible and bWaitOnReturn False keeps it
' non-blocking, so a scheduled job raises no UI surface and steals no focus.
' Every scheduled task shall be registered through this wrapper.
' Optional arguments:
'   --script=<one of the allowed entry points>     default: src\watchdog.py
'   --execute                                      forwarded to the target script
' The allowed entry points are exactly src\watchdog.py and src\health.py.
' Any other --script= value, and any argument not listed above, is refused
' with exit code 2 so Task Scheduler records the refusal.
' Only the project's own entry points may be launched, so the wrapper cannot be
' turned into a general silent runner for an arbitrary file.
'
' The interpreter is looked for in the runtime location first and in the
' project tree second. The runtime location is preferred because a virtual
' environment inside a cloud-synced project directory is not durable: a sync
' or hygiene routine can move it away, after which Run would silently launch
' nothing and Task Scheduler would still report success.
'   1. %LOCALAPPDATA%\ZenWiFiMonitor\.venv\Scripts\python.exe
'   2. <project root>\.venv\Scripts\python.exe
' Exit codes: 2 refused argument, 3 no interpreter found, 4 target script
' missing. Nothing is launched in any of those cases, and every refusal is
' also written to the bootstrap error log, because an exit code alone is
' invisible unless somebody happens to read the task history.
Option Explicit
Dim shell, fso, scriptDirectory, projectRoot, targetScript, executionArgument
Dim argument, commandLine, candidate, allowed, interpreter, runtimeInterpreter
Dim projectInterpreter, targetPath, configPath, runtimeDirectory
Set shell = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetParentFolderName(scriptDirectory)

Function LocalAppData()
  ' Resolve the per-user local data directory without trusting a single
  ' mechanism. Under Task Scheduler the process environment can lack
  ' LOCALAPPDATA, in which case ExpandEnvironmentStrings returns the literal
  ' "%LOCALAPPDATA%" and every path built from it silently misses. An empty
  ' result is returned rather than a wrong one, so the caller can react.
  Dim value
  value = ""
  On Error Resume Next
  value = shell.Environment("Process")("LOCALAPPDATA")
  If value = "" Then value = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
  If value = "" Or InStr(value, "%") > 0 Then
    value = shell.Environment("Process")("USERPROFILE")
    If value = "" Then value = shell.ExpandEnvironmentStrings("%USERPROFILE%")
    If value = "" Or InStr(value, "%") > 0 Then
      value = ""
    Else
      value = value & "\AppData\Local"
    End If
  End If
  On Error Goto 0
  LocalAppData = value
End Function

Sub Refuse(code, reason)
  Dim logDirectory, logFile, stream, base
  On Error Resume Next
  base = LocalAppData()
  ' A refusal that cannot be written where the Python code writes is still
  ' worth recording, so fall back to the project tree rather than losing it.
  If base = "" Then base = projectRoot
  logDirectory = base & "\ZenWiFiMonitor"
  If Not fso.FolderExists(logDirectory) Then fso.CreateFolder(logDirectory)
  logFile = logDirectory & "\bootstrap-errors.log"
  Set stream = fso.OpenTextFile(logFile, 8, True)
  stream.WriteLine Year(Now) & "-" & Right("0" & Month(Now), 2) & "-" & _
    Right("0" & Day(Now), 2) & " " & Right("0" & Hour(Now), 2) & ":" & _
    Right("0" & Minute(Now), 2) & ":" & Right("0" & Second(Now), 2) & _
    " launcher refused with code " & code & ": " & reason
  stream.Close
  On Error Goto 0
  WScript.Quit code
End Sub

targetScript = "src\watchdog.py"
executionArgument = ""
For Each argument In WScript.Arguments
  If LCase(argument) = "--execute" Then
    executionArgument = " --execute"
  ElseIf LCase(Left(argument, 9)) = "--script=" Then
    candidate = LCase(Replace(Mid(argument, 10), "/", "\"))
    allowed = "|src\watchdog.py|src\health.py|"
    If InStr(allowed, "|" & candidate & "|") = 0 Then
      Refuse 2, "the entry point " & Chr(34) & candidate & Chr(34) & _
        " is not one of the allowed entry points"
    End If
    targetScript = candidate
  Else
    ' An argument the wrapper does not recognise is refused rather than
    ' ignored, so a typo such as -script= cannot silently launch the default
    ' job with an operator believing a different one was started.
    Refuse 2, "the argument " & Chr(34) & argument & Chr(34) & " is not recognised"
  End If
Next

runtimeDirectory = LocalAppData()
If runtimeDirectory = "" Then
  runtimeInterpreter = ""
Else
  runtimeInterpreter = runtimeDirectory & "\ZenWiFiMonitor\.venv\Scripts\python.exe"
End If
projectInterpreter = projectRoot & "\.venv\Scripts\python.exe"
If runtimeInterpreter <> "" And fso.FileExists(runtimeInterpreter) Then
  interpreter = runtimeInterpreter
ElseIf fso.FileExists(projectInterpreter) Then
  interpreter = projectInterpreter
Else
  ' Refuse rather than call Run on a path that does not exist. Run reports no
  ' error in window mode 0 without waiting, so launching a missing interpreter
  ' would leave the job silently dead with a successful task result.
  Refuse 3, "no interpreter at " & Chr(34) & runtimeInterpreter & Chr(34) & _
    " nor at " & Chr(34) & projectInterpreter & Chr(34) & _
    "; local data directory resolved to " & Chr(34) & runtimeDirectory & Chr(34)
End If

targetPath = projectRoot & "\" & targetScript
If Not fso.FileExists(targetPath) Then
  Refuse 4, "the entry point is missing at " & Chr(34) & targetPath & Chr(34)
End If

configPath = projectRoot & "\config.local.json"
commandLine = Chr(34) & interpreter & Chr(34) & " " & _
  Chr(34) & targetPath & Chr(34) & _
  " --config " & Chr(34) & configPath & Chr(34) & executionArgument
shell.Run commandLine, 0, False
