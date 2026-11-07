# Changelog

## Unreleased

- Separate the collection overview from Files-specific documentation in `plugins/files/README.md`; replace placeholder setup paths with standard macOS locations and explicit live-server selection.
- Replace the custom Files browser with a native **RexDeck Files** action launching unmodified Yazi in the invoking terminal's captured working directory, preserving its normal UI, configuration, navigation, previews, and file operations.
- Remove Files' LazyRex hub/SDK dependency, renderer/navigation patches, Go runtime preparation, lock/build receipts, and obsolete browser/preview commands and tests. Yazi remains write-capable; the launch directory is not a root jail.
- Return Yazi quit to the exact captured terminal through the explicit Rex endpoint, with visible launch/exit/focus errors and no replacement-pane adoption. Independent Yazi panes survive stop/disable.
- Keep explicit inspect/apply/start/status/stop/disable with tracked owned Lua registration, source integrity, disabled-state preservation, conservative conflicts, atomic rollback, config symlink/mode preservation, and cancellation diagnostics. Refuse legacy receipts; old installations require explicit prior-code cleanup and a fresh native workspace.
- Make LazyRex optional for future integrations that actually need an existing capability; defer the broader collection and public distribution. No release, live configuration migration, installer, hooks, fonts, or autostart is included.
