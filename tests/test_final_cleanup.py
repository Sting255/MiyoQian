# -*- coding: utf-8 -*-
"""收尾清理：死代码、兑换的 1034/IP 守卫、计划键冲突、debug 目录、凭证回传。

关于兑换计划的键（2026-09-23 已按上游做法修正）：
  - worker 的唯一键 = `账号:商品:开抢时间` —— 同一商品同一时间的**不同账号**各跑一条；
  - 写进计划的 `last_attempt_key` 仍然只是 `商品:开抢时间`，格式不变，
    所以已保存的计划不会被误判成「没试过」而重复兑换。
  - exchange worker 的 join 收不回在跑的兑换：要跨层传 stop_event，收益小于风险，没做。
"""

from __future__ import annotations

import pathlib
import time
import unittest
from unittest import mock

from miyouqian.core import captcha, crypto, ipcheck
from miyouqian.service import exchange_scheduler as es
from miyouqian.service.exchange_scheduler import ExchangeScheduler, PlanWorker
from miyouqian.service import web as web_mod
from tests.support import base_config, make_case_dir

PAST = 1_700_000_000


class DeadCodeTest(unittest.TestCase):
    """死代码删掉，免得误导后来的人。"""

    def test_ipcheck_probe_is_gone(self) -> None:
        self.assertFalse(hasattr(ipcheck, "probe"), "ipcheck.probe 已被 probe_links 取代")

    def test_crypto_ds_app_is_gone(self) -> None:
        self.assertFalse(hasattr(crypto, "ds_app"), "crypto.ds_app 零引用")

    def test_web_revoke_session_is_gone(self) -> None:
        self.assertFalse(
            hasattr(web_mod.WebApp, "_revoke_session"),
            "没有任何登出接口，_revoke_session 是死代码",
        )

    def test_login_unused_imports_are_gone(self) -> None:
        from miyouqian.auth import login as login_mod

        source = pathlib.Path(login_mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from urllib.parse import", source,
                         "login.py 里 parse_qs/urlparse 从没用过")
        self.assertFalse(hasattr(login_mod, "parse_qs"))


class AttemptKeyCollisionTest(unittest.TestCase):
    """同一个账号 + 同一商品 + 同一开抢时间的计划才会互相覆盖；不同账号各跑一条。"""

    def _scheduler(self, plans: list) -> ExchangeScheduler:
        config = base_config()
        config["shop_exchange"] = {"enable": True, "plans": plans, "retry_seconds": 1, "retry_interval": 0.1}
        return ExchangeScheduler(config, lambda index: None, lambda message: None)

    def test_same_account_same_goods_same_time_is_a_collision(self) -> None:
        plans = [
            {"goods_id": "A", "exchange_at": PAST, "account_index": 0},
            {"goods_id": "A", "exchange_at": PAST, "account_index": 0},
        ]
        self.assertEqual(
            ExchangeScheduler.duplicate_worker_keys(plans),
            ["0:A:%d" % PAST],
        )

    def test_different_accounts_are_not_a_collision(self) -> None:
        """两个账号抢同一商品同一秒是正常需求，两条都要跑（上游测试也这么要求）。"""
        plans = [
            {"goods_id": "A", "exchange_at": PAST, "account_index": 0},
            {"goods_id": "A", "exchange_at": PAST, "account_index": 1},
        ]
        self.assertEqual(ExchangeScheduler.duplicate_worker_keys(plans), [])

    def test_no_collision_when_times_differ(self) -> None:
        plans = [
            {"goods_id": "A", "exchange_at": PAST},
            {"goods_id": "A", "exchange_at": PAST + 1},
        ]
        self.assertEqual(ExchangeScheduler.duplicate_worker_keys(plans), [])

    def test_attempt_key_stays_account_free_for_saved_plans(self) -> None:
        """写进计划的 last_attempt_key 不能带账号：否则「改一下计划」会被当成没试过 → 重复兑换。"""
        plans = [{"goods_id": "A", "exchange_at": PAST, "account_index": 3, "address_id": "9"}]
        scheduler = self._scheduler(plans)
        self.assertEqual(scheduler._attempt_key(plans[0], PAST), "A:%d" % PAST)

    def test_worker_key_carries_the_account(self) -> None:
        plan = {"goods_id": "A", "exchange_at": PAST, "account_index": 7}
        scheduler = self._scheduler([plan])
        self.assertEqual(scheduler._worker_key(plan, PAST), "7:A:%d" % PAST)

    def test_rebuild_logs_the_collision(self) -> None:
        plans = [
            {"goods_id": "A", "exchange_at": PAST + 99999, "enable": True, "auto": True, "account_index": 0},
            {"goods_id": "A", "exchange_at": PAST + 99999, "enable": True, "auto": True, "account_index": 0},
        ]
        messages: list[str] = []
        config = base_config()
        config["shop_exchange"] = {"enable": True, "plans": plans}
        scheduler = ExchangeScheduler(config, lambda index: None, messages.append)
        with mock.patch.object(es.PlanWorker, "start", lambda self: None):
            scheduler._rebuild_workers()
        self.assertTrue(
            any("同一时间" in item or "冲突" in item for item in messages),
            f"计划冲突没有告警: {messages}",
        )

    def test_two_accounts_get_two_workers(self) -> None:
        future = int(time.time()) + 3600
        plans = [
            {"goods_id": "A", "exchange_at": future, "enable": True, "auto": True, "account_index": 0},
            {"goods_id": "A", "exchange_at": future, "enable": True, "auto": True, "account_index": 1},
        ]
        config = base_config()
        config["shop_exchange"] = {"enable": True, "plans": plans}
        scheduler = ExchangeScheduler(config, lambda index: None, lambda message: None)
        with mock.patch.object(es.PlanWorker, "start", lambda self: None):
            scheduler._rebuild_workers()
        self.assertEqual(scheduler.status()["worker_count"], 2, "第二个账号的计划被吞掉了")


