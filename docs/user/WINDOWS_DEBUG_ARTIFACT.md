# Windows x64 Debug test artifact

This package is for local testing, not a public release or a statement that a game is safe or compatible. No games run during CI. AnyPS5 converts game code into native Windows executables; it is not a security sandbox. Marvel's Wolverine has not been tested by this workflow.

## Download and check the result

1. Open `plussma3-byte/AnyPS5-WZH` on GitHub, select **Actions → Windows x64 Debug artifact**, then select the run for the intended commit on `codex/windows-debug-validation`.
2. Inspect the build and test jobs. Download `AnyPS5-Windows-x64-Debug-<full-commit>-<attempt>` from **Artifacts** only after the packaging and upload steps succeed. Artifacts follow repository access permissions and expire after seven days. They are not GitHub Releases.
3. Extract the outer Actions artifact. It contains the runtime ZIP and its `.zip.sha256` file. Compare `Get-FileHash -Algorithm SHA256 <runtime.zip>` with the adjacent checksum before extracting the runtime ZIP into a fresh folder, for example `C:\AnyPS5-Test\Runtime`.
4. Read `build-info.json` and `logs/test-summary.md`. Match the source commit. Skips and the two known unregistered test targets are not successful tests. Hosted Windows runners generally do not provide the target Vulkan GPU. A green build does not establish Vulkan runtime compatibility.
5. `SHA256.json` lists all package files except the manifest itself. The adjacent ZIP hash checks consistency; it is not an independent signature or malware certificate.

If the job fails, only `Windows-Debug-diagnostics-<commit>-<attempt>` should be available. It is for diagnosing the build; do not treat it as a runnable version. Review `configure.log`, `build.log`, `libraries.log`, `ctest.log`, `ctest.xml`, `test-summary.json`, and `pe-imports.json` when present. A killed job may not produce every log.

## Contents

- `relinker.exe`: the locally built converter, with required MinGW runtime DLLs alongside it.
- `libs/`: all patched AnyPS5 PRX libraries and the three pinned WinLibs runtime DLLs. These are not Sony system libraries.
- `symbols/unpatched/`: unpatched Debug PRX files for symbol investigation; do not use these in the runtime `libs` directory. Keep the whole package and matching source commit. GCC Debug information is generally DWARF, not a promise of PDB support.
- `logs/`, `dependencies.json`, `build-info.json`, and `SHA256.json`: provenance, hashes, compiler/image versions, checks and test outcomes.
- `tools/`: optional, local-only capture and flip-timing summary scripts. Review them before running them. They are not executed by CI and do not upload data.
- `licenses/` and `docs/`: license notices and project usage documentation. This review artifact is not a replacement for a complete public distribution/license audit.

## Windows 11 prerequisites

Use an up-to-date NVIDIA driver for the RTX 3060 from https://www.nvidia.com/Download/index.aspx . The Vulkan loader comes from the driver; do not obtain individual DLLs from download sites. Optionally install the Vulkan SDK tools and validation layers from https://vulkan.lunarg.com/sdk/home#windows and run `vulkaninfo --summary`. Verify that the selected device is the RTX 3060. The SDK is not a required CI download: headers are pinned submodules and Vulkan loads dynamically through `vulkan-1.dll`.

Do not disable Defender, bypass execution policy, or run the game as administrator. Use trusted, legally obtained inputs and a separate standard test account without personal secrets. A standard account reduces privileges but is not full isolation.

On a 16 GB machine, keep the first experiment short and watch **Task Manager → Memory → Committed**. Windows direct-memory allocations may commit up to 13824 MiB for a title, in addition to the rest of the process. A system-managed page file can help the commit limit but does not replace RAM or guarantee acceptable performance. The scripts do not change page-file settings.

## First conversion and run

Follow [USAGE.md](docs/USAGE.md) for a clean, legally obtained ELF and its bundled title modules. This workflow does not decrypt SELF/PKG, supply keys, or distribute PS5 firmware. Put bundled ELF modules in `sce_module`, `sce_modules`, or `prx` beside the input according to the documented layout. Keep original game files separate from the output directory.

