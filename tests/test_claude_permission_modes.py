"""Claude permission-mode semantics (#741) and allowlist validation (#742).

Reference: Claude Code CLI 2.1.228, verified on lba-1 2026-08-12 by
``claude --help`` plus a per-value ``--permission-mode`` spawn probe.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from untether.runners.run_options import (
    CLAUDE_CLI_PERMISSION_MODES,
    CLAUDE_PLAN_AUTO_MODE,
    LEGACY_CLAUDE_PLAN_AUTO_MODE,
    VALID_PERMISSION_MODES_BY_ENGINE,
    claude_cli_permission_mode,
    is_claude_plan_auto,
)

# ---------------------------------------------------------------------------
# #742 — the canonical allowlist
# ---------------------------------------------------------------------------


def test_cli_mode_set_matches_cli_2_1_228() -> None:
    """The set derived from CLI 2.1.228, including the `manual` alias.

    ``claude --help`` lists ``manual`` in place of ``default``; both are
    accepted by the binary (probed 2026-08-12).
    """
    expected = frozenset(
        {
            "default",
            "manual",
            "plan",
            "auto",
            "acceptEdits",
            "dontAsk",
            "bypassPermissions",
        }
    )
    assert expected == CLAUDE_CLI_PERMISSION_MODES


def test_default_is_still_accepted() -> None:
    """#742 claimed `default` was CLI-rejected; the probe disproved it."""
    assert "default" in CLAUDE_CLI_PERMISSION_MODES


def test_manual_and_dont_ask_are_reachable() -> None:
    """Both were legal upstream but rejected by our parse-time validator."""
    allowed = VALID_PERMISSION_MODES_BY_ENGINE["claude"]
    assert "manual" in allowed
    assert "dontAsk" in allowed


def test_allowlist_is_cli_set_plus_untether_sugar() -> None:
    allowed = VALID_PERMISSION_MODES_BY_ENGINE["claude"]
    assert allowed == CLAUDE_CLI_PERMISSION_MODES | {CLAUDE_PLAN_AUTO_MODE}


# ---------------------------------------------------------------------------
# #741 — `auto` reaches the CLI unmodified; the sugar is renamed
# ---------------------------------------------------------------------------


def test_auto_passes_through_verbatim() -> None:
    """The load-bearing fix: `auto` must no longer be remapped to `plan`."""
    assert claude_cli_permission_mode("auto") == "auto"


def test_plan_auto_sugar_maps_to_cli_plan() -> None:
    assert claude_cli_permission_mode(CLAUDE_PLAN_AUTO_MODE) == "plan"


@pytest.mark.parametrize(
    "mode",
    ["default", "manual", "plan", "acceptEdits", "dontAsk", "bypassPermissions"],
)
def test_genuine_modes_pass_through(mode: str) -> None:
    assert claude_cli_permission_mode(mode) == mode


def test_none_stays_none() -> None:
    assert claude_cli_permission_mode(None) is None


def test_is_claude_plan_auto_only_matches_the_sugar() -> None:
    assert is_claude_plan_auto(CLAUDE_PLAN_AUTO_MODE) is True
    # The whole point of #741: upstream `auto` must NOT arm the plan-gate
    # rubber stamp.
    assert is_claude_plan_auto("auto") is False
    assert is_claude_plan_auto("plan") is False
    assert is_claude_plan_auto(None) is False


def test_legacy_spelling_constant_is_auto() -> None:
    """Documents what pre-0.35.5rc8 chat prefs hold."""
    assert LEGACY_CLAUDE_PLAN_AUTO_MODE == "auto"
    assert CLAUDE_PLAN_AUTO_MODE != LEGACY_CLAUDE_PLAN_AUTO_MODE


# ---------------------------------------------------------------------------
# Drift detection — fails when the installed CLI diverges from the constant
# ---------------------------------------------------------------------------


