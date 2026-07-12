import CryptoKit
import CoreFoundation
import Darwin
import Foundation
import Metal
@preconcurrency import Network
import Security

struct AcceleratorBridgeConfiguration: Sendable {
    let scratchHostRoot: URL
    var scratchGuestRoot = "/run/forge/accelerator-scratch"
    var limits = AcceleratorBridgeLimits()
    var backend: (any AcceleratorBridgeBackend)?
}

protocol AcceleratorBridgeBackend: Sendable {
    var supportsCoreML: Bool { get }
    var supportsMetal: Bool { get }
    var coreMLFormats: [String] { get }
    var computeUnits: [String] { get }
    var metalLanguageVersion: String { get }
    var metalFamilies: [String] { get }

    func resourceUsage() async -> AcceleratorBackendResourceUsage
    func respond(to request: AcceleratorBackendRequest) async throws -> AcceleratorBackendResponse
}

struct AcceleratorBackendResourceUsage: Sendable {
    let activeJobs: Int
    let modelHandles: Int
    let libraryHandles: Int
}

struct AcceleratorVerifiedScratchObject: Sendable {
    let reference: AcceleratorScratchReference
    /// Private, read-only host copy whose bytes were hashed before backend delegation.
    let hostURL: URL
}

struct AcceleratorBackendRequest: Sendable {
    let method: String
    let path: String
    let query: String?
    let body: Data
    let requestID: UUID
    let bootID: UUID
    /// Every scratch object as a private immutable copy, never as a guest-replaceable path.
    let verifiedScratchObjects: [AcceleratorVerifiedScratchObject]
    /// Async backends must retain this lease until the accepted job reaches a terminal state.
    let lease: AcceleratorBackendLease
}

final class AcceleratorBackendLease: @unchecked Sendable {
    private let lock = NSLock()
    private var released = false
    private let stagingRoot: URL
    private let onRelease: @Sendable () async -> Void

    init(stagingRoot: URL, onRelease: @escaping @Sendable () async -> Void) {
        self.stagingRoot = stagingRoot
        self.onRelease = onRelease
    }

    func release() {
        let shouldRelease = lock.withLock {
            guard !released else { return false }
            released = true
            return true
        }
        guard shouldRelease else { return }
        let stagingRoot = stagingRoot
        let onRelease = onRelease
        Task.detached(priority: .utility) {
            try? FileManager.default.removeItem(at: stagingRoot)
            await onRelease()
        }
    }

    deinit { release() }
}

struct AcceleratorBackendResponse: Sendable {
    var status = 200
    var contentType = "application/json"
    let body: Data
}