For the Intel i5-10400, request the supported AMD-to-Intel instruction conversion. In PowerShell, from the extracted runtime directory:

```powershell
New-Item -ItemType Directory -Force C:\AnyPS5-Test\FirstRun | Out-Null
.\relinker.exe --windows --windows-diagnostics --to-intel --registry C:\LegalGame\input.elf C:\AnyPS5-Test\FirstRun\app.exe *> C:\AnyPS5-Test\FirstRun\conversion.log
if ($LASTEXITCODE -ne 0) { throw 'Conversion failed; do not run the output' }
Copy-Item -Recurse .\libs C:\AnyPS5-Test\FirstRun\libs
```

Use a fresh `FirstRun` directory. Do not use `--autorun`, `--skip-syscall-check`, or exclusions to hide conversion failures. Place game resources in `FirstRun\app0`, preserving the **converted** guest modules printed by the converter. Do not overwrite them with original PS5 PRX files or put original console modules in `libs`. If you have the source checkout, run its `tools/import_audit.py` with the generated registry and the matching `libs` and title-module directories before launch. Import availability does not establish semantic compatibility.

For an initial console run:

```powershell
Set-Location C:\AnyPS5-Test\FirstRun
$env:ANYPS5_GPU = 'RTX 3060'
$env:APS5_TRACE_MEMORY = '1'
$env:APS5_PROFILE_DRAW = '1'
.\app.exe 1>stdout.log 2>stderr.log
$LASTEXITCODE | Set-Content exit-code.txt
```

Start with a trusted homebrew or small sample if available. A converted executable starting or showing a window does not prove Wolverine is playable. Stop and retain the error when unsupported instructions/functions or resource failures occur; do not replace them with fake success values.

## Logs and measurements

The Debug build enables SPIRV-Tools and frame-timing logging. `stdout.log` and `stderr.log` include startup, memory, unsupported-function, shader and crash diagnostics when the program reaches the corresponding handlers. `frame-timing.log` is written in the working directory and overwritten by the next run; save it first. `shader_cache` is next to the executable. Cache contents and raw shader dumps are not part of the initial log submission.

For bounded local CPU, RAM and whole-GPU/VRAM sampling, use PowerShell 7.4 or newer and the reviewed capture script from the runtime directory:

```powershell
.\tools\Capture-Local.ps1 -GameDirectory C:\AnyPS5-Test\FirstRun -Seconds 120
```

It creates `FirstRun\logs\<run-id>`. If the game is still running after sampling, close it normally and then copy `frame-timing.log` into that log directory. It does not force-kill the game, install tools, change Defender, or upload anything. Missing GPU counters are unknown, not zero. GPU utilization is for the whole device, not exclusively the game. Add `-Validation` only after confirming the Khronos validation layer is installed; check its output rather than assuming it loaded. `-ShaderAddress <hex>` enables a targeted IR dump, which can contain game shader content.

```powershell
python .\tools\summarize_frames.py C:\AnyPS5-Test\FirstRun\logs\RUN\frame-timing.log --output C:\AnyPS5-Test\FirstRun\logs\RUN\flip-summary.json
```

These are **flip-completion intervals**, marked `display_confirmed=0`, not confirmed displayed FPS. For presentation measurements use the official PresentMon from https://github.com/GameTechDev/PresentMon/releases and check the options for the installed version. Keep its original CSV and distinguish displayed, presented and dropped frames. Debug, validation and detailed logging add overhead; do not use them to claim a Release performance improvement. Compare cold and warm cache separately.

Share selected text logs, `build-info.json`, `test-summary.json`, conversion errors, driver version and reproduction steps here after removing personal paths or account/network details. Do not send the game, keys, firmware, complete shader caches, or full memory dumps by default. No public release is created by this workflow.
