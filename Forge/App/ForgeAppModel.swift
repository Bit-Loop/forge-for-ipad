import ForgeCore
import Combine
import SwiftUI

@MainActor
final class ForgeAppModel: ObservableObject {
    @Published private(set) var workspaces: [WorkspaceSummary] = []
    @Published private(set) var jobs: [ForgeJob] = []
    @Published private(set) var runtime = RuntimeSnapshot.offline
    @Published private(set) var runnerEndpoints: [RunnerEndpoint] = []
    @Published private(set) var runnerDetail = "Searching the local network"
    @Published private(set) var outputByWorkspace: [UUID: String] = [:]
    @Published private(set) var selectedRunnerID = UserDefaults.standard.string(forKey: "runner.selectedEndpointID")
    @Published private(set) var isBootstrapping = true
    @Published var selectedWorkspaceID: UUID?
    @Published var alert: ForgeAlert?

    let assets = AssetManager()
    let jobsStore = JobStore()
    let runtimeManager = RuntimeManager()
    let runnerDiscovery = RunnerDiscovery()
    let runnerVault = RunnerCredentialVault()
    let workspaceExecution = WorkspaceExecutionService()
    let workspacesStore = WorkspaceCoordinator()

    private var didBootstrap = false
    private var runnerDiscoveryTask: Task<Void, Never>?
    private var observedRemoteJobs: Set<UUID> = []
    private var authenticatingRunnerDiscoveries: Set<UUID> = []

    func bootstrap() async {
        guard !didBootstrap else { return }
        didBootstrap = true
        do {
            async let workspaceLoad = workspacesStore.list()
            async let jobLoad = jobsStore.load()
            async let runtimeLoad = runtimeManager.snapshot()
            workspaces = try await workspaceLoad
            jobs = try await jobLoad
            runtime = await runtimeLoad
        } catch {
            alert = .init(title: "Forge could not restore its state", message: error.localizedDescription)
        }
        isBootstrapping = false
        scheduleBundledSeedInstall()
        startRunnerDiscovery()
    }

    func createWorkspace(named name: String) {
        Task { [self] in
            do {
                let workspace = try await workspacesStore.create(named: name)
                workspaces.insert(workspace, at: 0)
                selectedWorkspaceID = workspace.id
            } catch {
                alert = .init(title: "Could not create workspace", message: error.localizedDescription)
            }
        }
    }

    func registerExternalWorkspace(_ url: URL) {
        Task {
            do {
                let workspace: WorkspaceSummary
                do {
                    workspace = try await workspacesStore.registerExternalFolder(url)
                } catch WorkspaceError.notDirectory {
                    workspace = try await workspacesStore.importExternalFile(url)
                }
                workspaces.removeAll { $0.id == workspace.id }
                workspaces.insert(workspace, at: 0)
                selectedWorkspaceID = workspace.id
            } catch {
                alert = .init(title: "Could not open folder", message: error.localizedDescription)
            }
        }
    }

    func handle(_ url: URL) {
        if url.isFileURL {
            if let workspace = workspaces.first(where: {
                let root = $0.root.standardizedFileURL.path
                let candidate = url.standardizedFileURL.path
                return candidate == root || candidate.hasPrefix(root + "/")
            }) {
                selectedWorkspaceID = workspace.id
                return
            }
            registerExternalWorkspace(url)
            return
        }
        guard let route = ForgeRoute(url: url) else {
            alert = .init(title: "Unsupported Forge link", message: url.absoluteString)
            return
        }
        switch route {
        case .open(let workspace, _, _, _), .terminal(let workspace):
            selectedWorkspaceID = workspaces.first { $0.name == workspace }?.id
        case .run(_, let task):
            enqueue(title: task)
        case .artifact(let digest):
            alert = .init(title: "Artifact", message: digest)
        case .pairRunner(let endpoint):
            alert = .init(title: "Runner pairing", message: endpoint.absoluteString)
        }
    }

    func enqueue(title: String) {
        Task {
            do {
                let job = try await jobsStore.enqueue(title: title, workspaceID: selectedWorkspaceID)
                jobs.insert(job, at: 0)
            } catch {
                alert = .init(title: "Could not queue job", message: error.localizedDescription)
            }
        }
    }

