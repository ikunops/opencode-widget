' 启动Go用量面板.vbs — 无痕启动 opencode-widget (data_server.py + Electron)
' 双击本文件启动, 不弹出任何控制台窗口。
' 已带防重复逻辑: data_server / Electron 已在运行时不会重复拉起。

Option Explicit

Dim fso, ws, wmi, baseDir, pythonw, electronExe, procs, q, code
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws  = CreateObject("WScript.Shell")
Set wmi = GetObject("winmgmts:\\.\root\cimv2")

baseDir    = fso.GetParentFolderName(WScript.ScriptFullName) & "\"
pythonw    = ws.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python311\pythonw.exe"
electronExe = baseDir & "electron\node_modules\electron\dist\electron.exe"

' 1) data_server: 未运行时启动 (pythonw 无控制台窗口)
Set procs = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='pythonw.exe' AND CommandLine LIKE '%data_server.py%'")
If procs.Count = 0 Then
    ws.Run """" & pythonw & """ """ & baseDir & "data_server.py""", 0, False
End If

' 2) Electron: 未运行时启动, 先等 data_server 绑定端口
Set procs = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='electron.exe' AND CommandLine LIKE '%opencode-widget%'")
If procs.Count = 0 Then
    WScript.Sleep 1200
    ws.Run """" & electronExe & """ """ & baseDir & "electron""", 1, False
End If
