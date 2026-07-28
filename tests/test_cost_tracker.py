"""Tests for cost tracking and budget enforcement."""

from __future__ import annotations

from untether.cost_tracker import (
    CostAlert,
    CostBudget,
    check_run_budget,
    format_cost_alert,
    get_daily_cost,
    record_run_cost,
)


def _reset_daily():
    """Reset the global daily cost tracker."""
    import untether.cost_tracker as mod

    mod._daily_cost = ("", 0.0)


class TestRecordRunCost:
    def setup_method(self):
        _reset_daily()

    def test_records_cost(self):
        record_run_cost(0.50)
        assert get_daily_cost() == 0.50

    def test_accumulates_cost(self):
        record_run_cost(0.50)
        record_run_cost(0.30)
        assert get_daily_cost() == 0.80

    def test_resets_on_new_day(self):
        import untether.cost_tracker as mod

        mod._daily_cost = ("1999-01-01", 99.0)
        record_run_cost(0.10)
        assert get_daily_cost() == 0.10


class TestCheckRunBudget:
    def setup_method(self):
        _reset_daily()

    def test_no_budget_returns_none(self):
        budget = CostBudget()
        assert check_run_budget(1.0, budget) is None

    def test_under_per_run_budget(self):
        budget = CostBudget(max_cost_per_run=5.0, warn_at_pct=70)
        assert check_run_budget(1.0, budget) is None

    def test_warn_per_run_budget(self):
        budget = CostBudget(max_cost_per_run=5.0, warn_at_pct=70)
        alert = check_run_budget(4.0, budget)
        assert alert is not None
        assert alert.level == "warning"
        assert "$4.00" in alert.message
        assert not alert.should_cancel

    def test_exceed_per_run_budget(self):
        budget = CostBudget(max_cost_per_run=5.0)
        alert = check_run_budget(6.0, budget)
        assert alert is not None
        assert alert.level == "exceeded"
        assert "$6.00" in alert.message

    def test_exceed_per_run_with_auto_cancel(self):
        budget = CostBudget(max_cost_per_run=5.0, auto_cancel=True)
        alert = check_run_budget(6.0, budget)
        assert alert is not None
        assert alert.should_cancel

    def test_daily_budget_warning(self):
        record_run_cost(7.0)
        budget = CostBudget(max_cost_per_day=10.0, warn_at_pct=70)
        alert = check_run_budget(0.01, budget)
        assert alert is not None
        assert alert.level == "warning"

    def test_daily_budget_exceeded(self):
        record_run_cost(11.0)
        budget = CostBudget(max_cost_per_day=10.0)
        alert = check_run_budget(0.01, budget)
        assert alert is not None
        assert alert.level == "exceeded"

    def test_zero_cost_no_alert(self):
        budget = CostBudget(max_cost_per_run=5.0)
        assert check_run_budget(0.0, budget) is None


class TestFormatCostAlert:
    def test_formats_message(self):
        alert = CostAlert(level="warning", message="test message")
        assert format_cost_alert(alert) == "test message"


class TestConcurrentRecord:
    """#379: read-modify-write under concurrent callers must not lose updates."""

    def setup_method(self):
        _reset_daily()

    def test_concurrent_record_run_cost_atomic(self):
        from concurrent.futures import ThreadPoolExecutor

        n_calls = 200
        unit_cost = 0.01

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(record_run_cost, unit_cost) for _ in range(n_calls)]
            for future in futures:
                future.result()

        # If the read-modify-write were unguarded, concurrent threads racing
        # the (today, total + cost) assignment would lose updates and the
        # observed total would be < n * unit. The lock makes this impossible.
        expected = round(n_calls * unit_cost, 2)
        observed = round(get_daily_cost(), 2)
        assert observed == expected, (
            f"lost cost updates under concurrency: "
            f"expected ${expected:.2f}, got ${observed:.2f}"
        )


# ---------------------------------------------------------------------------
# #658: config.cost_visibility_gap one-shot warning (runner_bridge)
# ---------------------------------------------------------------------------


