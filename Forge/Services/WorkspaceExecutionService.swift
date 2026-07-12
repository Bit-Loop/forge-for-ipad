import CryptoKit
import ForgeCore
import Foundation

actor WorkspaceExecutionService {
    struct PreparedSubmission: Sendable {
        let request: RunnerJobRequest
        let snapshotDigest: String
    }

    struct Submission: Sendable {
        let runnerJobID: String
        let replayed: Bool
        let snapshotDigest: String
    }

    func prepare(
        workspace: WorkspaceSummary,
        commands: [ToolchainCommand],
        idempotencyKey: String,
        targetArchitecture: String,
        client: RunnerClient
    ) async throws -> PreparedSubmission {
        guard !commands.isEmpty else {
            throw RunnerClientError.invalidRequest("A runner pipeline cannot be empty.")
        }
        let files = try snapshotFiles(at: workspace.root)
        var entries: [RunnerSnapshotEntry] = []
        entries.reserveCapacity(files.count)
        for file in files {
            let exists = try await client.containsBlob(digest: file.digest)
            if !exists {
                try await client.uploadBlob(file: file.url, digest: file.digest)
            }
            entries.append(.init(
                path: file.relativePath,
                kind: .file,
                digest: file.digest,
                size: file.size,
                mode: file.mode
            ))
        }
        let snapshot = try await client.createSnapshot(entries: entries)
        let networkedSteps = commands.prefix { $0.phase == .fetch }.count
        let request = RunnerJobRequest(
            idempotencyKey: idempotencyKey,
            snapshotDigest: snapshot.digest,
            argv: nil,
            steps: commands.map { [$0.executable] + $0.arguments },
            image: nil,
            targetArchitecture: targetArchitecture,
            limits: .init(cpus: 4, memoryMB: 8_192, timeoutSeconds: 7_200, pids: 4_096),
            network: .init(
                enabled: networkedSteps > 0,
                networkedSteps: networkedSteps > 0 ? networkedSteps : nil
            ),
            artifactGlobs: [".forge/build/**/*", "dist/**/*", "target/release/*"]
        )
        try request.validate()
        return .init(request: request, snapshotDigest: snapshot.digest)
    }

    func submit(_ prepared: PreparedSubmission, client: RunnerClient) async throws -> Submission {
        let created = try await client.submit(prepared.request)
        return .init(
            runnerJobID: created.job.id,
            replayed: created.replayed,
            snapshotDigest: prepared.snapshotDigest
        )
    }

    func plans(for workspace: WorkspaceSummary) throws -> [ToolchainPlan] {
        ToolchainPlanner.plans(files: try snapshotFiles(at: workspace.root).map(\.relativePath))
    }

    private struct SnapshotFile {
        let url: URL
        let relativePath: String
        let digest: String
        let size: Int64
        let mode: Int
    }

    private func snapshotFiles(at root: URL) throws -> [SnapshotFile] {
        let keys: [URLResourceKey] = [.isRegularFileKey, .isDirectoryKey, .isSymbolicLinkKey, .fileSizeKey]
        guard let enumerator = FileManager.default.enumerator(
            at: root,
            includingPropertiesForKeys: keys,
            options: [],
            errorHandler: { _, _ in false }
        ) else { throw WorkspaceExecutionError.unreadableWorkspace }
        var result: [SnapshotFile] = []
        for case let url as URL in enumerator {
            let relative = String(url.path.dropFirst(root.path.count + 1))
            if shouldSkip(relative) {
                if (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true {
                    enumerator.skipDescendants()
                }
                continue
            }
            let values = try url.resourceValues(forKeys: Set(keys))
            guard values.isSymbolicLink != true else { throw WorkspaceExecutionError.symbolicLink(relative) }
            guard values.isRegularFile == true else { continue }
            guard result.count < 250_000, let size = values.fileSize, size >= 0 else {
                throw WorkspaceExecutionError.workspaceTooLarge
            }
            result.append(.init(
                url: url,
                relativePath: relative,
                digest: try digest(url),
                size: Int64(size),
                mode: ((try? FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions]) as? NSNumber)?.intValue ?? 0o644
            ))
        }
        return result.sorted { $0.relativePath < $1.relativePath }
    }

    private func shouldSkip(_ path: String) -> Bool {
        let first = path.split(separator: "/").first.map(String.init)
        return first == ".git" || path.hasPrefix(".forge/build") || first == ".build"
    }

    private func digest(_ url: URL) throws -> String {
        let input = try FileHandle(forReadingFrom: url)
        defer { try? input.close() }
        var hash = SHA256()
        while let data = try input.read(upToCount: 1_048_576), !data.isEmpty { hash.update(data: data) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

enum WorkspaceExecutionError: LocalizedError {
    case unreadableWorkspace
    case workspaceTooLarge
    case symbolicLink(String)

    var errorDescription: String? {
        switch self {
        case .unreadableWorkspace: "The workspace could not be enumerated."
        case .workspaceTooLarge: "The workspace exceeds the 250,000-file runner limit."
        case .symbolicLink(let path): "The runner snapshot rejected symbolic link \(path)."
        }
    }
}
