import Foundation
import ForgeCore

/// A resolved, policy-checked Forge Runner advertised on the local network.
struct RunnerEndpoint: Codable, Hashable, Identifiable, Sendable {
    let discoveryID: UUID
    let name: String
    let baseURL: URL
    let interfaceNames: [String]
    let metadata: [String: String]
    let advertisedInstanceID: RunnerInstanceID?
    let instanceID: RunnerInstanceID?

    var id: String {
        instanceID?.rawValue ?? discoveryID.uuidString.lowercased()
    }

    init(
        name: String,
        baseURL: URL,
        interfaceNames: [String] = [],
        metadata: [String: String] = [:],
        discoveryID: UUID = UUID(),
        advertisedInstanceID: RunnerInstanceID? = nil,
        instanceID: RunnerInstanceID? = nil
    ) throws {
        self.discoveryID = discoveryID
        self.name = name
        self.baseURL = try RunnerEndpointPolicy.validatedBaseURL(baseURL)
        self.interfaceNames = interfaceNames.sorted()
        self.metadata = metadata
        self.advertisedInstanceID = advertisedInstanceID
        self.instanceID = instanceID
    }

    func authenticated(instanceID: RunnerInstanceID) throws -> RunnerEndpoint {
        let bound = try RunnerInstanceID.bind(
            advertised: advertisedInstanceID,
            authenticated: instanceID
        )
        return try RunnerEndpoint(
            name: name,
            baseURL: baseURL,
            interfaceNames: interfaceNames,
            metadata: metadata,
            discoveryID: discoveryID,
            advertisedInstanceID: advertisedInstanceID,
            instanceID: bound
        )
    }
}

struct RunnerCredential: Codable, Sendable {
    let endpointID: String
    let pairedBaseURL: URL
    let tokenID: String
    let bearerToken: String
    let pairedAt: Date
}

enum RunnerJSONValue: Codable, Hashable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([RunnerJSONValue])
    case object([String: RunnerJSONValue])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let value = try? container.decode(Bool.self) { self = .bool(value) }
        else if let value = try? container.decode(Double.self) { self = .number(value) }
        else if let value = try? container.decode(String.self) { self = .string(value) }
        else if let value = try? container.decode([RunnerJSONValue].self) { self = .array(value) }
        else { self = .object(try container.decode([String: RunnerJSONValue].self)) }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }
}

struct RunnerPairRequest: Codable, Sendable {
    let code: String
    var clientName = "Forge for iPad"
    var existingTokenID: String?

    enum CodingKeys: String, CodingKey {
        case code
        case clientName = "client_name"
        case existingTokenID = "existing_token_id"
    }
}

struct RunnerPairResponse: Codable, Sendable {
    let token: String
    let tokenID: String

    enum CodingKeys: String, CodingKey {
        case token
        case tokenID = "token_id"
    }
}

struct RunnerCapabilities: Codable, Equatable, Sendable {
    let apiVersion: String
    let instanceID: RunnerInstanceID
    let serverVersion: String
    let hostArchitecture: String
    let targetArchitectures: [String]
    let executor: String
    let features: [String]

    enum CodingKeys: String, CodingKey {
        case apiVersion = "api_version"
        case instanceID = "instance_id"
        case serverVersion = "server_version"
        case hostArchitecture = "host_architecture"
        case targetArchitectures = "target_architectures"
        case executor, features
    }
}

struct RunnerSnapshotEntry: Codable, Hashable, Sendable {
    enum Kind: String, Codable, Sendable { case file, directory, symlink }

    let path: String
    let kind: Kind
    var digest: String?
    var size: Int64?
    var mode = 0o644
    var target: String?
}

struct RunnerSnapshotRequest: Codable, Sendable { let entries: [RunnerSnapshotEntry] }

struct RunnerSnapshotResponse: Codable, Sendable {
    let digest: String
    let entryCount: Int
    let totalBytes: Int64

    enum CodingKeys: String, CodingKey {
        case digest
        case entryCount = "entry_count"
        case totalBytes = "total_bytes"
    }
}

struct RunnerResourceLimits: Codable, Hashable, Sendable {
    var cpus = 2.0
    var memoryMB = 2_048
    var timeoutSeconds = 3_600
    var pids = 2_048

    enum CodingKeys: String, CodingKey {
        case cpus
        case memoryMB = "memory_mb"
        case timeoutSeconds = "timeout_seconds"
        case pids
    }
}

