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
Dim shell, fso, root, targetScript, executionArgument, argument, commandLine, candidate, allowed
Set shell = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
targetScript = "src\watchdog.py"
executionArgument = ""
For Each argument In WScript.Arguments
  If LCase(argument) = "--execute" Then
    executionArgument = " --execute"
  ElseIf LCase(Left(argument, 9)) = "--script=" Then
    candidate = LCase(Replace(Mid(argument, 10), "/", "\"))
    allowed = "|src\watchdog.py|src\health.py|"
    If InStr(allowed, "|" & candidate & "|") = 0 Then
      ' Exit non-zero so Task Scheduler records the refusal rather than the
      ' wrapper silently launching nothing.
      WScript.Quit 2
    End If
    targetScript = candidate
  Else
    ' An argument the wrapper does not recognise is refused rather than
    ' ignored, so a typo such as -script= cannot silently launch the default
    ' job with an operator believing a different one was started.
    WScript.Quit 2
  End If
Next
commandLine = Chr(34) & root & "\..\.venv\Scripts\python.exe" & Chr(34) & " " & _
  Chr(34) & root & "\..\" & targetScript & Chr(34) & _
  " --config " & Chr(34) & root & "\..\config.local.json" & Chr(34) & executionArgument
shell.Run commandLine, 0, False
