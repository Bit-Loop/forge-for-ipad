import Foundation
import Testing
@testable import ForgeCore

@Test func sceneRoundTrips() throws {
    let scene = ForgeScene(kind: .terminal, workspaceID: UUID())
    let data = try JSONEncoder().encode(scene)
    #expect(try JSONDecoder().decode(ForgeScene.self, from: data) == scene)
}

@Test func jobTransitionsAreExplicit() throws {
    var job = ForgeJob(title: "Build")
    try job.transition(to: .running)
    try job.transition(to: .checkpointing)
    try job.transition(to: .suspended)
    try job.transition(to: .resuming)
    try job.transition(to: .running)
    try job.transition(to: .succeeded)
    #expect(job.progress == 1)

    var invalid = ForgeJob(title: "Invalid")
    #expect(throws: ForgeJobError.self) { try invalid.transition(to: .succeeded) }
}

@Test func remoteJobCursorPersistsAcrossEncoding() throws {
    var job = ForgeJob(title: "Remote build")
    job.remoteReference = .init(endpointID: "runner", jobID: "abc", lastEventSequence: 42)
    let data = try JSONEncoder().encode(job)
    let restored = try JSONDecoder().decode(ForgeJob.self, from: data)
    #expect(restored.remoteReference?.jobID == "abc")
    #expect(restored.remoteReference?.lastEventSequence == 42)
}

@Test func runnerInstanceIdentityRequiresAuthenticatedAdvertisementMatch() throws {
    let first = try #require(RunnerInstanceID("eb2f62fe-37a7-4de2-ae54-a0db2b268cf5"))
    let second = try #require(RunnerInstanceID("492a1f9f-eeda-43f3-b569-7f7d665664c9"))

    #expect(try RunnerInstanceID.bind(advertised: first, authenticated: first) == first)
    #expect(try RunnerInstanceID.bind(advertised: nil, authenticated: first) == first)
    #expect(throws: RunnerInstanceIdentityError.advertisementMismatch) {
        try RunnerInstanceID.bind(advertised: first, authenticated: second)
    }
    #expect(RunnerInstanceID("not-a-uuid") == nil)
}

@Test func bonjourIdentityCannotRedirectSavedRunnerCredential() throws {
    let identity = try #require(RunnerInstanceID("eb2f62fe-37a7-4de2-ae54-a0db2b268cf5"))
    let paired = try #require(URL(string: "http://192.168.1.10:4778"))
    let attacker = try #require(URL(string: "http://192.168.1.99:4778"))

    #expect(RunnerCredentialPresentationPolicy.allows(
        advertised: identity,
        savedInstanceID: identity,
        pairedBaseURL: paired,
        candidateBaseURL: paired
    ))
    #expect(!RunnerCredentialPresentationPolicy.allows(
        advertised: identity,
        savedInstanceID: identity,
        pairedBaseURL: paired,
        candidateBaseURL: attacker
    ))
}

@Test func routesRejectUnsafeInputs() throws {
    let open = try #require(ForgeRoute(url: URL(string: "forge://open?workspace=Demo&file=main.c&line=4")!))
    #expect(open == .open(workspace: "Demo", file: "main.c", line: 4, column: nil))
    #expect(ForgeRoute(url: URL(string: "forge://artifact?digest=nope")!) == nil)
    #expect(ForgeRoute(url: URL(string: "https://example.com")!) == nil)
}

@Test func revisionRejectsTraversal() {
    #expect(throws: WorkspaceRevisionError.self) {
        try WorkspaceRevision.Entry(path: "../secret", kind: .file, digest: String(repeating: "a", count: 64), mode: 0o644, size: 1)
    }
}

@Test func assetPackIdentityIsOneConservativePathComponent() {
    for valid in ["ubuntu-core", "python_3", "2026.07.12", "rust-1.90.0"] {
        #expect(AssetPackIdentity.isValidComponent(valid))
    }
    for unsafe in [
        "", ".", "..", "../pack", "pack/child", "pack\\child", "/absolute",
        "c:/absolute", "C:\\absolute", "pack..child", "pack.-child", ".hidden",
        "trailing.", "UPPERCASE",
    ] {
        #expect(!AssetPackIdentity.isValidComponent(unsafe))
    }
}

@Test func assetPackInventoryRejectsMissingExtraAndDuplicateFiles() {
    let declared = ["bin/clang", "share/notice.txt"]
    let exact = Set(declared + [AssetPackInventory.storedManifestPath])
    #expect(AssetPackInventory.isExact(
        declaredFiles: declared,
        actualFiles: exact,
        includesStoredManifest: true
    ))
    #expect(!AssetPackInventory.isExact(
        declaredFiles: declared,
        actualFiles: exact.union(["undeclared"]),
        includesStoredManifest: true
    ))
    #expect(!AssetPackInventory.isExact(
        declaredFiles: declared,
        actualFiles: ["bin/clang"],
        includesStoredManifest: false
    ))
    #expect(!AssetPackInventory.isExact(
        declaredFiles: ["bin/clang", "bin/clang"],
        actualFiles: ["bin/clang"],
        includesStoredManifest: false
    ))
}

@Test func manifestNormalizesPorts() throws {
    let command = try ProjectManifest.Command(arguments: ["cargo", "test"])
    let manifest = try ProjectManifest(name: "Demo", commands: ["test": command], ports: [8080, 8080, 3000])
    #expect(manifest.ports == [3000, 8080])
}

@Test func rustPlanUsesLockedReproducibleCommands() throws {
    let plan = try #require(ToolchainPlanner.plans(files: ["Cargo.toml", "Cargo.lock", "src/main.rs"]).first)
    #expect(plan.language == .rust)
    #expect(plan.commands.first == .init(.fetch, "cargo", ["fetch", "--locked"]))
    #expect(plan.commands.contains { $0.phase == .lint && $0.arguments.contains("clippy") })
}

@Test func pythonPlanUsesFrozenUvAndCommonLinters() throws {
    let plan = try #require(ToolchainPlanner.plans(files: ["pyproject.toml", "uv.lock", "main.py"]).first)
    #expect(plan.commands.first == .init(.fetch, "uv", ["sync", "--frozen"]))
    let arguments = plan.commands.filter { $0.phase == .lint }.flatMap(\.arguments)
    #expect(arguments.contains("ruff"))
    #expect(arguments.contains("pyright"))
    #expect(arguments.contains("mypy"))
}

@Test func cmakeCppPlanUsesDependencyLintAndTestTools() throws {
    let plan = try #require(ToolchainPlanner.plans(files: ["CMakeLists.txt", "Sources/Main.CPP", "vcpkg.json"]).first)
    #expect(plan.language == .cpp)
    #expect(plan.commands.contains { $0.executable == "vcpkg" })
    #expect(plan.commands.contains { $0.executable == "clang-tidy" })
    #expect(plan.commands.contains { $0.executable == "ctest" })
    #expect(plan.pipeline(through: .build).first?.phase == .fetch)
}

@Test func singleCPlanUsesStructuredArgv() throws {
    let plan = try #require(ToolchainPlanner.plans(files: ["hello.c"]).first)
    #expect(plan.commands.last?.executable == ".forge/build/app")
    #expect(plan.commands.allSatisfy { !$0.executable.contains(" ") })
}

@Test func cRunPipelineBuildsInTheSameWorkspace() throws {
    let plan = try #require(ToolchainPlanner.plans(files: ["hello.c"]).first)
    let pipeline = plan.pipeline(through: .run)
    #expect(pipeline.map(\.phase) == [.build, .build, .run])
    #expect(pipeline.last == .init(.run, ".forge/build/app"))
}