/// Guest-only HTTP bridge. The token and all backend handles are replaced for every guest boot.
actor AcceleratorBridgeServer {
    let credentials: AcceleratorBootCredentials

    private let configuration: AcceleratorBridgeConfiguration
    private let encoder: JSONEncoder
    private let queue = DispatchQueue(label: "com.bitloop.forge.accelerator-bridge", qos: .userInitiated)
    private var listener: NWListener?
    private var activeBackendRequests = 0
    private var pendingModelHandles = 0
    private var pendingLibraryHandles = 0

    init(configuration: AcceleratorBridgeConfiguration) throws {
        self.configuration = configuration
        credentials = AcceleratorBootCredentials(
            bootID: UUID(),
            bearerToken: try Self.generateBearerToken()
        )
        encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    }

    var guestEnvironment: [String: String] { credentials.guestEnvironment }

    func start() throws {
        guard listener == nil else { return }
        try FileManager.default.createDirectory(
            at: configuration.scratchHostRoot,
            withIntermediateDirectories: true
        )
        let port = NWEndpoint.Port(rawValue: 4_777)!
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: port)
        let listener = try NWListener(using: parameters)
        listener.newConnectionLimit = 8
        listener.newConnectionHandler = { [weak self] connection in
            Task { await self?.accept(connection) }
        }
        listener.stateUpdateHandler = { state in
            if case .failed(let error) = state {
                NSLog("Forge accelerator listener failed: %@", String(describing: error))
            }
        }
        self.listener = listener
        listener.start(queue: queue)
    }

    func stop() {
        listener?.cancel()
        listener = nil
    }

    private func accept(_ connection: NWConnection) {
        let gate = ConnectionReadyGate()
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready where gate.claim():
                let requestGate = ConnectionReadyGate()
                self?.queue.asyncAfter(deadline: .now() + 30) {
                    if requestGate.claim() { connection.cancel() }
                }
                Task { await self?.receive(connection, buffer: Data(), requestGate: requestGate) }
            case .failed, .cancelled:
                connection.cancel()
            default:
                break
            }
        }
        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 30) {
            if gate.claim() { connection.cancel() }
        }
    }

    private func receive(
        _ connection: NWConnection,
        buffer: Data,
        requestGate: ConnectionReadyGate
    ) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1_024) {
            [weak self] content, _, complete, error in
            Task {
                guard let self else { return }
                var accumulated = buffer
                if let content { accumulated.append(content) }
                do {
                    switch try BridgeHTTPRequest.parse(
                        accumulated,
                        maxBodyBytes: self.configuration.limits.maxRequestBytes
                    ) {
                    case .incomplete:
                        if complete || error != nil {
                            _ = requestGate.claim()
                            connection.cancel()
                        } else {
                            await self.receive(
                                connection,
                                buffer: accumulated,
                                requestGate: requestGate
                            )
                        }
                    case .ready(let request):
                        guard requestGate.claim() else {
                            connection.cancel()
                            return
                        }
                        let response = await self.route(request)
                        await self.send(response, on: connection)
                    }
                } catch let error as AcceleratorHTTPError {
                    _ = requestGate.claim()
                    let response = await self.problem(
                        status: error.status,
                        code: error.code,
                        message: error.message,
                        requestID: UUID()
                    )
                    await self.send(response, on: connection)
                } catch {
                    _ = requestGate.claim()
                    connection.cancel()
                }
            }
        }
    }

    private func route(_ request: BridgeHTTPRequest) async -> BridgeHTTPResponse {
        let requestID = request.headers["x-request-id"].flatMap(UUID.init(uuidString:)) ?? UUID()
        guard request.headers["host"]?.lowercased() == AcceleratorBootCredentials.guestAuthority else {
            return problem(
                status: 403,
                code: "forbidden_authority",
                message: "The bridge accepts only the fixed QEMU guest authority.",
                requestID: requestID
            )
        }
        guard request.headers["x-forge-protocol-version"] == "1.0" else {
            return problem(
                status: 400,
                code: "invalid_request",
                message: "X-Forge-Protocol-Version must be 1.0.",
                requestID: requestID
            )
        }
        guard let authorization = request.headers["authorization"],
              authorization.hasPrefix("Bearer "),
              Self.constantTimeEqual(
                String(authorization.dropFirst("Bearer ".count)),
                credentials.bearerToken
              ) else {
            return problem(
                status: 401,
                code: "unauthorized",
                message: "The per-boot bearer token is missing, stale, or invalid.",
                requestID: requestID
            )
        }
        guard request.path.hasPrefix("/accelerator/v1") else {
            return problem(status: 404, code: "not_found", message: "Unknown route.", requestID: requestID)
        }
        let route = String(request.path.dropFirst("/accelerator/v1".count))

        if request.method == "GET", route == "/capabilities" {
            guard request.query == nil, request.body.isEmpty else {
                return problem(
                    status: 400,
                    code: "invalid_request",
                    message: "Capabilities does not accept a query or request body.",
                    requestID: requestID
                )
            }
            return json(status: 200, value: capabilities(), requestID: requestID)
        }
        if request.method == "POST", route == "/scratch/verify" {
            do {
                try Self.requireJSONContentType(request)
                let envelope = try AcceleratorRequestValidator.scratchVerify(
                    body: request.body,
                    query: request.query,
                    limits: configuration.limits
                )
                let verified = try await ScratchObjectVerifier.verify(
                    envelope.object,
                    root: configuration.scratchHostRoot,
                    maxBytes: configuration.limits.maxScratchObjectBytes
                )
                return json(status: 200, value: verified, requestID: requestID)
            } catch let error as AcceleratorScratchError {
                return problem(
                    status: error.status,
                    code: error.code,
                    message: error.message,
                    requestID: requestID
                )
            } catch let error as AcceleratorRequestValidationError {
                return problem(
                    status: error.status,
                    code: error.code,
                    message: error.message,
                    requestID: requestID
                )
            } catch {
                return problem(
                    status: 400,
                    code: "invalid_request",
                    message: "The scratch verification request is invalid.",
                    requestID: requestID
                )
            }
        }
        if Self.backendRoute(method: request.method, path: route) {
            guard let backend = configuration.backend else {
                return problem(
                    status: 501,
                    code: "unsupported",
                    message: "No Core ML or Metal execution backend is attached.",
                    requestID: requestID
                )
            }
            guard Self.backendSupports(backend, method: request.method, path: route) else {
                return problem(
                    status: 501,
                    code: "unsupported",
                    message: "The requested accelerator feature is not advertised for this boot.",
                    requestID: requestID
                )
            }
            do {
                if !request.body.isEmpty { try Self.requireJSONContentType(request) }
                let validated = try AcceleratorRequestValidator.validateBackendRequest(
                    method: request.method,
                    path: route,
                    query: request.query,
                    body: request.body,
                    limits: configuration.limits,
                    backend: backend
                )
                try await reserveBackendCapacity(for: route, backend: backend)
                let stagingRoot: URL
                do {
                    stagingRoot = try scratchStagingRoot(requestID: requestID)
                } catch {
                    releaseBackendCapacity(for: route)
                    throw error
                }
                let lease = AcceleratorBackendLease(stagingRoot: stagingRoot) { [weak self] in
                    await self?.releaseBackendCapacity(for: route)
                }
                let verified = try await stageScratchObjects(
                    validated.scratchReferences,
                    at: stagingRoot
                )
                let response = try await backend.respond(
                    to: AcceleratorBackendRequest(
                        method: request.method,
                        path: route,
                        query: request.query,
                        body: validated.body,
                        requestID: requestID,
                        bootID: credentials.bootID,
                        verifiedScratchObjects: verified,
                        lease: lease
                    )
                )
                return BridgeHTTPResponse(
                    status: response.status,
                    contentType: response.contentType,
                    headers: ["X-Request-ID": requestID.uuidString.lowercased()],
                    body: response.body
                )
            } catch let error as AcceleratorScratchError {
                return problem(
                    status: error.status,
                    code: error.code,
                    message: error.message,
                    requestID: requestID
                )
            } catch let error as AcceleratorRequestValidationError {
                return problem(
                    status: error.status,
                    code: error.code,
                    message: error.message,
                    requestID: requestID
                )
            } catch {
                return problem(
                    status: 500,
                    code: "internal",
                    message: "The accelerator backend rejected the operation.",
                    requestID: requestID
                )
            }
        }
        return problem(status: 404, code: "not_found", message: "Unknown route.", requestID: requestID)
    }

    private func stageScratchObjects(
        _ references: [AcceleratorScratchReference],
        at stagingRoot: URL
    ) async throws -> [AcceleratorVerifiedScratchObject] {
        var verified: [AcceleratorVerifiedScratchObject] = []
        verified.reserveCapacity(references.count)
        for (index, reference) in references.enumerated() {
            verified.append(
                try await ScratchObjectVerifier.stage(
                    reference,
                    root: configuration.scratchHostRoot,
                    maxBytes: configuration.limits.maxScratchObjectBytes,
                    destination: stagingRoot.appending(
                        path: "\(index)-\(reference.sha256).blob",
                        directoryHint: .notDirectory
                    )
                )
            )
        }
        return verified
    }

    private func scratchStagingRoot(requestID: UUID) throws -> URL {
        let caches = try FileManager.default.url(
            for: .cachesDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let root = caches.appending(
            path: "Forge/AcceleratorVerified/\(credentials.bootID.uuidString)/\(requestID.uuidString)-\(UUID().uuidString)",
            directoryHint: .isDirectory
        )
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        return root
    }

    private func reserveBackendCapacity(
        for route: String,
        backend: any AcceleratorBridgeBackend
    ) async throws {
        guard route.hasPrefix("/coreml/") || route.hasPrefix("/metal/") else { return }
        let usage = await backend.resourceUsage()
        guard usage.activeJobs >= 0, usage.modelHandles >= 0, usage.libraryHandles >= 0 else {
            throw AcceleratorRequestValidationError.invalid("Backend resource usage is invalid.")
        }
        let reservesModel = route == "/coreml/compilations"
        let reservesLibrary = route == "/metal/libraries"
        let reservesJob = reservesModel || route == "/coreml/predictions" ||
            reservesLibrary || route == "/metal/dispatches"
        if reservesModel,
           usage.modelHandles + pendingModelHandles >= configuration.limits.maxModelHandles {
            throw AcceleratorRequestValidationError.limit("Core ML model handle limit reached.")
        }
        if reservesLibrary,
           usage.libraryHandles + pendingLibraryHandles >= configuration.limits.maxLibraryHandles {
            throw AcceleratorRequestValidationError.limit("Metal library handle limit reached.")
        }
        if reservesJob {
            guard usage.activeJobs + activeBackendRequests < configuration.limits.maxConcurrentJobs else {
                throw AcceleratorRequestValidationError.limit("Accelerator job limit reached.")
            }
        }
        if reservesModel { pendingModelHandles += 1 }
        if reservesLibrary { pendingLibraryHandles += 1 }
        if reservesJob { activeBackendRequests += 1 }
    }

    private func releaseBackendCapacity(for route: String) {
        if route == "/coreml/compilations" { pendingModelHandles = max(0, pendingModelHandles - 1) }
        if route == "/metal/libraries" { pendingLibraryHandles = max(0, pendingLibraryHandles - 1) }
        if route == "/coreml/compilations" || route == "/coreml/predictions" ||
            route == "/metal/libraries" || route == "/metal/dispatches" {
            activeBackendRequests = max(0, activeBackendRequests - 1)
        }
    }

    private func capabilities() -> AcceleratorBridgeCapabilities {
        let backend = configuration.backend
        let coreMLAvailable = backend?.supportsCoreML == true
        let metalAvailable = backend?.supportsMetal == true
        let metalDevice = MTLCreateSystemDefaultDevice()
        let units = (backend?.computeUnits ?? ["cpu"])
            .filter { ["cpu", "cpu_gpu", "cpu_ane", "all"].contains($0) }
        let coreMLFormats = (backend?.coreMLFormats ?? [])
            .filter { $0 == "mlmodel" }
        return AcceleratorBridgeCapabilities(
            serverVersion: "0.1.0",
            bootID: credentials.bootID,
            deviceName: metalDevice?.name ?? "iPad",
            computeUnits: units.isEmpty ? ["cpu"] : Array(Set(units)).sorted(),
            coreml: .init(
                available: coreMLAvailable,
                formats: coreMLAvailable ? Array(Set(coreMLFormats)).sorted() : []
            ),
            metal: .init(
                available: metalAvailable,
                languageVersion: metalAvailable ? (backend?.metalLanguageVersion ?? "") : "",
                families: metalAvailable ? (backend?.metalFamilies ?? []) : []
            ),
            scratch: .init(guestRoot: configuration.scratchGuestRoot),
            limits: configuration.limits
        )
    }

    private static func backendRoute(method: String, path: String) -> Bool {
        let exact: Set<String> = [
            "POST /coreml/compilations",
            "POST /coreml/predictions",
            "POST /metal/libraries",
            "POST /metal/dispatches",
        ]
        if exact.contains("\(method) \(path)") { return true }
        if path.range(of: #"^/jobs/[0-9A-Fa-f-]+$"#, options: .regularExpression) != nil {
            return method == "GET" || method == "DELETE"
        }
        if method == "DELETE",
           path.range(
               of: #"^/(coreml/models|metal/libraries)/[0-9A-Fa-f-]+$"#,
               options: .regularExpression
           ) != nil {
            return true
        }
        return method == "GET" &&
            path.range(of: #"^/jobs/[0-9A-Fa-f-]+/events$"#, options: .regularExpression) != nil
    }

    private static func backendSupports(
        _ backend: any AcceleratorBridgeBackend,
        method: String,
        path: String
    ) -> Bool {
        if path.hasPrefix("/coreml/") { return backend.supportsCoreML }
        if path.hasPrefix("/metal/") { return backend.supportsMetal }
        if path.hasPrefix("/jobs/") { return backend.supportsCoreML || backend.supportsMetal }
        return false
    }

    private static func requireJSONContentType(_ request: BridgeHTTPRequest) throws {
        guard let value = request.headers["content-type"],
              value.split(separator: ";", maxSplits: 1).first?
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .lowercased() == "application/json" else {
            throw AcceleratorRequestValidationError.invalid(
                "Content-Type must be application/json."
            )
        }
    }

    private func json<Value: Encodable>(status: Int, value: Value, requestID: UUID) -> BridgeHTTPResponse {
        do {
            return BridgeHTTPResponse(
                status: status,
                contentType: "application/json",
                headers: [
                    "X-Request-ID": requestID.uuidString.lowercased(),
                    "X-Forge-Boot-ID": credentials.bootID.uuidString.lowercased(),
                ],
                body: try encoder.encode(value)
            )
        } catch {
            return BridgeHTTPResponse(status: 500, contentType: "application/json", headers: [:], body: Data())
        }
    }

    private func problem(
        status: Int,
        code: String,
        message: String,
        requestID: UUID
    ) -> BridgeHTTPResponse {
        let value = AcceleratorBridgeErrorEnvelope(
            error: .init(code: code, message: message, retriable: status >= 500, requestID: requestID)
        )
        var response = json(status: status, value: value, requestID: requestID)
        response.contentType = "application/problem+json"
        return response
    }

    private func send(_ response: BridgeHTTPResponse, on connection: NWConnection) {
        let reason = HTTPReason.phrase(status: response.status)
        var header = "HTTP/1.1 \(response.status) \(reason)\r\n"
        header += "Content-Type: \(response.contentType)\r\n"
        header += "Content-Length: \(response.body.count)\r\n"
        header += "Cache-Control: no-store\r\nConnection: close\r\n"
        for (name, value) in response.headers { header += "\(name): \(value)\r\n" }
        header += "\r\n"
        var data = Data(header.utf8)
        data.append(response.body)
        connection.send(content: data, contentContext: .finalMessage, isComplete: true, completion: .contentProcessed { _ in
            connection.cancel()
        })
    }

    private nonisolated static func generateBearerToken() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw AcceleratorBridgeServerError.randomGenerationFailed
        }
        return Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    private nonisolated static func constantTimeEqual(_ lhs: String, _ rhs: String) -> Bool {
        let left = Array(lhs.utf8)
        let right = Array(rhs.utf8)
        var difference = UInt64(left.count ^ right.count)
        let count = max(left.count, right.count)
        for index in 0..<count {
            let a = index < left.count ? left[index] : 0
            let b = index < right.count ? right[index] : 0
            difference |= UInt64(a ^ b)
        }
        return difference == 0
    }
}