    func pairRunner(endpointID: String, code: String) {
        guard let endpoint = runnerEndpoints.first(where: { $0.id == endpointID }) else {
            alert = .init(title: "Runner disappeared", message: "Wait for Bonjour to discover it again.")
            return
        }
        Task {
            do {
                let client = try await RunnerClient.pair(endpoint: endpoint, code: code, vault: runnerVault)
                let capabilities = try await client.capabilities()
                bindDiscoveredEndpoint(client.endpoint)
                selectedRunnerID = client.endpoint.id
                UserDefaults.standard.set(client.endpoint.id, forKey: "runner.selectedEndpointID")
                runnerDetail = "Paired · \(capabilities.executor) · \(capabilities.hostArchitecture)"
            } catch {
                alert = .init(title: "Runner pairing failed", message: error.localizedDescription)
            }
        }
    }

    func run(_ commands: [ToolchainCommand], in workspace: WorkspaceSummary) {
        guard let target = commands.last else {
            alert = .init(title: "Invalid task pipeline", message: "A task pipeline cannot be empty.")
            return
        }
        let endpoint = selectedRunnerID.flatMap { id in runnerEndpoints.first { $0.id == id } }
            ?? runnerEndpoints.first
        guard let endpoint else {
            alert = .init(title: "No Forge Runner", message: "Start Forge Runner on the LAN, then pair it in Settings.")
            return
        }
        Task { [self] in
            do {
                guard let credential = try await runnerVault.credential(for: endpoint.id) else {
                    throw RunnerClientError.notPaired
                }
                let client = try RunnerClient(endpoint: endpoint, credential: credential)
                let capabilities = try await client.capabilities()
                guard !capabilities.executor.hasSuffix(":unavailable"),
                      capabilities.features.contains("default-image"),
                      capabilities.targetArchitectures.contains(capabilities.hostArchitecture) else {
                    throw RunnerClientError.invalidResponse
                }
                let localJob = try await jobsStore.enqueue(
                    title: "\(target.phase.rawValue.capitalized) · \(target.executable)",
                    workspaceID: workspace.id
                )
                jobs.insert(localJob, at: 0)
                let operation: @Sendable (Progress) async -> Bool = { [weak self] progress in
                    guard let self else { return false }
                    progress.totalUnitCount = 100
                    let prepared: WorkspaceExecutionService.PreparedSubmission
                    do {
                        prepared = try await self.workspaceExecution.prepare(
                            workspace: workspace,
                            commands: commands,
                            idempotencyKey: "forge:\(localJob.id.uuidString.lowercased())",
                            targetArchitecture: capabilities.hostArchitecture,
                            client: client
                        )
                        try await self.jobsStore.savePending(.init(
                            localJobID: localJob.id,
                            endpointID: endpoint.id,
                            request: prepared.request
                        ))
                        progress.completedUnitCount = 10
                    } catch {
                        if let failed = try? await self.jobsStore.transition(id: localJob.id, to: .failed) {
                            await self.replace(failed)
                        }
                        await self.reportExecutionError(error)
                        return false
                    }
                    let submission: WorkspaceExecutionService.Submission
                    do {
                        submission = try await self.workspaceExecution.submit(prepared, client: client)
                    } catch {
                        await self.reportExecutionError(error)
                        return false
                    }
                    let reference = ForgeJob.RemoteReference(
                        endpointID: endpoint.id,
                        jobID: submission.runnerJobID
                    )
                    do {
                        let attached = try await self.jobsStore.attachRemoteAndStart(
                            id: localJob.id,
                            reference: reference
                        )
                        guard await self.claimObservation(localJob.id) else { return false }
                        await self.replace(attached)
                    } catch {
                        await self.reportExecutionError(error)
                        return false
                    }
                    progress.completedUnitCount = 35
                    do {
                        let terminal = try await self.observe(
                            localJobID: localJob.id,
                            reference: reference,
                            client: client
                        )
                        let state = Self.localState(for: terminal)
                        let finished = try await self.jobsStore.transition(id: localJob.id, to: state)
                        await self.replace(finished)
                        await self.releaseObservation(localJob.id)
                        progress.completedUnitCount = 100
                        return terminal == .succeeded
                    } catch {
                        await self.releaseObservation(localJob.id)
                        await self.reportExecutionError(error)
                        return false
                    }
                }
                do {
                    _ = try await BackgroundExecutionCoordinator.shared.submit(
                        title: "Forge \(target.phase.rawValue)",
                        subtitle: workspace.name,
                        operation: operation
                    )
                } catch {
                    _ = await operation(Progress(totalUnitCount: 100))
                }
            } catch {
                alert = .init(title: "Could not start \(target.phase.rawValue)", message: error.localizedDescription)
            }
        }
    }

