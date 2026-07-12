import CryptoKit
import Foundation

actor WorkspaceCoordinator {
    private struct ExternalWorkspace: Codable {
        var name: String
        var bookmark: Data
    }

    private let fileManager = FileManager.default
    private var activeSecurityScopes: [URL] = []
    private let encoder: JSONEncoder = {
        let value = JSONEncoder()
        value.dateEncodingStrategy = .iso8601
        value.outputFormatting = [.prettyPrinted, .sortedKeys]
        return value
    }()

    func list() throws -> [WorkspaceSummary] {
        let root = try workspacesRoot()
        let local: [WorkspaceSummary] = try fileManager.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.contentModificationDateKey, .isDirectoryKey],
            options: [.skipsHiddenFiles]
        ).compactMap { url -> WorkspaceSummary? in
            let values = try url.resourceValues(forKeys: [.contentModificationDateKey, .isDirectoryKey])
            guard values.isDirectory == true else { return nil }
            let id = stableID(for: url.absoluteString)
            return WorkspaceSummary(
                id: id,
                name: url.lastPathComponent,
                root: url,
                lastOpenedAt: values.contentModificationDate ?? .distantPast
            )
        }
        let external = try loadExternalWorkspaces()
        return (local + external).sorted { $0.lastOpenedAt > $1.lastOpenedAt }
    }

    func create(named rawName: String) throws -> WorkspaceSummary {
        let name = try sanitizedName(rawName)
        let root = try workspacesRoot().appending(path: name, directoryHint: .isDirectory)
        guard !fileManager.fileExists(atPath: root.path) else {
            throw WorkspaceError.alreadyExists(name)
        }
        try fileManager.createDirectory(at: root, withIntermediateDirectories: false)
        try fileManager.createDirectory(at: root.appending(path: ".forge"), withIntermediateDirectories: false)
        let readme = "# \(name)\n\nCreated with Forge for iPad.\n"
        try Data(readme.utf8).write(to: root.appending(path: "README.md"), options: .atomic)
        let manifest = "schema = 1\nname = \"\(name)\"\nruntime = \"ubuntu\"\n"
        try Data(manifest.utf8).write(to: root.appending(path: ".forge/project.toml"), options: .atomic)
        return WorkspaceSummary(id: stableID(for: root.absoluteString), name: name, root: root, lastOpenedAt: .now)
    }

    func registerExternalFolder(_ url: URL) throws -> WorkspaceSummary {
        let canonicalURL = url.standardizedFileURL
        let alreadyActive = activeSecurityScopes.contains(canonicalURL)
        guard alreadyActive || url.startAccessingSecurityScopedResource() else {
            throw WorkspaceError.accessDenied
        }
        do {
            let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true else {
                throw WorkspaceError.notDirectory
            }
            let bookmark = try url.bookmarkData(
                options: .minimalBookmark,
                includingResourceValuesForKeys: [.isDirectoryKey],
                relativeTo: nil,
            )
            var records = try externalRecords()
            records.removeAll { record in
                var stale = false
                guard let existing = try? URL(
                    resolvingBookmarkData: record.bookmark,
                    options: .withoutUI,
                    relativeTo: nil,
                    bookmarkDataIsStale: &stale
                ) else { return false }
                return existing.standardizedFileURL == canonicalURL
            }
            records.append(.init(name: url.lastPathComponent, bookmark: bookmark))
            try saveExternalRecords(records)
            let result = try summary(for: url, name: url.lastPathComponent)
            if !alreadyActive { activeSecurityScopes.append(canonicalURL) }
            return result
        } catch {
            if !alreadyActive { url.stopAccessingSecurityScopedResource() }
            throw error
        }
    }

    func importExternalFile(_ url: URL) throws -> WorkspaceSummary {
        guard url.startAccessingSecurityScopedResource() else { throw WorkspaceError.accessDenied }
        defer { url.stopAccessingSecurityScopedResource() }
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true else {
            throw WorkspaceError.notRegularFile
        }
        let rawBase = url.deletingPathExtension().lastPathComponent
        let cleanedBase = rawBase.components(separatedBy: CharacterSet(charactersIn: "/:")).joined(separator: "-")
        let base = try sanitizedName(cleanedBase.isEmpty ? "Imported" : cleanedBase)
        var name = base
        let workspaces = try workspacesRoot()
        var root = workspaces.appending(path: name, directoryHint: .isDirectory)
        var suffix = 2
        while fileManager.fileExists(atPath: root.path) {
            name = "\(base)-\(suffix)"
            root = workspaces.appending(path: name, directoryHint: .isDirectory)
            suffix += 1
        }
        try fileManager.createDirectory(at: root, withIntermediateDirectories: false)
        try fileManager.createDirectory(at: root.appending(path: ".forge"), withIntermediateDirectories: false)
        let destination = root.appending(path: url.lastPathComponent)
        try fileManager.copyItem(at: url, to: destination)
        let manifest = "schema = 1\nname = \"\(name)\"\nruntime = \"ubuntu\"\n"
        try Data(manifest.utf8).write(to: root.appending(path: ".forge/project.toml"), options: .atomic)
        return WorkspaceSummary(id: stableID(for: root.absoluteString), name: name, root: root, lastOpenedAt: .now)
    }

    func appendJournal(_ operation: EditJournalOperation, workspaceID: UUID) throws {
        let root = try applicationSupport().appending(path: "Journals/\(workspaceID.uuidString)")
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
        let file = root.appending(path: "operations.jsonl")
        var data = try encoder.encode(operation)
        data.append(0x0A)
        if !fileManager.fileExists(atPath: file.path) {
            try data.write(to: file, options: [.atomic, .completeFileProtectionUnlessOpen])
            return
        }
        let handle = try FileHandle(forWritingTo: file)
        defer { try? handle.close() }
        try handle.seekToEnd()
        try handle.write(contentsOf: data)
        try handle.synchronize()
    }

    func commitEdit(
        _ operation: EditJournalOperation,
        workspaceID: UUID,
        file: URL,
        contents: Data,
        expectedDigest: String
    ) throws -> String {
        let current = try Data(contentsOf: file)
        guard Self.digest(current) == expectedDigest else { throw WorkspaceError.staleEdit }
        try appendJournal(operation, workspaceID: workspaceID)
        try contents.write(to: file, options: [.atomic, .completeFileProtectionUnlessOpen])
        return Self.digest(contents)
    }

    private static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private func workspacesRoot() throws -> URL {
        let root = try fileManager.url(
            for: .documentDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appending(path: "Workspaces", directoryHint: .isDirectory)
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private func applicationSupport() throws -> URL {
        let root = try fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        ).appending(path: "Forge", directoryHint: .isDirectory)
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private func sanitizedName(_ raw: String) throws -> String {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, value != ".", value != "..",
              !value.contains("/"), !value.contains(":") else {
            throw WorkspaceError.invalidName
        }
        return value
    }

    private func stableID(for identity: String) -> UUID {
        let digest = SHA256.hash(data: Data(identity.utf8))
        var bytes = Array(digest.prefix(16))
        bytes[6] = (bytes[6] & 0x0F) | 0x50
        bytes[8] = (bytes[8] & 0x3F) | 0x80
        return UUID(uuid: (
            bytes[0], bytes[1], bytes[2], bytes[3],
            bytes[4], bytes[5], bytes[6], bytes[7],
            bytes[8], bytes[9], bytes[10], bytes[11],
            bytes[12], bytes[13], bytes[14], bytes[15]
        ))
    }

    private func externalRecordsURL() throws -> URL {
        try applicationSupport().appending(path: "State/external-workspaces.json")
    }

    private func externalRecords() throws -> [ExternalWorkspace] {
        let url = try externalRecordsURL()
        guard fileManager.fileExists(atPath: url.path) else { return [] }
        return try JSONDecoder().decode([ExternalWorkspace].self, from: Data(contentsOf: url))
    }

    private func saveExternalRecords(_ records: [ExternalWorkspace]) throws {
        let url = try externalRecordsURL()
        try fileManager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try encoder.encode(records).write(to: url, options: [.atomic, .completeFileProtectionUnlessOpen])
    }

    private func loadExternalWorkspaces() throws -> [WorkspaceSummary] {
        var resolved: [WorkspaceSummary] = []
        var refreshed: [ExternalWorkspace] = []
        for record in try externalRecords() {
            var stale = false
            guard let url = try? URL(
                resolvingBookmarkData: record.bookmark,
                options: .withoutUI,
                relativeTo: nil,
                bookmarkDataIsStale: &stale
            ) else { continue }
            let canonicalURL = url.standardizedFileURL
            let alreadyActive = activeSecurityScopes.contains(canonicalURL)
            guard alreadyActive || url.startAccessingSecurityScopedResource() else { continue }
            guard let summary = try? summary(for: url, name: record.name) else {
                if !alreadyActive { url.stopAccessingSecurityScopedResource() }
                continue
            }
            if !alreadyActive { activeSecurityScopes.append(canonicalURL) }
            resolved.append(summary)
            let bookmark = stale
                ? (try? url.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil))
                : record.bookmark
            refreshed.append(.init(name: record.name, bookmark: bookmark ?? record.bookmark))
        }
        try saveExternalRecords(refreshed)
        return resolved
    }

    private func summary(for url: URL, name: String) throws -> WorkspaceSummary {
        let values = try url.resourceValues(forKeys: [.contentModificationDateKey, .isDirectoryKey])
        guard values.isDirectory == true else { throw WorkspaceError.notDirectory }
        return WorkspaceSummary(
            id: stableID(for: url.standardizedFileURL.absoluteString),
            name: name,
            root: url,
            lastOpenedAt: values.contentModificationDate ?? .now
        )
    }
}

struct EditJournalOperation: Codable, Sendable {
    let sequence: UInt64
    let relativePath: String
    let UTF16Location: Int
    let UTF16Length: Int
    let replacement: String
    let recordedAt: Date
}

enum WorkspaceError: LocalizedError {
    case invalidName
    case alreadyExists(String)
    case notDirectory
    case notRegularFile
    case accessDenied
    case invalidEncoding
    case staleEdit

    var errorDescription: String? {
        switch self {
        case .invalidName: "Choose a nonempty folder name without slashes or colons."
        case .alreadyExists(let name): "A workspace named \(name) already exists."
        case .notDirectory: "Choose a folder rather than a file or symbolic link."
        case .notRegularFile: "Choose a regular source file rather than a folder or symbolic link."
        case .accessDenied: "Forge could not retain access to that Files folder."
        case .invalidEncoding: "Forge currently edits UTF-8 source files only."
        case .staleEdit: "This file changed in another window. Reload it before saving again."
        }
    }
}
