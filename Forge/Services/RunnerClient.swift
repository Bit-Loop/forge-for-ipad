import CryptoKit
import Foundation
import ForgeCore

/// Typed client for the durable Forge Runner v1 contract.
final class RunnerClient: @unchecked Sendable {
    let endpoint: RunnerEndpoint
    private let credential: RunnerCredential?
    private let session: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    convenience init(endpoint: RunnerEndpoint, credential: RunnerCredential? = nil) throws {
        try self.init(endpoint: endpoint, credential: credential, allowUnverifiedCredential: false)
    }

    private init(
        endpoint: RunnerEndpoint,
        credential: RunnerCredential?,
        allowUnverifiedCredential: Bool
    ) throws {
        self.endpoint = try RunnerEndpoint(
            name: endpoint.name,
            baseURL: endpoint.baseURL,
            interfaceNames: endpoint.interfaceNames,
            metadata: endpoint.metadata,
            discoveryID: endpoint.discoveryID,
            advertisedInstanceID: endpoint.advertisedInstanceID,
            instanceID: endpoint.instanceID
        )
        if let credential {
            let isVerifiedMatch = endpoint.instanceID?.rawValue == credential.endpointID
            guard credential.pairedBaseURL == endpoint.baseURL,
                  isVerifiedMatch || allowUnverifiedCredential else {
                throw RunnerClientError.invalidCredential
            }
        }
        self.credential = credential
        encoder = JSONEncoder()
        decoder = JSONDecoder()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = true
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 7 * 24 * 3_600
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        session = URLSession(
            configuration: configuration,
            delegate: RunnerRedirectDelegate.shared,
            delegateQueue: nil
        )
    }

    static func pair(
        endpoint: RunnerEndpoint,
        code: String,
        vault: RunnerCredentialVault
    ) async throws -> RunnerClient {
        guard (6...128).contains(code.count) else {
            throw RunnerClientError.invalidRequest("Pairing codes are 6 to 128 characters.")
        }
        let existingCredential: RunnerCredential?
        if let instanceID = endpoint.advertisedInstanceID {
            existingCredential = try await vault.credential(for: instanceID.rawValue)
        } else {
            existingCredential = nil
        }
        let client = try RunnerClient(endpoint: endpoint)
        let response: RunnerPairResponse = try await client.send(
            method: "POST",
            path: "/pair",
            body: RunnerPairRequest(
                code: code,
                existingTokenID: existingCredential?.tokenID
            ),
            authenticated: false
        )
        guard response.token.count >= 32,
              !response.token.contains(where: { $0.isWhitespace }),
              existingCredential == nil || response.tokenID == existingCredential?.tokenID else {
            throw RunnerClientError.invalidCredential
        }
        let provisionalCredential = RunnerCredential(
            endpointID: endpoint.id,
            pairedBaseURL: endpoint.baseURL,
            tokenID: response.tokenID,
            bearerToken: response.token,
            pairedAt: Date()
        )
        let provisionalClient = try RunnerClient(
            endpoint: endpoint,
            credential: provisionalCredential,
            allowUnverifiedCredential: true
        )
        let capabilities = try await provisionalClient.capabilities()
        let boundEndpoint: RunnerEndpoint
        do {
            boundEndpoint = try endpoint.authenticated(instanceID: capabilities.instanceID)
        } catch {
            throw RunnerClientError.instanceIdentityMismatch
        }
        let credential = RunnerCredential(
            endpointID: boundEndpoint.id,
            pairedBaseURL: boundEndpoint.baseURL,
            tokenID: response.tokenID,
            bearerToken: response.token,
            pairedAt: Date()
        )
        try await vault.save(credential)
        return try RunnerClient(endpoint: boundEndpoint, credential: credential)
    }

