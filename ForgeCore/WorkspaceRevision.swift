import Foundation

public struct WorkspaceRevision: Codable, Hashable, Sendable {
    public struct Entry: Codable, Hashable, Sendable {
        public enum Kind: String, Codable, Sendable { case file, directory, symbolicLink }

        public let path: String
        public let kind: Kind
        public let digest: String?
        public let mode: UInt16
        public let size: UInt64

        public init(path: String, kind: Kind, digest: String?, mode: UInt16, size: UInt64) throws {
            guard Self.isSafeRelativePath(path) else { throw WorkspaceRevisionError.unsafePath(path) }
            if kind == .file {
                guard let digest, digest.count == 64, digest.allSatisfy(\.isHexDigit) else {
                    throw WorkspaceRevisionError.invalidDigest
                }
            }
            self.path = path
            self.kind = kind
            self.digest = digest?.lowercased()
            self.mode = mode
            self.size = size
        }

        private static func isSafeRelativePath(_ path: String) -> Bool {
            guard !path.isEmpty, !path.hasPrefix("/"), !path.contains("\\") else { return false }
            return !path.split(separator: "/", omittingEmptySubsequences: false).contains("..")
        }
    }

    public let workspaceID: UUID
    public let sequence: UInt64
    public let entries: [Entry]

    public init(workspaceID: UUID, sequence: UInt64, entries: [Entry]) {
        self.workspaceID = workspaceID
        self.sequence = sequence
        self.entries = entries.sorted { $0.path < $1.path }
    }
}

public enum WorkspaceRevisionError: Error, Equatable, Sendable {
    case unsafePath(String)
    case invalidDigest
}