private struct AcceleratorRequestValidationError: Error {
    let status: Int
    let code: String
    let message: String

    static func invalid(_ message: String) -> Self {
        Self(status: 400, code: "invalid_request", message: message)
    }

    static func limit(_ message: String) -> Self {
        Self(status: 413, code: "limit_exceeded", message: message)
    }

    static func unsupported(_ message: String) -> Self {
        Self(status: 501, code: "unsupported", message: message)
    }
}

private struct AcceleratorValidatedBackendPayload {
    let body: Data
    let scratchReferences: [AcceleratorScratchReference]
}

/// Enforces the OpenAPI request contract before any untrusted value reaches a backend.
private enum AcceleratorRequestValidator {
    private struct Context {
        let limits: AcceleratorBridgeLimits
        var inlineBytes: Int64 = 0
        var scratchBytes: Int64 = 0
        var scratchReferences: [AcceleratorScratchReference] = []
    }

    static func scratchVerify(
        body: Data,
        query: String?,
        limits: AcceleratorBridgeLimits
    ) throws -> AcceleratorScratchVerifyRequest {
        try rejectQuery(query, route: "Scratch verification")
        let root = try jsonObject(body)
        try exactKeys(root, required: ["object"], context: "scratch verification")
        var context = Context(limits: limits)
        let reference = try scratchReference(root["object"], context: &context)
        return AcceleratorScratchVerifyRequest(object: reference)
    }