    /// Reauthenticates only at the paired URL; a Bonjour UUID alone never receives a saved bearer.
    static func authenticateDiscovered(
        endpoint: RunnerEndpoint,
        credential: RunnerCredential
    ) async throws -> RunnerClient {
        guard endpoint.instanceID == nil,
              let savedInstanceID = RunnerInstanceID(credential.endpointID),
              RunnerCredentialPresentationPolicy.allows(
                  advertised: endpoint.advertisedInstanceID,
                  savedInstanceID: savedInstanceID,
                  pairedBaseURL: credential.pairedBaseURL,
                  candidateBaseURL: endpoint.baseURL
              ) else {
            throw RunnerClientError.invalidCredential
        }
        let provisionalClient = try RunnerClient(
            endpoint: endpoint,
            credential: credential,
            allowUnverifiedCredential: true
        )
        let capabilities = try await provisionalClient.capabilities()
        let boundEndpoint: RunnerEndpoint
        do {
            boundEndpoint = try endpoint.authenticated(instanceID: capabilities.instanceID)
        } catch {
            throw RunnerClientError.instanceIdentityMismatch
        }
        guard boundEndpoint.id == credential.endpointID else {
            throw RunnerClientError.instanceIdentityMismatch
        }
        return try RunnerClient(endpoint: boundEndpoint, credential: credential)
    }

    func capabilities() async throws -> RunnerCapabilities {
        let value: RunnerCapabilities = try await send(method: "GET", path: "/capabilities")
        guard value.apiVersion == "forge/v1" else {
            throw RunnerClientError.incompatibleAPI(value.apiVersion)
        }
        if let instanceID = endpoint.instanceID, value.instanceID != instanceID {
            throw RunnerClientError.instanceIdentityMismatch
        }
        return value
    }

    func containsBlob(digest: String) async throws -> Bool {
        try validateDigest(digest)
        let (_, response) = try await perform(request(method: "HEAD", path: "/blobs/\(digest)"))
        guard let http = response as? HTTPURLResponse else { throw RunnerClientError.invalidResponse }
        if http.statusCode == 404 { return false }
        guard http.statusCode == 200 else { throw responseError(status: http.statusCode, data: Data()) }
        return true
    }

    func uploadBlob(_ data: Data, digest: String) async throws {
        try validateDigest(digest)
        guard Self.sha256(data) == digest else { throw RunnerClientError.digestMismatch }
        var request = try request(method: "PUT", path: "/blobs/\(digest)")
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        request.setValue(String(data.count), forHTTPHeaderField: "Content-Length")
        let (_, response) = try await session.upload(for: request, from: data)
        try accept(response, data: Data(), statuses: [201, 204])
    }

    func uploadBlob(file: URL, digest: String) async throws {
        try validateDigest(digest)
        guard try Self.sha256(file: file) == digest else { throw RunnerClientError.digestMismatch }
        var request = try request(method: "PUT", path: "/blobs/\(digest)")
        request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        let size = try file.resourceValues(forKeys: [.fileSizeKey]).fileSize
        guard let size, size >= 0 else { throw RunnerClientError.invalidRequest("Blob is not a regular file.") }
        request.setValue(String(size), forHTTPHeaderField: "Content-Length")
        let (_, response) = try await session.upload(for: request, fromFile: file)
        try accept(response, data: Data(), statuses: [201, 204])
    }

    func createSnapshot(entries: [RunnerSnapshotEntry]) async throws -> RunnerSnapshotResponse {
        guard entries.count <= 250_000 else {
            throw RunnerClientError.invalidRequest("A snapshot may contain at most 250,000 entries.")
        }
        return try await send(
            method: "POST",
            path: "/snapshots",
            body: RunnerSnapshotRequest(entries: entries)
        )
    }

    func submit(_ request: RunnerJobRequest) async throws -> RunnerJobCreated {
        try request.validate()
        return try await send(method: "POST", path: "/jobs", body: request, statuses: [202])
    }

    func job(id: String) async throws -> RunnerJob {
        try await send(method: "GET", path: "/jobs/\(try pathComponent(id))")
    }

    func cancelJob(id: String) async throws -> RunnerJob {
        try await send(method: "DELETE", path: "/jobs/\(try pathComponent(id))")
    }

