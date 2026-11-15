from datetime import datetime, timezone

import pytest

from starsort.web.jobs import STALE_SECONDS
from starsort.web.limits import WEEK_SECONDS, Limits, SpendCounter, UsageStore, cost_micro_usd, month_key

NOW = 1_800_000_000


@pytest.fixture
def usage(usage_table) -> UsageStore:
    return UsageStore(usage_table)


def test_first_run_is_allowed(usage):
    assert usage.try_start("1", now=NOW).allowed


def test_second_run_while_the_first_is_running_is_blocked(usage):
    usage.try_start("1", now=NOW)

    check = usage.try_start("1", now=NOW + 60)

    assert (check.allowed, check.reason) == (False, "running")


def test_second_run_in_the_same_week_is_blocked_with_the_next_date(usage):
    usage.try_start("1", now=NOW)
    usage.finish("1")

    check = usage.try_start("1", now=NOW + 3600)

    assert (check.allowed, check.reason, check.retry_at) == (False, "weekly", NOW + WEEK_SECONDS)


def test_a_run_is_allowed_again_after_a_week(usage):
    usage.try_start("1", now=NOW)
    usage.finish("1")

    assert usage.try_start("1", now=NOW + WEEK_SECONDS).allowed


def test_a_failed_run_gives_the_week_back(usage):
    usage.try_start("1", now=NOW)
    usage.finish("1", refund=True)

    assert usage.try_start("1", now=NOW + 60).allowed


def test_a_job_that_died_stops_counting_as_running(usage):
    usage.try_start("1", weekly=False, now=NOW)

    assert usage.try_start("1", weekly=False, now=NOW + STALE_SECONDS + 1).allowed


def test_unlimited_users_skip_the_weekly_limit_but_not_the_running_check(usage):
    usage.try_start("1", weekly=False, now=NOW)
    assert usage.try_start("1", weekly=False, now=NOW + 60).reason == "running"

    usage.finish("1")
    assert usage.try_start("1", weekly=False, now=NOW + 120).allowed


def test_two_starts_at_the_same_moment_cannot_both_win(usage, usage_table):
    usage.try_start("1", now=NOW)
    usage.finish("1", refund=True)
    real_get = usage_table.get_item

    def get_then_someone_else_starts(Key):
        snapshot = real_get(Key)
        usage_table.items["1"]["active_until"] = NOW + STALE_SECONDS
        return snapshot

    usage_table.get_item = get_then_someone_else_starts

    assert usage.try_start("1", now=NOW + 10).allowed is False


def test_finish_for_unknown_user_does_nothing(usage, usage_table):
    usage.finish("nobody")

    assert usage_table.items == {}


def test_spend_counter_adds_up_per_month(spend_table):
    spend = SpendCounter(spend_table)

    spend.add(1_500_000, now=NOW)
    spend.add(250_000, now=NOW)

    assert spend.month_total(now=NOW) == 1_750_000
    assert spend.month_total(now=NOW + 40 * 24 * 3600) == 0


def test_month_key_uses_utc():
    assert month_key(datetime(2026, 11, 30, 23, 30, tzinfo=timezone.utc).timestamp()) == "2026-11"


def test_cost_in_micro_dollars():
    assert cost_micro_usd(1_000_000, 0, 3.3, 16.5) == 3_300_000
    assert cost_micro_usd(10_000, 2_000, 3.3, 16.5) == 33_000 + 33_000


def test_limits_from_env(monkeypatch):
    monkeypatch.setenv("MONTHLY_SPEND_CAP_USD", "50")
    monkeypatch.setenv("UNLIMITED_LOGINS", "ZaidShabanDev, other")

    limits = Limits.from_env()

    assert limits.monthly_cap_micro_usd == 50_000_000
    assert limits.is_unlimited("zaidshabandev")
    assert not limits.is_unlimited("someone")
