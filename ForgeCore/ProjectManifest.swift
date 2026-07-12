import Foundation

public struct ProjectManifest: Codable, Equatable, Sendable {
    public enum Runtime: String, Codable, CaseIterable, Sendable {
        case ubuntu
        case manjaro
        case wasi
        case runner
    }

    public struct Command: Codable, Equatable, Sendable {
        public var arguments: [String]?
        public var shell: String?

        public init(arguments: [String]? = nil, shell: String? = nil) throws {
            guard (arguments?.isEmpty == false) != (shell?.isEmpty == false) else {
                throw ManifestError.commandMustChooseArgumentsOrShell
            }
            self.arguments = arguments
            self.shell = shell
        }
    }

    public var schema: Int
    public var name: String
    public var runtime: Runtime
    public var commands: [String: Command]
    public var ports: [Int]
    public var artifactGlobs: [String]
    public var syncExclusions: [String]

    public init(
        schema: Int = 1,
        name: String,
        runtime: Runtime = .ubuntu,
        commands: [String: Command] = [:],
        ports: [Int] = [],
        artifactGlobs: [String] = [],
        syncExclusions: [String] = []
    ) throws {
        guard schema == 1 else { throw ManifestError.unsupportedSchema(schema) }
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw ManifestError.emptyName
        }
        guard ports.allSatisfy({ (1...65_535).contains($0) }) else {
            throw ManifestError.invalidPort
        }
        self.schema = schema
        self.name = name
        self.runtime = runtime
        self.commands = commands
        self.ports = Array(Set(ports)).sorted()
        self.artifactGlobs = artifactGlobs
        self.syncExclusions = syncExclusions
    }
}

public enum ManifestError: Error, Equatable, Sendable {
    case unsupportedSchema(Int)
    case emptyName
    case invalidPort
    case commandMustChooseArgumentsOrShell
}
