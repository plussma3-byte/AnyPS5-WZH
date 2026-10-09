# Windows Debug CI review and authorization

## Execution boundary

The dedicated `.github/workflows/windows-debug.yml` builds on `windows-2022` after a push to **`codex/windows-debug-validation`**. `workflow_dispatch` is also restricted to that branch. For a new workflow not on the default branch, the initial push is the reliable trigger; the manual Run workflow button may not exist until the workflow is on the default branch. Existing runs can be rerun through Actions.

**Do not push or dispatch this workflow until the repository owner approves the external downloads and execution described below.** Preparing these files and reading publisher checksum/reference metadata does not execute the workflow. There is no pull-request, main-branch or tag trigger, no publication job, no `gh release`, and no repository-write permission inside the job.

The build must include the existing constant-folding and security fixes plus their tests. A build from the old base commit alone would not test those fixes.

## Requested permissions and downloads

| Component | Pin or permission | Purpose |
|---|---|---|
| Hosted runner | `windows-2022`, 90-minute job limit | Build and run project-owned tests; hosted image updates are recorded, not an immutable VM image pin. |
| Workflow token | `contents: read` only | Checkout/read dependencies. Artifact upload uses the job-scoped Actions artifact service; no `contents: write`, release, issue or PR write permission. |
| Repository operation outside the job | Push a review branch containing workflow/source changes | Requires branch push and permission to update workflow files. Dispatch/rerun through API additionally requires Actions write access. This does not modify `main` or create a release. |
| Checkout | `actions/checkout` v7.0.0, `9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0` | Exact job commit and recursive submodules; persisted credentials disabled. |
| Python setup | `actions/setup-python` v6.0.0, `e797f83bcb11b83ae66e0230d6156d7c80228e7c` | Python 3.12.12 x64 from the Actions tool distribution if not already cached. |
| Artifact upload | `actions/upload-artifact` v4.6.2, `ea165f8d65b6e75b540449e92b4886f43607fa02` | Store runtime ZIP and diagnostics for seven days. Artifact visibility follows repository permissions. |
| WinLibs | GCC 15.2.0 UCRT POSIX SEH, `15.2.0posix-14.0.0-ucrt-r7` | GCC, CMake, CTest, Ninja and objdump must come from the SHA256-locked bundle; no fallback to unpinned runner copies. Actual bundled tool versions are recorded. |
| FFmpeg | `KytyPS5/ext-ffmpeg-core` package `9ac4cfd195f1`, `ffmpeg-mingw-x64.zip` | Reviewed project dependency, a third-party fork rather than an official FFmpeg binary distribution. |
| Submodules | Exact gitlinks from the source commit and recursive gitlinks | SDL2, Vulkan/SPIR-V headers/tools, glslang, VMA, LibAtrac9, FFmpeg headers/build metadata, FreeType/dlg, stb, libjpeg-turbo. URLs are in `.gitmodules`. |

The full archive URLs, publisher checksum URLs and pinned SHA256 values are in `tools/ci/windows-dependencies.json`. Hashes were read from the publisher checksum files during preparation; only checksum/reference metadata was fetched, not the executable archives. A hash mismatch fails before extraction or execution. A hash verifies the approved bytes, not the absence of malware or the trustworthiness of a compromised publisher. No additional Vulkan SDK, driver, antivirus-disabling command, game, key, firmware, or telemetry service is downloaded or used.

The Windows runner can consume Actions minutes and artifact storage. Build parallelism is two. Windows 11 / RTX 3060 testing still happens on the user's machine; a hosted runner is not that hardware.

## Build and test gate

The documented CMake/WinLibs path is used with `Debug`, `BUILD_TESTING=ON`, `ANYPS5_ENABLE_SPIRV_TOOLS=ON`, and `APS5_ENABLE_TIMING_LOG=ON`. FFmpeg is explicitly supplied via `FFMPEG_PREBUILT_DIR`, avoiding the upstream unchecked automatic download. The default build is followed by the separate `libs` target.

CI runs packaging unit tests, checks registration, records all registered CTest tests, and attempts the complete Windows CTest set. Tests are compiled from reviewed project sources and may generate/execute their own small synthetic fixtures. No commercial game or arbitrary supplied executable runs. CTest's existing skip codes are honored and reported separately; the workflow does not turn a failure or timeout into a skip. No automatic heavy-test retries are added.

The ZIP requires no failed/incomplete/missing/unexpected CTest results, and actual passes for `autorun_exit_status`, `buffer_bounds`, `constant_folding`, `param_json_parser`, and `agc_validate_program`. Tests requiring absent Vulkan capabilities may skip; every skip is listed. The two pre-existing unregistered targets remain disclosed. Diagnostics upload runs after failure without labeling it a successful runtime build.

Every packaged runtime binary must be AMD64 PE32+. Static DLL imports are checked using pinned objdump against packaged libraries and an explicit Windows-system allowlist. Unexpected imports fail packaging. This does not inspect every dynamic DLL load, certify system DLL availability on every Windows edition, scan for all malware, or prove game compatibility. All files are hashed and the ZIP is checked for corruption. Unstripped Debug files and unpatched PRX copies are retained for investigation.

## Local validation before approval

```sh
python -m unittest discover -s tools/tests -p test_windows_debug_package.py
python tools/check_conventions.py --base HEAD
git diff --check
```

The Python tests exercise result gating, skips, missing results, PE architecture rejection and unexpected DLL imports using inert synthetic data. They do not execute synthetic PE files. Linux validation is not a Windows build or a PowerShell runtime test.

Once approved, publish only the dedicated branch, then monitor its Actions run. Do not force-push over another contributor's branch. If GitHub API access is blocked, approval does not bypass that block: use an authorized repository connection or have the owner push the reviewed branch from their authenticated machine. Do not paste access tokens into chat. The initial run's SHA, test summary and artifact URL must be reported only after GitHub has actually produced them.

See [Windows artifact instructions](../user/WINDOWS_DEBUG_ARTIFACT.md) for download, verification, first conversion/run and logs. Do not create a GitHub Release without a separate explicit approval.
