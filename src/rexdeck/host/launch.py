"""Start one unmodified terminal tool in a native Rex pane and restore its origin."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from urllib.parse import unquote, urlsplit


def _local(raw: str) -> Path:
    """Decode only an explicit absolute local path or file URI, never a fallback."""
    if not raw or "\x00" in raw:
        raise ValueError("An explicit local path without NUL characters is required")
    raw.encode("utf-8", errors="strict")
    if raw.lower().startswith("file:"):
        if any(ord(char) < 32 or ord(char) == 127 for char in raw):
            raise ValueError("File URI contains unescaped control characters")
        uri = urlsplit(raw)
        authority = uri.netloc.casefold()
        if (
            authority not in ("", "localhost")
            and authority != socket.gethostname().casefold()
            and authority != socket.getfqdn().casefold()
        ):
            raise ValueError("Remote or credentialed file URI is not supported")
        if "?" in raw or "#" in raw:
            raise ValueError("File URI must not contain a query or fragment")
        if re.search(r"%(?![0-9a-fA-F]{2})", uri.path):
            raise ValueError("File URI contains invalid percent encoding")
        raw = unquote(uri.path, encoding="utf-8", errors="strict")
    if "\x00" in raw or not raw.startswith("/") or raw.startswith("//"):
        raise ValueError("An absolute local path is required")
    try:
        return Path(raw).resolve(strict=True)
    except RuntimeError as error:
        # Python 3.12 reports symlink loops as RuntimeError rather than OSError.
        raise ValueError(str(error)) from error


def directory(raw: str) -> str:
    """Decode only an explicit absolute local directory, never a fallback."""
    path = _local(raw)
    if not path.is_dir():
        raise ValueError("The invoking terminal's cwd is not a directory")
    return str(path)


MARKDOWN_SUFFIXES = (".md", ".markdown")


def document(raw: str, selected: str | None = None) -> str:
    """Resolve an explicitly selected Markdown file or else the invoking directory.

    A relative selection is joined to the invoking directory; a cwd alone never
    names a document, so without a selection the directory itself is browsed.
    """
    if selected is None:
        return directory(raw)
    if not selected or "\x00" in selected:
        raise ValueError("The selected file must be a nonempty path")
    if selected.startswith("/") or selected.lower().startswith("file:"):
        raw = selected
    else:
        raw = str(Path(directory(raw)) / selected)
    path = _local(raw)
    if path.is_dir():
        return str(path)
    if path.is_file() and path.suffix.lower() in MARKDOWN_SUFFIXES:
        return str(path)
    raise ValueError(
        "Select a Markdown file (" + ", ".join(MARKDOWN_SUFFIXES) + ") or a directory"
    )


MEDIA_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".svg")


def media(raw: str, selected: str | None = None) -> str:
    """Require a selected local rich-format file; never silently browse a cwd."""
    if selected is None or not selected or "\x00" in selected:
        raise ValueError("Select a local PDF, JPG, PNG or SVG with the file argument")
    if not selected.startswith("/") and not selected.lower().startswith("file:"):
        selected = str(Path(directory(raw)) / selected)
    path = _local(selected)
    if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
        raise ValueError("Select a local file ending in " + ", ".join(MEDIA_SUFFIXES))
    # Detect obvious mislabeled files before a preview pane opens. Full decoding
    # remains the upstream previewer's job, not a RexDeck rendering pipeline.
    suffix = path.suffix.lower()
    with path.open("rb") as stream:
        if suffix == ".svg":
            try:
                _, root = next(ET.iterparse(stream, events=("start",)))
                valid = root.tag in ("svg", "{http://www.w3.org/2000/svg}svg")
            except (ET.ParseError, StopIteration):
                valid = False
        else:
            header = stream.read(1024)
            valid = (
                b"%PDF-" in header
                if suffix == ".pdf"
                else header.startswith(b"\xff\xd8\xff")
                if suffix in (".jpg", ".jpeg")
                else header.startswith(b"\x89PNG\r\n\x1a\n")
            )
    if not valid:
        raise ValueError(f"Invalid {suffix} file signature: {path}")
    return str(path)


SELECTED_FILE = "selected-file"


def _accepted(spec: str) -> tuple[tuple[str, ...], bool]:
    """Decode a descriptor's ``.suffix,...[,/]`` selection spec exactly."""
    entries = spec.split(",")
    suffixes = tuple(entry for entry in entries if entry != "/")
    directories = "/" in entries
    if (
        not spec
        or len(set(entries)) != len(entries)
        or not all(re.fullmatch(r"\.[a-z0-9]+", suffix) for suffix in suffixes)
    ):
        raise ValueError(f"Invalid selected-file specification: {spec!r}")
    return suffixes, directories


