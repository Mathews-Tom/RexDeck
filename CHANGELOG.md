# Changelog

## Unreleased

- Package the native Files Lua action and Python launcher so installed RexDeck works outside a checkout. Replace manual workspace flags and shell wrappers with one-time `setup`, `enable files`, `status`, and `disable files`; persist the explicitly approved Rex target and derive Files state paths automatically.
- Replace the custom Files browser with a native **RexDeck Files** action launching unmodified Yazi in the invoking terminal's captured working directory, preserving its normal UI, configuration, navigation, previews, and file operations.
- Remove Files' LazyRex hub/SDK dependency, renderer/navigation patches, Go runtime preparation, lock/build receipts, and obsolete browser/preview commands and tests. Yazi remains write-capable; the launch directory is not a root jail.
- Return Yazi quit to the exact captured terminal through the explicit Rex endpoint, with visible launch/exit/focus errors and no replacement-pane adoption. Independent Yazi panes survive stop/disable.
- Preserve tracked native registration, source integrity, conservative conflicts, atomic rollback, config symlink/mode preservation, and cancellation diagnostics. Package location is update provenance rather than ownership identity, so re-enabling refreshes an unchanged owned integration after reinstall. Refuse legacy receipts; old LazyRex installations require explicit prior-code cleanup.
- Make LazyRex optional for future integrations that actually need an existing capability; defer the broader collection. No PyPI release, automatic tool installation, live configuration migration, hooks, fonts, or autostart is included.
