"""Command backend for toggling Claude Code plan mode via /planmode."""

from __future__ import annotations

from ...commands import CommandBackend, CommandContext, CommandResult
from ...logging import get_logger
from ...runners.run_options import CLAUDE_PLAN_AUTO_MODE, claude_cli_permission_mode

logger = get_logger(__name__)

PLANMODE_USAGE = (
    "usage: `/planmode`, `/planmode on`, `/planmode plan-auto`,"
    " `/planmode auto`, `/planmode off`, `/planmode show`, or"
    " `/planmode clear`"
)

# #741 `plan-auto` is Untether's own sugar (CLI plan mode + auto-approved
# ExitPlanMode).  `auto` is now the CLI's native classifier-gated mode, which
# this command shadowed until 0.35.5rc8.
PERMISSION_MODES = {
    "on": "plan",
    "plan-auto": CLAUDE_PLAN_AUTO_MODE,
    "auto": "auto",
    "off": "acceptEdits",
}

_MODE_LABELS = {
    "plan": "<b>on</b> (plan mode)",
    CLAUDE_PLAN_AUTO_MODE: ("<b>plan-auto</b> (plan mode, auto-approve ExitPlanMode)"),
    "auto": "<b>auto</b> (Claude Code auto mode — classifier-gated)",
}

# Modes that mean "planning is active" for the bare `/planmode` toggle.
_PLANNING_MODES = ("plan", CLAUDE_PLAN_AUTO_MODE)

# Engines that support the /planmode command (Claude-style permission modes).
# Codex and Gemini have approval policies but use different semantics —
# they should use /config → Approval policy instead.
_PLANMODE_ENGINES = frozenset({"claude"})


class PlanModeCommand:
    """Command backend for toggling Claude Code permission mode."""

    id = "planmode"
    description = "Toggle Claude Code plan mode on/auto/off"

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
        from ..chat_prefs import ChatPrefsStore, resolve_prefs_path
        from ..engine_overrides import EngineOverrides
        from ._resolve_engine import resolve_effective_engine

        config_path = ctx.config_path
        if config_path is None:
            return CommandResult(
                text="plan mode overrides unavailable (no config path).",
                notify=True,
            )

        current_engine = await resolve_effective_engine(ctx)
        if current_engine not in _PLANMODE_ENGINES:
            hint = ""
            if current_engine in {"codex", "gemini"}:
                hint = " Use /config → Approval policy instead."
            return CommandResult(
                text=(
                    f"Plan mode is only available for Claude Code."
                    f" Current engine: <b>{current_engine}</b>.{hint}"
                ),
                notify=True,
                parse_mode="HTML",
            )

        chat_prefs = ChatPrefsStore(resolve_prefs_path(config_path))
        chat_id = ctx.message.channel_id
        engine = current_engine
        args = ctx.args_text.strip().lower()

        if args == "show":
            current = await chat_prefs.get_engine_override(chat_id, engine)
            mode = current.permission_mode if current else None
            if mode in _MODE_LABELS:
                label = _MODE_LABELS[mode]
            elif mode is not None:
                label = f"<b>off</b> ({mode})"
            else:
                label = "default (uses engine config)"
            return CommandResult(
                text=f"plan mode: {label}", notify=True, parse_mode="HTML"
            )

        if args == "":
            # Toggle: if currently plan/auto mode, turn off; otherwise turn on
            current = await chat_prefs.get_engine_override(chat_id, engine)
            current_mode = current.permission_mode if current else None
            args = "off" if current_mode in _PLANNING_MODES else "on"

        if args in PERMISSION_MODES:
            mode = PERMISSION_MODES[args]
            current = await chat_prefs.get_engine_override(chat_id, engine)
            updated = EngineOverrides(
                model=current.model if current else None,
                reasoning=current.reasoning if current else None,
                permission_mode=mode,
                ask_questions=current.ask_questions if current else None,
                diff_preview=current.diff_preview if current else None,
                show_api_cost=current.show_api_cost if current else None,
                show_subscription_usage=current.show_subscription_usage
                if current
                else None,
                show_resume_line=current.show_resume_line if current else None,
                budget_enabled=current.budget_enabled if current else None,
                budget_auto_cancel=current.budget_auto_cancel if current else None,
            )
            await chat_prefs.set_engine_override(chat_id, engine, updated)
            cli_mode = claude_cli_permission_mode(mode)
            logger.info(
                "planmode.set",
                chat_id=chat_id,
                mode=args,
                cli_mode=cli_mode,
                command="planmode",
            )
            return CommandResult(
                text=(
                    f"plan mode <b>{args}</b> for this chat.\n"
                    f"new sessions will use <code>--permission-mode {cli_mode}</code>."
                ),
                notify=True,
                parse_mode="HTML",
            )

        if args == "clear":
            current = await chat_prefs.get_engine_override(chat_id, engine)
            updated = EngineOverrides(
                model=current.model if current else None,
                reasoning=current.reasoning if current else None,
                permission_mode=None,
                ask_questions=current.ask_questions if current else None,
                diff_preview=current.diff_preview if current else None,
                show_api_cost=current.show_api_cost if current else None,
                show_subscription_usage=current.show_subscription_usage
                if current
                else None,
                show_resume_line=current.show_resume_line if current else None,
                budget_enabled=current.budget_enabled if current else None,
                budget_auto_cancel=current.budget_auto_cancel if current else None,
            )
            await chat_prefs.set_engine_override(chat_id, engine, updated)
            logger.info("planmode.cleared", chat_id=chat_id, command="planmode")
            return CommandResult(
                text="plan mode <b>override cleared</b> (using engine config default).",
                notify=True,
                parse_mode="HTML",
            )

        return CommandResult(text=PLANMODE_USAGE, notify=True)


BACKEND: CommandBackend = PlanModeCommand()
