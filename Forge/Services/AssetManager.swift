import CryptoKit
import ForgeCore
import Foundation

actor AssetManager {
    struct InstallResult: Sendable {
        let packID: String
        let version: String
        let disposition: Disposition

        enum Disposition: Sendable {
            case installed
            case alreadyValid
            case keptNewer
        }
    }

    private struct Manifest: Decodable {
        let schemaVersion: Int
        let packID: String
        let version: String
        let expandedSize: UInt64
        let minimumRuntimeABI: String
        let compatibility: Compatibility
        let chunks: [Chunk]
        let files: [FileRecord]
        let signature: Signature

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case packID = "pack_id"
            case version
            case expandedSize = "expanded_size"
            case minimumRuntimeABI = "minimum_runtime_abi"
            case compatibility
            case chunks
            case files
            case signature
        }
    }

    private struct Compatibility: Decodable {
        let architecture: String
        let minimumIPadOS: String

        enum CodingKeys: String, CodingKey {
            case architecture
            case minimumIPadOS = "minimum_ipados"
        }
    }

    private struct Chunk: Decodable, Hashable {
        let sha256: String
        let size: UInt64
    }

    private struct FileRecord: Decodable {
        let relativePath: String
        let sha256: String
        let size: UInt64
        let mode: Int
        let chunks: [Chunk]

        enum CodingKeys: String, CodingKey {
            case relativePath = "path"
            case sha256
            case size
            case mode
            case chunks
        }
    }

    private struct Signature: Decodable {
        let algorithm: String
        let keyID: String
        let valueBase64: String

        enum CodingKeys: String, CodingKey {
            case algorithm
            case keyID = "key_id"
            case valueBase64 = "value_base64"
        }
    }

    private struct Registry: Codable {
        var schemaVersion = 1
        var packs: [String: String] = [:]

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case packs
        }
    }

    private let fileManager = FileManager.default

    /// Installs signed packs embedded by the Seed release. A thin build has no
    /// SeedAssets directory and returns immediately. Valid newer downloads win.
    func installBundledSeed() throws -> [InstallResult] {
        guard let bundleRoot = Bundle.main.resourceURL?.appending(path: "SeedAssets"),
              fileManager.fileExists(atPath: bundleRoot.path) else {
            return []
        }
        let manifestsRoot = bundleRoot.appending(path: "manifests", directoryHint: .isDirectory)
        let chunkRoot = bundleRoot.appending(path: "chunks", directoryHint: .isDirectory)
        let manifests = try fileManager.contentsOfDirectory(
            at: manifestsRoot,
            includingPropertiesForKeys: [.isRegularFileKey],
            options: [.skipsHiddenFiles]
        ).filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        guard !manifests.isEmpty else { throw AssetError.seedHasNoManifests }
        return try manifests.map { try install(manifestURL: $0, chunkRoot: chunkRoot) }
    }

    func verify(file: URL, expectedDigest: String, expectedSize: UInt64) throws {
        guard try safeFileSize(file) == expectedSize else { throw AssetError.invalidSize }
        guard try digest(file) == normalizedDigest(expectedDigest) else { throw AssetError.invalidDigest }
    }

    private func install(manifestURL: URL, chunkRoot: URL) throws -> InstallResult {
        let manifestData = try Data(contentsOf: manifestURL)
        let manifest = try verifiedManifest(manifestData)
        let runtimeRoot = try applicationSupport().appending(path: "Runtime", directoryHint: .isDirectory)
        let packsRoot = runtimeRoot.appending(path: "packs", directoryHint: .isDirectory)
        let registryURL = runtimeRoot.appending(path: "active-packs.json")
        try fileManager.createDirectory(at: packsRoot, withIntermediateDirectories: true)
        var registry = try loadRegistry(registryURL)

        if let active = registry.packs[manifest.packID] {
            guard AssetPackIdentity.isValidComponent(active) else { throw AssetError.unsafePath }
            if active.compare(manifest.version, options: .numeric) == .orderedDescending {
                let activeRoot = packsRoot
                    .appending(path: manifest.packID, directoryHint: .isDirectory)
                    .appending(path: active, directoryHint: .isDirectory)
                if try treeIsValid(
                    activeRoot,
                    manifestURL: activeRoot.appending(path: AssetPackInventory.storedManifestPath),
                    expectedPackID: manifest.packID,
                    expectedVersion: active
                ) {
                    return .init(packID: manifest.packID, version: active, disposition: .keptNewer)
                }
            }
        }

        let packRoot = packsRoot.appending(path: manifest.packID, directoryHint: .isDirectory)
        let destination = packRoot.appending(path: manifest.version, directoryHint: .isDirectory)
        if try treeIsValid(destination, manifest: manifest, includesStoredManifest: true) {
            try manifestData.write(
                to: destination.appending(path: AssetPackInventory.storedManifestPath),
                options: [.atomic, .completeFileProtectionUnlessOpen]
            )
            registry.packs[manifest.packID] = manifest.version
            try saveRegistry(registry, to: registryURL)
            return .init(packID: manifest.packID, version: manifest.version, disposition: .alreadyValid)
        }

        let staging = packRoot.appending(path: ".\(manifest.version).staging", directoryHint: .isDirectory)
        try fileManager.createDirectory(at: staging, withIntermediateDirectories: true)
        let staleStoredManifest = staging.appending(path: AssetPackInventory.storedManifestPath)
        if fileManager.fileExists(atPath: staleStoredManifest.path) {
            try fileManager.removeItem(at: staleStoredManifest)
        }
        for file in manifest.files {
            let target = try resolving(file.relativePath, beneath: staging)
            if (try? verify(file: target, expectedDigest: file.sha256, expectedSize: file.size)) != nil {
                continue
            }
            try materialize(file, from: chunkRoot, to: target)
        }
        guard try treeIsValid(staging, manifest: manifest, includesStoredManifest: false) else {
            throw AssetError.materializationFailed
        }
        try manifestData.write(
            to: staging.appending(path: AssetPackInventory.storedManifestPath),
            options: [.atomic, .completeFileProtectionUnlessOpen]
        )
        try fileManager.createDirectory(at: packRoot, withIntermediateDirectories: true)
        if fileManager.fileExists(atPath: destination.path) {
            try fileManager.removeItem(at: destination)
        }
        try fileManager.moveItem(at: staging, to: destination)
        registry.packs[manifest.packID] = manifest.version
        try saveRegistry(registry, to: registryURL)
        return .init(packID: manifest.packID, version: manifest.version, disposition: .installed)
    }

    private func verifiedManifest(_ data: Data) throws -> Manifest {
        guard var object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let signature = object.removeValue(forKey: "signature") as? [String: Any],
              signature["algorithm"] as? String == ForgeReleaseKey.algorithm,
              signature["key_id"] as? String == ForgeReleaseKey.keyID,
              let encodedSignature = signature["value_base64"] as? String,
              let signatureData = Data(base64Encoded: encodedSignature),
              let keyData = Data(base64Encoded: ForgeReleaseKey.base64) else {
            throw AssetError.invalidSignature
        }
        try validateCanonicalJSON(object)
        let canonical = try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
        let key = try Curve25519.Signing.PublicKey(rawRepresentation: keyData)
        guard key.isValidSignature(signatureData, for: canonical) else { throw AssetError.invalidSignature }
        let manifest = try JSONDecoder().decode(Manifest.self, from: data)
        guard manifest.schemaVersion == 1,
              AssetPackIdentity.isValidComponent(manifest.packID),
              AssetPackIdentity.isValidComponent(manifest.version),
              Int(manifest.minimumRuntimeABI).map({ (1...1).contains($0) }) == true,
              ["arm64", "aarch64"].contains(manifest.compatibility.architecture.lowercased()),
              supports(minimumOS: manifest.compatibility.minimumIPadOS),
              manifest.signature.keyID == ForgeReleaseKey.keyID,
              manifest.signature.algorithm == ForgeReleaseKey.algorithm else {
            throw AssetError.unsupportedManifest
        }
        var declared: [String: UInt64] = [:]
        for chunk in manifest.chunks {
            guard isDigest(chunk.sha256),
                  chunk.size <= 64 * 1_024 * 1_024,
                  declared.updateValue(chunk.size, forKey: normalizedDigest(chunk.sha256)) == nil else {
                throw AssetError.unsupportedManifest
            }
        }
        var expanded: UInt64 = 0
        var declaredFiles: Set<String> = []
        for file in manifest.files {
            _ = try safeComponents(file.relativePath)
            guard declaredFiles.insert(file.relativePath).inserted,
                  isDigest(file.sha256) else { throw AssetError.unsupportedManifest }
            var fileSize: UInt64 = 0
            for chunk in file.chunks {
                let addition = fileSize.addingReportingOverflow(chunk.size)
                guard !addition.overflow,
                      declared[normalizedDigest(chunk.sha256)] == chunk.size else {
                    throw AssetError.unsupportedManifest
                }
                fileSize = addition.partialValue
            }
            let addition = expanded.addingReportingOverflow(file.size)
            guard file.mode >= 0, file.mode <= 0o777,
                  fileSize == file.size,
                  !addition.overflow else {
                throw AssetError.unsupportedManifest
            }
            expanded = addition.partialValue
        }
        guard expanded == manifest.expandedSize else { throw AssetError.unsupportedManifest }
        return manifest
    }

    private func validateCanonicalJSON(_ value: Any) throws {
        switch value {
        case is NSNull, is String, is Bool:
            return
        case let number as NSNumber:
            guard number.doubleValue.isFinite,
                  number.doubleValue.rounded(.towardZero) == number.doubleValue else {
                throw AssetError.unsupportedManifest
            }
        case let array as [Any]:
            try array.forEach(validateCanonicalJSON)
        case let dictionary as [String: Any]:
            try dictionary.values.forEach(validateCanonicalJSON)
        default:
            throw AssetError.unsupportedManifest
        }
    }

    private func supports(minimumOS value: String) -> Bool {
        let parts = value.split(separator: ".").compactMap { Int($0) }
        guard !parts.isEmpty, parts.count <= 3 else { return false }
        return ProcessInfo.processInfo.isOperatingSystemAtLeast(
            .init(
                majorVersion: parts[0],
                minorVersion: parts.count > 1 ? parts[1] : 0,
                patchVersion: parts.count > 2 ? parts[2] : 0
            )
        )
    }

    private func materialize(_ record: FileRecord, from chunkRoot: URL, to target: URL) throws {
        try fileManager.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
        let partial = target.deletingLastPathComponent().appending(path: ".\(target.lastPathComponent).part")
        if record.size == 0, !fileManager.fileExists(atPath: partial.path) {
            guard fileManager.createFile(atPath: partial.path, contents: Data()) else {
                throw AssetError.materializationFailed
            }
        }
        let validChunks = try verifiedPrefix(of: partial, chunks: record.chunks)
        if validChunks < record.chunks.count {
            if !fileManager.fileExists(atPath: partial.path) {
                fileManager.createFile(atPath: partial.path, contents: nil)
            }
            let output = try FileHandle(forWritingTo: partial)
            defer { try? output.close() }
            try output.seekToEnd()
            for chunk in record.chunks.dropFirst(validChunks) {
                let source = chunkRoot.appending(path: normalizedDigest(chunk.sha256))
                try verify(file: source, expectedDigest: chunk.sha256, expectedSize: chunk.size)
                try output.write(contentsOf: Data(contentsOf: source, options: .mappedIfSafe))
                try output.synchronize()
            }
        }
        try verify(file: partial, expectedDigest: record.sha256, expectedSize: record.size)
        try fileManager.setAttributes([.posixPermissions: record.mode], ofItemAtPath: partial.path)
        if fileManager.fileExists(atPath: target.path) { try fileManager.removeItem(at: target) }
        try fileManager.moveItem(at: partial, to: target)
    }

    private func verifiedPrefix(of partial: URL, chunks: [Chunk]) throws -> Int {
        guard fileManager.fileExists(atPath: partial.path) else { return 0 }
        let input = try FileHandle(forReadingFrom: partial)
        defer { try? input.close() }
        var valid = 0
        var validBytes: UInt64 = 0
        for chunk in chunks {
            guard let data = try input.read(upToCount: Int(chunk.size)),
                  UInt64(data.count) == chunk.size,
                  digest(data) == normalizedDigest(chunk.sha256) else { break }
            valid += 1
            validBytes += chunk.size
        }
        let output = try FileHandle(forWritingTo: partial)
        defer { try? output.close() }
        try output.truncate(atOffset: validBytes)
        return valid
    }

    private func treeIsValid(
        _ root: URL,
        manifest: Manifest,
        includesStoredManifest: Bool
    ) throws -> Bool {
        guard fileManager.fileExists(atPath: root.path) else { return false }
        for file in manifest.files {
            let url = try resolving(file.relativePath, beneath: root)
            guard (try? verify(file: url, expectedDigest: file.sha256, expectedSize: file.size)) != nil else {
                return false
            }
        }
        guard let actualFiles = try fileInventory(beneath: root) else { return false }
        return AssetPackInventory.isExact(
            declaredFiles: manifest.files.map(\.relativePath),
            actualFiles: actualFiles,
            includesStoredManifest: includesStoredManifest
        )
    }

    private func treeIsValid(
        _ root: URL,
        manifestURL: URL,
        expectedPackID: String,
        expectedVersion: String
    ) throws -> Bool {
        guard let data = try? Data(contentsOf: manifestURL),
              let manifest = try? verifiedManifest(data),
              manifest.packID == expectedPackID,
              manifest.version == expectedVersion else { return false }
        return try treeIsValid(root, manifest: manifest, includesStoredManifest: true)
    }

    private func fileInventory(beneath root: URL) throws -> Set<String>? {
        let keys: [URLResourceKey] = [.isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey]
        var enumerationFailed = false
        guard let enumerator = fileManager.enumerator(
            at: root,
            includingPropertiesForKeys: keys,
            options: [],
            errorHandler: { _, _ in
                enumerationFailed = true
                return false
            }
        ) else { return nil }
        let rootPath = root.standardizedFileURL.path
        let prefix = rootPath.hasSuffix("/") ? rootPath : rootPath + "/"
        var files: Set<String> = []
        for case let candidate as URL in enumerator {
            let values = try candidate.resourceValues(forKeys: Set(keys))
            if values.isSymbolicLink == true { return nil }
            if values.isDirectory == true { continue }
            guard values.isRegularFile == true else { return nil }
            let path = candidate.standardizedFileURL.path
            guard path.hasPrefix(prefix) else { return nil }
            files.insert(String(path.dropFirst(prefix.count)))
        }
        return enumerationFailed ? nil : files
    }

    private func resolving(_ path: String, beneath root: URL) throws -> URL {
        try safeComponents(path).reduce(root) { $0.appending(path: $1) }
    }

    private func safeComponents(_ path: String) throws -> [String] {
        let parts = path.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        guard !path.hasPrefix("/"), !parts.isEmpty,
              parts.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else {
            throw AssetError.unsafePath
        }
        return parts
    }

    private func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private func digest(_ file: URL) throws -> String {
        let input = try FileHandle(forReadingFrom: file)
        defer { try? input.close() }
        var hasher = SHA256()
        while let data = try input.read(upToCount: 1_048_576), !data.isEmpty {
            hasher.update(data: data)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private func normalizedDigest(_ value: String) -> String {
        value.lowercased()
    }

    private func isDigest(_ value: String) -> Bool {
        value.utf8.count == 64 && value.utf8.allSatisfy { byte in
            (48...57).contains(byte) || (97...102).contains(byte)
        }
    }

    private func safeFileSize(_ file: URL) throws -> UInt64 {
        let values = try file.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              let size = values.fileSize, size >= 0 else { throw AssetError.invalidSize }
        return UInt64(size)
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

    private func loadRegistry(_ url: URL) throws -> Registry {
        guard fileManager.fileExists(atPath: url.path) else { return Registry() }
        return try JSONDecoder().decode(Registry.self, from: Data(contentsOf: url))
    }

    private func saveRegistry(_ registry: Registry, to url: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        try encoder.encode(registry).write(to: url, options: [.atomic, .completeFileProtectionUnlessOpen])
    }
}

enum AssetError: LocalizedError {
    case invalidSize
    case invalidDigest
    case invalidSignature
    case unsupportedManifest
    case unsafePath
    case seedHasNoManifests
    case materializationFailed

    var errorDescription: String? {
        switch self {
        case .invalidSize: "An asset size does not match its signed manifest."
        case .invalidDigest: "An asset checksum does not match its signed manifest."
        case .invalidSignature: "The asset catalog signature is invalid."
        case .unsupportedManifest: "The asset manifest is malformed or unsupported."
        case .unsafePath: "The asset manifest contains an unsafe file path."
        case .seedHasNoManifests: "The Seed payload contains no signed pack manifests."
        case .materializationFailed: "The Seed payload could not be verified after installation."
        }
    }
}
