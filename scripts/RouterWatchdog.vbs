Set shell = CreateObject("Wscript.Shell")
root = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run Chr(34) & root & "\..\.venv\Scripts\python.exe" & Chr(34) & " " & Chr(34) & root & "\..\src\watchdog.py" & Chr(34) & " --config " & Chr(34) & root & "\..\config.local.json" & Chr(34), 0, False
