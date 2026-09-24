# -*- coding: utf-8 -*-
"""账号之间的防风控等待：可配置化。

原来硬编码在 `runner.ACCOUNT_GAP_RANGE = (3600, 7200)`，
现在放在配置段 `account_gap`（分钟），网页「每日调度」里可以直接改。

这里要守住两条：
1. 归一化必须把乱七八糟的输入（负数、反了的上下限、超大值、null）拉回可用状态；
2. `run_tasks` 真的要按配置的分钟数去等，且 0 / 关闭时一秒都不等。
"""

from __future__ import annotations

import unittest
from unittest import mock

from miyouqian.core.config import (
    MAX_ACCOUNT_GAP_MINUTES,
    load_config,
    normalize_config,
)
from miyouqian.service import runner
from miyouqian.service.runner import account_gap_seconds
from tests.support import IsolatedConfigTest, base_config


class GapNormalizeTest(unittest.TestCase):
    def _normalized(self, section) -> dict:
        config = base_config()
        config["account_gap"] = section
        normalize_config(config)
        return config["account_gap"]

    def test_defaults_match_old_hardcoded_behaviour(self) -> None:
        """默认必须是旧的 1~2 小时，升级后行为不变。"""
        gap = self._normalized({"enable": True, "min_minutes": 60, "max_minutes": 120})
        self.assertEqual((gap["min_minutes"], gap["max_minutes"]), (60, 120))

    def test_min_and_max_are_swapped_when_reversed(self) -> None:
        gap = self._normalized({"enable": True, "min_minutes": 200, "max_minutes": 30})
        self.assertEqual((gap["min_minutes"], gap["max_minutes"]), (30, 200))

    def test_negative_becomes_zero(self) -> None:
        gap = self._normalized({"enable": True, "min_minutes": -30, "max_minutes": -1})
        self.assertEqual((gap["min_minutes"], gap["max_minutes"]), (0, 0))

    def test_absurd_value_is_capped_at_one_day(self) -> None:
        gap = self._normalized({"enable": True, "min_minutes": 10, "max_minutes": 999999})
        self.assertEqual(gap["max_minutes"], MAX_ACCOUNT_GAP_MINUTES)

    def test_junk_values_fall_back_to_defaults(self) -> None:
        gap = self._normalized({"enable": True, "min_minutes": "abc", "max_minutes": None})
        self.assertEqual((gap["min_minutes"], gap["max_minutes"]), (60, 120))

    def test_float_minutes_are_truncated(self) -> None:
        gap = self._normalized({"enable": True, "min_minutes": 5.9, "max_minutes": "45.5"})
        self.assertEqual((gap["min_minutes"], gap["max_minutes"]), (5, 45))

    def test_null_section_gets_defaults(self) -> None:
        config = base_config()
        config["account_gap"] = None
        normalize_config(config)
        self.assertEqual(config["account_gap"]["min_minutes"], 60)
        self.assertTrue(config["account_gap"]["enable"])

    def test_string_false_disables(self) -> None:
        gap = self._normalized({"enable": "false", "min_minutes": 60, "max_minutes": 120})
        self.assertFalse(gap["enable"])

    def test_normalize_is_idempotent(self) -> None:
        config = base_config()
        config["account_gap"] = {"enable": True, "min_minutes": 90, "max_minutes": 30}
        normalize_config(config)
        first = dict(config["account_gap"])
        normalize_config(config)
        self.assertEqual(config["account_gap"], first)


