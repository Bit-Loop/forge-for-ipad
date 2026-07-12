import ForgeCore
import SwiftUI

struct ForgeSceneRoot: View {
    let scene: ForgeScene
    @EnvironmentObject private var model: ForgeAppModel

    var body: some View {
        Group {
            switch scene.kind {
            case .hub:
                WorkspaceHubView()
            case .workspace:
                WorkspaceSceneView(workspaceID: scene.workspaceID)
            case .terminal:
                TerminalSceneView(workspaceID: scene.workspaceID)
            case .desktop:
                DesktopSceneView()
            case .debugger:
                DebuggerSceneView(workspaceID: scene.workspaceID)
            case .assistant:
                AssistantSceneView(workspaceID: scene.workspaceID)
            }
        }
        .tint(ForgeTheme.amber)
        .preferredColorScheme(.dark)
        .alert(item: Binding(get: { model.alert }, set: { model.alert = $0 })) { alert in
            Alert(title: Text(alert.title), message: Text(alert.message))
        }
    }
}
