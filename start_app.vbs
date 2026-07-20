' COF Film App - silent launcher (double-click this file, no console window)
' Starts the App in background via pythonw and opens the browser automatically.
' Errors are shown as popup dialogs by silent_launch.py.
' For debugging with live terminal logs, use start_app.bat instead.

Option Explicit

Dim fso, shell, scriptDir, pythonw, launcher, cmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = "E:\ANACONDA\pythonw.exe"
launcher = scriptDir & "\silent_launch.py"

If Not fso.FileExists(pythonw) Then
    MsgBox "Python environment not found:" & vbCrLf & pythonw & vbCrLf & vbCrLf & _
           "Please make sure the Anaconda base environment exists (App needs gradio 6.20).", _
           vbCritical, "COF App launch failed"
    WScript.Quit 1
End If

If Not fso.FileExists(launcher) Then
    MsgBox "Launcher script not found:" & vbCrLf & launcher, vbCritical, "COF App launch failed"
    WScript.Quit 1
End If

' intWindowStyle = 0 hides the window; bWaitOnReturn = False returns immediately
cmd = """" & pythonw & """ """ & launcher & """"
shell.Run cmd, 0, False