def _gap_settings(
    *,
    enabled: bool = False,
    per_run: float | None = None,
    per_day: float | None = None,
    show_api_cost: bool = False,
    show_subscription_usage: bool = True,
):
    from types import SimpleNamespace

    return SimpleNamespace(
        cost_budget=SimpleNamespace(
            enabled=enabled,
            max_cost_per_run=per_run,
            max_cost_per_day=per_day,
        ),
        footer=SimpleNamespace(
            show_api_cost=show_api_cost,
            show_subscription_usage=show_subscription_usage,
        ),
    )


def _capture_gap_warnings(monkeypatch):
    from untether import runner_bridge

    warnings: list[tuple[str, dict]] = []

    class _Logger:
        def warning(self, event, **kw):
            warnings.append((event, kw))

        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(runner_bridge, "logger", _Logger())
    monkeypatch.setattr(runner_bridge, "_cost_visibility_gap_warned", False)
    return warnings


def test_cost_visibility_gap_fires_once(monkeypatch) -> None:
    from untether.runner_bridge import _warn_cost_visibility_gap

    warnings = _capture_gap_warnings(monkeypatch)
    settings = _gap_settings()
    _warn_cost_visibility_gap(7.5, settings, False)
    _warn_cost_visibility_gap(3.0, settings, False)

    events = [w for w in warnings if w[0] == "config.cost_visibility_gap"]
    assert len(events) == 1
    fields = events[0][1]
    assert fields["total_cost_usd"] == 7.5
    assert fields["show_api_cost"] is False
    assert fields["show_subscription_usage"] is True
    assert fields["cost_budget_enabled"] is False


def test_cost_visibility_gap_silent_when_cost_displayed(monkeypatch) -> None:
    from untether.runner_bridge import _warn_cost_visibility_gap

    warnings = _capture_gap_warnings(monkeypatch)
    _warn_cost_visibility_gap(7.5, _gap_settings(show_api_cost=True), False)
    assert warnings == []


def test_cost_visibility_gap_silent_with_effective_budget(monkeypatch) -> None:
    from untether.runner_bridge import _warn_cost_visibility_gap

    warnings = _capture_gap_warnings(monkeypatch)
    _warn_cost_visibility_gap(7.5, _gap_settings(enabled=True, per_run=20.0), True)
    assert warnings == []


def test_cost_visibility_gap_fires_when_enabled_but_capless(monkeypatch) -> None:
    """[cost_budget] enabled=true with both caps None provides no protection
    — the gap warning must still fire."""
    from untether.runner_bridge import _warn_cost_visibility_gap

    warnings = _capture_gap_warnings(monkeypatch)
    _warn_cost_visibility_gap(7.5, _gap_settings(enabled=True), True)
    events = [w for w in warnings if w[0] == "config.cost_visibility_gap"]
    assert len(events) == 1
    assert events[0][1]["has_per_run_budget"] is False
    assert events[0][1]["has_per_day_budget"] is False


# ---------------------------------------------------------------------------
# #702: cost.run_outlier — a per-run spend signal that survives no [cost_budget]
# ---------------------------------------------------------------------------


def _outlier_settings(
    *,
    enabled: bool = False,
    warn_run_above_usd: float | None = None,
    notify_run_outlier: bool = True,
    show_api_cost: bool = False,
    show_subscription_usage: bool = True,
):
    from types import SimpleNamespace

    return SimpleNamespace(
        cost_budget=SimpleNamespace(
            enabled=enabled,
            warn_run_above_usd=warn_run_above_usd,
            notify_run_outlier=notify_run_outlier,
        ),
        footer=SimpleNamespace(
            show_api_cost=show_api_cost,
            show_subscription_usage=show_subscription_usage,
        ),
    )


def _capture_outlier(monkeypatch, settings):
    """Capture warnings and pin the settings _check_run_cost_outlier loads."""
    from untether import runner_bridge
    from untether import settings as settings_mod

    warnings: list[tuple[str, dict]] = []

    class _Logger:
        def warning(self, event, **kw):
            warnings.append((event, kw))

        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(runner_bridge, "logger", _Logger())
    monkeypatch.setattr(
        settings_mod, "load_settings_if_exists", lambda: (settings, None)
    )
    return warnings


