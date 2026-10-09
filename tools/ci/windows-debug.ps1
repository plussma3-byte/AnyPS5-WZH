[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('Dependencies','Configure','Build','Libraries','Test','Package')][string]$Phase)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
if (-not $IsWindows) { throw 'This entry point requires a Windows runner.' }
$build = Join-Path $PWD $env:BUILD_DIR
$logs = Join-Path $PWD $env:LOG_DIR
New-Item -ItemType Directory -Force -Path $logs | Out-Null
function Invoke-Checked([string]$Program, [string[]]$Arguments, [string]$Name) {
    & $Program @Arguments 2>&1 | Tee-Object -FilePath (Join-Path $logs $Name)
    $code = $LASTEXITCODE
    if ($code -ne 0) { throw "$Program exited with code $code; see $Name" }
}
if ($Phase -eq 'Dependencies') {
    $sourceCommit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $sourceCommit -ne $env:GITHUB_SHA) { throw 'Checked-out source differs from the approved run commit.' }
    $dependencies = Get-Content -Raw tools/ci/windows-dependencies.json | ConvertFrom-Json
    $tools = Join-Path $env:RUNNER_TEMP 'anyps5-tools'
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    foreach ($name in 'winlibs','ffmpeg') {
        $dependency = $dependencies.$name
        if ($dependency.sha256 -notmatch '^[0-9a-f]{64}$' -or $dependency.url -notlike 'https://github.com/*') { throw 'Invalid dependency lock entry.' }
        $archive = Join-Path $tools "$name.zip"
        Invoke-WebRequest -Uri $dependency.url -OutFile $archive
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $dependency.sha256) { throw "SHA256 mismatch: $name" }
        "$name $($dependency.version) $actual" | Add-Content "$logs/downloads.log"
        Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $tools $name)
        Remove-Item -LiteralPath $archive
    }
    $bin = Join-Path $tools 'winlibs/mingw64/bin'
    foreach ($program in 'gcc.exe','g++.exe','cmake.exe','ctest.exe','ninja.exe','objdump.exe') {
        if (-not (Test-Path -LiteralPath (Join-Path $bin $program))) { throw "Pinned WinLibs bundle is missing $program; do not fall back to runner tools." }
    }
    $env:PATH = "$bin;$env:PATH"
    if ((& "$bin/g++.exe" -dumpfullversion).Trim() -ne '15.2.0' -or $LASTEXITCODE -ne 0) { throw 'Unexpected GCC version.' }
    if ((& "$bin/g++.exe" -dumpmachine).Trim() -ne 'x86_64-w64-mingw32' -or $LASTEXITCODE -ne 0) { throw 'Unexpected GCC architecture.' }
    $ffmpegCommit = (& git -C 3rdparty/ffmpeg-core rev-parse --short=12 HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $ffmpegCommit -ne $dependencies.ffmpeg.version) { throw 'FFmpeg source/package revision mismatch.' }
    $bin | Add-Content $env:GITHUB_PATH
    "ANYPS5_WINLIBS_BIN=$bin" | Add-Content $env:GITHUB_ENV
    "ANYPS5_FFMPEG_DIR=$(Join-Path $tools 'ffmpeg')" | Add-Content $env:GITHUB_ENV
    Invoke-Checked "$bin/g++.exe" @('-v') 'compiler.log'
    Invoke-Checked "$bin/cmake.exe" @('--version') 'cmake-version.log'
    Invoke-Checked "$bin/ninja.exe" @('--version') 'ninja-version.log'
    Invoke-Checked 'python' @('--version') 'python-version.log'
    Invoke-Checked 'git' @('submodule','status','--recursive') 'submodules.log'
    [pscustomobject]@{Commit=$env:GITHUB_SHA; RunId=$env:GITHUB_RUN_ID; Attempt=$env:GITHUB_RUN_ATTEMPT; ImageOS=$env:ImageOS; ImageVersion=$env:ImageVersion; OS=[Environment]::OSVersion.VersionString; PowerShell=$PSVersionTable.PSVersion.ToString(); VulkanLoaderPresent=(Test-Path "$env:WINDIR/System32/vulkan-1.dll")} | ConvertTo-Json | Set-Content "$logs/runner.json"
    Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free | ConvertTo-Json | Set-Content "$logs/disk.json"
    Copy-Item tools/ci/windows-dependencies.json "$logs/dependencies.json"
    exit 0
}
foreach ($program in 'cmake.exe','ctest.exe','ninja.exe','g++.exe','objdump.exe') {
    $resolved = (Get-Command $program -ErrorAction Stop).Source
    if ([IO.Path]::GetDirectoryName($resolved) -ne $env:ANYPS5_WINLIBS_BIN) { throw "Unpinned tool resolved: $resolved" }
}
switch ($Phase) {
    'Configure' {
        Invoke-Checked 'cmake' @('-S','.', '-B',$build, '-G','Ninja', '-DCMAKE_BUILD_TYPE=Debug', '-DCMAKE_C_COMPILER=gcc', '-DCMAKE_CXX_COMPILER=g++', '-DBUILD_TESTING=ON', '-DANYPS5_ENABLE_SPIRV_TOOLS=ON', '-DAPS5_ENABLE_TIMING_LOG=ON', "-DFFMPEG_PREBUILT_DIR=$env:ANYPS5_FFMPEG_DIR") 'configure.log'
        Invoke-Checked 'python' @('tools/check_test_registration.py',$build) 'registration.log'
        Invoke-Checked 'ctest' @('--test-dir',$build,'--show-only=json-v1') 'test-inventory.json'
    }
    'Build' { Invoke-Checked 'cmake' @('--build',$build,'--parallel','2') 'build.log' }
    'Libraries' { Invoke-Checked 'cmake' @('--build',$build,'--target','libs','--parallel','2') 'libraries.log' }
    'Test' {
        Invoke-Checked 'python' @('-m','unittest','discover','-s','tools/tests','-p','test_windows_debug_package.py') 'packaging-tests.log'
        $env:PATH = "$(Join-Path $build 'core/libs/libs/unpatched');$env:PATH"
        & ctest --test-dir $build --output-on-failure --timeout 120 --parallel 2 --no-tests=error --output-junit "$logs/ctest.xml" 2>&1 | Tee-Object -FilePath "$logs/ctest.log"
        $testCode = $LASTEXITCODE
        & python tools/package_windows_debug.py summarize --logs $logs
        $summaryCode = $LASTEXITCODE
        if ($testCode -ne 0 -or $summaryCode -ne 0) { throw "Tests incomplete or failed: CTest=$testCode summary=$summaryCode" }
    }
    'Package' {
        Invoke-Checked 'python' @('tools/package_windows_debug.py','package','--build',$build,'--logs',$logs,'--toolchain',$env:ANYPS5_WINLIBS_BIN,'--ffmpeg',$env:ANYPS5_FFMPEG_DIR,'--output','dist','--commit',$env:GITHUB_SHA) 'package.log'
    }
}
