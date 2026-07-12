import SwiftUI

struct ForgeSettingsView: View {
    @EnvironmentObject private var model: ForgeAppModel
    @AppStorage("telemetry.enabled") private var telemetryEnabled = false
    @AppStorage("editor.vimMode") private var vimMode = true
    @AppStorage("storage.cacheQuotaGB") private var cacheQuotaGB = 300
    @StateObject private var state = ForgeSettingsState()

    var body: some View {
        NavigationSplitView {
            List {
                Label("Runtime", systemImage: "shippingbox")
                Label("Editor", systemImage: "text.cursor")
                Label("Storage", systemImage: "internaldrive")
                Label("Privacy", systemImage: "hand.raised")
            }
            .navigationTitle("Settings")
        } detail: {
            Form {
                Section("Forge Runner") {
                    if model.runnerEndpoints.isEmpty {
                        LabeledContent("Discovery", value: model.runnerDetail)
                    } else {
                        Picker("Runner", selection: $state.runnerID) {
                            Text("Choose a runner").tag("")
                            ForEach(model.runnerEndpoints) { endpoint in
                                Text(endpoint.name).tag(endpoint.id)
                            }
                        }
                        SecureField("One-time pairing code", text: $state.pairingCode)
                            .textContentType(.oneTimeCode)
                            .keyboardType(.asciiCapable)
                        Button("Pair and Verify") {
                            model.pairRunner(endpointID: state.runnerID, code: state.pairingCode)
                            state.pairingCode = ""
                        }
                        .disabled(state.runnerID.isEmpty || state.pairingCode.count < 6)
                        Text(model.runnerDetail)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Section("Editor") {
                    Toggle("Vim mode by default (planned)", isOn: $vimMode)
                        .disabled(true)
                }
                Section("Storage") {
                    Stepper("Regenerable cache: \(cacheQuotaGB) GB", value: $cacheQuotaGB, in: 50...500, step: 25)
                        .disabled(true)
                    Text("Workspaces and external recovery copies are never evicted.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Section("Anonymous health telemetry") {
                    Toggle("Send to configured Forge Runner (planned)", isOn: $telemetryEnabled)
                        .disabled(true)
                    Text("No telemetry is currently collected or sent.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            .navigationTitle("Forge")
        }
    }
}

private final class ForgeSettingsState: ObservableObject {
    @Published var runnerID = ""
    @Published var pairingCode = ""
}
