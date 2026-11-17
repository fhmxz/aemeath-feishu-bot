' 无窗口后台启动 aemeath.py。
' 如果 python 不在 PATH 里，把下面这行改成 python.exe 的完整路径，
' 例如：PYTHON = "C:\Python311\pythonw.exe"
PYTHON = "pythonw.exe"

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base

scriptName = "aemeath.py"
If WScript.Arguments.Count > 0 Then scriptName = WScript.Arguments(0)

sh.Run """" & PYTHON & """ """ & base & "\" & scriptName & """", 0, False
