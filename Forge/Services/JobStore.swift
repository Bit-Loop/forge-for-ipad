import ForgeCore
import Foundation

actor JobStore {
    private var jobs: [ForgeJob] = []
    private var pendingSubmissions: [PendingRunnerSubmission] = []
    private let encoder: JSONEncoder = {
        let value = JSONEncoder()
        value.dateEncodingStrategy = .iso8601
        value.outputFormatting = [.prettyPrinted, .sortedKeys]
        return value
    }()
    private let decoder: JSONDecoder = {
        let value = JSONDecoder()
        value.dateDecodingStrategy = .iso8601
        return value
    }()

    func load() throws -> [ForgeJob] {
        let file = try storeURL()
        jobs = FileManager.default.fileExists(atPath: file.path)
            ? try decoder.decode([ForgeJob].self, from: Data(contentsOf: file))
            : []
        let pendingFile = try pendingStoreURL()
        pendingSubmissions = FileManager.default.fileExists(atPath: pendingFile.path)
            ? try decoder.decode([PendingRunnerSubmission].self, from: Data(contentsOf: pendingFile))
            : []
        let liveJobIDs = Set(jobs.filter { $0.remoteReference == nil && !$0.state.isTerminal }.map(\.id))
        pendingSubmissions.removeAll { !liveJobIDs.contains($0.localJobID) }
        try persistPending()
        let pendingIDs = Set(pendingSubmissions.map(\.localJobID))
        var recovered = false
        for index in jobs.indices where jobs[index].remoteReference == nil && !pendingIDs.contains(jobs[index].id) {
            if jobs[index].state == .queued {
                try jobs[index].transition(to: .cancelled)
                recovered = true
            } else if jobs[index].state == .running || jobs[index].state == .checkpointing || jobs[index].state == .resuming {
                try jobs[index].transition(to: .failed)
                recovered = true
            }
        }
        if recovered { try persist() }
        return jobs.sorted { $0.updatedAt > $1.updatedAt }
    }

    func pending() -> [PendingRunnerSubmission] { pendingSubmissions }

    func savePending(_ submission: PendingRunnerSubmission) throws {
        guard let job = jobs.first(where: { $0.id == submission.localJobID }),
              job.state == .queued, job.remoteReference == nil else {
            throw JobStoreError.invalidSubmissionState
        }
        pendingSubmissions.removeAll { $0.localJobID == submission.localJobID }
        pendingSubmissions.append(submission)
        try persistPending()
    }

    func enqueue(title: String, workspaceID: UUID?) throws -> ForgeJob {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw JobStoreError.emptyTitle }
        let job = ForgeJob(title: trimmed, workspaceID: workspaceID)
        jobs.append(job)
        try persist()
        return job
    }

    func transition(id: UUID, to state: ForgeJob.State) throws -> ForgeJob {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else {
            throw JobStoreError.notFound
        }
        try jobs[index].transition(to: state)
        try persist()
        return jobs[index]
    }

    func attachRemoteAndStart(id: UUID, reference: ForgeJob.RemoteReference) throws -> ForgeJob {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { throw JobStoreError.notFound }
        jobs[index].remoteReference = reference
        if jobs[index].state == .queued { try jobs[index].transition(to: .running) }
        else { jobs[index].updatedAt = .now }
        try persist()
        pendingSubmissions.removeAll { $0.localJobID == id }
        try persistPending()
        return jobs[index]
    }

    func updateRemoteCursor(id: UUID, sequence: Int64) throws -> ForgeJob {
        guard let index = jobs.firstIndex(where: { $0.id == id }),
              var reference = jobs[index].remoteReference else { throw JobStoreError.notFound }
        reference.lastEventSequence = max(reference.lastEventSequence, sequence)
        jobs[index].remoteReference = reference
        jobs[index].updatedAt = .now
        try persist()
        return jobs[index]
    }

    private func persist() throws {
        let data = try encoder.encode(jobs)
        try data.write(to: storeURL(), options: [.atomic, .completeFileProtectionUnlessOpen])
    }

    private func persistPending() throws {
        let data = try encoder.encode(pendingSubmissions)
        try data.write(to: pendingStoreURL(), options: [.atomic, .completeFileProtectionUnlessOpen])
    }

    private func storeURL() throws -> URL {
        let directory = try FileManager.default.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appending(path: "Forge/State", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appending(path: "jobs.json")
    }

    private func pendingStoreURL() throws -> URL {
        try storeURL().deletingLastPathComponent().appending(path: "pending-runner-submissions.json")
    }
}

enum JobStoreError: LocalizedError {
    case emptyTitle
    case notFound
    case invalidSubmissionState

    var errorDescription: String? {
        switch self {
        case .emptyTitle: "The job title cannot be empty."
        case .notFound: "The requested job no longer exists."
        case .invalidSubmissionState: "The runner submission is no longer queued."
        }
    }
}