def test_run_outlier_fires_without_any_budget(monkeypatch) -> None:
    """The #702 core claim: `enabled=False` (the fleet default) must no longer
    mean that no amount of spend can produce a signal."""
    from untether.runner_bridge import _check_run_cost_outlier

    warnings = _capture_outlier(monkeypatch, _outlier_settings())
    text = _check_run_cost_outlier({"total_cost_usd": 22.06})

    events = [w for w in warnings if w[0] == "cost.run_outlier"]
    assert len(events) == 1
    fields = events[0][1]
    assert fields["total_cost_usd"] == 22.06
    assert fields["threshold_usd"] == 20.0
    assert fields["budget_configured"] is False
    assert fields["show_api_cost"] is False
    assert text is not None
    assert "$22.06" in text


def test_run_outlier_silent_below_threshold(monkeypatch) -> None:
    from untether.runner_bridge import _check_run_cost_outlier

    warnings = _capture_outlier(monkeypatch, _outlier_settings())
    assert _check_run_cost_outlier({"total_cost_usd": 14.44}) is None
    assert warnings == []


def test_run_outlier_honours_custom_threshold(monkeypatch) -> None:
    from untether.runner_bridge import _check_run_cost_outlier

    warnings = _capture_outlier(monkeypatch, _outlier_settings(warn_run_above_usd=5.0))
    assert _check_run_cost_outlier({"total_cost_usd": 9.30}) is not None
    assert [w for w in warnings if w[0] == "cost.run_outlier"]


def test_run_outlier_threshold_zero_disables(monkeypatch) -> None:
    from untether.runner_bridge import _check_run_cost_outlier

    warnings = _capture_outlier(monkeypatch, _outlier_settings(warn_run_above_usd=0.0))
    assert _check_run_cost_outlier({"total_cost_usd": 999.0}) is None
    assert warnings == []


def test_run_outlier_notice_opt_out_keeps_the_log(monkeypatch) -> None:
    """`notify_run_outlier = false` silences the chat line only — the log
    event is what the issue watcher ingests and must survive."""
    from untether.runner_bridge import _check_run_cost_outlier

    warnings = _capture_outlier(
        monkeypatch, _outlier_settings(notify_run_outlier=False)
    )
    assert _check_run_cost_outlier({"total_cost_usd": 22.06}) is None
    assert len([w for w in warnings if w[0] == "cost.run_outlier"]) == 1


def test_run_outlier_fires_with_a_budget_configured(monkeypatch) -> None:
    """A $22 run under a $100 cap trips no budget alert but is still spend
    worth reporting."""
    from untether.runner_bridge import _check_run_cost_outlier

    warnings = _capture_outlier(monkeypatch, _outlier_settings(enabled=True))
    assert _check_run_cost_outlier({"total_cost_usd": 22.06}) is not None
    events = [w for w in warnings if w[0] == "cost.run_outlier"]
    assert events[0][1]["budget_configured"] is True


def test_run_outlier_ignores_empty_and_zero_usage(monkeypatch) -> None:
    from untether.runner_bridge import _check_run_cost_outlier

    warnings = _capture_outlier(monkeypatch, _outlier_settings())
    assert _check_run_cost_outlier(None) is None
    assert _check_run_cost_outlier({}) is None
    assert _check_run_cost_outlier({"total_cost_usd": 0.0}) is None
    assert warnings == []


def test_run_outlier_fails_open_on_settings_error(monkeypatch) -> None:
    from untether import runner_bridge
    from untether import settings as settings_mod
    from untether.runner_bridge import _check_run_cost_outlier

    warnings: list[tuple[str, dict]] = []

    class _Logger:
        def warning(self, event, **kw):
            warnings.append((event, kw))

        def __getattr__(self, name):
            return lambda *a, **k: None

    def _boom():
        raise RuntimeError("toml exploded")

    monkeypatch.setattr(runner_bridge, "logger", _Logger())
    monkeypatch.setattr(settings_mod, "load_settings_if_exists", _boom)

    assert _check_run_cost_outlier({"total_cost_usd": 99.0}) is None
    assert [w for w in warnings if w[0] == "cost.run_outlier_check_failed"]
