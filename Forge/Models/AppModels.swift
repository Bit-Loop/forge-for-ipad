import Foundation

struct WorkspaceSummary: Codable, Hashable, Identifiable, Sendable {
    let id: UUID
    var name: String
    var root: URL
    var lastOpenedAt: Date
}

enum RuntimeEnvironment: String, Codable, CaseIterable, Sendable {
    case ubuntu = "Ubuntu 26.04"
    case manjaro = "Manjaro ARM"
    case wasi = "WASI"
}

enum RuntimePhase: String, Codable, Sendable {
    case offline
    case requestingJIT
    case booting
    case ready
    case checkpointing
    case suspended
    case failed
}

struct RuntimeSnapshot: Codable, Equatable, Sendable {
    var environment: RuntimeEnvironment
    var phase: RuntimePhase
    var availableMemoryBytes: UInt64
    var jitEnabled: Bool
    var detail: String

    static let offline = Self(
        environment: .ubuntu,
        phase: .offline,
        availableMemoryBytes: 0,
        jitEnabled: false,
        detail: "Runtime is stopped"
    )
}
