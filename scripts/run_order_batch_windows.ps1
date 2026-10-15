param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$PythonBin,
    [Parameter(Mandatory = $true)][string]$ProcessorPath,
    [string]$CodexBin = "",
    [switch]$Reiniciar
)

$ErrorActionPreference = 'Stop'

function Initialize-ArgumentParser {
    if (-not ('MeuryBatch.NativeArguments' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace MeuryBatch {
    public static class NativeArguments {
        [DllImport("shell32.dll", SetLastError = true)]
        private static extern IntPtr CommandLineToArgvW(
            [MarshalAs(UnmanagedType.LPWStr)] string command, out int count);
        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr pointer);
        public static string[] Split(string command) {
            int count;
            IntPtr pointer = CommandLineToArgvW(command, out count);
            if (pointer == IntPtr.Zero) throw new System.ComponentModel.Win32Exception();
            try {
                string[] arguments = new string[count];
                for (int i = 0; i < count; i++)
                    arguments[i] = Marshal.PtrToStringUni(
                        Marshal.ReadIntPtr(pointer, i * IntPtr.Size));
                return arguments;
            } finally { LocalFree(pointer); }
        }
    }
}
'@
    }
}

function Test-BatchProcess($Process, [string]$Project, [string]$Processor) {
    if (-not $Process.CommandLine) { return $false }
    $parts = [MeuryBatch.NativeArguments]::Split($Process.CommandLine)
    # Only a direct Python invocation of this exact script and project qualifies.
    if ($parts.Count -lt 4 -or -not [IO.Path]::IsPathRooted($parts[1])) {
        return $false
    }
    if ([IO.Path]::GetFullPath($parts[1]) -ine $Processor) { return $false }
    for ($i = 2; $i -lt ($parts.Count - 1); $i++) {
        if ($parts[$i] -eq '--projeto') {
            return ([IO.Path]::IsPathRooted($parts[$i + 1]) -and
                [IO.Path]::GetFullPath($parts[$i + 1]).TrimEnd('\') -ieq $Project)
        }
    }
    return $false
}

function Get-BatchProcesses([string]$Project, [string]$Processor) {
    # Fail closed if Windows denies process inspection.
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
        Where-Object { Test-BatchProcess $_ $Project $Processor }
}

function Stop-BatchProcesses($Processes, [string]$Project, [string]$Processor) {
    $processIds = @($Processes | ForEach-Object { $_.ProcessId })
    $roots = @($Processes | Where-Object { $_.ParentProcessId -notin $processIds })
    foreach ($previous in $roots) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($previous.ProcessId)"
        if (-not $current) { continue }
        if ($current.CreationDate -ne $previous.CreationDate -or
            -not (Test-BatchProcess $current $Project $Processor)) {
            throw 'O processo mudou durante a verificacao. Reinicio cancelado.'
        }
        # Kill only the verified batch PID and its descendants, including Codex.
        & "$env:SystemRoot\System32\taskkill.exe" /PID $current.ProcessId /T /F | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw 'Nao foi possivel confirmar o encerramento de toda a arvore do lote anterior. Reinicio cancelado.'
        }
    }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while (@(Get-BatchProcesses $Project $Processor).Count -gt 0) {
        if ($timer.Elapsed.TotalSeconds -ge 10) {
            throw 'O lote anterior ainda esta ativo. Reinicio cancelado.'
        }
        Start-Sleep -Milliseconds 100
    }
}

function Enter-LaunchGate($Gate) {
    try {
        if (-not $Gate.WaitOne(30000)) {
            throw 'Outro inicializador esta verificando o lote. Tente novamente.'
        }
    } catch [Threading.AbandonedMutexException] {
        # Windows releases the gate when its owner exits abruptly.
    }
}

function Remove-OwnMarker([string]$Marker, [string]$Token, [string]$LockDirectory) {
    if (-not (Test-Path -LiteralPath $Marker)) { return }
    $owner = Get-Content -LiteralPath $Marker -Raw | ConvertFrom-Json
    if ($owner.token -eq $Token) {
        Remove-Item -LiteralPath $Marker
        # No recursive deletion: unknown files in the control directory survive.
        if ((Test-Path -LiteralPath $LockDirectory) -and
            @(Get-ChildItem -LiteralPath $LockDirectory -Force).Count -eq 0) {
            [IO.Directory]::Delete($LockDirectory)
        }
    }
}

function Invoke-OrderBatch {
    $project = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $processor = [IO.Path]::GetFullPath($ProcessorPath)
    $lockDirectory = Join-Path $project 'pedidos_pdf\.controle\em_execucao'
    # Outside the legacy directory: an older BAT may still remove that directory
    # after its worker is stopped, while the replacement is already starting.
    $marker = Join-Path (Split-Path $lockDirectory) 'windows_execution.json'
    $token = [Guid]::NewGuid().ToString('N')
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = [BitConverter]::ToString($hash.ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($project.ToUpperInvariant()))).Replace('-', '')
    } finally { $hash.Dispose() }
    $gate = New-Object Threading.Mutex($false, "Local\MeuryOrderBatch_$digest")
    $gateHeld = $false
    $worker = $null
    $result = 1
    try {
        Initialize-ArgumentParser
        Enter-LaunchGate $gate
        $gateHeld = $true
        $active = @(Get-BatchProcesses $project $processor)
        if ($active.Count -gt 0) {
            Write-Host "Ja existe um processamento em lote em andamento. PID(s): $($active.ProcessId -join ', ')"
            if (-not $Reiniciar) {
                Write-Host 'Deseja encerrar o lote anterior e reiniciar? [S/N]'
                $answer = Read-Host
                if ($answer -ine 'S') { return 2 }
            }
            Write-Host 'Encerrando o lote anterior e seus processos filhos...'
            Stop-BatchProcesses $active $project $processor
        } elseif (Test-Path -LiteralPath $lockDirectory) {
            Write-Host 'Trava antiga sem lote ativo encontrada. Liberando e iniciando novamente...'
        }

        [IO.Directory]::CreateDirectory($lockDirectory) | Out-Null
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = $PythonBin
        $start.Arguments = '"' + $processor + '" --projeto "' + $project + '" --codex "' + $CodexBin + '"'
        $start.WorkingDirectory = $project
        $start.UseShellExecute = $false
        @{
            token = $token
            startedAt = [DateTime]::UtcNow.ToString('o')
            project = $project
        } | ConvertTo-Json | Set-Content -LiteralPath $marker -Encoding UTF8
        $worker = [Diagnostics.Process]::Start($start)
        $gate.ReleaseMutex()
        $gateHeld = $false
        $worker.WaitForExit()
        $result = $worker.ExitCode
    } catch {
        Write-Host "ERRO: $($_.Exception.Message)"
    } finally {
        if ($worker -or (Test-Path -LiteralPath $marker)) {
            try {
                if (-not $gateHeld) {
                    Enter-LaunchGate $gate
                    $gateHeld = $true
                }
                Remove-OwnMarker $marker $token $lockDirectory
            } catch { Write-Host "AVISO ao liberar a trava: $($_.Exception.Message)" }
            if ($worker) { $worker.Dispose() }
        }
        if ($gateHeld) { $gate.ReleaseMutex() }
        $gate.Dispose()
    }
    return $result
}

if ($MyInvocation.InvocationName -ne '.') {
    exit (Invoke-OrderBatch)
}
