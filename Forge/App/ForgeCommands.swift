import ForgeCore
import SwiftUI

struct ForgeCommands: Commands {
    @Environment(\.openWindow) private var openWindow

    var body: some Commands {
        CommandMenu("Forge") {
            Button("New Workspace Window") { openWindow(value: ForgeScene.hub) }
                .keyboardShortcut("n", modifiers: [.command, .shift])
            Divider()
            Button("New Terminal") { openWindow(value: ForgeScene(kind: .terminal)) }
                .keyboardShortcut("t", modifiers: [.command, .shift])
            Button("Linux Desktop") { openWindow(value: ForgeScene(kind: .desktop)) }
                .keyboardShortcut("d", modifiers: [.command, .option])
            Button("Assistant") { openWindow(value: ForgeScene(kind: .assistant)) }
                .keyboardShortcut("i", modifiers: [.command, .shift])
        }
    }
}