    static func validateBackendRequest(
        method: String,
        path: String,
        query: String?,
        body: Data,
        limits: AcceleratorBridgeLimits,
        backend: any AcceleratorBridgeBackend
    ) throws -> AcceleratorValidatedBackendPayload {
        var context = Context(limits: limits)
        var canonicalBody = Data()
        switch (method, path) {
        case ("POST", "/coreml/compilations"):
            try rejectQuery(query, route: "Core ML compilation")
            let root = try jsonObject(body)
            try coreMLCompilation(root, context: &context, backend: backend)
            canonicalBody = try canonicalJSON(root)
        case ("POST", "/coreml/predictions"):
            try rejectQuery(query, route: "Core ML prediction")
            let root = try jsonObject(body)
            try coreMLPrediction(root, context: &context, backend: backend)
            canonicalBody = try canonicalJSON(root)
        case ("POST", "/metal/libraries"):
            try rejectQuery(query, route: "Metal compilation")
            let root = try jsonObject(body)
            try metalCompilation(root, context: &context, backend: backend)
            canonicalBody = try canonicalJSON(root)
        case ("POST", "/metal/dispatches"):
            try rejectQuery(query, route: "Metal dispatch")
            let root = try jsonObject(body)
            try metalDispatch(root, context: &context)
            canonicalBody = try canonicalJSON(root)
        case ("GET", let route) where route.hasSuffix("/events"):
            try emptyBody(body, route: "Job events")
            try jobIdentifier(in: route, suffix: "/events")
            try eventQuery(query, maxBytes: min(limits.maxRequestBytes, 8 * 1_024))
        case ("GET", let route) where route.hasPrefix("/jobs/"):
            try emptyBody(body, route: "Job")
            try rejectQuery(query, route: "Job")
            try jobIdentifier(in: route)
        case ("DELETE", let route) where route.hasPrefix("/jobs/"):
            try emptyBody(body, route: "Job")
            try rejectQuery(query, route: "Job")
            try jobIdentifier(in: route)
        case ("DELETE", let route) where route.hasPrefix("/coreml/models/"):
            try emptyBody(body, route: "Core ML model release")
            try rejectQuery(query, route: "Core ML model release")
            try pathUUID(route, prefix: "/coreml/models/")
        case ("DELETE", let route) where route.hasPrefix("/metal/libraries/"):
            try emptyBody(body, route: "Metal library release")
            try rejectQuery(query, route: "Metal library release")
            try pathUUID(route, prefix: "/metal/libraries/")
        default:
            throw AcceleratorRequestValidationError.invalid("Unknown accelerator request route.")
        }
        return AcceleratorValidatedBackendPayload(
            body: canonicalBody,
            scratchReferences: context.scratchReferences
        )
    }

    private static func coreMLCompilation(
        _ root: [String: Any],
        context: inout Context,
        backend: any AcceleratorBridgeBackend
    ) throws {
        try exactKeys(
            root,
            required: ["source", "format"],
            optional: ["compute_units"],
            context: "Core ML compilation"
        )
        let source = try scratchReference(root["source"], context: &context)
        guard source.size <= context.limits.maxModelBytes else {
            throw AcceleratorRequestValidationError.limit("Core ML model exceeds max_model_bytes.")
        }
        let format = try string(root["format"], name: "format", maxBytes: 32)
        let protocolFormats = Set(["mlmodel"])
        guard protocolFormats.contains(format) else {
            throw AcceleratorRequestValidationError.invalid("Core ML format is invalid.")
        }
        guard backend.coreMLFormats.contains(format) else {
            throw AcceleratorRequestValidationError.unsupported(
                "Core ML format is not advertised for this boot."
            )
        }
        try validateComputeUnits(root["compute_units"], backend: backend)
    }

    private static func coreMLPrediction(
        _ root: [String: Any],
        context: inout Context,
        backend: any AcceleratorBridgeBackend
    ) throws {
        try exactKeys(
            root,
            required: ["model_id", "inputs"],
            optional: ["compute_units", "output_delivery", "max_inline_bytes"],
            context: "Core ML prediction"
        )
        try uuid(root["model_id"], name: "model_id")
        let inputs = try object(root["inputs"], name: "inputs")
        guard !inputs.isEmpty else {
            throw AcceleratorRequestValidationError.invalid("inputs must not be empty.")
        }
        guard inputs.count <= context.limits.maxInputs else {
            throw AcceleratorRequestValidationError.limit("Input count exceeds max_inputs.")
        }
        for (name, value) in inputs {
            try identifier(name, name: "input name")
            _ = try tensor(value, context: &context)
        }
        try validateComputeUnits(root["compute_units"], backend: backend)
        if let value = root["output_delivery"] {
            let delivery = try string(value, name: "output_delivery", maxBytes: 16)
            guard ["auto", "inline", "scratch"].contains(delivery) else {
                throw AcceleratorRequestValidationError.invalid("output_delivery is invalid.")
            }
        }
        if let value = root["max_inline_bytes"] {
            let requested = try integer(value, name: "max_inline_bytes", minimum: 0)
            guard requested <= Int64(context.limits.maxInlineBytes) else {
                throw AcceleratorRequestValidationError.limit(
                    "max_inline_bytes exceeds the advertised host limit."
                )
            }
        }
    }