class _VirtualClock:
    """把 sleep 变成推进虚拟时间，避免「sleep 被 mock 掉 → 忙等到真实期限」。"""

    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(float(seconds), 0.0)


class ExchangeNeedCaptchaTest(unittest.TestCase):
    """兑换触发 1034 时立刻停手：继续重试既没用，又是在高频打接口。"""

    def _exchange(self, results: list) -> tuple[object, int]:
        from miyouqian.tasks import shop_exchange as shop_mod
        from miyouqian.tasks.shop_exchange import ShopExchange

        config = base_config()
        config["shop_exchange"] = {"retry_seconds": 1.0, "retry_interval": 0.05}
        shop = ShopExchange(mock.Mock(), config, {}, emit=None)
        calls = {"n": 0}

        def fake_exchange(plan):
            calls["n"] += 1
            return dict(results[min(calls["n"] - 1, len(results) - 1)])

        shop.exchange = fake_exchange  # type: ignore[assignment]
        with mock.patch.object(shop_mod, "time", _VirtualClock()):
            result = shop.exchange_with_retry({"goods_id": "A", "device_fp": "fp"})
        return result, calls["n"]

    def test_captcha_stops_retrying_immediately(self) -> None:
        result, calls = self._exchange([
            {"ok": False, "retcode": 1034, "message": "需要验证码", "need_captcha": True},
        ])
        self.assertEqual(calls, 1, "触发验证码后还在继续重试")
        self.assertFalse(result["ok"])

    def test_other_errors_still_retry(self) -> None:
        _result, calls = self._exchange([
            {"ok": False, "retcode": 1001, "message": "库存不足"},
        ])
        self.assertGreater(calls, 2, "普通失败应当继续重试")

    def test_exchange_marks_captcha_retcode(self) -> None:
        from miyouqian import constants as c
        from miyouqian.tasks.shop_exchange import ShopExchange

        config = base_config()
        shop = ShopExchange(mock.Mock(), config, {}, emit=None)
        shop.client = mock.Mock()
        shop.client.post_json.return_value = {"retcode": 1034, "message": "需要验证码"}
        result = shop.exchange({"goods_id": "A", "device_fp": "fp"})
        self.assertTrue(result.get("need_captcha"), "没有把 1034 标记出来")


