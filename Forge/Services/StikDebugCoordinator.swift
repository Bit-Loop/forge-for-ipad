import Foundation
import UIKit

@MainActor
struct StikDebugCoordinator {
    enum Error: Swift.Error { case missingScript, invalidURL }

    func requestJIT(bundleID: String = "com.bitloop.forge") throws {
        guard let url = Bundle.module.url(forResource: "ForgeJIT", withExtension: "js"),
              let script = try? Data(contentsOf: url) else { throw Error.missingScript }
        let encoded = script.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        var components = URLComponents()
        components.scheme = "stikjit"
        components.host = "enable-jit"
        components.queryItems = [
            .init(name: "bundle-id", value: bundleID),
            .init(name: "script-name", value: "Forge UTM JIT"),
            .init(name: "script-data", value: encoded),
        ]
        guard let requestURL = components.url else { throw Error.invalidURL }
        UIApplication.shared.open(requestURL)
    }
}
