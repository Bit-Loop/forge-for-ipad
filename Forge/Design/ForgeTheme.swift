import SwiftUI

enum ForgeTheme {
    static let amber = Color(red: 0.92, green: 0.53, blue: 0.16)
    static let amberMuted = Color(red: 0.56, green: 0.34, blue: 0.16)
    static let graphite = Color(red: 0.10, green: 0.105, blue: 0.11)
    static let raised = Color(red: 0.145, green: 0.15, blue: 0.16)
    static let separator = Color.white.opacity(0.12)
    static let secondaryText = Color.white.opacity(0.62)

    static let compactSpacing: CGFloat = 8
    static let standardSpacing: CGFloat = 12
    static let panePadding: CGFloat = 16
}

struct PaneHeader: View {
    let title: String
    var detail: String?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: ForgeTheme.compactSpacing) {
            Text(title)
                .font(.system(.caption, design: .rounded, weight: .semibold))
                .textCase(.uppercase)
                .tracking(0.8)
            Spacer(minLength: 8)
            if let detail {
                Text(detail)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
        }
        .padding(.horizontal, ForgeTheme.standardSpacing)
        .frame(height: 34)
        .background(.bar)
        .overlay(alignment: .bottom) { Divider() }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

struct ForgeStatusBadge: View {
    let label: String
    let systemImage: String
    let isActive: Bool

    var body: some View {
        Label(label, systemImage: systemImage)
            .font(.caption.weight(.medium))
            .foregroundStyle(isActive ? ForgeTheme.amber : .secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(.quaternary, in: Capsule())
            .accessibilityLabel(label)
    }
}