struct RunnerPublishedPort: Codable, Hashable, Sendable {
    var containerPort: UInt16
    var hostPort: UInt16?
    var `protocol` = "tcp"

    enum CodingKeys: String, CodingKey {
        case containerPort = "container_port"
        case hostPort = "host_port"
        case `protocol`
    }
}

struct RunnerNetworkPolicy: Codable, Hashable, Sendable {
    var enabled = false
    var networkedSteps: Int?
    var publishedPorts: [RunnerPublishedPort] = []

    enum CodingKeys: String, CodingKey {
        case enabled
        case networkedSteps = "networked_steps"
        case publishedPorts = "published_ports"
    }
}

struct RunnerJobRequest: Codable, Hashable, Sendable {
    let idempotencyKey: String
    let snapshotDigest: String
    var argv: [String]?
    var steps: [[String]]?
    var shell: String?
    var cwd = "."
    var image: String?
    var targetArchitecture = "arm64"
    var limits = RunnerResourceLimits()
    var network = RunnerNetworkPolicy()
    var secretReferences: [String] = []
    var artifactGlobs: [String] = []

    enum CodingKeys: String, CodingKey {
        case idempotencyKey = "idempotency_key"
        case snapshotDigest = "snapshot_digest"
        case argv, steps, shell, cwd, image
        case targetArchitecture = "target_architecture"
        case limits, network
        case secretReferences = "secret_references"
        case artifactGlobs = "artifact_globs"
    }

    func validate() throws {
        guard idempotencyKey.range(of: #"^[A-Za-z0-9._:-]{1,128}$"#, options: .regularExpression) != nil else {
            throw RunnerClientError.invalidRequest("Invalid idempotency key.")
        }
        guard RunnerDigest.isSHA256(snapshotDigest) else {
            throw RunnerClientError.invalidRequest("Snapshot digest must be lowercase SHA-256.")
        }
        guard [argv != nil, steps != nil, shell != nil].filter({ $0 }).count == 1 else {
            throw RunnerClientError.invalidRequest("Supply exactly one of argv, steps, or shell.")
        }
        if let argv, argv.isEmpty || argv.contains(where: { $0.isEmpty || $0.contains("\0") }) {
            throw RunnerClientError.invalidRequest("argv must contain nonempty, NUL-free values.")
        }
        if shell?.contains("\0") == true {
            throw RunnerClientError.invalidRequest("shell must not contain NUL.")
        }
        if let steps,
           steps.isEmpty || steps.count > 64 || steps.contains(where: {
               $0.isEmpty || $0.count > 1_024 || $0.contains(where: { $0.isEmpty || $0.contains("\0") })
           }) {
            throw RunnerClientError.invalidRequest("steps must contain nonempty, NUL-free argv arrays.")
        }
        guard ["arm64", "amd64"].contains(targetArchitecture) else {
            throw RunnerClientError.invalidRequest("Target architecture must be arm64 or amd64.")
        }
    }
}

struct PendingRunnerSubmission: Codable, Hashable, Sendable {
    let localJobID: UUID
    let endpointID: String
    let request: RunnerJobRequest
}

enum RunnerJobStatus: String, Codable, Sendable {
    case queued, running, succeeded, failed, cancelled

    var isTerminal: Bool { self == .succeeded || self == .failed || self == .cancelled }
}

struct RunnerJob: Codable, Sendable {
    let id: String
    let status: RunnerJobStatus
    let request: RunnerJobRequest
    let createdAt: String
    let updatedAt: String
    let exitCode: Int?
    let error: String?

    enum CodingKeys: String, CodingKey {
        case id, status, request, error
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case exitCode = "exit_code"
    }
}

struct RunnerJobCreated: Codable, Sendable {
    let job: RunnerJob
    let replayed: Bool
}

struct RunnerEvent: Codable, Hashable, Sendable {
    let sequence: Int64
    let type: String
    let data: [String: RunnerJSONValue]
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case sequence, type, data
        case createdAt = "created_at"
    }
}

struct RunnerArtifact: Codable, Hashable, Sendable {
    let name: String
    let digest: String
    let size: Int64
    let mediaType: String

    enum CodingKeys: String, CodingKey {
        case name, digest, size
        case mediaType = "media_type"
    }
}

enum RunnerDigest {
    static func isSHA256(_ value: String) -> Bool {
        let lowercaseHex = Set("0123456789abcdef".utf8)
        return value.utf8.count == 64 && value.utf8.allSatisfy(lowercaseHex.contains)
    }
}