    private func replace(_ job: ForgeJob) {
        if let index = jobs.firstIndex(where: { $0.id == job.id }) { jobs[index] = job }
        else { jobs.insert(job, at: 0) }
    }

    private func claimObservation(_ id: UUID) -> Bool {
        observedRemoteJobs.insert(id).inserted
    }

    private func releaseObservation(_ id: UUID) {
        observedRemoteJobs.remove(id)
    }

    private func reportExecutionError(_ error: Error) {
        alert = .init(title: "Forge task failed", message: error.localizedDescription)
    }

    private func startRunnerDiscovery() {
        runnerDiscoveryTask?.cancel()
        runnerDiscoveryTask = Task { [weak self] in
            guard let self else { return }
            let updates = await runnerDiscovery.updates()
            for await endpoints in updates {
                guard !Task.isCancelled else { return }
                let authenticated = Dictionary(
                    uniqueKeysWithValues: runnerEndpoints.compactMap { endpoint in
                        endpoint.instanceID == nil ? nil : (endpoint.discoveryID, endpoint)
                    }
                )
                runnerEndpoints = endpoints.map { authenticated[$0.discoveryID] ?? $0 }
                runnerDetail = endpoints.isEmpty ? "No runner found" : "\(endpoints.count) runner\(endpoints.count == 1 ? "" : "s") found"
                resumeDurableJobs(using: runnerEndpoints)
                authenticateKnownRunners(runnerEndpoints)
            }
        }
    }

    private func authenticateKnownRunners(_ endpoints: [RunnerEndpoint]) {
        for endpoint in endpoints {
            guard endpoint.instanceID == nil,
                  let advertisedID = endpoint.advertisedInstanceID,
                  authenticatingRunnerDiscoveries.insert(endpoint.discoveryID).inserted else { continue }
            Task { [weak self] in
                guard let self else { return }
                defer { authenticatingRunnerDiscoveries.remove(endpoint.discoveryID) }
                do {
                    guard let credential = try await runnerVault.credential(for: advertisedID.rawValue) else {
                        return
                    }
                    let client = try await RunnerClient.authenticateDiscovered(
                        endpoint: endpoint,
                        credential: credential
                    )
                    bindDiscoveredEndpoint(client.endpoint)
                } catch {
                    runnerDetail = "Runner identity verification failed"
                }
            }
        }
    }