    private static func metalCompilation(
        _ root: [String: Any],
        context: inout Context,
        backend: any AcceleratorBridgeBackend
    ) throws {
        try exactKeys(
            root,
            required: ["source"],
            optional: ["language_version", "fast_math", "macros"],
            context: "Metal compilation"
        )
        let source = try object(root["source"], name: "source")
        let storage = try string(source["storage"], name: "source.storage", maxBytes: 16)
        switch storage {
        case "inline":
            try exactKeys(source, required: ["storage", "text"], context: "inline Metal source")
            let text = try string(source["text"], name: "source.text")
            guard text.utf8.count <= context.limits.maxMetalSourceBytes else {
                throw AcceleratorRequestValidationError.limit(
                    "Metal source exceeds max_metal_source_bytes."
                )
            }
        case "scratch":
            try exactKeys(source, required: ["storage", "object"], context: "scratch Metal source")
            let reference = try scratchReference(source["object"], context: &context)
            guard reference.size <= Int64(context.limits.maxMetalSourceBytes) else {
                throw AcceleratorRequestValidationError.limit(
                    "Metal source exceeds max_metal_source_bytes."
                )
            }
        default:
            throw AcceleratorRequestValidationError.invalid("source.storage is invalid.")
        }
        if let value = root["language_version"] {
            let requested = try string(value, name: "language_version", maxBytes: 32)
            guard requested == backend.metalLanguageVersion else {
                throw AcceleratorRequestValidationError.unsupported(
                    "Metal language version is not advertised for this boot."
                )
            }
        }
        if let value = root["fast_math"] { try boolean(value, name: "fast_math") }
        if let value = root["macros"] {
            try scalarMap(value, name: "macros", maxCount: 128)
        }
    }

    private static func metalDispatch(
        _ root: [String: Any],
        context: inout Context
    ) throws {
        try exactKeys(
            root,
            required: ["library_id", "function", "grid", "threadgroup", "buffers"],
            optional: ["constants", "output_delivery"],
            context: "Metal dispatch"
        )
        try uuid(root["library_id"], name: "library_id")
        try identifier(try string(root["function"], name: "function", maxBytes: 128), name: "function")
        try vector3(root["grid"], name: "grid")
        try vector3(root["threadgroup"], name: "threadgroup")
        guard let buffers = root["buffers"] as? [Any] else {
            throw AcceleratorRequestValidationError.invalid("buffers must be an array.")
        }
        guard buffers.count <= 31 else {
            throw AcceleratorRequestValidationError.limit("Metal buffer count exceeds 31.")
        }
        var indexes = Set<Int64>()
        var inputCount = 0
        var outputCount = 0
        for value in buffers {
            let buffer = try object(value, name: "buffer")
            try exactKeys(
                buffer,
                required: ["index", "access", "tensor"],
                context: "Metal buffer"
            )
            let index = try integer(buffer["index"], name: "buffer.index", minimum: 0, maximum: 30)
            guard indexes.insert(index).inserted else {
                throw AcceleratorRequestValidationError.invalid("Metal buffer indexes must be unique.")
            }
            let access = try string(buffer["access"], name: "buffer.access", maxBytes: 16)
            guard ["read", "write", "read_write"].contains(access) else {
                throw AcceleratorRequestValidationError.invalid("buffer.access is invalid.")
            }
            if access != "write" { inputCount += 1 }
            if access != "read" { outputCount += 1 }
            _ = try tensor(buffer["tensor"], context: &context)
        }
        guard inputCount <= context.limits.maxInputs else {
            throw AcceleratorRequestValidationError.limit("Input count exceeds max_inputs.")
        }
        guard outputCount <= context.limits.maxOutputs else {
            throw AcceleratorRequestValidationError.limit("Output count exceeds max_outputs.")
        }
        if let value = root["constants"] {
            try scalarMap(value, name: "constants", maxCount: 128)
        }
        if let value = root["output_delivery"] {
            let delivery = try string(value, name: "output_delivery", maxBytes: 16)
            guard ["auto", "inline", "scratch"].contains(delivery) else {
                throw AcceleratorRequestValidationError.invalid("output_delivery is invalid.")
            }
        }
    }

    @discardableResult
    private static func tensor(_ value: Any?, context: inout Context) throws -> Int64 {
        let tensor = try object(value, name: "tensor")
        let storage = try string(tensor["storage"], name: "tensor.storage", maxBytes: 16)
        let dtype = try string(tensor["dtype"], name: "tensor.dtype", maxBytes: 16)
        guard let elementBytes = [
            "bool": 1, "int8": 1, "uint8": 1,
            "int16": 2, "uint16": 2, "float16": 2,
            "int32": 4, "uint32": 4, "float32": 4,
            "int64": 8, "uint64": 8, "float64": 8,
        ][dtype] else {
            throw AcceleratorRequestValidationError.invalid("tensor.dtype is invalid.")
        }
        guard let shapeValues = tensor["shape"] as? [Any],
              !shapeValues.isEmpty else {
            throw AcceleratorRequestValidationError.invalid("tensor.shape must not be empty.")
        }
        guard shapeValues.count <= context.limits.maxTensorRank else {
            throw AcceleratorRequestValidationError.limit("Tensor rank exceeds max_tensor_rank.")
        }
        var expectedBytes = Int64(elementBytes)
        for value in shapeValues {
            let dimension = try integer(
                value,
                name: "tensor.shape dimension",
                minimum: 0,
                maximum: 2_147_483_647
            )
            let product = expectedBytes.multipliedReportingOverflow(by: dimension)
            guard !product.overflow else {
                throw AcceleratorRequestValidationError.limit("Tensor byte size overflows the host limit.")
            }
            expectedBytes = product.partialValue
        }
        guard expectedBytes <= context.limits.maxBufferBytes else {
            throw AcceleratorRequestValidationError.limit("Tensor exceeds max_buffer_bytes.")
        }

        switch storage {
        case "inline":
            try exactKeys(
                tensor,
                required: ["storage", "dtype", "shape", "data_base64"],
                context: "inline tensor"
            )
            let encoded = try string(tensor["data_base64"], name: "tensor.data_base64")
            guard let decoded = Data(base64Encoded: encoded, options: []) else {
                throw AcceleratorRequestValidationError.invalid("tensor.data_base64 is invalid base64.")
            }
            guard Int64(decoded.count) == expectedBytes else {
                throw AcceleratorRequestValidationError.invalid(
                    "Inline tensor byte count does not match dtype and shape."
                )
            }
            let aggregate = context.inlineBytes.addingReportingOverflow(Int64(decoded.count))
            guard !aggregate.overflow,
                  aggregate.partialValue <= Int64(context.limits.maxInlineBytes) else {
                throw AcceleratorRequestValidationError.limit(
                    "Inline tensor bytes exceed max_inline_bytes."
                )
            }
            context.inlineBytes = aggregate.partialValue
        case "scratch":
            try exactKeys(
                tensor,
                required: ["storage", "dtype", "shape", "object"],
                optional: ["byte_offset", "byte_length"],
                context: "scratch tensor"
            )
            let reference = try scratchReference(tensor["object"], context: &context)
            let offset = try tensor["byte_offset"].map {
                try integer($0, name: "tensor.byte_offset", minimum: 0)
            } ?? 0
            let length = try tensor["byte_length"].map {
                try integer($0, name: "tensor.byte_length", minimum: 0)
            } ?? (reference.size - offset)
            guard offset <= reference.size,
                  length == expectedBytes,
                  length <= reference.size - offset else {
                throw AcceleratorRequestValidationError.invalid(
                    "Scratch tensor slice does not match dtype, shape, or object size."
                )
            }
        default:
            throw AcceleratorRequestValidationError.invalid("tensor.storage is invalid.")
        }
        return expectedBytes
    }

