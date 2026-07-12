import SwiftUI

enum BottomPanelKind: String, CaseIterable, Identifiable {
    case terminal
    case problems
    case output
    var id: Self { self }
}

struct BottomPanel: View {
    @Binding var selection: BottomPanelKind
    let workspaceID: UUID

    var body: some View {
        VStack(spacing: 0) {
            Picker("Bottom panel", selection: $selection) {
                ForEach(BottomPanelKind.allCases) { kind in
                    Text(kind.rawValue.capitalized).tag(kind)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, ForgeTheme.standardSpacing)
            .frame(height: 38)
            .background(.bar)
            Group {
                switch selection {
                case .terminal: TerminalSurface(workspaceID: workspaceID)
                case .problems: ProblemsSurface()
                case .output: OutputSurface(workspaceID: workspaceID)
                }
            }
        }
    }
}

struct TerminalSurface: View {
    let workspaceID: UUID?
    @EnvironmentObject private var model: ForgeAppModel

    var body: some View {
        ScrollView {
            Text(output)
                .font(.system(size: 13, design: .monospaced))
                .foregroundStyle(.primary)
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .padding(ForgeTheme.standardSpacing)
        }
        .background(Color.black.opacity(0.34))
        .accessibilityLabel("Terminal output")
    }

    private var output: String {
        guard let workspaceID else { return "No workspace is attached to this terminal.\n" }
        return model.outputByWorkspace[workspaceID]
            ?? "No runner output yet. Choose Fetch, Lint, Build, Test, or Run.\n"
    }
}

private struct ProblemsSurface: View {
    var body: some View {
        ContentUnavailableView("No problems", systemImage: "checkmark.circle")
            .foregroundStyle(.secondary)
    }
}

private struct OutputSurface: View {
    let workspaceID: UUID
    @EnvironmentObject private var model: ForgeAppModel

    var body: some View {
        ScrollView {
            Text(model.outputByWorkspace[workspaceID] ?? "Build and task output appears here.")
            .font(.callout.monospaced())
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(ForgeTheme.standardSpacing)
        }
    }
}