    private func bindDiscoveredEndpoint(_ endpoint: RunnerEndpoint) {
        guard runnerEndpoints.contains(where: { $0.discoveryID == endpoint.discoveryID }) else { return }
        runnerEndpoints.removeAll {
            $0.discoveryID == endpoint.discoveryID ||
                ($0.instanceID != nil && $0.id == endpoint.id)
        }
        runnerEndpoints.append(endpoint)
        runnerEndpoints.sort {
            $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
        resumeDurableJobs(using: runnerEndpoints)
    }

    private func resumeDurableJobs(using endpoints: [RunnerEndpoint]) {
        resumePendingSubmissions(using: endpoints)
        for localJob in jobs where localJob.state == .running || localJob.state == .resuming {
            guard let reference = localJob.remoteReference,
                  !observedRemoteJobs.contains(localJob.id),
                  let endpoint = endpoints.first(where: { $0.id == reference.endpointID }) else { continue }
            observedRemoteJobs.insert(localJob.id)
            Task { [weak self] in
                guard let self else { return }
                defer { observedRemoteJobs.remove(localJob.id) }
                do {
                    guard let credential = try await runnerVault.credential(for: endpoint.id) else { return }
                    let client = try RunnerClient(endpoint: endpoint, credential: credential)
                    let terminal = try await observe(localJobID: localJob.id, reference: reference, client: client)
                    let finished = try await jobsStore.transition(id: localJob.id, to: Self.localState(for: terminal))
                    replace(finished)
                } catch {
                    runnerDetail = "Runner job is durable; reconnect pending"
                }
            }
        }
    }

    private func resumePendingSubmissions(using endpoints: [RunnerEndpoint]) {
        Task { [weak self] in
            guard let self else { return }
            for pending in await jobsStore.pending() {
                guard !observedRemoteJobs.contains(pending.localJobID),
                      let endpoint = endpoints.first(where: { $0.id == pending.endpointID }) else { continue }
                observedRemoteJobs.insert(pending.localJobID)
                Task { [weak self] in
                    guard let self else { return }
                    defer { observedRemoteJobs.remove(pending.localJobID) }
                    do {
                        guard let credential = try await runnerVault.credential(for: endpoint.id) else { return }
                        let client = try RunnerClient(endpoint: endpoint, credential: credential)
                        let prepared = WorkspaceExecutionService.PreparedSubmission(
                            request: pending.request,
                            snapshotDigest: pending.request.snapshotDigest
                        )
                        let submission = try await workspaceExecution.submit(prepared, client: client)
                        let reference = ForgeJob.RemoteReference(
                            endpointID: endpoint.id,
                            jobID: submission.runnerJobID
                        )
                        let attached = try await jobsStore.attachRemoteAndStart(
                            id: pending.localJobID,
                            reference: reference
                        )
                        replace(attached)
                        let terminal = try await observe(
                            localJobID: pending.localJobID,
                            reference: reference,
                            client: client
                        )
                        let finished = try await jobsStore.transition(
                            id: pending.localJobID,
                            to: Self.localState(for: terminal)
                        )
                        replace(finished)
                    } catch {
                        runnerDetail = "Runner submission is durable; reconciliation pending"
                    }
                }
            }
        }
    }

    private func observe(
        localJobID: UUID,
        reference: ForgeJob.RemoteReference,
        client: RunnerClient
    ) async throws -> RunnerJobStatus {
        var terminal: RunnerJobStatus?
        for try await event in client.events(
            jobID: reference.jobID,
            after: reference.lastEventSequence
        ) {
            let updated = try await jobsStore.updateRemoteCursor(id: localJobID, sequence: event.sequence)
            replace(updated)
            if event.type == "output", case .string(let text) = event.data["text"],
               let workspaceID = updated.workspaceID {
                appendOutput(text, workspaceID: workspaceID)
            }
            if event.type == "status", case .string(let raw) = event.data["status"] {
                terminal = RunnerJobStatus(rawValue: raw)
            }
            if terminal?.isTerminal == true { break }
        }
        if let terminal { return terminal }
        return try await client.job(id: reference.jobID).status
    }

    private nonisolated static func localState(for remote: RunnerJobStatus) -> ForgeJob.State {
        switch remote {
        case .succeeded: .succeeded
        case .cancelled: .cancelled
        case .failed: .failed
        case .queued, .running: .failed
        }
    }

    private func appendOutput(_ text: String, workspaceID: UUID) {
        let limit = 1_000_000
        var output = outputByWorkspace[workspaceID, default: ""]
        output.append(text)
        if output.utf8.count > limit {
            output = String(output.suffix(limit))
        }
        outputByWorkspace[workspaceID] = output
    }

    private func scheduleBundledSeedInstall() {
        guard let seedRoot = Bundle.main.resourceURL?.appending(path: "SeedAssets"),
              FileManager.default.fileExists(atPath: seedRoot.path) else { return }
        let assets = assets
        Task {
            do {
                _ = try await assets.installBundledSeed()
            } catch {
                alert = .init(title: "Seed recovery data is unavailable", message: error.localizedDescription)
            }
        }
    }
}

struct ForgeAlert: Identifiable {
    let id = UUID()
    let title: String
    let message: String
}