    private static func scratchReference(
        _ value: Any?,
        context: inout Context
    ) throws -> AcceleratorScratchReference {
        let object = try object(value, name: "scratch object")
        try exactKeys(
            object,
            required: ["relative_path", "sha256", "size"],
            optional: ["media_type", "delete_after_read"],
            context: "scratch object"
        )
        let relativePath = try string(object["relative_path"], name: "relative_path", maxBytes: 1_024)
        let digest = try string(object["sha256"], name: "sha256", maxBytes: 64)
        let size = try integer(
            object["size"],
            name: "size",
            minimum: 0,
            maximum: context.limits.maxScratchObjectBytes
        )
        let mediaType = try object["media_type"].map {
            try string($0, name: "media_type", maxBytes: 128)
        } ?? "application/octet-stream"
        let deleteAfterRead = try object["delete_after_read"].map {
            try boolean($0, name: "delete_after_read")
        } ?? false
        let reference = AcceleratorScratchReference(
            relativePath: relativePath,
            sha256: digest,
            size: size,
            mediaType: mediaType,
            deleteAfterRead: deleteAfterRead
        )
        let aggregate = context.scratchBytes.addingReportingOverflow(size)
        guard !aggregate.overflow,
              aggregate.partialValue <= context.limits.maxScratchObjectBytes else {
            throw AcceleratorRequestValidationError.limit(
                "Aggregate scratch bytes exceed max_scratch_object_bytes."
            )
        }
        context.scratchBytes = aggregate.partialValue
        context.scratchReferences.append(reference)
        return reference
    }

    private static func validateComputeUnits(
        _ value: Any?,
        backend: any AcceleratorBridgeBackend
    ) throws {
        guard let value else { return }
        let units = try string(value, name: "compute_units", maxBytes: 16)
        guard ["cpu", "cpu_gpu", "cpu_ane", "all"].contains(units) else {
            throw AcceleratorRequestValidationError.invalid("compute_units is invalid.")
        }
        let available = backend.computeUnits
            .filter { ["cpu", "cpu_gpu", "cpu_ane", "all"].contains($0) }
        let advertised = available.isEmpty ? ["cpu"] : available
        guard advertised.contains(units) else {
            throw AcceleratorRequestValidationError.unsupported(
                "Requested compute_units are not advertised for this boot."
            )
        }
    }

    private static func jsonObject(_ body: Data) throws -> [String: Any] {
        guard !body.isEmpty else {
            throw AcceleratorRequestValidationError.invalid("A JSON request body is required.")
        }
        do {
            let value = try JSONSerialization.jsonObject(with: body)
            return try object(value, name: "request body")
        } catch let error as AcceleratorRequestValidationError {
            throw error
        } catch {
            throw AcceleratorRequestValidationError.invalid("The request body is not valid JSON.")
        }
    }

    private static func canonicalJSON(_ object: [String: Any]) throws -> Data {
        do {
            return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        } catch {
            throw AcceleratorRequestValidationError.invalid(
                "The request body contains an unsupported JSON value."
            )
        }
    }

    private static func object(_ value: Any?, name: String) throws -> [String: Any] {
        guard let object = value as? [String: Any] else {
            throw AcceleratorRequestValidationError.invalid("\(name) must be an object.")
        }
        return object
    }

    private static func exactKeys(
        _ object: [String: Any],
        required: Set<String>,
        optional: Set<String> = [],
        context: String
    ) throws {
        let keys = Set(object.keys)
        let missing = required.subtracting(keys)
        guard missing.isEmpty else {
            throw AcceleratorRequestValidationError.invalid(
                "\(context) is missing \(missing.sorted().joined(separator: ", "))."
            )
        }
        let unknown = keys.subtracting(required.union(optional))
        guard unknown.isEmpty else {
            throw AcceleratorRequestValidationError.invalid(
                "\(context) contains unknown field \(unknown.sorted().joined(separator: ", "))."
            )
        }
    }

    private static func string(
        _ value: Any?,
        name: String,
        maxBytes: Int? = nil
    ) throws -> String {
        guard let value = value as? String,
              maxBytes.map({ value.utf8.count <= $0 }) ?? true else {
            throw AcceleratorRequestValidationError.invalid("\(name) must be a valid string.")
        }
        return value
    }

    @discardableResult
    private static func boolean(_ value: Any?, name: String) throws -> Bool {
        guard let value = value as? NSNumber,
              CFGetTypeID(value) == CFBooleanGetTypeID() else {
            throw AcceleratorRequestValidationError.invalid("\(name) must be a boolean.")
        }
        return value.boolValue
    }

    private static func integer(
        _ value: Any?,
        name: String,
        minimum: Int64 = Int64.min,
        maximum: Int64 = Int64.max
    ) throws -> Int64 {
        guard let value = value as? NSNumber,
              CFGetTypeID(value) != CFBooleanGetTypeID() else {
            throw AcceleratorRequestValidationError.invalid("\(name) must be an integer.")
        }
        let number = value.doubleValue
        guard number.isFinite,
              number.rounded(.towardZero) == number,
              number >= Double(minimum),
              number <= Double(maximum) else {
            throw AcceleratorRequestValidationError.invalid("\(name) is outside its integer range.")
        }
        return value.int64Value
    }

    private static func identifier(_ value: String, name: String) throws {
        guard value.utf8.count <= 128,
              value.range(
                  of: #"^[A-Za-z_][A-Za-z0-9_.-]*$"#,
                  options: .regularExpression
              ) != nil else {
            throw AcceleratorRequestValidationError.invalid("\(name) is not a valid identifier.")
        }
    }

