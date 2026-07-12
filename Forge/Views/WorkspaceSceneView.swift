import ForgeCore
import SwiftUI

struct WorkspaceSceneView: View {
    let workspaceID: UUID?
    @EnvironmentObject private var model: ForgeAppModel

    var body: some View {
        if let workspace = model.workspaces.first(where: { $0.id == workspaceID }) {
            GeometryReader { proxy in
                ResponsiveWorkspace(workspace: workspace, width: proxy.size.width)
            }
            .navigationTitle(workspace.name)
        } else {
            ContentUnavailableView(
                "Workspace unavailable",
                systemImage: "folder.badge.questionmark",
                description: Text("Open an existing workspace from the Forge hub.")
            )
        }
    }
}

private struct ResponsiveWorkspace: View {
    let workspace: WorkspaceSummary
    let width: CGFloat
    @StateObject private var state = WorkspaceViewState()

    var body: some View {
        VStack(spacing: 0) {
            WorkspaceActionBar(
                workspace: workspace,
                selectedFile: $state.selectedFile,
                showsCompactNavigator: width < 820
            )
            Divider()
            HStack(spacing: 0) {
                if width >= 820 {
                    WorkspaceNavigator(workspace: workspace, selectedFile: $state.selectedFile)
                        .frame(width: width >= 1180 ? 230 : 190)
                    Divider()
                }
                VStack(spacing: 0) {
                    SourceEditorView(workspace: workspace, relativePath: state.selectedFile)
                    Divider()
                    BottomPanel(selection: $state.bottomPanel, workspaceID: workspace.id)
                        .frame(height: width >= 820 ? 210 : 170)
                }
                if width >= 1180 {
                    Divider()
                    WorkspaceInspector(workspace: workspace)
                        .frame(width: 260)
                }
            }
            WorkspaceStatusBar(workspace: workspace)
        }
        .background(ForgeTheme.graphite)
    }
}

private struct WorkspaceActionBar: View {
    let workspace: WorkspaceSummary
    @Binding var selectedFile: String
    let showsCompactNavigator: Bool
    @EnvironmentObject private var model: ForgeAppModel
    @StateObject private var state = WorkspaceActionState()

    var body: some View {
        HStack(spacing: 8) {
            if showsCompactNavigator {
                Menu {
                    ForEach(state.files, id: \.self) { file in
                        Button(file) { selectedFile = file }
                    }
                } label: {
                    Label("Files", systemImage: "folder")
                }
            }
            Menu {
                ForEach(state.actions) { action in
                    Button(action.title) { model.run(action.commands, in: workspace) }
                }
            } label: {
                Label("Tasks", systemImage: "hammer")
            }
            .disabled(state.actions.isEmpty)
            ForEach([ToolchainPhase.build, .test, .run], id: \.self) { phase in
                Button {
                    if let action = state.actions.first(where: { $0.phase == phase }) {
                        model.run(action.commands, in: workspace)
                    }
                } label: {
                    Label(phase.rawValue.capitalized, systemImage: icon(phase))
                }
                .disabled(!state.actions.contains { $0.phase == phase })
            }
            Spacer()
            Text(model.runnerDetail)
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .buttonStyle(.borderless)
        .padding(.horizontal, ForgeTheme.standardSpacing)
        .frame(height: 40)
        .background(.bar)
        .task(id: workspace.root) {
            state.files = await WorkspaceFileIndex.files(in: workspace.root)
            do {
                let plans = try await model.workspaceExecution.plans(for: workspace)
                state.actions = plans.flatMap { plan in
                    Set(plan.commands.map(\.phase)).map { phase in
                        .init(language: plan.language, phase: phase, commands: plan.pipeline(through: phase))
                    }
                }.sorted { $0.title < $1.title }
            } catch {
                model.alert = .init(title: "Could not inspect workspace", message: error.localizedDescription)
            }
        }
    }

    private func icon(_ phase: ToolchainPhase) -> String {
        switch phase {
        case .build: "hammer"
        case .test: "checkmark.circle"
        case .run: "play.fill"
        case .fetch: "shippingbox.and.arrow.backward"
        case .lint: "text.magnifyingglass"
        }
    }
}

private final class WorkspaceActionState: ObservableObject {
    struct Action: Identifiable {
        let id = UUID()
        let language: ProjectLanguage
        let phase: ToolchainPhase
        let commands: [ToolchainCommand]
        var title: String { "\(language.rawValue.uppercased()) · \(phase.rawValue) · \(commands.count) step\(commands.count == 1 ? "" : "s")" }
    }

