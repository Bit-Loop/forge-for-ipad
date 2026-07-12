public enum AssetPackIdentity {
    /// A signed pack identity is also a single on-disk path component.
    /// Keep this grammar in sync with `release/forge_release/assets.py`.
    public static func isValidComponent(_ value: String) -> Bool {
        guard !value.isEmpty, value.utf8.count <= 128 else { return false }
        var previousWasSeparator = true
        for byte in value.utf8 {
            let isLowercaseLetter = (97...122).contains(byte)
            let isDigit = (48...57).contains(byte)
            if isLowercaseLetter || isDigit {
                previousWasSeparator = false
            } else if byte == 45 || byte == 46 || byte == 95 { // -, ., _
                guard !previousWasSeparator else { return false }
                previousWasSeparator = true
            } else {
                return false
            }
        }
        return !previousWasSeparator
    }
}

public enum AssetPackInventory {
    public static let storedManifestPath = ".forge-pack-manifest.json"

    public static func isExact(
        declaredFiles: [String],
        actualFiles: Set<String>,
        includesStoredManifest: Bool
    ) -> Bool {
        var expected = Set(declaredFiles)
        guard expected.count == declaredFiles.count else { return false }
        if includesStoredManifest {
            expected.insert(storedManifestPath)
        }
        return expected == actualFiles
    }
}
