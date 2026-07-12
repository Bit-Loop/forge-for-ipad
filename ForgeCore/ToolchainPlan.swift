import Foundation

public enum ProjectLanguage: String, Codable, CaseIterable, Sendable {
    case c
    case cpp
    case rust
    case python
}

public enum ToolchainPhase: String, Codable, Sendable {
    case fetch
    case lint
    case build
    case test
    case run
}

public struct ToolchainCommand: Codable, Equatable, Sendable {
    public let phase: ToolchainPhase
    public let executable: String
    public let arguments: [String]

    public init(_ phase: ToolchainPhase, _ executable: String, _ arguments: [String] = []) {
        self.phase = phase
        self.executable = executable
        self.arguments = arguments
    }
}

public struct ToolchainPlan: Codable, Equatable, Sendable {
    public let language: ProjectLanguage
    public let commands: [ToolchainCommand]

    public func pipeline(through target: ToolchainPhase) -> [ToolchainCommand] {
        let phases: Set<ToolchainPhase> = switch target {
        case .fetch: [.fetch]
        case .lint: [.fetch, .lint]
        case .build: [.fetch, .build]
        case .test: [.fetch, .test]
        case .run where language == .c || language == .cpp: [.fetch, .build, .run]
        case .run: [.fetch, .run]
        }
        return commands.filter { phases.contains($0.phase) }
    }
}

public enum ToolchainPlanner {
    public static func plans(files rawFiles: some Sequence<String>) -> [ToolchainPlan] {
        let files = Set(rawFiles.map(normalize))
        let names = Set(files.map { $0.lowercased() })
        var plans: [ToolchainPlan] = []
        if names.contains("cargo.toml") {
            plans.append(rust(names))
        }
        if names.contains("pyproject.toml") || names.contains("requirements.txt") || names.contains("uv.lock") {
            plans.append(python(names))
        }
        let c = files.filter { URL(fileURLWithPath: $0).pathExtension.lowercased() == "c" }.sorted()
        let cppExtensions = Set(["cc", "cpp", "cxx"])
        let cpp = files.filter { cppExtensions.contains(URL(fileURLWithPath: $0).pathExtension.lowercased()) }.sorted()
        if !cpp.isEmpty {
            plans.append(cFamily(language: .cpp, sources: cpp, names: names))
        } else if !c.isEmpty {
            plans.append(cFamily(language: .c, sources: c, names: names))
        }
        return plans.sorted { $0.language.rawValue < $1.language.rawValue }
    }

    private static func rust(_ names: Set<String>) -> ToolchainPlan {
        let locked = names.contains("cargo.lock") ? ["--locked"] : []
        return .init(language: .rust, commands: [
            .init(.fetch, "cargo", ["fetch"] + locked),
            .init(.lint, "cargo", ["fmt", "--all", "--", "--check"]),
            .init(.lint, "cargo", ["clippy", "--all-targets", "--all-features"] + locked + ["--", "-D", "warnings"]),
            .init(.build, "cargo", ["build", "--all-targets", "--all-features"] + locked),
            .init(.test, "cargo", ["test", "--all-targets", "--all-features"] + locked),
            .init(.run, "cargo", ["run"] + locked),
        ])
    }

    private static func python(_ names: Set<String>) -> ToolchainPlan {
        var sync = ["sync"]
        if names.contains("uv.lock") { sync.append("--frozen") }
        return .init(language: .python, commands: [
            .init(.fetch, "uv", sync),
            .init(.lint, "uv", ["run", "ruff", "check", "."]),
            .init(.lint, "uv", ["run", "ruff", "format", "--check", "."]),
            .init(.lint, "uv", ["run", "pyright"]),
            .init(.lint, "uv", ["run", "mypy", "."]),
            .init(.build, "uv", ["build"]),
            .init(.test, "uv", ["run", "pytest"]),
            .init(.run, "uv", ["run", "python", "-m", projectModule(names)]),
        ])
    }

    private static func cFamily(
        language: ProjectLanguage,
        sources: [String],
        names: Set<String>
    ) -> ToolchainPlan {
        var commands: [ToolchainCommand] = []
        if names.contains("conanfile.py") || names.contains("conanfile.txt") {
            commands.append(.init(.fetch, "conan", ["install", ".", "--output-folder=.forge/conan", "--build=missing"]))
        }
        if names.contains("vcpkg.json") {
            commands.append(.init(.fetch, "vcpkg", ["install", "--x-manifest-root=."]))
        }
        if names.contains("cmakelists.txt") {
            commands += [
                .init(.build, "cmake", ["-S", ".", "-B", ".forge/build", "-G", "Ninja", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]),
                .init(.build, "cmake", ["--build", ".forge/build", "--parallel"]),
                .init(.lint, "cmake", ["-S", ".", "-B", ".forge/build", "-G", "Ninja", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]),
                .init(.lint, "clang-tidy", sources + ["-p", ".forge/build"]),
                .init(.test, "cmake", ["-S", ".", "-B", ".forge/build", "-G", "Ninja"]),
                .init(.test, "cmake", ["--build", ".forge/build", "--parallel"]),
                .init(.test, "ctest", ["--test-dir", ".forge/build", "--output-on-failure"]),
            ]
        } else if names.contains("meson.build") {
            commands += [
                .init(.build, "meson", ["setup", ".forge/build"]),
                .init(.build, "meson", ["compile", "-C", ".forge/build"]),
                .init(.test, "meson", ["setup", ".forge/build"]),
                .init(.test, "meson", ["compile", "-C", ".forge/build"]),
                .init(.test, "meson", ["test", "-C", ".forge/build", "--print-errorlogs"]),
            ]
        } else if names.contains("makefile") {
            commands += [
                .init(.build, "make", ["-j"]),
                .init(.test, "make", ["test"]),
            ]
        } else {
            let compiler = language == .cpp ? "clang++" : "clang"
            let standard = language == .cpp ? "-std=c++23" : "-std=c23"
            commands += [
                .init(.lint, compiler, [standard, "-Wall", "-Wextra", "-Wpedantic", "-fsyntax-only"] + sources),
                .init(.build, "mkdir", ["-p", ".forge/build"]),
                .init(.build, compiler, [standard, "-O2", "-g"] + sources + ["-o", ".forge/build/app"]),
                .init(.run, ".forge/build/app"),
            ]
        }
        return .init(language: language, commands: commands)
    }

    private static func normalize(_ path: String) -> String {
        path.replacingOccurrences(of: "\\", with: "/")
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }

    private static func projectModule(_ names: Set<String>) -> String {
        names.contains("src/main.py") || names.contains("main.py") ? "main" : "app"
    }
}
