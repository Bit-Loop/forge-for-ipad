import CryptoKit
import SwiftUI

struct SourceEditorView: View {
    let workspace: WorkspaceSummary
    let relativePath: String
    @EnvironmentObject private var model: ForgeAppModel
    @StateObject private var state = SourceEditorState()

    var body: some View {
        VStack(spacing: 0) {
            PaneHeader(title: relativePath, detail: "Vim")
            if let loadError = state.loadError {
                ContentUnavailableView(
                    "Could not open file",
                    systemImage: "exclamationmark.triangle",
                    description: Text(loadError)
                )
            } else {
                TextEditor(text: $state.text)
                    .font(.system(size: 14, weight: .regular, design: .monospaced))
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .scrollContentBackground(.hidden)
                    .padding(.horizontal, 6)
                    .background(ForgeTheme.graphite)
                    .disabled(state.isLoading || state.loadedPath != relativePath)
                    .overlay {
                        if state.isLoading { ProgressView().controlSize(.small) }
                    }
                    .onChange(of: state.text) { oldValue, newValue in
                        guard !state.consumeLoadedValue(newValue) else { return }
                        guard !state.isLoading, state.loadedPath == relativePath else { return }
                        journal(old: oldValue, new: newValue)
                    }
                    .accessibilityLabel("Source editor for \(relativePath)")
            }
        }
        .task(id: relativePath) { await load() }
    }

    private func load() async {
        state.loadGeneration += 1
        let generation = state.loadGeneration
        state.isLoading = true
        defer {
            if state.loadGeneration == generation { state.isLoading = false }
        }
        if let saveTask = state.saveTask { await saveTask.value }
        guard !Task.isCancelled, state.loadGeneration == generation else { return }
        let url = workspace.root.appending(path: relativePath)
        do {
            let data = try Data(contentsOf: url)
            guard let value = String(data: data, encoding: .utf8) else {
                throw WorkspaceError.invalidEncoding
            }
            guard !Task.isCancelled, state.loadGeneration == generation else { return }
            state.pendingLoadedValue = value
            state.text = value
            state.priorText = value
            state.loadedPath = relativePath
            state.committedDigest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            state.loadError = nil
        } catch {
            guard !Task.isCancelled, state.loadGeneration == generation else { return }
            state.loadedPath = nil
            state.loadError = error.localizedDescription
        }
    }

    private func journal(old: String, new: String) {
        guard old != new else { return }
        state.sequence += 1
        let delta = TextDelta.between(old, new)
        let operation = EditJournalOperation(
            sequence: state.sequence,
            relativePath: relativePath,
            UTF16Location: delta.location,
            UTF16Length: delta.length,
            replacement: delta.replacement,
            recordedAt: .now
        )
        let priorSave = state.saveTask
        state.saveTask = Task {
            if let priorSave { await priorSave.value }
            do {
                guard let expectedDigest = state.committedDigest else {
                    throw WorkspaceError.staleEdit
                }
                state.committedDigest = try await model.workspacesStore.commitEdit(
                    operation,
                    workspaceID: workspace.id,
                    file: workspace.root.appending(path: relativePath),
                    contents: Data(new.utf8),
                    expectedDigest: expectedDigest
                )
                state.priorText = new
            } catch {
                model.alert = .init(title: "Edit was not saved", message: error.localizedDescription)
            }
        }
    }
}

@MainActor
private final class SourceEditorState: ObservableObject {
    @Published var text = ""
    @Published var loadError: String?
    @Published var isLoading = true
    var priorText = ""
    var sequence: UInt64 = 0
    var saveTask: Task<Void, Never>?
    var pendingLoadedValue: String?
    var loadedPath: String?
    var committedDigest: String?
    var loadGeneration: UInt64 = 0

    func consumeLoadedValue(_ value: String) -> Bool {
        guard let pendingLoadedValue else { return false }
        self.pendingLoadedValue = nil
        return pendingLoadedValue == value
    }
}

private struct TextDelta {
    let location: Int
    let length: Int
    let replacement: String

    static func between(_ old: String, _ new: String) -> Self {
        let oldUnits = Array(old.utf16)
        let newUnits = Array(new.utf16)
        var prefix = 0
        while prefix < oldUnits.count, prefix < newUnits.count, oldUnits[prefix] == newUnits[prefix] {
            prefix += 1
        }
        var oldSuffix = oldUnits.count
        var newSuffix = newUnits.count
        while oldSuffix > prefix, newSuffix > prefix,
              oldUnits[oldSuffix - 1] == newUnits[newSuffix - 1] {
            oldSuffix -= 1
            newSuffix -= 1
        }
        let replacement = String(decoding: newUnits[prefix..<newSuffix], as: UTF16.self)
        return .init(location: prefix, length: oldSuffix - prefix, replacement: replacement)
    }
}
