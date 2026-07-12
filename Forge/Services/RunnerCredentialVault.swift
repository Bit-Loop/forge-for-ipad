import Foundation
import Security

/// Stores runner bearer credentials in the device Keychain, scoped to this app and device.
actor RunnerCredentialVault {
    private let service = "com.bitloop.forge.runner-credentials.v1"
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    func save(_ credential: RunnerCredential) throws {
        guard !credential.bearerToken.isEmpty,
              !credential.bearerToken.contains(where: { $0.isWhitespace }) else {
            throw RunnerCredentialError.invalidToken
        }
        let data = try encoder.encode(credential)
        let key = query(endpointID: credential.endpointID)
        let attributes: [CFString: Any] = [
            kSecValueData: data,
            kSecAttrAccessible: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecAttrSynchronizable: false,
        ]
        let status = SecItemUpdate(key as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var insertion = key
            attributes.forEach { insertion[$0] = $1 }
            try check(SecItemAdd(insertion as CFDictionary, nil))
        } else {
            try check(status)
        }
    }

    func credential(for endpointID: String) throws -> RunnerCredential? {
        var lookup = query(endpointID: endpointID)
        lookup[kSecReturnData] = true
        lookup[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(lookup as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        try check(status)
        guard let data = result as? Data else { throw RunnerCredentialError.invalidRecord }
        return try decoder.decode(RunnerCredential.self, from: data)
    }

    func remove(endpointID: String) throws {
        let status = SecItemDelete(query(endpointID: endpointID) as CFDictionary)
        if status != errSecItemNotFound { try check(status) }
    }

    private func query(endpointID: String) -> [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: service,
            kSecAttrAccount: endpointID,
            kSecAttrSynchronizable: false,
        ]
    }

    private func check(_ status: OSStatus) throws {
        guard status == errSecSuccess else { throw RunnerCredentialError.keychain(status) }
    }
}

enum RunnerCredentialError: LocalizedError {
    case invalidToken
    case invalidRecord
    case keychain(OSStatus)

    var errorDescription: String? {
        switch self {
        case .invalidToken: "Runner returned an invalid bearer token."
        case .invalidRecord: "The paired runner credential is damaged."
        case .keychain(let status):
            (SecCopyErrorMessageString(status, nil) as String?) ?? "Keychain error \(status)."
        }
    }
}
