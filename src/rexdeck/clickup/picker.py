"""Guided ClickUp selection: the token names the account; the user picks the rest.

Every option comes from the authenticated API, so nothing is typed from memory,
and every choice is shown before it is saved. A workspace or space with exactly
one option is selected without asking, but still shown.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Protocol

from rexdeck.clickup.engine import client as backend
from rexdeck.clickup.engine import credentials, diagnostics
from rexdeck.clickup.frontend import (
    ClickUpError,
    Selection,
    engine_error,
    obj,
    rows,
    save_selection,
    text,
)

Ask = Callable[[str], str]
Say = Callable[[str], None]


class Reader(Protocol):
    """The read-only slice of the engine client the guided selection uses."""

    def get(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> Mapping[str, object]: ...


class Cancelled(Exception):
    """The user left the guided selection; nothing was saved."""


def _choose(
    say: Say,
    ask: Ask,
    kind: str,
    options: Sequence[tuple[str, str]],
    query: str = "",
) -> tuple[str, str]:
    """Return one `(id, label)`; a number picks, other text filters, `q` cancels.

    A `query` narrows the first menu; a single match is chosen and shown.
    """
    if not options:
        raise ClickUpError("empty", f"No {kind}s are available to this account")
    shown = list(options)
    if query:
        matches = [item for item in options if query.casefold() in item[1].casefold()]
        if len(matches) == 1:
            say(f"{kind.capitalize()}: {matches[0][1]} (matches {query!r})")
            return matches[0]
        if matches:
            shown = matches
        else:
            # Never take the only option for a query it does not match; ask.
            say(f"No {kind} matches {query!r}; showing all.")
    elif len(options) == 1:
        say(f"{kind.capitalize()}: {options[0][1]} (the only one available)")
        return options[0]
    while True:
        say(f"\nChoose a {kind}:")
        for number, (_, label) in enumerate(shown, 1):
            say(f"  {number:>3}. {label}")
        answer = ask(f"{kind} number, text to filter, or q to cancel: ").strip()
        if answer.casefold() in {"q", "quit"}:
            raise Cancelled
        if answer.isdecimal() and 1 <= int(answer) <= len(shown):
            return shown[int(answer) - 1]
        if not answer:
            shown = list(options)
            continue
        matches = [item for item in options if answer.casefold() in item[1].casefold()]
        if matches:
            shown = matches
        else:
            say(f"No {kind} matches {answer!r}; showing all.")
            shown = list(options)


def _lists(client: Reader, space: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    unarchived = {"archived": "false"}
    for folder in rows(client.get(f"/space/{space}/folder", unarchived).get("folders")):
        folder_name = text(folder.get("name"))
        for entry in rows(folder.get("lists") or []):
            found.append(
                (text(entry.get("id")), f"{folder_name} / {text(entry.get('name'))}")
            )
    for entry in rows(client.get(f"/space/{space}/list", unarchived).get("lists")):
        found.append((text(entry.get("id")), text(entry.get("name"))))
    return found


def _interruptible(ask: Ask) -> Ask:
    """End-of-input or Ctrl-C at a prompt cancels instead of raising a traceback."""

    def asking(prompt: str) -> str:
        try:
            return ask(prompt)
        except (EOFError, KeyboardInterrupt):
            raise Cancelled from None

    return asking


def _named(items: object) -> list[tuple[str, str]]:
    return [(text(item.get("id")), text(item.get("name"))) for item in rows(items)]


@contextmanager
def _reader(credential_env: str, client: Reader | None) -> Iterator[Reader]:
    """Authenticated reads; engine errors and Ctrl-C mid-request map to the picker's."""
    credentials.validate_env_name(credential_env)
    token = os.environ.get(credential_env, "").strip()
    if client is None and not token:
        raise ClickUpError(
            "missing-credential",
            f"{credential_env} is not set in this terminal's environment. Run "
            f"`export {credential_env}=<token>` (add it to your shell profile to "
            "keep it), then try again.",
        )
    owned: backend.Client | None = None
    session: Reader
    if client is None:
        session = owned = backend.Client(token)
    else:
        session = client
    try:
        yield session
    except diagnostics.EngineError as error:
        raise engine_error(error) from None
    except KeyboardInterrupt:
        # Ctrl-C while a request is in flight cancels like Ctrl-C at a prompt.
        raise Cancelled from None
    finally:
        if owned is not None:
            owned.close()


def _space_and_list(
    say: Say, ask: Ask, session: Reader, team: str, query: str = ""
) -> tuple[str, str, str, str]:
    """Pick a space of one workspace, then one of its lists; empty spaces drop out."""
    spaces = _named(
        session.get(f"/team/{team}/space", {"archived": "false"}).get("spaces")
    )
    while True:
        space, space_name = _choose(say, ask, "space", spaces, query)
        query = ""
        lists = _lists(session, space)
        if lists:
            break
        spaces = [item for item in spaces if item[0] != space]
        if not spaces:
            raise ClickUpError("empty", "No space in this workspace has any lists")
        say(f"Space {space_name} has no lists; choose another space.")
    listing, list_name = _choose(say, ask, "list", lists)
    return space, space_name, listing, list_name


def guided(
    say: Say = print,
    ask: Ask = input,
    credential_env: str = credentials.TOKEN_ENV,
    client: Reader | None = None,
) -> Selection:
    """Walk account → workspace → space → list, confirm, save and return it."""
    ask = _interruptible(ask)
    with _reader(credential_env, client) as session:
        user = obj(session.get("/user").get("user"))
        account = text(user.get("id"))
        say(f"ClickUp account: {text(user.get('username') or account)} ({account})")
        team, team_name = _choose(
            say, ask, "workspace", _named(session.get("/team").get("teams"))
        )
        space, space_name, listing, list_name = _space_and_list(say, ask, session, team)

    selection = Selection(account, team, listing, credential_env)
    say(
        "\nSelected:\n"
        f"  workspace  {team_name} ({team})\n"
        f"  space      {space_name} ({space})\n"
        f"  list       {list_name} ({listing})"
    )
    if ask("Save this selection? [Y/n] ").strip().casefold() not in {"", "y", "yes"}:
        raise Cancelled
    save_selection(selection)
    say("Saved. Change it later with: rexdeck clickup select")
    return selection


def switch_space(
    selection: Selection,
    query: str = "",
    say: Say = print,
    ask: Ask = input,
    client: Reader | None = None,
) -> Selection:
    """Track a list in another space of the same account/workspace, and save it.

    The menu choice is the confirmation; `query` narrows the space menu and a
    single match is taken directly. Cancelling keeps the current list.
    """
    ask = _interruptible(ask)
    with _reader(selection.credential_env, client) as session:
        # The token must still name the selection's account before it is rewritten.
        if text(obj(session.get("/user").get("user")).get("id")) != selection.account:
            raise ClickUpError(
                "account-mismatch",
                "Credential account differs from the pane's selection",
            )
        _, space_name, listing, list_name = _space_and_list(
            say, ask, session, selection.team, query
        )
    chosen = replace(selection, list=listing)
    save_selection(chosen)
    say(f"Now tracking {space_name} / {list_name}; saved for next time.")
    return chosen
