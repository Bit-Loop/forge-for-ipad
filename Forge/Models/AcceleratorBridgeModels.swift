import Foundation

struct AcceleratorBootCredentials: Sendable {
    static let guestAuthority = "10.0.2.2:4777"
    static let guestEndpoint = "http://\(guestAuthority)/accelerator/v1"

    let bootID: UUID
    let bearerToken: String

    var guestEnvironment: [String: String] {
        [
            "FORGE_ACCEL_ENDPOINT": Self.guestEndpoint,
            "FORGE_ACCEL_TOKEN": bearerToken,
            "FORGE_ACCEL_BOOT_ID": bootID.uuidString.lowercased(),
        ]
    }
}

struct AcceleratorScratchReference: Codable, Equatable, Sendable {
    let relativePath: String
    let sha256: String
    let size: Int64
    var mediaType = "application/octet-stream"
    var deleteAfterRead = false

    enum CodingKeys: String, CodingKey {
        case relativePath = "relative_path"
        case sha256, size
        case mediaType = "media_type"
        case deleteAfterRead = "delete_after_read"
    }
}

struct AcceleratorScratchVerifyRequest: Codable, Sendable {
    let object: AcceleratorScratchReference
}

struct AcceleratorBridgeCapabilities: Codable, Sendable {
    struct CoreMLCapability: Codable, Sendable {
        let available: Bool
        let formats: [String]
    }

    struct MetalCapability: Codable, Sendable {
        let available: Bool
        let languageVersion: String
        let families: [String]

        enum CodingKeys: String, CodingKey {
            case available, families
            case languageVersion = "language_version"
        }
    }

    struct ScratchCapability: Codable, Sendable {
        let guestRoot: String
        let requiresSHA256 = true

        enum CodingKeys: String, CodingKey {
            case guestRoot = "guest_root"
            case requiresSHA256 = "requires_sha256"
        }
    }

    let protocolVersion = "1.0"
    let serverVersion: String
    let bootID: UUID
    let deviceName: String
    let computeUnits: [String]
    let coreml: CoreMLCapability
    let metal: MetalCapability
    let scratch: ScratchCapability
    let limits: AcceleratorBridgeLimits

    /// iPadOS has no public API for direct guest ANE passthrough. It is never advertised.
    var advertisesDirectANEPassthrough: Bool { false }

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
        case serverVersion = "server_version"
        case bootID = "boot_id"
        case deviceName = "device_name"
        case computeUnits = "compute_units"
        case coreml, metal, scratch, limits
    }
}

struct AcceleratorBridgeLimits: Codable, Sendable {
    var maxRequestBytes = 4 * 1_024 * 1_024
    var maxInlineBytes = 1 * 1_024 * 1_024
    var maxScratchObjectBytes: Int64 = 16 * 1_024 * 1_024 * 1_024
    var maxTensorRank = 16
    var maxInputs = 256
    var maxOutputs = 256
    var maxConcurrentJobs = 2
    var maxModelHandles = 8
    var maxLibraryHandles = 16
    var maxModelBytes: Int64 = 8 * 1_024 * 1_024 * 1_024
    var maxMetalSourceBytes = 4 * 1_024 * 1_024
    var maxBufferBytes: Int64 = 2 * 1_024 * 1_024 * 1_024
    var jobRetentionSeconds = 3_600

    enum CodingKeys: String, CodingKey {
        case maxRequestBytes = "max_request_bytes"
        case maxInlineBytes = "max_inline_bytes"
        case maxScratchObjectBytes = "max_scratch_object_bytes"
        case maxTensorRank = "max_tensor_rank"
        case maxInputs = "max_inputs"
        case maxOutputs = "max_outputs"
        case maxConcurrentJobs = "max_concurrent_jobs"
        case maxModelHandles = "max_model_handles"
        case maxLibraryHandles = "max_library_handles"
        case maxModelBytes = "max_model_bytes"
        case maxMetalSourceBytes = "max_metal_source_bytes"
        case maxBufferBytes = "max_buffer_bytes"
        case jobRetentionSeconds = "job_retention_seconds"
    }
}

struct AcceleratorBridgeErrorEnvelope: Codable, Sendable {
    struct Detail: Codable, Sendable {
        let code: String
        let message: String
        let retriable: Bool
        let requestID: UUID

        enum CodingKeys: String, CodingKey {
            case code, message, retriable
            case requestID = "request_id"
        }
    }

    let error: Detail
}
