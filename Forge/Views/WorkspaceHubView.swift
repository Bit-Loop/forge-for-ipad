import ForgeCore
import SwiftUI
import UniformTypeIdentifiers

struct WorkspaceHubView: View {
    @EnvironmentObject private var model: ForgeAppModel
    @Environment(\.openWindow) private var openWindow
    @StateObject private var state = HubViewState()

    var body: some View {
        NavigationSplitView {
            List {
                Section("Workspaces") {
                    ForEach(model.workspaces) { workspace in
                        Button {
                            model.selectedWorkspaceID = workspace.id
                        } label: {
                            WorkspaceRow(workspace: workspace)
                        }
                        .buttonStyle(.plain)
                        .listRowBackground(
                            model.selectedWorkspaceID == workspace.id ? ForgeTheme.amber.opacity(0.12) : nil
                        )
                            .contextMenu {
                                Button("Open in New Window") {
                                    openWindow(value: ForgeScene(kind: .workspace, workspaceID: workspace.id))
                                }
                            }
                    }
                }
            }
            .navigationTitle("Forge")
            .toolbar {
                ToolbarItem(placement: .secondaryAction) {
                    Button("Settings", systemImage: "gear") { state.isShowingSettings = true }
                }
                ToolbarItem(placement: .primaryAction) {
                    Menu("Add Workspace", systemImage: "plus") {
                        Button("New Forge Workspace", systemImage: "folder.badge.plus") {
                            state.isCreatingWorkspace = true
                        }
                        Button("Open Folder from Files", systemImage: "folder") {
                            state.isImportingWorkspace = true
                        }
                    }
                    .accessibilityHint("Creates a Forge folder or opens a Working Copy or Textastic folder")
                }
            }
        } detail: {
            if model.isBootstrapping {
                ProgressView("Restoring Forge")
                    .accessibilityLabel("Restoring Forge state")
            } else if let workspace = model.workspaces.first(where: { $0.id == model.selectedWorkspaceID }) {
                WorkspaceOverview(workspace: workspace) {
                    openWindow(value: ForgeScene(kind: .workspace, workspaceID: workspace.id))
                }
            } else {
                RuntimeOverview()
            }
        }
        .sheet(isPresented: $state.isCreatingWorkspace) {
            NavigationStack {
                Form {
                    TextField("Workspace name", text: $state.workspaceName)
                        .textInputAutocapitalization(.words)
                        .autocorrectionDisabled()
                }
                .navigationTitle("New Workspace")
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { state.isCreatingWorkspace = false }
                    }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Create") {
                            model.createWorkspace(named: state.workspaceName)
                            state.workspaceName = ""
                            state.isCreatingWorkspace = false
                        }
                        .disabled(state.workspaceName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    }
                }
            }
            .presentationDetents([.medium])
        }
        .sheet(isPresented: $state.isShowingSettings) {
            NavigationStack {
                ForgeSettingsView()
                    .environmentObject(model)
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("Done") { state.isShowingSettings = false }
                        }
                    }
            }
        }
        .fileImporter(
            isPresented: $state.isImportingWorkspace,
            allowedContentTypes: [.folder],
            allowsMultipleSelection: false
        ) { result in
            switch result {
            case .success(let urls):
                if let url = urls.first { model.registerExternalWorkspace(url) }
            case .failure(let error):
                model.alert = .init(title: "Could not open Files", message: error.localizedDescription)
            }
        }
    }
}

private final class HubViewState: ObservableObject {
    @Published var isCreatingWorkspace = false
    @Published var isShowingSettings = false
    @Published var isImportingWorkspace = false
    @Published var workspaceName = ""
}

private struct WorkspaceRow: View {
    let workspace: WorkspaceSummary

    var body: some View {
        HStack(spacing: ForgeTheme.standardSpacing) {
            Image(systemName: "folder")
                .foregroundStyle(ForgeTheme.amber)
            VStack(alignment: .leading, spacing: 2) {
                Text(workspace.name).lineLimit(1)
                Text(workspace.lastOpenedAt, style: .relative)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private struct WorkspaceOverview: View {
    let workspace: WorkspaceSummary
    let open: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            Spacer()
            Image(systemName: "hammer.fill")
                .font(.system(size: 42, weight: .semibold))
                .foregroundStyle(ForgeTheme.amber)
            Text(workspace.name)
                .font(.largeTitle.weight(.semibold))
            Text(workspace.root.path(percentEncoded: false))
                .font(.callout.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            Button("Open Workspace", action: open)
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            Spacer()
        }
        .padding(40)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct RuntimeOverview: View {
    @EnvironmentObject private var model: ForgeAppModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Forge for iPad")
                .font(.largeTitle.weight(.semibold))
            Text("Native editing, ARM64 Linux, WASI, and durable LAN execution.")
                .font(.title3)
                .foregroundStyle(.secondary)
            Divider()
            LabeledContent("Runtime", value: model.runtime.environment.rawValue)
            LabeledContent("State", value: model.runtime.phase.rawValue.capitalized)
            LabeledContent("Available memory", value: ByteCountFormatter.string(fromByteCount: Int64(model.runtime.availableMemoryBytes), countStyle: .memory))
            LabeledContent("JIT", value: model.runtime.jitEnabled ? "Verified" : "Not enabled")
            HStack {
                Button("Open Terminal") { openWindow(value: ForgeScene(kind: .terminal)) }
                    .buttonStyle(.borderedProminent)
                Button("Linux Desktop") { openWindow(value: ForgeScene(kind: .desktop)) }
                    .buttonStyle(.bordered)
            }
            Spacer()
        }
        .padding(40)
        .frame(maxWidth: 720, maxHeight: .infinity, alignment: .leading)
    }
}