    @Published var actions: [Action] = []
    @Published var files: [String] = []
}

private final class WorkspaceViewState: ObservableObject {
    @Published var selectedFile = "README.md"
    @Published var bottomPanel: BottomPanelKind = .terminal
}

private struct WorkspaceNavigator: View {
    let workspace: WorkspaceSummary
    @Binding var selectedFile: String
    @StateObject private var state = NavigatorState()

    var body: some View {
        VStack(spacing: 0) {
            PaneHeader(title: "Explorer", detail: workspace.name)
            List {
                ForEach(state.files, id: \.self) { path in
                    Button { selectedFile = path } label: {
                        Label(path, systemImage: sourceIcon(path))
                            .lineLimit(1)
                    }
                    .buttonStyle(.plain)
                }
            }
            .listStyle(.sidebar)
        }
        .task(id: workspace.root) { state.files = await WorkspaceFileIndex.files(in: workspace.root) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Workspace files")
    }

    private func sourceIcon(_ path: String) -> String {
        switch URL(fileURLWithPath: path).pathExtension.lowercased() {
        case "c", "h": "c.square"
        case "cc", "cpp", "cxx", "hpp": "plus.forwardslash.minus"
        case "rs": "gearshape.2"
        case "py": "chevron.left.forwardslash.chevron.right"
        case "md": "doc.richtext"
        default: "doc.plaintext"
        }
    }
}

private final class NavigatorState: ObservableObject {
    @Published var files = ["README.md"]
}

private enum WorkspaceFileIndex {
    static func files(in root: URL) async -> [String] {
        await Task.detached(priority: .utility) { filesSynchronously(in: root) }.value
    }

    private static func filesSynchronously(in root: URL) -> [String] {
        let manager = FileManager.default
        let keys: [URLResourceKey] = [.isRegularFileKey, .isSymbolicLinkKey]
        guard let enumerator = manager.enumerator(
            at: root,
            includingPropertiesForKeys: keys,
            options: [],
            errorHandler: { _, _ in true }
        ) else { return [] }
        var result: [String] = []
        for case let url as URL in enumerator {
            if url.lastPathComponent == ".git" {
                enumerator.skipDescendants()
                continue
            }
            guard result.count < 10_000,
                  let values = try? url.resourceValues(forKeys: Set(keys)),
                  values.isRegularFile == true,
                  values.isSymbolicLink != true else { continue }
            result.append(String(url.path.dropFirst(root.path.count + 1)))
        }
        return result.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }
}

private struct WorkspaceInspector: View {
    let workspace: WorkspaceSummary

    var body: some View {
        VStack(spacing: 0) {
            PaneHeader(title: "Inspector")
            Form {
                Section("Project") {
                    LabeledContent("Runtime", value: "Ubuntu")
                    LabeledContent("Build", value: "Detected")
                    LabeledContent("Git", value: "Working Copy")
                }
                Section("Diagnostics") {
                    Label("No problems", systemImage: "checkmark.circle")
                        .foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
        }
    }
}

private struct WorkspaceStatusBar: View {
    let workspace: WorkspaceSummary

    var body: some View {
        HStack(spacing: 14) {
            ForgeStatusBadge(label: "Ubuntu", systemImage: "shippingbox", isActive: false)
            Text("main")
            Spacer()
            Text("UTF-8")
            Text("LF")
            Text("VIM: NORMAL")
                .foregroundStyle(ForgeTheme.amber)
        }
        .font(.caption.monospaced())
        .padding(.horizontal, ForgeTheme.standardSpacing)
        .frame(height: 28)
        .background(ForgeTheme.raised)
        .overlay(alignment: .top) { Divider() }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Workspace status")
    }
}
