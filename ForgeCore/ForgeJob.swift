import Foundation

public struct ForgeJob: Codable, Hashable, Identifiable, Sendable {
    public struct RemoteReference: Codable, Hashable, Sendable {
        public let endpointID: String
        public let jobID: String
        public var lastEventSequence: Int64

        public init(endpointID: String, jobID: String, lastEventSequence: Int64 = 0) {
            self.endpointID = endpointID
            self.jobID = jobID
            self.lastEventSequence = max(0, lastEventSequence)
        }
    }

    public enum State: String, Codable, CaseIterable, Sendable {
        case queued
        case running
        case checkpointing
        case suspended
        case resuming
        case succeeded
        case failed
        case cancelled

        public var isTerminal: Bool {
            self == .succeeded || self == .failed || self == .cancelled
        }

        public func canTransition(to next: Self) -> Bool {
            switch (self, next) {
            case (.queued, .running), (.queued, .failed), (.queued, .cancelled),
                 (.running, .checkpointing), (.running, .succeeded),
                 (.running, .failed), (.running, .cancelled),
                 (.checkpointing, .suspended), (.checkpointing, .running),
                 (.checkpointing, .failed), (.suspended, .resuming),
                 (.suspended, .cancelled), (.resuming, .running),
                 (.resuming, .failed):
                true
            default:
                false
            }
        }
    }

    public let id: UUID
    public let title: String
    public let workspaceID: UUID?
    public var state: State
    public var progress: Double
    public var remoteReference: RemoteReference?
    public let createdAt: Date
    public var updatedAt: Date

    public init(
        id: UUID = UUID(),
        title: String,
        workspaceID: UUID? = nil,
        state: State = .queued,
        progress: Double = 0,
        remoteReference: RemoteReference? = nil,
        createdAt: Date = .now,
        updatedAt: Date = .now
    ) {
        self.id = id
        self.title = title
        self.workspaceID = workspaceID
        self.state = state
        self.progress = min(max(progress, 0), 1)
        self.remoteReference = remoteReference
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    public mutating func transition(to next: State, at date: Date = .now) throws {
        guard state.canTransition(to: next) else {
            throw ForgeJobError.invalidTransition(from: state, to: next)
        }
        state = next
        updatedAt = date
        if next == .succeeded { progress = 1 }
    }
}

public enum ForgeJobError: Error, Equatable, Sendable {
    case invalidTransition(from: ForgeJob.State, to: ForgeJob.State)
}
