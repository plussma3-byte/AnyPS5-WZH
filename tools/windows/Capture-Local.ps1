#requires -Version 7.4
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$GameDirectory,
    [ValidateRange(10,600)][int]$Seconds = 120,
    [switch]$Validation,
    [ValidatePattern('^[0-9A-Fa-f]+$')][string]$ShaderAddress
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'Windows is required.' }
$game = (Resolve-Path -LiteralPath $GameDirectory).Path
$exe = Join-Path $game 'app.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Expected a locally converted and reviewed app.exe.' }
$run = Join-Path $game ('logs\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $run -Force | Out-Null
$timing = Join-Path $game 'frame-timing.log'
if (Test-Path -LiteralPath $timing) { Move-Item -LiteralPath $timing -Destination "$run\previous-frame-timing.log" }
$settings = @{ANYPS5_GPU='RTX 3060'; APS5_TRACE_MEMORY='1'; APS5_PROFILE_DRAW='1'}
if ($ShaderAddress) { $settings.APS5_DUMP_IR = $ShaderAddress }
if ($Validation) {
    $settings.VK_INSTANCE_LAYERS = 'VK_LAYER_KHRONOS_validation'
    $settings.VK_LAYER_SETTINGS_PATH = $run
    @('khronos_validation.debug_action = VK_DBG_LAYER_ACTION_LOG_MSG',
      'khronos_validation.report_flags = error,warn',
      'khronos_validation.log_filename = stdout') | Set-Content "$run\vk_layer_settings.txt"
}
$saved = @{}
foreach ($key in $settings.Keys) { $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process'); [Environment]::SetEnvironmentVariable($key, $settings[$key], 'Process') }
try {
    $nvidia = @("$env:WINDIR\System32\nvidia-smi.exe", "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    'elapsed_seconds,index,name,gpu_utilization_percent,vram_used_MiB,vram_total_MiB' | Set-Content "$run\gpu.csv"
    $logical = [Environment]::ProcessorCount
    [pscustomobject]@{ExecutableSHA256=(Get-FileHash -LiteralPath $exe).Hash; LogicalProcessors=$logical; CaptureSeconds=$Seconds; ValidationRequested=[bool]$Validation; GpuSamplingAvailable=[bool]$nvidia; GpuCountersScope='whole GPU, not game-only'; TimingEndpoint='flip_complete, not confirmed display'} | ConvertTo-Json | Set-Content "$run\run.json"
    $process = Start-Process -FilePath $exe -WorkingDirectory $game -PassThru -RedirectStandardOutput "$run\stdout.log" -RedirectStandardError "$run\stderr.log"
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $previousTime = 0.0
    $previousCpu = 0.0
    while (-not $process.HasExited -and $timer.Elapsed.TotalSeconds -lt $Seconds) {
        Start-Sleep -Seconds 1
        $process.Refresh()
        if ($process.HasExited) { break }
        $now = $timer.Elapsed.TotalSeconds
        $cpu = $process.TotalProcessorTime.TotalSeconds
        $free = (Get-CimInstance -ClassName Win32_OperatingSystem -Property FreePhysicalMemory).FreePhysicalMemory * 1KB
        [pscustomobject]@{ElapsedSeconds=$now; CpuPercentOfMachine=(100 * ($cpu-$previousCpu) / (($now-$previousTime)*$logical)); WorkingSetBytes=$process.WorkingSet64; PrivateBytes=$process.PrivateMemorySize64; SystemAvailablePhysicalBytes=$free} | Export-Csv -NoTypeInformation -Append -Path "$run\process.csv"
        $previousCpu=$cpu; $previousTime=$now
        if ($nvidia) {
            & $nvidia '--query-gpu=index,name,utilization.gpu,memory.used,memory.total' '--format=csv,noheader,nounits' 2>> "$run\gpu-errors.log" | ForEach-Object { "$now,$($_)" } | Add-Content "$run\gpu.csv"
        }
        $process.Refresh()
    }
    if ($process.HasExited) {
        $process.WaitForExit()
        $process.ExitCode | Set-Content "$run\exit-code.txt"
        if (Test-Path -LiteralPath $timing) { Copy-Item -LiteralPath $timing -Destination "$run\frame-timing.log" }
    } else {
        'Sampling ended; the game is still running. Close it normally, then copy frame-timing.log here. No exit code was captured.' | Set-Content "$run\still-running.txt"
    }
    Write-Output "Local logs: $run. No data was uploaded. Review logs before sharing."
} finally {
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
}
