' ZenWiFi Monitor version: 0.1.0
' Silent launcher for every ZenWiFi Monitor scheduled job.
' Window mode 0 keeps the run invisible and bWaitOnReturn False keeps it
' non-blocking, so a scheduled job raises no UI surface and steals no focus.
' Every scheduled task shall be registered through this wrapper.
' Optional arguments:
'   --script=<path relative to the project root>   default: src\watchdog.py
'   --execute                                      forwarded to the target script
Dim shell, fso, root, targetScript, executionArgument, argument, commandLine
Set shell = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
targetScript = "src\watchdog.py"
executionArgument = ""
For Each argument In WScript.Arguments
  If LCase(argument) = "--execute" Then
    executionArgument = " --execute"
  ElseIf LCase(Left(argument, 9)) = "--script=" Then
    targetScript = Mid(argument, 10)
  End If
Next
commandLine = Chr(34) & root & "\..\.venv\Scripts\python.exe" & Chr(34) & " " & _
  Chr(34) & root & "\..\" & targetScript & Chr(34) & _
  " --config " & Chr(34) & root & "\..\config.local.json" & Chr(34) & executionArgument
shell.Run commandLine, 0, False
