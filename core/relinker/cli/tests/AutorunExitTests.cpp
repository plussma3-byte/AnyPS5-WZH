#include <Cli.hpp>
#include <chrono>
#include <csignal>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#endif

int main(int, char* argv[]) {
    const auto executable = std::filesystem::absolute(argv[0]);
    const auto name = executable.stem().string();
    if (name.starts_with("anyps5-autorun-child-")) {
        if (name.find("signal") != std::string::npos) std::raise(SIGILL);
        return 42;
    }
    auto child = std::filesystem::temp_directory_path() / ("anyps5-autorun-child-" +
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) + " with spaces" + executable.extension().string());
#ifndef _WIN32
    const auto signaled = child.parent_path() / ("anyps5-autorun-child-signal-" + child.filename().string());
#endif
    std::istringstream input("\n\n\n");
    auto* savedInput = std::cin.rdbuf(input.rdbuf());
    int result = 0;
    try {
        std::filesystem::copy_file(executable, child);
        std::vector<std::string> suffixes{""};
#ifdef _WIN32
        if (!SetEnvironmentVariableA("ANYPS5_AUTORUN_LITERAL", "expanded"))
            throw std::runtime_error("Cannot set autorun test environment");
        suffixes.insert(suffixes.end(), {" %ANYPS5_AUTORUN_LITERAL%", " & literal", " (literal)", " !literal!"});
#else
        suffixes.insert(suffixes.end(), {" $HOME", " $(printf substituted)", " `printf substituted`",
            " 'single'", " \"double\"", " \\backslash", " line\nbreak"});
#endif
        const auto originalChild = child;
        for (const auto& suffix : suffixes) {
            const auto renamed = originalChild.parent_path() /
                (originalChild.stem().string() + suffix + originalChild.extension().string());
            if (renamed != child) std::filesystem::rename(child, renamed);
            child = renamed;
            for (const bool windows : {false, true}) {
                const int code = Cli::Autorun(child.string(), windows);
                if (code != 42) throw std::runtime_error("Autorun changed exit code 42 to " +
                    std::to_string(code) + " for " + child.string());
            }
        }
#ifndef _WIN32
        std::filesystem::rename(child, signaled);
        const int code = Cli::Autorun(signaled.string(), false);
        if (code != 128 + SIGILL) throw std::runtime_error("Autorun lost the child signal");
#endif
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        result = 1;
    }
    std::cin.rdbuf(savedInput);
    std::filesystem::remove(child);
#ifndef _WIN32
    std::filesystem::remove(signaled);
#endif
    return result;
}