    func artifacts(jobID: String) async throws -> [RunnerArtifact] {
        try await send(method: "GET", path: "/jobs/\(try pathComponent(jobID))/artifacts")
    }

    /// Downloads to a sibling staging file, verifies SHA-256, then atomically replaces the target.
    func downloadArtifact(digest: String, to destination: URL) async throws {
        try validateDigest(digest)
        let request = try request(method: "GET", path: "/artifacts/\(digest)")
        let (temporary, response) = try await session.download(for: request)
        try accept(response, data: Data(), statuses: [200])
        let directory = destination.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let staging = directory.appending(path: ".\(destination.lastPathComponent).\(UUID().uuidString).partial")
        defer { try? FileManager.default.removeItem(at: staging) }
        try FileManager.default.moveItem(at: temporary, to: staging)
        guard try Self.sha256(file: staging) == digest else { throw RunnerClientError.digestMismatch }
        if FileManager.default.fileExists(atPath: destination.path) {
            _ = try FileManager.default.replaceItemAt(destination, withItemAt: staging)
        } else {
            try FileManager.default.moveItem(at: staging, to: destination)
        }
    }

    /// Reconnects with `after=<last sequence>` so an interruption cannot duplicate log events.
    func events(
        jobID: String,
        after initialSequence: Int64 = 0,
        follow: Bool = true
    ) -> AsyncThrowingStream<RunnerEvent, Error> {
        AsyncThrowingStream(bufferingPolicy: .unbounded) { continuation in
            let task = Task {
                do {
                    let id = try pathComponent(jobID)
                    var cursor = max(0, initialSequence)
                    var terminal = false
                    var retry = 0
                    repeat {
                        do {
                            var streamRequest = try request(
                                method: "GET",
                                path: "/jobs/\(id)/events",
                                query: [
                                    .init(name: "after", value: String(cursor)),
                                    .init(name: "follow", value: follow ? "true" : "false"),
                                ]
                            )
                            streamRequest.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                            streamRequest.setValue(String(cursor), forHTTPHeaderField: "Last-Event-ID")
                            let (bytes, response) = try await session.bytes(for: streamRequest)
                            guard let http = response as? HTTPURLResponse else {
                                throw RunnerClientError.invalidResponse
                            }
                            guard (200..<300).contains(http.statusCode) else {
                                var errorData = Data()
                                for try await byte in bytes { errorData.append(byte) }
                                throw responseError(status: http.statusCode, data: errorData)
                            }
                            retry = 0
                            var parser = RunnerSSEParser(decoder: decoder)
                            for try await line in bytes.lines {
                                try Task.checkCancellation()
                                if let event = try parser.consume(line: line) {
                                    guard event.sequence > cursor else { continue }
                                    cursor = event.sequence
                                    continuation.yield(event)
                                    terminal = Self.isTerminal(event)
                                }
                            }
                        } catch let error as URLError where follow && Self.retryable(error) {
                            retry = min(retry + 1, 6)
                            try await Task.sleep(for: .milliseconds(250 * (1 << retry)))
                            continue
                        } catch is CancellationError {
                            throw CancellationError()
                        } catch {
                            throw error
                        }
                        if follow && !terminal {
                            try await Task.sleep(for: .milliseconds(350))
                        }
                    } while follow && !terminal
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func send<Response: Decodable, Body: Encodable>(
        method: String,
        path: String,
        body: Body,
        authenticated: Bool = true,
        statuses: Set<Int> = [200]
    ) async throws -> Response {
        var request = try request(method: method, path: path, authenticated: authenticated)
        request.httpBody = try encoder.encode(body)
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (data, response) = try await perform(request)
        try accept(response, data: data, statuses: statuses)
        do { return try decoder.decode(Response.self, from: data) }
        catch { throw RunnerClientError.decoding(error) }
    }

    private func send<Response: Decodable>(
        method: String,
        path: String,
        statuses: Set<Int> = [200]
    ) async throws -> Response {
        let (data, response) = try await perform(request(method: method, path: path))
        try accept(response, data: data, statuses: statuses)
        do { return try decoder.decode(Response.self, from: data) }
        catch { throw RunnerClientError.decoding(error) }
    }

    private func perform(_ request: URLRequest) async throws -> (Data, URLResponse) {
        do { return try await session.data(for: request) }
        catch { throw RunnerClientError.transport(error) }
    }

    private func request(
        method: String,
        path: String,
        query: [URLQueryItem] = [],
        authenticated: Bool = true
    ) throws -> URLRequest {
        guard path.hasPrefix("/"), !path.contains("..") else {
            throw RunnerClientError.invalidRequest("Invalid API path.")
        }
        var components = URLComponents(url: endpoint.baseURL, resolvingAgainstBaseURL: false)
        components?.path = "/forge/v1\(path)"
        components?.queryItems = query.isEmpty ? nil : query
        guard let url = components?.url else { throw RunnerClientError.invalidEndpoint }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Forge-for-iPad/0.1", forHTTPHeaderField: "User-Agent")
        if authenticated {
            guard let credential else { throw RunnerClientError.notPaired }
            request.setValue("Bearer \(credential.bearerToken)", forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private func accept(_ response: URLResponse, data: Data, statuses: Set<Int>) throws {
        guard let http = response as? HTTPURLResponse else { throw RunnerClientError.invalidResponse }
        guard statuses.contains(http.statusCode) else {
            throw responseError(status: http.statusCode, data: data)
        }
    }

    private func responseError(status: Int, data: Data) -> RunnerClientError {
        let detail = (try? decoder.decode(RunnerProblem.self, from: data).detail) ??
            HTTPURLResponse.localizedString(forStatusCode: status)
        return .server(status: status, message: detail)
    }

    private func validateDigest(_ digest: String) throws {
        guard RunnerDigest.isSHA256(digest) else {
            throw RunnerClientError.invalidRequest("Digest must be lowercase SHA-256.")
        }
    }

    private func pathComponent(_ value: String) throws -> String {
        guard !value.isEmpty,
              value.rangeOfCharacter(from: CharacterSet.alphanumerics.union(.init(charactersIn: "-_" )).inverted) == nil else {
            throw RunnerClientError.invalidRequest("Invalid identifier.")
        }
        return value
    }

    private static func isTerminal(_ event: RunnerEvent) -> Bool {
        guard event.type == "status", case .string(let value) = event.data["status"] else { return false }
        return ["succeeded", "failed", "cancelled"].contains(value)
    }

    private static func retryable(_ error: URLError) -> Bool {
        switch error.code {
        case .cancelled, .badURL, .unsupportedURL, .userAuthenticationRequired,
             .secureConnectionFailed, .serverCertificateUntrusted,
             .serverCertificateHasBadDate, .serverCertificateHasUnknownRoot,
             .serverCertificateNotYetValid, .clientCertificateRejected:
            return false
        default:
            return true
        }
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func sha256(file: URL) throws -> String {
        guard let input = InputStream(url: file) else { throw RunnerClientError.invalidResponse }
        input.open()
        defer { input.close() }
        var digest = SHA256()
        let buffer = UnsafeMutablePointer<UInt8>.allocate(capacity: 1_048_576)
        defer { buffer.deallocate() }
        while true {
            let count = input.read(buffer, maxLength: 1_048_576)
            if count < 0 { throw input.streamError ?? RunnerClientError.invalidResponse }
            if count == 0 { break }
            digest.update(bufferPointer: UnsafeRawBufferPointer(start: buffer, count: count))
        }
        return digest.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

enum RunnerEndpointPolicy {
    static func validatedBaseURL(_ url: URL) throws -> URL {
        guard var components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              let scheme = components.scheme?.lowercased(),
              let host = components.host?.lowercased(),
              components.path.isEmpty || components.path == "/" else {
            throw RunnerClientError.invalidEndpoint
        }
        guard scheme == "https" || (scheme == "http" && isLocal(host)) else {
            throw RunnerClientError.insecureEndpoint
        }
        if components.port == nil { components.port = scheme == "https" ? 443 : 80 }
        components.scheme = scheme
        components.host = host
        components.path = ""
        guard let normalized = components.url else { throw RunnerClientError.invalidEndpoint }
        return normalized
    }

    private static func isLocal(_ rawHost: String) -> Bool {
        let host = rawHost.replacingOccurrences(of: "%25", with: "%")
        if host == "localhost" || host.hasSuffix(".local") { return true }
        if host == "::1" { return true }
        let address = host.split(separator: "%", maxSplits: 1).first.map(String.init) ?? host
        if address.contains(":") {
            guard let first = address.split(separator: ":", omittingEmptySubsequences: false).first,
                  let value = UInt16(first, radix: 16) else { return false }
            return value & 0xffc0 == 0xfe80 || value & 0xfe00 == 0xfc00
        }
        let pieces = address.split(separator: ".", omittingEmptySubsequences: false)
        guard pieces.count == 4 else { return false }
        let parsed = pieces.map { UInt8($0) }
        guard parsed.allSatisfy({ $0 != nil }) else { return false }
        let octets = parsed.compactMap { $0 }
        return octets[0] == 10 ||
            octets[0] == 127 ||
            (octets[0] == 169 && octets[1] == 254) ||
            (octets[0] == 172 && (16...31).contains(octets[1])) ||
            (octets[0] == 192 && octets[1] == 168)
    }
}

private struct RunnerProblem: Decodable { let detail: String }

private struct RunnerSSEParser {
    let decoder: JSONDecoder
    private var dataLines: [String] = []

    init(decoder: JSONDecoder) { self.decoder = decoder }

    mutating func consume(line: String) throws -> RunnerEvent? {
        if line.isEmpty {
            defer { dataLines.removeAll(keepingCapacity: true) }
            guard !dataLines.isEmpty else { return nil }
            let payload = dataLines.joined(separator: "\n")
            guard let data = payload.data(using: .utf8) else { throw RunnerClientError.invalidResponse }
            return try decoder.decode(RunnerEvent.self, from: data)
        }
        if line.hasPrefix(":" ) { return nil }
        let (field, value) = Self.field(line)
        if field == "data" { dataLines.append(value) }
        return nil
    }

    private static func field(_ line: String) -> (Substring, String) {
        guard let colon = line.firstIndex(of: ":") else { return (line[...], "") }
        let field = line[..<colon]
        let start = line.index(after: colon)
        var value = String(line[start...])
        if value.first == " " { value.removeFirst() }
        return (field, value)
    }
}

private final class RunnerRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    static let shared = RunnerRedirectDelegate()

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

enum RunnerClientError: LocalizedError {
    case invalidEndpoint
    case insecureEndpoint
    case invalidCredential
    case notPaired
    case incompatibleAPI(String)
    case instanceIdentityMismatch
    case invalidRequest(String)
    case invalidResponse
    case digestMismatch
    case server(status: Int, message: String)
    case decoding(Error)
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .invalidEndpoint: "The runner URL is malformed."
        case .insecureEndpoint: "Plain HTTP is permitted only for loopback or local-network runners."
        case .invalidCredential: "The paired runner credential is invalid."
        case .notPaired: "Pair with this runner before sending authenticated requests."
        case .incompatibleAPI(let version): "Runner API \(version) is not compatible with forge/v1."
        case .instanceIdentityMismatch: "The runner's authenticated identity does not match its Bonjour advertisement or saved pairing."
        case .invalidRequest(let message): message
        case .invalidResponse: "The runner returned an invalid response."
        case .digestMismatch: "Downloaded or uploaded bytes do not match the expected SHA-256 digest."
        case .server(let status, let message): "Runner HTTP \(status): \(message)"
        case .decoding(let error): "Could not decode the runner response: \(error.localizedDescription)"
        case .transport(let error): "Runner connection failed: \(error.localizedDescription)"
        }
    }
}
