import Foundation
#if canImport(Darwin)
import Darwin
#endif

actor RuntimeManager {
    private var value = RuntimeSnapshot.offline

    func snapshot() -> RuntimeSnapshot {
        var current = value
        current.availableMemoryBytes = availableMemory()
        return current
    }

    func beginBoot(environment: RuntimeEnvironment, jitEnabled: Bool) throws -> RuntimeSnapshot {
        guard value.phase == .offline || value.phase == .suspended || value.phase == .failed else {
            throw RuntimeError.alreadyActive
        }
        value = RuntimeSnapshot(
            environment: environment,
            phase: .booting,
            availableMemoryBytes: availableMemory(),
            jitEnabled: jitEnabled,
            detail: "Starting \(environment.rawValue)"
        )
        return value
    }

    func markReady() -> RuntimeSnapshot {
        value.phase = .ready
        value.detail = "SSH and guest agent are ready"
        value.availableMemoryBytes = availableMemory()
        return value
    }

    func suspend(reason: String) -> RuntimeSnapshot {
        value.phase = .suspended
        value.detail = reason
        return value
    }

    private func availableMemory() -> UInt64 {
        #if os(iOS)
        return UInt64(os_proc_available_memory())
        #else
        return ProcessInfo.processInfo.physicalMemory
        #endif
    }
}

enum RuntimeError: LocalizedError {
    case alreadyActive

    var errorDescription: String? { "A Forge runtime is already active." }
}