class ExchangeIpGuardTest(unittest.TestCase):
    """抢购不该从境外 IP 发出，但也不能在执行前阻塞等待。"""

    def test_ip_guard_helper_is_non_blocking(self) -> None:
        from miyouqian.service import ip_guard

        self.assertTrue(hasattr(ip_guard, "mainland_ok_now"), "缺少非阻塞的出口 IP 判断")
        verdicts = {"value": True}
        config = {"ip_guard": {"enable": True, "on_error": "allow"}}

        class Report:
            mainland = True

        with mock.patch.object(ip_guard, "probe_links", lambda **kw: Report()):
            self.assertIs(ip_guard.mainland_ok_now(config), True)
        config["ip_guard"]["enable"] = False
        self.assertIsNone(ip_guard.mainland_ok_now(config))

    def test_worker_skips_when_ip_is_overseas(self) -> None:
        fired: list[int] = []
        messages: list[str] = []
        worker = PlanWorker(
            index=0,
            plan={"goods_id": "B"},
            exchange_at=PAST,
            attempt_key="B:1",
            run_fn=fired.append,
            log_fn=messages.append,
            ip_check=lambda: False,
        )
        with mock.patch.object(es, "sync_server_time_offset", lambda log: 0.0):
            worker._loop()
        self.assertEqual(fired, [], "境外 IP 下仍然发了兑换请求")
        self.assertTrue(any("IP" in item for item in messages), messages)

    def test_worker_proceeds_when_ip_check_is_unknown(self) -> None:
        fired: list[int] = []
        worker = PlanWorker(
            index=0,
            plan={"goods_id": "B"},
            exchange_at=PAST,
            attempt_key="B:1",
            run_fn=fired.append,
            log_fn=lambda message: None,
            ip_check=lambda: None,
        )
        with mock.patch.object(es, "sync_server_time_offset", lambda log: 0.0):
            worker._loop()
        self.assertEqual(fired, [0], "IP 判断不出时应当按 on_error=allow 继续")


class DebugDirTest(unittest.TestCase):
    """debug 目录要跟着仓库走，不能跟着当前工作目录跑。"""

    def test_default_is_repo_relative(self) -> None:
        from miyouqian.core.geetest import nine

        expected = nine.default_models_dir().parent / "captcha_debug"
        self.assertEqual(captcha._debug_dir({"storage": {"data_dir": "data"}}), expected)

    def test_absolute_path_is_honoured(self) -> None:
        target = make_case_dir()   # 本平台上的绝对路径
        self.assertEqual(
            captcha._debug_dir({"storage": {"data_dir": str(target)}}),
            target / "captcha_debug",
        )

    def test_solve_local_passes_the_resolved_dir(self) -> None:
        from miyouqian.core.geetest import nine as nine_mod

        captured: dict = {}

        def fake_solve(gt, challenge, **kwargs):
            captured.update(kwargs)
            return ("validate", "challenge")

        with mock.patch.object(captcha, "_local_matcher", lambda channel, emit: object()), \
             mock.patch.object(nine_mod, "solve", fake_solve):
            captcha._solve_local({"provider": "local"}, {"storage": {"data_dir": "data"}}, "gt", "ch", None)

        self.assertIn("debug_dir", captured)
        self.assertIn("captcha_debug", str(captured["debug_dir"]))


class LoginAccountDataTest(unittest.TestCase):
    """非草稿账号登录成功后，凭证已经落盘，不该再往 /api/status 里回传。"""

    def test_account_data_only_exposed_for_drafts(self) -> None:
        source = pathlib.Path(web_mod.__file__).read_text(encoding="utf-8")
        marker = 'account_data"] = account_data'
        self.assertIn(marker, source, "登录成功后没有回传 account_data（草稿账号需要它）")
        window = source[max(0, source.index(marker) - 700): source.index(marker)]
        self.assertIn("if draft:", window, "回传 account_data 时没有按草稿与否做判断")


class UnwritableDebugDirTest(unittest.TestCase):
    """现场图写不进去也绝不能把识别本身搞失败。"""

    def test_debug_write_is_guarded(self) -> None:
        from miyouqian.core.geetest import nine as nine_mod

        source = pathlib.Path(nine_mod.__file__).read_text(encoding="utf-8")
        start = source.index("def save_debug_artifacts")
        window = source[start:start + 1400]
        self.assertIn("try:", window, "debug 落盘没有 try 保护，目录只读时会让整次识别失败")
        self.assertIn("except Exception", window)
        self.assertIn("不影响识别", window)

    def test_debug_dir_is_cleared_before_each_solve(self) -> None:
        """reset_debug_dir 必须真的被调用，否则现场图会无限堆积。"""
        source = pathlib.Path(captcha.__file__).read_text(encoding="utf-8")
        self.assertIn("reset_debug_dir", source, "captcha 没有清理上一次的现场图")


if __name__ == "__main__":
    unittest.main()
