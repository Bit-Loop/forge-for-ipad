import Foundation

public struct ForgeScene: Codable, Hashable, Identifiable, Sendable {
    public enum Kind: String, Codable, CaseIterable, Sendable {
        case hub
        case workspace
        case terminal
        case desktop
        case debugger
        case assistant
    }

    public let id: UUID
    public var kind: Kind
    public var workspaceID: UUID?

    public init(id: UUID = UUID(), kind: Kind, workspaceID: UUID? = nil) {
        self.id = id
        self.kind = kind
        self.workspaceID = workspaceID
    }

    public static var hub: Self { .init(kind: .hub) }
}
