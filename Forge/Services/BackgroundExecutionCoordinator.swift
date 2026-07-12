import BackgroundTasks
import Foundation

final class BackgroundExecutionCoordinator: @unchecked Sendable {
    static let shared = BackgroundExecutionCoordinator()

    static let processingIdentifier = "com.bitloop.forge.processing"
    static let continuedPrefix = "com.bitloop.forge.continued"
    private let lock = NSLock()
    private var handlers: [String: @Sendable (Progress) async -> Bool] = [:]
    private var registered = false

    private init() {}

    func register() {
        lock.withLock {
            guard !registered else { return }
            registered = true
            BGTaskScheduler.shared.register(
                forTaskWithIdentifier: Self.processingIdentifier,
                using: nil,
                launchHandler: handleProcessing
            )
            BGTaskScheduler.shared.register(
                forTaskWithIdentifier: "\(Self.continuedPrefix).*",
                using: nil,
                launchHandler: handleContinued
            )
        }
    }

    func submit(
        title: String,
        subtitle: String,
        operation: @escaping @Sendable (Progress) async -> Bool
    ) async throws -> String {
        let identifier = "\(Self.continuedPrefix).\(UUID().uuidString)"
        lock.withLock { handlers[identifier] = operation }
        let request = BGContinuedProcessingTaskRequest(
            identifier: identifier,
            title: title,
            subtitle: subtitle
        )
        request.strategy = .queue
        do {
            try await BGTaskScheduler.shared.submitTaskRequest(request)
        } catch {
            _ = lock.withLock { handlers.removeValue(forKey: identifier) }
            throw error
        }
        return identifier
    }

    private func handleProcessing(task: BGTask) {
        task.expirationHandler = { task.setTaskCompleted(success: false) }
        task.setTaskCompleted(success: true)
    }

    private func handleContinued(task: BGTask) {
        guard let task = task as? BGContinuedProcessingTask else {
            task.setTaskCompleted(success: false)
            return
        }
        let operation = lock.withLock { handlers.removeValue(forKey: task.identifier) }
        guard let operation else {
            task.setTaskCompleted(success: false)
            return
        }
        let handle = ContinuedTaskHandle(task)
        let cancellation = CancellationBox()
        task.expirationHandler = {
            cancellation.cancel()
            handle.complete(success: false)
        }
        let worker = Task {
            let success = await operation(handle.progress)
            handle.complete(success: success && !Task.isCancelled)
        }
        cancellation.attach(worker)
    }
}

private final class ContinuedTaskHandle: @unchecked Sendable {
    private let task: BGContinuedProcessingTask
    private let lock = NSLock()
    private var completed = false
    var progress: Progress { task.progress }

    init(_ task: BGContinuedProcessingTask) {
        self.task = task
    }

    func complete(success: Bool) {
        let shouldComplete = lock.withLock {
            guard !completed else { return false }
            completed = true
            return true
        }
        guard shouldComplete else { return }
        task.setTaskCompleted(success: success)
    }
}

private final class CancellationBox: @unchecked Sendable {
    private let lock = NSLock()
    private var worker: Task<Void, Never>?
    private var isCancelled = false

    func attach(_ worker: Task<Void, Never>) {
        let cancel = lock.withLock {
            self.worker = worker
            return isCancelled
        }
        if cancel { worker.cancel() }
    }

    func cancel() {
        let worker = lock.withLock {
            isCancelled = true
            return self.worker
        }
        worker?.cancel()
    }
}
