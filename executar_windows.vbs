Option Explicit

Dim shell, fso, pasta, script, comando, codigo, log

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

pasta = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(pasta, "executar_windows.bat")
log = fso.BuildPath(pasta, "executar_windows.log")

If Not fso.FileExists(script) Then
    MsgBox "Nao foi encontrado:" & vbCrLf & script, vbCritical, "Organizador de Estampas"
    WScript.Quit 1
End If

shell.CurrentDirectory = pasta
comando = "cmd.exe /d /c ""set ORGANIZADOR_SEM_TERMINAL=1&& call """ & script & """ > """ & log & """ 2>&1"""
codigo = shell.Run(comando, 0, True)

If codigo <> 0 Then
    MsgBox "O aplicativo foi encerrado com erro." & vbCrLf & _
           "Consulte o arquivo:" & vbCrLf & log, _
           vbCritical, "Organizador de Estampas"
End If
