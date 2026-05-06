Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw stock_monitor.py", 0, False
Set WshShell = Nothing
