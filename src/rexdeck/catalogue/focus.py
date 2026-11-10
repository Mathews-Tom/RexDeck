"""Focus: the upstream pomo timer in the invoking directory."""

from __future__ import annotations

from rexdeck.integrations import Integration, Layout

INTEGRATION = Integration(
    id="focus",
    title="Focus",
    tool="pomo",
    tool_label="pomo",
    description="Start a pomo work session using the invoking directory's config defaults",
    keywords="focus pomo pomodoro timer work break pause",
    context="directory",
    versions=("1.2.1",),
    version_pattern=r"^pomo version (\d+\.\d+\.\d+)\s*$",
    release="https://github.com/Bahaaio/pomo/releases/tag/v1.2.1",
    license="MIT",
    capabilities=(
        "reads invoking-directory pomo.yaml instead of ~/.config/pomo/pomo.yaml "
        "when present, without merging the user config; otherwise uses user config "
        "or built-in defaults; only invoke Focus from directories you trust",
        "enable probes pomo --version in the shell's current directory with the full "
        "environment; pomo loads config even for --version, so wrong-typed config "
        "can fail the probe and produce install guidance; run enable from a neutral "
        "directory without pomo.yaml and with a valid user config",
        "executes completion commands from work.then and break.then lists with your "
        "user privileges; commands can write files, run programs or use the network; "
        "the whole list shares a five-second timeout; starting the next session "
        "cancels commands except with onSessionEnd: start; quitting waits for "
        "commands up to that timeout, and a second q forces exit; no sandbox",
        "sends desktop notifications on completion by default; on macOS runs "
        "osascript or terminal-notifier when on PATH, whose notification path "
        "writes temporary .icns files under TMPDIR; urgent alerts add sound and "
        "relative icon paths read from cwd; set work.notification.enabled and "
        "break.notification.enabled to false to disable notifications",
        "creates ~/.local/state/pomo and its SQLite database schema in pomo.db "
        "on every timer launch on macOS, even without recorded sessions; writes "
        "session statistics on completion, timer quit or skip; uses HOME, not "
        "XDG_STATE_HOME",
        "no built-in network service; DEBUG set to a nonempty value other than 0 "
        "writes debug.log in cwd, including during the enable version probe, "
        "potentially exposing config and completion-command arguments",
    ),
    default_layout=Layout("right", 50),
)