    private static func uuid(_ value: Any?, name: String) throws {
        let value = try string(value, name: name, maxBytes: 36)
        guard UUID(uuidString: value) != nil else {
            throw AcceleratorRequestValidationError.invalid("\(name) must be a UUID.")
        }
    }

    private static func vector3(_ value: Any?, name: String) throws {
        guard let values = value as? [Any], values.count == 3 else {
            throw AcceleratorRequestValidationError.invalid("\(name) must contain exactly three integers.")
        }
        for value in values {
            _ = try integer(value, name: name, minimum: 1, maximum: 4_294_967_295)
        }
    }

    private static func scalarMap(_ value: Any?, name: String, maxCount: Int) throws {
        let values = try object(value, name: name)
        guard values.count <= maxCount else {
            throw AcceleratorRequestValidationError.limit("\(name) contains too many entries.")
        }
        for (key, value) in values {
            guard key.utf8.count <= 128 else {
                throw AcceleratorRequestValidationError.invalid("\(name) key is too long.")
            }
            if value is String { continue }
            if let number = value as? NSNumber {
                if CFGetTypeID(number) == CFBooleanGetTypeID() { continue }
                guard number.doubleValue.isFinite else {
                    throw AcceleratorRequestValidationError.invalid("\(name) values must be finite.")
                }
                continue
            }
            throw AcceleratorRequestValidationError.invalid(
                "\(name) values must be strings, numbers, or booleans."
            )
        }
    }

    private static func emptyBody(_ body: Data, route: String) throws {
        guard body.isEmpty else {
            throw AcceleratorRequestValidationError.invalid("\(route) does not accept a request body.")
        }
    }

    private static func rejectQuery(_ query: String?, route: String) throws {
        guard query == nil else {
            throw AcceleratorRequestValidationError.invalid("\(route) does not accept query parameters.")
        }
    }

    private static func pathUUID(_ route: String, prefix: String) throws {
        let value = String(route.dropFirst(prefix.count))
        guard !value.contains("/"), UUID(uuidString: value) != nil else {
            throw AcceleratorRequestValidationError.invalid("Route handle must be a UUID.")
        }
    }

    private static func jobIdentifier(in route: String, suffix: String = "") throws {
        let prefix = "/jobs/"
        guard route.hasPrefix(prefix), suffix.isEmpty || route.hasSuffix(suffix) else {
            throw AcceleratorRequestValidationError.invalid("Job route is invalid.")
        }
        let end = suffix.isEmpty ? route.endIndex : route.index(route.endIndex, offsetBy: -suffix.count)
        let value = String(route[route.index(route.startIndex, offsetBy: prefix.count)..<end])
        guard !value.contains("/"), UUID(uuidString: value) != nil else {
            throw AcceleratorRequestValidationError.invalid("job_id must be a UUID.")
        }
    }

    private static func eventQuery(_ query: String?, maxBytes: Int) throws {
        guard let query else { return }
        guard query.utf8.count <= maxBytes else {
            throw AcceleratorRequestValidationError.limit("Job event query exceeds the request limit.")
        }
        if query.isEmpty { return }
        var values: [String: String] = [:]
        for field in query.split(separator: "&", omittingEmptySubsequences: false) {
            let pair = field.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard pair.count == 2,
                  let name = String(pair[0]).removingPercentEncoding,
                  let value = String(pair[1]).removingPercentEncoding,
                  ["after", "wait_seconds"].contains(name),
                  values.updateValue(value, forKey: name) == nil else {
                throw AcceleratorRequestValidationError.invalid("Job event query is invalid.")
            }
        }
        if let value = values["after"] {
            guard let after = Int64(value), after >= 0 else {
                throw AcceleratorRequestValidationError.invalid("after must be a nonnegative integer.")
            }
        }
        if let value = values["wait_seconds"] {
            guard let wait = Double(value), wait.isFinite, wait >= 0, wait <= 30 else {
                throw AcceleratorRequestValidationError.invalid("wait_seconds must be between 0 and 30.")
            }
        }
    }
}

private enum ScratchObjectVerifier {
    static func verify(
        _ reference: AcceleratorScratchReference,
        root: URL,
        maxBytes: Int64
    ) async throws -> AcceleratorScratchReference {
        try await Task.detached(priority: .utility) {
            let file = try openVerified(reference, root: root, maxBytes: maxBytes)
            defer { Darwin.close(file) }
            guard try copyAndDigest(file: file, destination: nil) == reference.sha256 else {
                throw AcceleratorScratchError.mismatch
            }
            return reference
        }.value
    }

    static func stage(
        _ reference: AcceleratorScratchReference,
        root: URL,
        maxBytes: Int64,
        destination: URL
    ) async throws -> AcceleratorVerifiedScratchObject {
        try await Task.detached(priority: .utility) {
            let file = try openVerified(reference, root: root, maxBytes: maxBytes)
            defer { Darwin.close(file) }
            do {
                guard try copyAndDigest(file: file, destination: destination) == reference.sha256 else {
                    throw AcceleratorScratchError.mismatch
                }
                return AcceleratorVerifiedScratchObject(reference: reference, hostURL: destination)
            } catch {
                try? FileManager.default.removeItem(at: destination)
                throw error
            }
        }.value
    }

