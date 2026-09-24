' MiyoQian: launch the watchdog with NO visible console window.
'
' Why this wrapper exists:
'   Task Scheduler starting powershell.exe directly always ends up with a visible
'   console window. powershell.exe is a console-subsystem program, so Windows
'   creates a console for it; "-WindowStyle Hidden" only hides the PowerShell host
'   window, and the processes it then starts (uv.exe / python.exe) are console
'   programs too -- they inherit that console and make the window show up again.
'
'   wscript.exe is a GUI-subsystem host, so running the command with window style 0
'   (SW_HIDE) never creates a visible console in the first place.
'
' This file is intentionally pure ASCII: Windows PowerShell 5.1 decodes BOM-less
' script files using the ANSI code page, and the project path may contain non-ASCII
' characters. The path is taken from WScript.ScriptFullName, which is Unicode-safe.

Option Explicit

Dim fso, shell, baseDir, ps1Path, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1Path = fso.BuildPath(baseDir, "start-miyoqian.ps1")

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1Path & """"
shell.Run command, 0, False
