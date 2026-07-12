import SwiftUI

struct TerminalSceneView: View {
    let workspaceID: UUID?

    var body: some View {
        VStack(spacing: 0) {
            PaneHeader(title: "Terminal", detail: "Durable PTY")
            TerminalSurface(workspaceID: workspaceID)
        }
        .background(ForgeTheme.graphite)
    }
}

struct DesktopSceneView: View {
    var body: some View {
        ContentUnavailableView(
            "Linux desktop is offline",
            systemImage: "display",
            description: Text("Start Ubuntu or Manjaro, then this scene attaches to the shared SPICE display.")
        )
        .background(ForgeTheme.graphite)
    }
}

struct DebuggerSceneView: View {
    let workspaceID: UUID?

    var body: some View {
        ContentUnavailableView(
            "No debug session",
            systemImage: "ladybug",
            description: Text("Run a detected debug task to populate threads, frames, variables, and watches.")
        )
        .background(ForgeTheme.graphite)
    }
}

struct AssistantSceneView: View {
    let workspaceID: UUID?

    var body: some View {
        VStack(spacing: 0) {
            PaneHeader(title: "Assistant", detail: "Local only")
            ContentUnavailableView(
                "No local model loaded",
                systemImage: "brain",
                description: Text("Apple Foundation Models and the verified MLX coding pack appear here when available.")
            )
        }
        .background(ForgeTheme.graphite)
    }
}
