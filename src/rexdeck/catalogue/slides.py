"""Slides: unmodified presenterm on one explicitly selected Markdown deck."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="slides",
    title="Slides",
    tool="presenterm",
    tool_label="presenterm",
    description="Present an explicitly selected Markdown slide deck with presenterm",
    keywords="slides markdown presentation presenterm deck",
    context="selected-file",
    versions=("0.16.1",),
    version_pattern=r"^presenterm (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/mfontanini/presenterm/releases/tag/v0.16.1",
    license="BSD-2-Clause",
    capabilities=(
        "reads the selected .md deck, included Markdown, local images and "
        "referenced theme files; file snippets can read and display any "
        "accessible file, including absolute paths and credentials; open only "
        "trusted decks; does not edit the deck; image protocol is auto-detected",
        "reads $XDG_CONFIG_HOME/presenterm/config.yaml, otherwise on macOS "
        "~/Library/Application Support/presenterm/config.yaml; "
        "PRESENTERM_CONFIG_FILE overrides the config file; reads themes/*.yaml "
        "and themes/highlighting under the default configuration directory",
        "no execution flags are passed, but user config snippet.validate=true "
        "runs +validate snippets automatically at load even when "
        "snippet.exec.enable=false; this is not a sandbox",
        "snippet.exec.enable=true enables Ctrl+E execution and runs +auto_exec "
        "blocks at load; no -p is passed, so development mode reruns them on "
        "each file reload; snippet.exec_replace.enable=true enables automatic "
        "+exec_replace and +image execution",
        "rendered Typst, LaTeX, Mermaid and D2 blocks can invoke external "
        "renderers and write temporary files independently of snippet execution",
        "remote images are unsupported; speaker_notes.always_publish in user "
        "config can send UDP speaker-note events to its configured address "
        "(default 127.0.0.1:59418); external renderers and enabled snippets "
        "can access files and the network",
    ),
    default_layout=Layout("right", 50),
    file_suffixes=(".md",),
)
