"""Guided ClickUp selection: choices come from the API and nothing saves unconfirmed."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from rexdeck.clickup import frontend as cu
from rexdeck.clickup import picker
from rexdeck.clickup.engine import diagnostics


class FakeClient:
    """Read-only hierarchy; any other path is a test failure."""

    def __init__(self, routes: Mapping[str, object]) -> None:
        self.routes = routes
        self.paths: list[str] = []

    def get(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> Mapping[str, object]:
        self.paths.append(path)
        if path.startswith(("/team/", "/space/")):
            assert params == {"archived": "false"}
        value = self.routes[path]
        assert isinstance(value, dict)
        return value


def hierarchy(**overrides: object) -> dict[str, object]:
    routes: dict[str, object] = {
        "/user": {"user": {"id": 42, "username": "owner"}},
        "/team": {"teams": [{"id": "900", "name": "Only workspace"}]},
        "/team/900/space": {
            "spaces": [
                {"id": "10", "name": "Engineering"},
                {"id": "20", "name": "Sales"},
            ]
        },
        "/space/10/folder": {
            "folders": [
                {
                    "id": "f1",
                    "name": "Sprints",
                    "lists": [
                        {"id": "101", "name": "Sprint 7"},
                        {"id": "102", "name": "Sprint 8"},
                    ],
                }
            ]
        },
        "/space/10/list": {"lists": [{"id": "103", "name": "Backlog"}]},
        "/space/20/folder": {"folders": []},
        "/space/20/list": {"lists": []},
    }
    routes.update(overrides)
    return routes


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path / "rexdeck/clickup.json"


def scripted(*answers: str) -> tuple[list[str], picker.Ask]:
    queue = list(answers)
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return queue.pop(0)

    return prompts, ask


def test_choices_come_from_the_account_and_the_confirmed_one_is_saved(
    config: Path,
) -> None:
    said: list[str] = []
    client = FakeClient(hierarchy())
    # space 1, filter lists by "8", pick the filtered first, confirm
    _, ask = scripted("1", "8", "1", "")

    chosen = picker.guided(said.append, ask, client=client)

    assert chosen == cu.Selection("42", "900", "102", "CLICKUP_API_TOKEN")
    assert json.loads(config.read_text()) == {
        "account": "42",
        "team": "900",
        "list": "102",
        "credential_env": "CLICKUP_API_TOKEN",
    }
    transcript = "\n".join(said)
    assert "Workspace: Only workspace (the only one available)" in transcript
    assert "Sprints / Sprint 7" in transcript and "Backlog" in transcript
    assert "list       Sprints / Sprint 8 (102)" in transcript


def test_a_space_without_lists_is_dropped_and_asked_again(config: Path) -> None:
    said: list[str] = []
    _, ask = scripted("2", "3", "y")

    chosen = picker.guided(said.append, ask, client=FakeClient(hierarchy()))

    assert chosen.list == "103"
    assert any("Sales has no lists" in line for line in said)
    # Engineering was then the only space left, so it was not asked for again.
    assert any("Space: Engineering (the only one available)" in line for line in said)


@pytest.mark.parametrize("answers", [("1", "1", "n"), ("q",), ("1", "q"), ("1",)])
def test_declining_quitting_or_ending_input_saves_nothing(
    config: Path, answers: tuple[str, ...]
) -> None:
    queue = list(answers)

    def ask(prompt: str) -> str:
        if not queue:
            raise EOFError  # Ctrl-D at the next prompt
        return queue.pop(0)

    with pytest.raises(picker.Cancelled):
        picker.guided(lambda _: None, ask, client=FakeClient(hierarchy()))

    assert not config.exists()


def test_a_missing_token_is_explained_before_any_request(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLICKUP_API_TOKEN", raising=False)

    with pytest.raises(cu.ClickUpError) as raised:
        picker.guided(lambda _: None, scripted()[1])

    assert raised.value.state == "missing-credential"
    assert "CLICKUP_API_TOKEN" in str(raised.value)
    assert not config.exists()


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        (["select", "--team", "900"], "Pass all of --account, --team and --list"),
        (["select"], "Interactive selection needs a terminal"),
    ],
)
def test_select_refuses_partial_ids_and_non_interactive_guidance(
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    error: str,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO())

    assert cu.configure(argv) == 1

    assert error in json.loads(capsys.readouterr().err)["error"]
    assert not config.exists()


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_first_pane_run_offers_the_guided_choice_only_without_a_selection(
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    offered: list[bool] = []

    def cancelled(*args: object, **kwargs: object) -> cu.Selection:
        offered.append(True)
        raise picker.Cancelled

    monkeypatch.setattr(picker, "guided", cancelled)
    monkeypatch.setattr(sys, "stdin", _Terminal())

    assert cu.main() == 0
    assert offered == [True]
    assert "No ClickUp selection yet" in capsys.readouterr().out

    config.parent.mkdir(parents=True)
    config.write_text("{broken")
    assert cu.main() == 1
    assert offered == [True]
    assert "INVALID-SELECTION" in capsys.readouterr().out


class _Failing(FakeClient):
    def __init__(self, failure: BaseException) -> None:
        super().__init__(hierarchy())
        self.failure = failure

    def get(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> Mapping[str, object]:
        raise self.failure


def test_an_api_refusal_becomes_a_pane_state_and_nothing_is_saved(
    config: Path,
) -> None:
    refused = diagnostics.EngineError(
        diagnostics.Diagnostic(
            code="auth/rejected",
            severity=diagnostics.SEVERITY_ERROR,
            message="ClickUp rejected the API token",
            subject="CLICKUP_API_TOKEN",
            evidence={"status": 401},
            supported_fixes=("export CLICKUP_API_TOKEN=<token>",),
        )
    )

    with pytest.raises(cu.ClickUpError) as raised:
        picker.guided(lambda _: None, scripted()[1], client=_Failing(refused))

    assert raised.value.state == "authentication"
    assert not config.exists()


def test_ctrl_c_during_a_request_cancels_instead_of_a_traceback(config: Path) -> None:
    with pytest.raises(picker.Cancelled):
        picker.guided(
            lambda _: None, scripted()[1], client=_Failing(KeyboardInterrupt())
        )
    assert not config.exists()


def test_a_workspace_whose_spaces_have_no_lists_says_so(config: Path) -> None:
    empty = hierarchy(
        **{"/team/900/space": {"spaces": [{"id": "20", "name": "Sales"}]}}
    )

    with pytest.raises(
        cu.ClickUpError, match="No space in this workspace has any lists"
    ):
        picker.guided(lambda _: None, scripted()[1], client=FakeClient(empty))


@pytest.mark.parametrize(("outcome", "code"), [("saved", 0), ("cancelled", 1)])
def test_guided_select_command_reports_its_outcome(
    config: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, code: int
) -> None:
    def guided(*args: object, **kwargs: object) -> cu.Selection:
        assert kwargs == {"credential_env": "CLICKUP_OTHER"}
        if outcome == "cancelled":
            raise picker.Cancelled
        return cu.Selection("42", "900", "102", "CLICKUP_OTHER")

    monkeypatch.setattr(picker, "guided", guided)
    monkeypatch.setattr(sys, "stdin", _Terminal())

    assert cu.configure(["select", "--credential-env", "CLICKUP_OTHER"]) == code


def test_first_pane_run_opens_the_list_it_just_chose(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = cu.Selection("42", "900", "102", "CLICKUP_API_TOKEN")
    opened: list[cu.Selection] = []

    class Opened(Exception):
        pass

    def controller(selection: cu.Selection) -> object:
        opened.append(selection)
        raise Opened

    monkeypatch.setattr(picker, "guided", lambda *a, **k: chosen)
    monkeypatch.setattr(cu, "Controller", controller)
    monkeypatch.setattr(sys, "stdin", _Terminal())

    with pytest.raises(Opened):
        cu.main()
    assert opened == [chosen]


def test_a_dangling_selection_symlink_is_not_treated_as_first_run(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(picker, "guided", lambda *a, **k: pytest.fail("offered"))
    monkeypatch.setattr(sys, "stdin", _Terminal())
    config.parent.mkdir(parents=True)
    config.symlink_to(config.parent / "missing.json")

    assert cu.main() == 1
