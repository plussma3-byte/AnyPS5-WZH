#include <Cli.hpp>
#include <filesystem>
#include <iostream>
#include <limits>
#include <string>
#include <cstdlib>
#ifdef _WIN32
#include <windows.h>
#include <system_error>
#endif

namespace Cli {

int Autorun(const std::string& absPath, bool toWindows) {
    if (!toWindows) {
        std::filesystem::permissions(absPath,
            std::filesystem::perms::owner_exec |
            std::filesystem::perms::group_exec |
            std::filesystem::perms::others_exec,
            std::filesystem::perm_options::add);
    }

#ifdef _WIN32
    const auto application = std::filesystem::path(absPath).wstring();
    std::wstring command = L"\"" + application + L"\"";
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(application.c_str(), command.data(), nullptr, nullptr, TRUE, 0,
                        nullptr, nullptr, &startup, &process)) {
        throw std::system_error(static_cast<int>(GetLastError()), std::system_category(), "Autorun CreateProcessW");
    }
    CloseHandle(process.hThread);
    const auto wait = WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 0;
    const bool completed = wait == WAIT_OBJECT_0 && GetExitCodeProcess(process.hProcess, &code);
    const auto error = completed ? ERROR_SUCCESS : GetLastError();
    CloseHandle(process.hProcess);
    if (!completed) throw std::system_error(static_cast<int>(error), std::system_category(), "Autorun wait");
    const int rawCode = static_cast<int>(code);
#else
    std::string cmd = "'";
    for (const char character : absPath) {
        if (character == '\'') cmd += "'\\''";
        else cmd += character;
    }
    cmd += '\'';
    const int rawCode = std::system(cmd.c_str());
#endif

    int exitCode = rawCode;
#ifdef _WIN32
    std::cout << "\nExit code: " << rawCode << '\n';
#else
    if (rawCode != -1) {
        const int signal = rawCode & 0x7F;
        exitCode = signal ? 128 + signal : (rawCode >> 8) & 0xFF;
    }
    std::cout << "\nRaw exit code: " << rawCode << "; Unpacked: " << exitCode << '\n';
#endif

    std::cout << "\nPress Enter to exit...\n";
    std::cin.ignore(std::numeric_limits<std::streamsize>::max(), '\n');

    return exitCode;
}

}