class GapSecondsTest(unittest.TestCase):
    def _config(self, **gap) -> dict:
        config = base_config()
        config["account_gap"] = gap
        return config

    def test_disabled_returns_zero(self) -> None:
        self.assertEqual(account_gap_seconds(self._config(enable=False, min_minutes=60, max_minutes=120)), 0.0)

    def test_zero_range_returns_zero(self) -> None:
        self.assertEqual(account_gap_seconds(self._config(enable=True, min_minutes=0, max_minutes=0)), 0.0)

    def test_fixed_range_is_exact(self) -> None:
        seconds = account_gap_seconds(self._config(enable=True, min_minutes=5, max_minutes=5))
        self.assertEqual(seconds, 300.0)

    def test_random_value_stays_inside_range(self) -> None:
        config = self._config(enable=True, min_minutes=10, max_minutes=20)
        samples = [account_gap_seconds(config) for _ in range(200)]
        self.assertGreaterEqual(min(samples), 600.0)
        self.assertLessEqual(max(samples), 1200.0)
        self.assertGreater(len(set(samples)), 1, "应该每次都不同（不然就固定成常数了）")

    def test_reversed_bounds_are_tolerated(self) -> None:
        seconds = account_gap_seconds(self._config(enable=True, min_minutes=20, max_minutes=10))
        self.assertGreaterEqual(seconds, 600.0)
        self.assertLessEqual(seconds, 1200.0)

    def test_missing_section_falls_back_to_legacy_range(self) -> None:
        config = base_config()
        config.pop("account_gap", None)
        seconds = account_gap_seconds(config)
        self.assertGreaterEqual(seconds, runner.ACCOUNT_GAP_RANGE[0])
        self.assertLessEqual(seconds, runner.ACCOUNT_GAP_RANGE[1])

    def test_one_minute_range(self) -> None:
        self.assertEqual(account_gap_seconds(self._config(enable=True, min_minutes=1, max_minutes=1)), 60.0)


class GapWiringTest(IsolatedConfigTest):
    """端到端：run_tasks 真的按配置去等（把 sleep 换成记录器，不真等）。"""

    def _run_with_gap(self, gap: dict) -> list[float]:
        config = base_config()
        config["accounts"] = [{"name": "A"}, {"name": "B"}]
        config["account_gap"] = gap
        self.write_config(config)

        waited: list[float] = []

        def fake_sleep(seconds: float, stop_event=None) -> bool:
            waited.append(seconds)
            return True

        class FakeGameCheckin:
            def __init__(self, client, config, account, emit=None):
                pass

            def run(self, only_games=None):
                return ["游戏社区签到汇总：成功 1，失败 0，跳过 0"]

        with (
            mock.patch.object(runner, "interruptible_sleep", fake_sleep),
            mock.patch.object(runner, "GameCheckin", FakeGameCheckin),
            mock.patch.object(runner, "ApiClient", lambda *a, **k: _Ctx()),
            mock.patch.object(runner, "ensure_mainland_ip", lambda *a, **k: True),
            mock.patch.object(runner, "save_config", lambda *a, **k: None),
        ):
            runner.run_tasks(load_config(self.config_path), str(self.config_path))
        return waited

    def test_configured_minutes_are_actually_waited(self) -> None:
        waited = self._run_with_gap({"enable": True, "min_minutes": 20, "max_minutes": 20})
        self.assertEqual(len(waited), 1, "两个账号之间应该只等一次")
        self.assertEqual(waited[0], 1200.0, "没有按配置的 20 分钟等待")

    def test_disabling_the_gap_skips_the_wait(self) -> None:
        waited = self._run_with_gap({"enable": False, "min_minutes": 60, "max_minutes": 120})
        self.assertEqual(waited, [], "关掉账号间隔后不应该等待")

    def test_zero_minutes_skips_the_wait(self) -> None:
        waited = self._run_with_gap({"enable": True, "min_minutes": 0, "max_minutes": 0})
        self.assertEqual(waited, [], "分钟数为 0 时不应该等待")

    def test_single_account_never_waits(self) -> None:
        config = base_config()
        config["accounts"] = [{"name": "A"}]
        config["account_gap"] = {"enable": True, "min_minutes": 60, "max_minutes": 120}
        self.write_config(config)
        waited: list[float] = []

        class FakeGameCheckin:
            def __init__(self, client, config, account, emit=None):
                pass

            def run(self, only_games=None):
                return []

        with (
            mock.patch.object(runner, "interruptible_sleep", lambda s, e=None: waited.append(s) or True),
            mock.patch.object(runner, "GameCheckin", FakeGameCheckin),
            mock.patch.object(runner, "ApiClient", lambda *a, **k: _Ctx()),
            mock.patch.object(runner, "ensure_mainland_ip", lambda *a, **k: True),
            mock.patch.object(runner, "save_config", lambda *a, **k: None),
        ):
            runner.run_tasks(load_config(self.config_path), str(self.config_path))
        self.assertEqual(waited, [], "只有一个账号时不该等待")


class _Ctx:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *exc: object) -> bool:
        return False


if __name__ == "__main__":
    unittest.main()
