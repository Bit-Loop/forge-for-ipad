import Foundation

public enum ForgeRoute: Equatable, Sendable {
    case open(workspace: String?, file: String?, line: Int?, column: Int?)
    case run(workspace: String?, task: String)
    case terminal(workspace: String?)
    case artifact(digest: String)
    case pairRunner(endpoint: URL)

    public init?(url: URL) {
        guard url.scheme?.lowercased() == "forge" else { return nil }
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let query = Dictionary(
            components?.queryItems?.compactMap { item in
                item.value.map { (item.name, $0) }
            } ?? [],
            uniquingKeysWith: { _, latest in latest }
        )
        let action = [url.host, url.path.split(separator: "/").first.map(String.init)]
            .compactMap { $0 }
            .first { !$0.isEmpty }

        switch action {
        case "open":
            self = .open(
                workspace: query["workspace"],
                file: query["file"],
                line: query["line"].flatMap(Int.init),
                column: query["column"].flatMap(Int.init)
            )
        case "run":
            guard let task = query["task"], !task.isEmpty else { return nil }
            self = .run(workspace: query["workspace"], task: task)
        case "terminal":
            self = .terminal(workspace: query["workspace"])
        case "artifact":
            guard let digest = query["digest"], Self.isSHA256(digest) else { return nil }
            self = .artifact(digest: digest.lowercased())
        case "runner":
            guard url.path == "/pair" || query["action"] == "pair",
                  let raw = query["endpoint"],
                  let endpoint = URL(string: raw),
                  endpoint.scheme == "http" || endpoint.scheme == "https"
            else { return nil }
            self = .pairRunner(endpoint: endpoint)
        default:
            return nil
        }
    }

    private static func isSHA256(_ value: String) -> Bool {
        value.count == 64 && value.allSatisfy { $0.isHexDigit }
    }
}
