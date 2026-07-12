import Foundation

/// A canonical UUID that identifies one durable Forge Runner installation.
public struct RunnerInstanceID: Codable, Hashable, RawRepresentable, Sendable {
    public let rawValue: String

    public init?(_ rawValue: String) {
        guard rawValue.count == 36, let uuid = UUID(uuidString: rawValue) else { return nil }
        self.rawValue = uuid.uuidString.lowercased()
    }

    public init(rawValue: String) {
        guard let value = Self(rawValue) else {
            preconditionFailure("RunnerInstanceID requires a valid UUID")
        }
        self = value
    }

    public init(_ uuid: UUID) {
        rawValue = uuid.uuidString.lowercased()
    }

    public init(from decoder: Decoder) throws {
        let rawValue = try decoder.singleValueContainer().decode(String.self)
        guard let value = Self(rawValue) else {
            throw DecodingError.dataCorrupted(.init(
                codingPath: decoder.codingPath,
                debugDescription: "Runner instance ID must be a UUID."
            ))
        }
        self = value
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }

    /// Bonjour claims are hints only. They become identity after an authenticated match.
    public static func bind(
        advertised: RunnerInstanceID?,
        authenticated: RunnerInstanceID
    ) throws -> RunnerInstanceID {
        guard advertised == nil || advertised == authenticated else {
            throw RunnerInstanceIdentityError.advertisementMismatch
        }
        return authenticated
    }
}

public enum RunnerInstanceIdentityError: Error, Equatable {
    case advertisementMismatch
}

/// Prevents an untrusted Bonjour UUID claim from redirecting a saved bearer token.
public enum RunnerCredentialPresentationPolicy {
    public static func allows(
        advertised: RunnerInstanceID?,
        savedInstanceID: RunnerInstanceID,
        pairedBaseURL: URL,
        candidateBaseURL: URL
    ) -> Bool {
        advertised == savedInstanceID && pairedBaseURL == candidateBaseURL
    }
}
