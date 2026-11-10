# Dependencies

RexDeck never installs, upgrades, downloads or removes software. It never cancels package transactions, and it never probes or runs Homebrew. Rex, Python, uv and every upstream tool remain externally managed prerequisites that you install yourself.

[Back to RexDeck](../README.md) · [Integrations](integrations.md) · [Usage](usage.md)

## Install the tool you select

1. Open the integration's guide from [Integrations](integrations.md). Its **Requirements** section names the qualified version, the official release and any helpers.
2. Install that version yourself from the upstream release, or through a package manager whose selected version you have checked (see Homebrew below).
3. Verify the version the guide names, usually with `<tool> --version`.
4. Run `rexdeck enable <id>`.

`setup` and `status` never install packages. `enable` validates only the selected integration's preinstalled tools. A missing or unqualified tool produces actionable, nonzero manual-install, version-check and rerun guidance, and registers nothing. Purpose-built views run on RexDeck's own Python package and need no separate TUI executable; some still require a CLI such as `gh`.

`disable` and uninstalling the Python package leave upstream tools and their configuration installed. No install-all, sudo, silent upgrade, tool configuration change or upstream package removal is performed.

## Homebrew

If you choose Homebrew:

1. Run `brew info <formula>` yourself, and inspect the version it selects and its dependency, source-build and upgrade effects.
2. **Skip Homebrew if its selected version is not qualified.** Use the qualified upstream release instead.
3. Run `brew install <formula>` yourself, only if the version qualifies and you accept those effects.

RexDeck does not qualify a Homebrew version, prefix or transaction; all package effects remain under your control.

## How RexDeck finds executables

- **`PATH` entries:** executable discovery ignores empty and relative `PATH` entries. The shared launcher also removes them from the upstream tool's environment, so helper lookup cannot silently search the selected file's folder through `.` or an empty entry. Absolute entries, including checkout directories and symlinks to them, remain eligible for version probes, lifecycle lookup and helper execution. Put only trusted tools in absolute `PATH` directories.
- **No absolute entries:** the launched tool uses Python's safe system `os.defpath`, never an empty `PATH`, which some helper lookups treat as the current directory.
- **Executable identity checks:** RexDeck records the followed target's device, inode, mode, size, nanosecond timestamps and owner before the version probe. It rechecks that identity afterwards, and again under its operation locks just before applying or enabling an integration.
  - Replacing or modifying the executable requires rerunning `enable`.
  - This is metadata-based detection. It is not pinned execution and does not protect against an executable changed after the final check.
