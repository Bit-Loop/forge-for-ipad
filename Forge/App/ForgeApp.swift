import ForgeCore
import SwiftUI

@main
struct ForgeApp: App {
    @StateObject private var model: ForgeAppModel

    init() {
        _model = StateObject(wrappedValue: ForgeAppModel())
        BackgroundExecutionCoordinator.shared.register()
    }

    var body: some Scene {
        WindowGroup(for: ForgeScene.self) { $scene in
            ForgeSceneHost(scene: scene, model: model)
        } defaultValue: {
            .hub
        }
        .commands { ForgeCommands() }
    }
}

private struct ForgeSceneHost: View {
    let scene: ForgeScene
    @ObservedObject var model: ForgeAppModel

    var body: some View {
        ForgeSceneRoot(scene: scene)
            .environmentObject(model)
            .task { await model.bootstrap() }
            .onOpenURL(perform: model.handle)
    }
}