    private static func openVerified(
        _ reference: AcceleratorScratchReference,
        root: URL,
        maxBytes: Int64
    ) throws -> Int32 {
        guard normalizedComponents(reference.relativePath) != nil,
              RunnerDigest.isSHA256(reference.sha256),
              reference.size >= 0,
              reference.size <= maxBytes else {
            throw AcceleratorScratchError.invalidReference
        }
        let components = normalizedComponents(reference.relativePath)!
        var directory = Darwin.open(root.path, O_RDONLY | O_DIRECTORY | O_CLOEXEC)
        guard directory >= 0 else { throw AcceleratorScratchError.unavailable }

        for component in components.dropLast() {
            let next = component.withCString {
                Darwin.openat(directory, $0, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
            }
            Darwin.close(directory)
            guard next >= 0 else { throw AcceleratorScratchError.outsideRoot }
            directory = next
        }
        let file = components.last!.withCString {
            Darwin.openat(directory, $0, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        }
        Darwin.close(directory)
        guard file >= 0 else { throw AcceleratorScratchError.outsideRoot }

        var attributes = stat()
        guard Darwin.fstat(file, &attributes) == 0,
              attributes.st_mode & S_IFMT == S_IFREG else {
            Darwin.close(file)
            throw AcceleratorScratchError.invalidReference
        }
        let measuredSize = Int64(attributes.st_size)
        guard measuredSize == reference.size else {
            Darwin.close(file)
            throw AcceleratorScratchError.mismatch
        }
        return file
    }

    private static func copyAndDigest(file: Int32, destination: URL?) throws -> String {
        let output: Int32
        if let destination {
            output = Darwin.open(
                destination.path,
                O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                S_IRUSR
            )
            guard output >= 0 else { throw AcceleratorScratchError.unavailable }
        } else {
            output = -1
        }
        defer {
            if output >= 0 { Darwin.close(output) }
        }
        var hasher = SHA256()
        let buffer = UnsafeMutableRawPointer.allocate(byteCount: 1_048_576, alignment: 64)
        defer { buffer.deallocate() }
        while true {
            let count = Darwin.read(file, buffer, 1_048_576)
            if count < 0 {
                if errno == EINTR { continue }
                throw AcceleratorScratchError.unavailable
            }
            if count == 0 { break }
            hasher.update(bufferPointer: UnsafeRawBufferPointer(start: buffer, count: count))
            var offset = 0
            while output >= 0, offset < count {
                let written = Darwin.write(output, buffer.advanced(by: offset), count - offset)
                if written < 0 {
                    if errno == EINTR { continue }
                    throw AcceleratorScratchError.unavailable
                }
                offset += written
            }
        }
        if output >= 0, Darwin.fsync(output) != 0 { throw AcceleratorScratchError.unavailable }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func normalizedComponents(_ path: String) -> [String]? {
        guard !path.isEmpty,
              path.utf8.count <= 1_024,
              !path.hasPrefix("/"),
              !path.contains("//"),
              !path.contains("\\"),
              !path.contains("\0") else { return nil }
        let components = path.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        guard !components.isEmpty,
              components.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else { return nil }
        return components
    }
}

private struct BridgeHTTPRequest: Sendable {
    enum ParseResult { case incomplete, ready(BridgeHTTPRequest) }

    let method: String
    let path: String
    let query: String?
    let headers: [String: String]
    let body: Data

    static func parse(_ data: Data, maxBodyBytes: Int) throws -> ParseResult {
        let delimiter = Data("\r\n\r\n".utf8)
        guard let boundary = data.range(of: delimiter) else {
            if data.count > 64 * 1_024 { throw AcceleratorHTTPError.headersTooLarge }
            return .incomplete
        }
        guard boundary.lowerBound <= 64 * 1_024,
              let text = String(data: data[..<boundary.lowerBound], encoding: .utf8) else {
            throw AcceleratorHTTPError.invalidRequest
        }
        let lines = text.components(separatedBy: "\r\n")
        let requestLine = lines.first?.split(separator: " ", omittingEmptySubsequences: false) ?? []
        guard requestLine.count == 3,
              requestLine[2] == "HTTP/1.1",
              ["GET", "POST", "DELETE"].contains(String(requestLine[0])) else {
            throw AcceleratorHTTPError.invalidRequest
        }
        let target = String(requestLine[1])
        guard target.hasPrefix("/"), !target.contains("#") else { throw AcceleratorHTTPError.invalidRequest }
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":") else { throw AcceleratorHTTPError.invalidRequest }
            let name = line[..<colon].lowercased()
            let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            guard !name.isEmpty, headers[name] == nil else { throw AcceleratorHTTPError.invalidRequest }
            headers[name] = value
        }
        guard headers["transfer-encoding"] == nil else { throw AcceleratorHTTPError.invalidRequest }
        let contentLength: Int
        if let raw = headers["content-length"] {
            guard let parsed = Int(raw), parsed >= 0, parsed <= maxBodyBytes else {
                throw AcceleratorHTTPError.bodyTooLarge
            }
            contentLength = parsed
        } else { contentLength = 0 }
        let bodyStart = boundary.upperBound
        guard data.count >= bodyStart + contentLength else { return .incomplete }
        guard data.count == bodyStart + contentLength else { throw AcceleratorHTTPError.invalidRequest }
        let pieces = target.split(separator: "?", maxSplits: 1, omittingEmptySubsequences: false)
        return .ready(
            BridgeHTTPRequest(
                method: String(requestLine[0]),
                path: String(pieces[0]),
                query: pieces.count == 2 ? String(pieces[1]) : nil,
                headers: headers,
                body: data.subdata(in: bodyStart..<(bodyStart + contentLength))
            )
        )
    }
}

private struct BridgeHTTPResponse: Sendable {
    let status: Int
    var contentType: String
    let headers: [String: String]
    let body: Data
}

private struct AcceleratorHTTPError: Error {
    let status: Int
    let code: String
    let message: String

    static let invalidRequest = Self(status: 400, code: "invalid_request", message: "Malformed HTTP request.")
    static let headersTooLarge = Self(status: 413, code: "limit_exceeded", message: "HTTP headers exceed 64 KiB.")
    static let bodyTooLarge = Self(status: 413, code: "limit_exceeded", message: "Request body exceeds the advertised limit.")
}

private enum AcceleratorScratchError: LocalizedError {
    case invalidReference, outsideRoot, mismatch, unavailable

    var status: Int {
        switch self {
        case .unavailable: 503
        default: 400
        }
    }

    var code: String {
        switch self {
        case .mismatch: "scratch_mismatch"
        case .unavailable: "internal"
        default: "invalid_request"
        }
    }

    var message: String {
        switch self {
        case .invalidReference: "Scratch reference must name a normalized regular file with SHA-256."
        case .outsideRoot: "Scratch path escapes the shared root or crosses a symbolic link."
        case .mismatch: "Scratch size or SHA-256 does not match the supplied reference."
        case .unavailable: "Scratch storage is unavailable."
        }
    }
}

private final class ConnectionReadyGate: @unchecked Sendable {
    private let lock = NSLock()
    private var claimed = false

    func claim() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard !claimed else { return false }
        claimed = true
        return true
    }
}

private enum HTTPReason {
    static func phrase(status: Int) -> String {
        switch status {
        case 200: "OK"
        case 202: "Accepted"
        case 400: "Bad Request"
        case 401: "Unauthorized"
        case 403: "Forbidden"
        case 404: "Not Found"
        case 405: "Method Not Allowed"
        case 413: "Payload Too Large"
        case 500: "Internal Server Error"
        case 501: "Not Implemented"
        case 503: "Service Unavailable"
        default: "Response"
        }
    }
}

enum AcceleratorBridgeServerError: LocalizedError {
    case randomGenerationFailed

    var errorDescription: String? { "Could not generate the per-boot accelerator token." }
}
