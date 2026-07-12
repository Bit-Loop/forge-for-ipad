// swift-tools-version: 6.2

import PackageDescription
import Foundation

let coreOnly = ProcessInfo.processInfo.environment["FORGE_CORE_ONLY"] == "1"
var products: [Product] = [.library(name: "ForgeCore", targets: ["ForgeCore"])]
var targets: [Target] = [
    .target(name: "ForgeCore", path: "ForgeCore"),
    .testTarget(
        name: "ForgeForiPadTests",
        dependencies: ["ForgeCore"],
        path: "ForgeTests"
    ),
]

if !coreOnly {
    products.append(.library(name: "ForgeForiPad", targets: ["ForgeForiPad"]))
    targets.append(
        .target(
            name: "ForgeForiPad",
            dependencies: ["ForgeCore"],
            path: "Forge",
            resources: [.process("Resources")],
            swiftSettings: [
                .unsafeFlags(["-strict-concurrency=complete"]),
            ]
        ),
    )
}

let package = Package(
    name: "ForgeForiPad",
    platforms: [.iOS("27.0")],
    products: products,
    targets: targets,
    swiftLanguageModes: [.v5]
)
