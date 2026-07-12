import Foundation
import ForgeCore
@preconcurrency import Network

/// Discovers and resolves Forge Runner services without retaining a socket after resolution.
actor RunnerDiscovery {
    private let queue = DispatchQueue(label: "com.bitloop.forge.runner-discovery", qos: .userInitiated)
    private var browser: NWBrowser?
    private var continuation: AsyncStream<[RunnerEndpoint]>.Continuation?
    private var endpoints: [String: RunnerEndpoint] = [:]
    private var pending: Set<String> = []
    private var visible: Set<String> = []

    func updates() -> AsyncStream<[RunnerEndpoint]> {
        AsyncStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            self.continuation?.finish()
            self.continuation = continuation
            continuation.yield(self.sortedEndpoints)
            continuation.onTermination = { [weak self] _ in
                Task { await self?.stop() }
            }
            self.startIfNeeded()
        }
    }

    func stop() {
        browser?.cancel()
        browser = nil
        pending.removeAll()
        visible.removeAll()
        endpoints.removeAll()
        continuation?.finish()
        continuation = nil
    }

    private var sortedEndpoints: [RunnerEndpoint] {
        endpoints.values.sorted {
            $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
    }

    private func startIfNeeded() {
        guard browser == nil else { return }
        let parameters = NWParameters.tcp
        parameters.includePeerToPeer = true
        let browser = NWBrowser(
            for: .bonjourWithTXTRecord(type: "_forge-runner._tcp", domain: nil),
            using: parameters
        )
        browser.browseResultsChangedHandler = { [weak self] results, _ in
            Task { await self?.received(results) }
        }
        browser.stateUpdateHandler = { [weak self] state in
            guard case .failed = state else { return }
            Task { await self?.stop() }
        }
        self.browser = browser
        browser.start(queue: queue)
    }

    private func received(_ results: Set<NWBrowser.Result>) {
        visible = Set(results.map { $0.endpoint.debugDescription })
        endpoints = endpoints.filter { visible.contains($0.key) }
        pending = pending.intersection(visible)
        continuation?.yield(sortedEndpoints)

        for result in results {
            let key = result.endpoint.debugDescription
            guard endpoints[key] == nil, pending.insert(key).inserted else { continue }
            Task {
                let endpoint = try? await Self.resolve(result)
                resolved(endpoint, key: key)
            }
        }
    }

    private func resolved(_ endpoint: RunnerEndpoint?, key: String) {
        pending.remove(key)
        if visible.contains(key), let endpoint { endpoints[key] = endpoint }
        continuation?.yield(sortedEndpoints)
    }

    private nonisolated static func resolve(_ result: NWBrowser.Result) async throws -> RunnerEndpoint {
        let resolved = try await BonjourResolver.resolve(result.endpoint)
        guard case .hostPort(let host, let port) = resolved else {
            throw RunnerDiscoveryError.unresolvedEndpoint
        }
        let metadata: [String: String]
        if case .bonjour(let txt) = result.metadata { metadata = txt.dictionary }
        else { metadata = [:] }
        guard metadata["api"] == nil || metadata["api"] == "forge/v1" else {
            throw RunnerDiscoveryError.incompatibleService
        }
        let advertisedInstanceID: RunnerInstanceID?
        if let rawIdentity = metadata["instance_id"] {
            guard let parsed = RunnerInstanceID(rawIdentity) else {
                throw RunnerDiscoveryError.invalidInstanceID
            }
            advertisedInstanceID = parsed
        } else {
            advertisedInstanceID = nil
        }

        var components = URLComponents()
        components.scheme = metadata["tls"] == "1" ? "https" : "http"
        components.host = String(describing: host)
            .trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        components.port = Int(port.rawValue)
        guard let url = components.url else { throw RunnerDiscoveryError.unresolvedEndpoint }
        let name: String
        if case .service(let serviceName, _, _, _) = result.endpoint { name = serviceName }
        else { name = host.debugDescription }
        return try RunnerEndpoint(
            name: name,
            baseURL: url,
            interfaceNames: result.interfaces.map(\.name),
            metadata: metadata,
            advertisedInstanceID: advertisedInstanceID
        )
    }
}

private enum BonjourResolver {
    static func resolve(_ endpoint: NWEndpoint) async throws -> NWEndpoint {
        try await withCheckedThrowingContinuation { continuation in
            let connection = NWConnection(to: endpoint, using: .tcp)
            let resolution = Resolution(connection: connection, continuation: continuation)
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    guard let endpoint = connection.currentPath?.remoteEndpoint else {
                        resolution.fail(RunnerDiscoveryError.unresolvedEndpoint)
                        return
                    }
                    resolution.succeed(endpoint)
                case .failed(let error): resolution.fail(error)
                case .cancelled: resolution.fail(CancellationError())
                default: break
                }
            }
            connection.start(queue: DispatchQueue.global(qos: .userInitiated))
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 5) {
                resolution.fail(RunnerDiscoveryError.unresolvedEndpoint)
            }
        }
    }

    private final class Resolution: @unchecked Sendable {
        private let lock = NSLock()
        private var continuation: CheckedContinuation<NWEndpoint, Error>?
        private let connection: NWConnection

        init(
            connection: NWConnection,
            continuation: CheckedContinuation<NWEndpoint, Error>
        ) {
            self.connection = connection
            self.continuation = continuation
        }

        func succeed(_ endpoint: NWEndpoint) { finish(.success(endpoint)) }
        func fail(_ error: Error) { finish(.failure(error)) }

        private func finish(_ result: Result<NWEndpoint, Error>) {
            lock.lock()
            let continuation = self.continuation
            self.continuation = nil
            lock.unlock()
            guard let continuation else { return }
            connection.stateUpdateHandler = nil
            connection.cancel()
            continuation.resume(with: result)
        }
    }
}

enum RunnerDiscoveryError: LocalizedError {
    case unresolvedEndpoint
    case incompatibleService
    case invalidInstanceID

    var errorDescription: String? {
        switch self {
        case .unresolvedEndpoint: "Bonjour could not resolve the runner to an IP address."
        case .incompatibleService: "The discovered service does not speak Forge Runner v1."
        case .invalidInstanceID: "The discovered runner advertised an invalid instance UUID."
        }
    }
}
