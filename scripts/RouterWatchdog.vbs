Set shell = CreateObject("Wscript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
executionArgument = ""
For Each argument In WScript.Arguments
  If LCase(argument) = "--execute" Then executionArgument = " --execute"
Next
shell.Run Chr(34) & root & "\..\.venv\Scripts\python.exe" & Chr(34) & " " & Chr(34) & root & "\..\src\watchdog.py" & Chr(34) & " --config " & Chr(34) & root & "\..\config.local.json" & Chr(34) & executionArgument, 0, False