def selected_file(spec: str, raw: str, selected: str | None = None) -> str:
    """Require an explicit existing file with an accepted suffix, or an accepted
    directory; a relative selection is joined to the invoking directory."""
    suffixes, directories = _accepted(spec)
    wanted = " or ".join(
        part
        for part in (
            ", ".join(suffixes) + " file" if suffixes else "",
            "directory" if directories else "",
        )
        if part
    )
    if selected is None or not selected or "\x00" in selected:
        raise ValueError(f"Select an existing {wanted} with the file argument")
    if not selected.startswith("/") and not selected.lower().startswith("file:"):
        selected = str(Path(directory(raw)) / selected)
    try:
        path = _local(selected)
    except FileNotFoundError:
        # repr() keeps terminal control characters in names from reaching the pane.
        raise ValueError(f"The selected path does not exist: {selected!r}") from None
    # Tools such as lnav glob their arguments (*, ?, [ and \ escapes) before opening
    # a path by name, so a selected path containing them could open other files.
    if any(char in str(path) for char in "*?[\\"):
        raise ValueError(
            f"Selected paths cannot contain *, ?, [ or \\ characters: {str(path)!r}"
        )
    if directories and path.is_dir():
        return str(path)
    if path.is_file() and path.suffix.lower() in suffixes:
        return str(path)
    raise ValueError(f"Select an existing {wanted}, not {str(path)!r}")


def git_directory(raw: str) -> str:
    """Keep cwd-based Git discovery; reject inherited repository redirection."""
    overrides = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    }
    selected = sorted(
        key
        for key in os.environ
        if key in overrides or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    )
    if selected:
        raise ValueError("Ambiguous inherited Git override: " + ", ".join(selected))
    cwd = directory(raw)
    git = executable("git")
    result = subprocess.run(
        [git, "rev-parse", "--is-inside-work-tree"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"Git exited {result.returncode}")
    if result.stdout.strip() != "true":
        raise ValueError("Invoking directory is not a non-bare Git worktree")
    return cwd


def executable(name: str) -> str:
    """Resolve helper programs only from the same trusted absolute PATH entries."""
    search = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep)
        if Path(entry).is_absolute()
    )
    found = shutil.which(name, path=search) if search else None
    if found is None:
        raise ValueError(
            f"Required executable not found in absolute PATH directories: {name}"
        )
    return found


def execution_environment() -> dict[str, str]:
    """Preserve configuration/auth while excluding cwd-relative helper lookup."""
    environment = dict(os.environ)
    environment["PATH"] = (
        os.pathsep.join(
            entry
            for entry in os.environ.get("PATH", os.defpath).split(os.pathsep)
            if Path(entry).is_absolute()
        )
        or os.defpath
    )
    return environment


def git_output(cwd: str, *args: str) -> str:
    result = subprocess.run(
        [executable("git"), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"Git exited {result.returncode}")
    return result.stdout.strip()


def github_repo(value: str) -> str:
    """Require an explicit DNS host and unambiguous owner/repository path."""
    parts = value.split("/")
    if len(parts) != 3:
        raise ValueError(
            "Use repo=HOST/OWNER/REPO (including the explicit GitHub host)"
        )
    host, owner, repo = parts
    repo = repo.removesuffix(".git")
    if (
        len(host) > 253
        or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in host.split(".")
        )
        or "." not in host
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", owner)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo)
        or repo in (".", "..")
    ):
        raise ValueError(
            "Invalid GitHub host/owner/repository; use repo=HOST/OWNER/REPO"
        )
    return f"{host.lower()}/{owner}/{repo}"