def _cli_declared_modes() -> set[str] | None:
    """Parse the choices commander prints when given an invalid value."""
    claude = shutil.which("claude")
    if claude is None:
        return None
    try:
        proc = subprocess.run(
            [claude, "--permission-mode", "__untether_drift_probe__", "-p", "x"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    blob = f"{proc.stderr}\n{proc.stdout}"
    marker = "Allowed choices are "
    if marker not in blob:
        return None
    tail = blob.split(marker, 1)[1]
    tail = tail.split(".", 1)[0]
    return {item.strip() for item in tail.split(",") if item.strip()}


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed")
def test_no_drift_against_installed_cli() -> None:
    """Catch allowlist rot automatically instead of by periodic audit (#742).

    ``default`` is deliberately excluded from the comparison: the CLI accepts
    it but omits it from the choices list, because ``manual`` is its label.
    """
    declared = _cli_declared_modes()
    if declared is None:
        pytest.skip("could not parse permission-mode choices from the installed CLI")
    ours = set(CLAUDE_CLI_PERMISSION_MODES) - {"default"}
    assert declared == ours, (
        f"installed CLI advertises {sorted(declared)} but "
        f"CLAUDE_CLI_PERMISSION_MODES holds {sorted(ours)} (excluding 'default'); "
        "re-derive the constant and update the version note in run_options.py"
    )


# ---------------------------------------------------------------------------
# #742 — cron and engine-config paths must accept/reject identically
# ---------------------------------------------------------------------------


def _engine_config_accepts(mode: str, tmp_path) -> bool:
    from untether.config import ConfigError
    from untether.runners.claude import _validate_permission_mode

    try:
        _validate_permission_mode(mode, tmp_path / "untether.toml")
    except ConfigError:
        return False
    return True


def _cron_accepts(mode: str) -> bool:
    from pydantic import ValidationError

    from untether.triggers.settings import CronConfig

    try:
        CronConfig(
            id="c1",
            schedule="0 9 * * *",
            prompt="hi",
            engine="claude",
            permission_mode=mode,
        )
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize("mode", sorted(VALID_PERMISSION_MODES_BY_ENGINE["claude"]))
def test_cron_and_engine_config_accept_every_allowed_mode(mode, tmp_path) -> None:
    assert _cron_accepts(mode) is True
    assert _engine_config_accepts(mode, tmp_path) is True


@pytest.mark.parametrize("mode", ["palan", "Plan", "", "  ", "yolo"])
def test_cron_and_engine_config_reject_identically(mode, tmp_path) -> None:
    """Parity: the same value must not pass in one table and fail in the other.

    Before #742, ``[engines.claude] permission_mode = "palan"`` sailed through
    config load and died at subprocess spawn instead.
    """
    assert _cron_accepts(mode) is False
    assert _engine_config_accepts(mode, tmp_path) is False


def test_engine_config_none_is_allowed(tmp_path) -> None:
    from untether.runners.claude import _validate_permission_mode

    assert _validate_permission_mode(None, tmp_path / "untether.toml") is None


def test_engine_config_rejects_non_string(tmp_path) -> None:
    from untether.config import ConfigError
    from untether.runners.claude import _validate_permission_mode

    with pytest.raises(ConfigError):
        _validate_permission_mode(123, tmp_path / "untether.toml")


def test_engine_config_strips_whitespace(tmp_path) -> None:
    from untether.runners.claude import _validate_permission_mode

    assert _validate_permission_mode("  plan  ", tmp_path / "untether.toml") == "plan"


def test_engine_config_error_names_the_key_and_path(tmp_path) -> None:
    from untether.config import ConfigError
    from untether.runners.claude import _validate_permission_mode

    path = tmp_path / "untether.toml"
    with pytest.raises(ConfigError) as excinfo:
        _validate_permission_mode("palan", path)
    message = str(excinfo.value)
    assert "claude.permission_mode" in message
    assert "palan" in message
    assert str(path) in message


# ---------------------------------------------------------------------------
# #741 — migration of the legacy `auto` spelling
# ---------------------------------------------------------------------------


def test_toml_auto_warns_once_and_keeps_the_new_meaning(tmp_path, monkeypatch) -> None:
    """Hand-authored TOML is not rewritten — the operator gets a WARN."""
    import untether.runners.claude as claude_mod

    monkeypatch.setattr(claude_mod, "_LEGACY_AUTO_WARNED", False)
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        claude_mod.logger,
        "warning",
        lambda event, **kw: warnings.append((event, kw)),
    )

    path = tmp_path / "untether.toml"
    assert claude_mod._validate_permission_mode("auto", path) == "auto"
    assert claude_mod._validate_permission_mode("auto", path) == "auto"

    # One-shot per process, not per run.
    assert len(warnings) == 1
    event, kwargs = warnings[0]
    assert event == "claude.permission_mode.auto_semantics_changed"
    assert CLAUDE_PLAN_AUTO_MODE in kwargs["note"]


def test_chat_prefs_auto_migrates_to_plan_auto() -> None:
    """Stored prefs were written by our own UI, so `auto` meant the sugar."""
    from untether.telegram.engine_overrides import (
        EngineOverrides,
        migrate_legacy_overrides,
        migrate_legacy_permission_mode,
    )

    assert migrate_legacy_permission_mode("claude", "auto") == CLAUDE_PLAN_AUTO_MODE

    migrated = migrate_legacy_overrides(
        "claude", EngineOverrides(permission_mode="auto")
    )
    assert migrated is not None
    assert migrated.permission_mode == CLAUDE_PLAN_AUTO_MODE


def test_migration_preserves_other_override_fields() -> None:
    from untether.telegram.engine_overrides import (
        EngineOverrides,
        migrate_legacy_overrides,
    )

    migrated = migrate_legacy_overrides(
        "claude",
        EngineOverrides(permission_mode="auto", model="opus", diff_preview=True),
    )
    assert migrated is not None
    assert migrated.model == "opus"
    assert migrated.diff_preview is True


def test_migration_is_claude_only() -> None:
    """Codex uses `auto` as a legitimate, differently-meaning value."""
    from untether.telegram.engine_overrides import migrate_legacy_permission_mode

    assert migrate_legacy_permission_mode("codex", "auto") == "auto"
    assert migrate_legacy_permission_mode("gemini", "auto") == "auto"


@pytest.mark.parametrize("mode", [None, "plan", "acceptEdits", CLAUDE_PLAN_AUTO_MODE])
def test_migration_leaves_other_modes_alone(mode) -> None:
    from untether.telegram.engine_overrides import migrate_legacy_permission_mode

    assert migrate_legacy_permission_mode("claude", mode) == mode


def test_migration_handles_none_overrides() -> None:
    from untether.telegram.engine_overrides import migrate_legacy_overrides

    assert migrate_legacy_overrides("claude", None) is None
