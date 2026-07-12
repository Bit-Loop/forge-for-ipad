import Foundation

protocol VirtualMachineDriver: Sendable {
    func boot(configuration: VirtualMachineConfiguration) async throws
    func requestCheckpoint(reason: CheckpointReason) async throws -> URL
    func restore(checkpoint: URL) async throws
    func stop() async throws
}

struct VirtualMachineConfiguration: Codable, Sendable {
    let disk: URL
    let memoryMiB: Int
    let vCPUs: Int
    let jitEnabled: Bool

    init(disk: URL, memoryMiB: Int, vCPUs: Int, jitEnabled: Bool) throws {
        guard memoryMiB >= 3_072, memoryMiB <= 10_240, vCPUs >= 1, vCPUs <= 10 else {
            throw VirtualMachineError.invalidConfiguration
        }
        self.disk = disk
        self.memoryMiB = memoryMiB
        self.vCPUs = vCPUs
        self.jitEnabled = jitEnabled
    }
}

enum CheckpointReason: String, Codable, Sendable {
    case user
    case background
    case memoryPressure
    case expiration
}

enum VirtualMachineError: LocalizedError {
    case invalidConfiguration
    case runtimeNotInstalled

    var errorDescription: String? {
        switch self {
        case .invalidConfiguration: "The virtual machine memory or CPU allocation is unsafe."
        case .runtimeNotInstalled: "Install a signed UTM runtime pack before starting Linux."
        }
    }
}

/// Default driver used by the thin app before a signed UTM runtime pack is
/// present. Keeping this explicit prevents the UI from reporting a VM as live.
struct UnavailableVirtualMachineDriver: VirtualMachineDriver {
    func boot(configuration: VirtualMachineConfiguration) async throws {
        throw VirtualMachineError.runtimeNotInstalled
    }

    func requestCheckpoint(reason: CheckpointReason) async throws -> URL {
        throw VirtualMachineError.runtimeNotInstalled
    }

    func restore(checkpoint: URL) async throws {
        throw VirtualMachineError.runtimeNotInstalled
    }

    func stop() async throws {
        throw VirtualMachineError.runtimeNotInstalled
    }
}