def github_remote(value: str) -> str:
    """Accept literal HTTPS/SSH GitHub remotes, not SSH aliases or URL rewriting."""
    match = re.fullmatch(r"git@([^/:]+):([^/]+/[^/]+)", value)
    if match:
        return github_repo(f"{match[1]}/{match[2]}")
    uri = urlsplit(value)
    if (
        uri.scheme not in ("https", "ssh")
        or not uri.hostname
        or uri.port is not None
        or uri.password is not None
        or (
            uri.username is not None
            and not (uri.scheme == "ssh" and uri.username == "git")
        )
        or uri.query
        or uri.fragment
    ):
        raise ValueError(
            "Unsupported or ambiguous GitHub remote; use repo=HOST/OWNER/REPO"
        )
    return github_repo(uri.hostname + "/" + uri.path.removeprefix("/"))


def github_selection(cwd: str, raw: str | None) -> dict[str, str]:
    """Freeze a deliberate remote/repo and account without asking Git to guess."""
    value = json.loads(raw or "{}")
    if not isinstance(value, dict) or set(value) - {"repo", "remote", "account"}:
        raise ValueError("GitHub selection accepts only repo, remote and account")
    if any(
        not isinstance(item, str) or not item or "\x00" in item
        for item in value.values()
    ):
        raise ValueError("GitHub selection values must be nonempty strings")
    account = value.get("account", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", account):
        raise ValueError(
            "Explicit account=LOGIN is required; existing gh auth must match"
        )
    if "repo" in value and "remote" in value:
        raise ValueError("Choose repo=HOST/OWNER/REPO or remote=NAME, not both")
    if "repo" in value:
        repo = github_repo(value["repo"])
    else:
        remotes = git_output(cwd, "remote").splitlines()
        remote = value.get("remote")
        if remote is None:
            if len(remotes) != 1:
                raise ValueError(
                    "Repository remotes are missing or ambiguous; choose remote=NAME "
                    "or repo=HOST/OWNER/REPO. Remotes: " + ", ".join(remotes)
                )
            remote = remotes[0]
        if remote not in remotes:
            raise ValueError(f"Unknown repository remote {remote!r}")
        urls = git_output(cwd, "remote", "get-url", "--all", remote).splitlines()
        if len(urls) != 1:
            raise ValueError("Remote has multiple fetch URLs; use repo=HOST/OWNER/REPO")
        repo = github_remote(urls[0])
        if not repo.startswith("github.com/"):
            # A remote URL is repository-controlled; never send credentials to a host
            # the operator did not type themselves.
            raise ValueError(
                "Remote points to a non-github.com host; choose repo=HOST/OWNER/REPO"
            )
    return {"repo": repo, "account": account}


def github_environment(selection: dict[str, str], cwd: str) -> dict[str, str]:
    """Verify supported existing authentication; never read or persist a token."""
    if "FF_MOCK_DATA" in os.environ:
        raise ValueError(
            "Remove gh-dash FF_MOCK_DATA before launching real GitHub workflows"
        )
    host, owner, repo = selection["repo"].split("/")
    environment = execution_environment()
    gh = executable("gh")
    environment.update(
        GH_HOST=host, GH_REPO=selection["repo"], GH_PATH=gh, GH_PROMPT_DISABLED="1"
    )

    def api(endpoint: str, query: str) -> str:
        result = subprocess.run(
            [gh, "api", "--hostname", host, endpoint, "--jq", query],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if result.returncode:
            raise ValueError(
                f"GitHub authentication/API failed for {host} as "
                f"{selection['account']}: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout.strip()

    actual = api("user", ".login")
    if actual.casefold() != selection["account"].casefold():
        raise ValueError(
            f"GitHub account mismatch: requested {selection['account']}, authenticated "
            f"as {actual}. Use an already configured matching gh authentication "
            "environment; RexDeck never switches global accounts."
        )
    actual_repo = api(f"repos/{owner}/{repo}", ".full_name")
    if actual_repo.casefold() != f"{owner}/{repo}".casefold():
        raise ValueError(
            f"GitHub repository redirected to {host}/{actual_repo}; select it explicitly"
        )
    return environment


def github_config(cwd: str, selection: dict[str, str]) -> dict[str, object]:
    """Layer only repository sections over upstream global and selected settings."""
    invoking = Path(cwd)
    provided = os.environ.get("GH_DASH_CONFIG")
    includes = []
    if provided:
        includes.append(str((Path(cwd) / provided).resolve()))
    else:
        for name in (".gh-dash.yml", ".gh-dash.yaml"):
            candidate = invoking / name
            if candidate.exists():
                includes.append(str(candidate))
                break
    host, owner, repo = selection["repo"].split("/")
    title = f"{host}/{owner}/{repo} as {selection['account']}"
    return {
        "include": includes,
        "prSections": [{"title": title, "filters": f"repo:{owner}/{repo}"}],
        "issuesSections": [{"title": title, "filters": f"repo:{owner}/{repo}"}],
        "notificationsSections": [],
        "smartFilteringAtLaunch": False,
        "defaults": {"view": "prs"},
    }


class LaunchInterrupted(Exception):
    """Unwind the tool and temporary overlay on a terminating pane signal."""

    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(f"signal {signum}")


@contextmanager
def cleanup_signals() -> Iterator[None]:
    """Restore prior handlers after signal-driven stack unwinding cleans up."""

    def interrupt(signum: int, _frame: FrameType | None) -> None:
        raise LaunchInterrupted(signum)

    previous = {}
    try:
        for signum in (signal.SIGHUP, signal.SIGTERM):
            previous[signum] = signal.signal(signum, interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


# Mirrors rexdeck.integrations.CONTEXTS; this file runs standalone under ``python -I``.
# selected-file arrives as ``selected-file:SPEC`` and is resolved by ``resolve``.
CONTEXTS = {
    "directory": directory,
    "git-worktree": git_directory,
    "document": document,
    "github-worktree": git_directory,
    "media": media,
}


def acknowledge() -> None:
    if sys.stdin.isatty():
        try:
            input("Press Enter to close RexDeck: ")
        except (EOFError, KeyboardInterrupt):
            pass


def restore_focus(rex: str, server: str, session: str, origin: str) -> None:
    """Use API identities, not CLI label/suffix/directional target resolution."""
    environment = dict(os.environ)
    environment.pop("REX_SESSION", None)
    environment.pop("REX_BLOCK", None)
    environment.pop("REX_CLIENT", None)
    result = subprocess.run(
        [
            rex,
            "-S",
            server,
            "--autostart=false",
            "--timeout=10s",
            "api",
            "call",
            "session.focus_block",
            json.dumps({"session_id": session, "block_id": origin}),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Rex exited {result.returncode}: {result.stderr.strip()}")


def resolve(context: str, raw: str, selected: str | None = None) -> str:
    """Resolve one declared context; arbitrary cwd never selects a file or account.

    ``selected-file:SPEC`` carries the descriptor's accepted suffixes, because this
    launcher runs standalone and cannot read the catalogue.
    """
    name, separator, spec = context.partition(":")
    if name == SELECTED_FILE and separator:
        return selected_file(spec, raw, selected)
    if context not in CONTEXTS:
        raise ValueError(f"Unknown integration context: {context!r}")
    if context in ("document", "media"):
        return (
            document(raw, selected) if context == "document" else media(raw, selected)
        )
    if selected is not None:
        raise ValueError(f"The {context} context does not accept a selected file")
    return CONTEXTS[context](raw)


def workdir(target: str) -> str:
    """Run in the target directory, or a selected file's directory."""
    path = Path(target)
    return str(path if path.is_dir() else path.parent)


def run(
    context: str,
    title: str,
    label: str,
    tool: str,
    rex: str,
    server: str,
    session: str,
    origin: str,
    cwd: str,
    args: tuple[str, ...] = (),
) -> int:
    """Launch one upstream tool at its validated context, then restore origin focus."""
    exit_code = 1
    try:
        if not all((server, session, origin)):
            raise ValueError(
                "Explicit Rex server, session and origin identities are required"
            )
        if not Path(tool).is_absolute() or not Path(rex).is_absolute():
            raise ValueError("Absolute tool and Rex executable paths are required")
        # File targets are rechecked as explicit selections at launch time.
        file_context = context in ("document", "media") or context.startswith(
            SELECTED_FILE + ":"
        )
        checked = resolve(context, cwd, cwd if file_context else None)
        if context == "github-worktree":
            # The captured selection is always last; anything before it is an owned
            # view's own argv. Without one, the tool is gh-dash with its overlay.
            if not args:
                raise ValueError(
                    "GitHub requires its captured repository/account selection"
                )
            *view, raw = args
            selection = github_selection(checked, raw)
            environment = github_environment(selection, checked)
            if view:
                # Owned views read the verified host/repository from GH_HOST/GH_REPO
                # and call `gh` at GH_PATH; the account is shown, never switched.
                environment["REXDECK_GITHUB_ACCOUNT"] = selection["account"]
                # Views parse gh output; forced colour would corrupt captured JSON.
                for forced in ("GH_FORCE_TTY", "CLICOLOR_FORCE"):
                    environment.pop(forced, None)
                with cleanup_signals():
                    exit_code = subprocess.run(
                        [tool, *view], cwd=checked, env=environment, check=False
                    ).returncode
            else:
                with (
                    cleanup_signals(),
                    tempfile.TemporaryDirectory(prefix="rexdeck-github-") as temporary,
                ):
                    config = Path(temporary) / "config.yml"
                    config.write_text(
                        json.dumps(github_config(checked, selection)), encoding="utf-8"
                    )
                    exit_code = subprocess.run(
                        [tool, "--config", str(config)],
                        cwd=checked,
                        env=environment,
                        check=False,
                    ).returncode
        else:
            target = (checked,) if file_context else ()
            # Inherit the pane TTY/configuration, but never search its cwd for helpers.
            exit_code = subprocess.run(
                [tool, *args, *target],
                cwd=workdir(checked),
                env=execution_environment(),
                check=False,
            ).returncode
        if exit_code < 0:
            exit_code = 128 - exit_code
        if exit_code:
            print(f"RexDeck {title}: {label} exited {exit_code}", file=sys.stderr)
            acknowledge()
    except LaunchInterrupted as error:
        exit_code = 128 + error.signum
        print(
            f"RexDeck {title}: {label} interrupted by signal {error.signum}",
            file=sys.stderr,
        )
    except KeyboardInterrupt:
        exit_code = 130
        print(f"RexDeck {title}: {label} interrupted", file=sys.stderr)
        acknowledge()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"RexDeck {title}: cannot launch {label}: {error}", file=sys.stderr)
        acknowledge()
    try:
        if not all((server, session, origin)) or not Path(rex).is_absolute():
            raise ValueError(
                "Cannot restore focus without explicit Rex origin and executable"
            )
        restore_focus(rex, server, session, origin)
    except KeyboardInterrupt:
        print(
            f"RexDeck {title}: return focus to original pane {origin!r} interrupted; "
            "focus outcome is unknown. No replacement pane was selected.",
            file=sys.stderr,
        )
        acknowledge()
        return exit_code or 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(
            f"RexDeck {title}: cannot return focus to original pane {origin!r}: {error}. "
            "No replacement pane was selected.",
            file=sys.stderr,
        )
        acknowledge()
        return exit_code or 1
    return exit_code


def main() -> int:
    """Dispatch standalone context validation and native pane launch commands."""
    args = sys.argv[1:]
    if len(args) in (3, 4) and args[0] == "context":
        try:
            if args[1] == "github-worktree":
                target = git_directory(args[2])
                selection = github_selection(
                    target, args[3] if len(args) == 4 else None
                )
                print(
                    json.dumps(
                        {
                            "cwd": target,
                            "target": target,
                            "selection": json.dumps(selection),
                        }
                    )
                )
            else:
                target = resolve(args[1], args[2], args[3] if len(args) == 4 else None)
                print(json.dumps({"cwd": workdir(target), "target": target}))
            return 0
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"RexDeck: invalid invoking context: {error}", file=sys.stderr)
            return 1
    if len(args) >= 10 and args[0] == "run":
        context, title, label, tool, rex, server, session, origin, cwd = args[1:10]
        return run(
            context,
            title,
            label,
            tool,
            rex,
            server,
            session,
            origin,
            cwd,
            args=tuple(args[10:]),
        )
    print(
        "Use context CONTEXT RAW_PWD [SELECTED] or run CONTEXT TITLE LABEL TOOL REX "
        "SERVER SESSION ORIGIN TARGET [ARG...]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
