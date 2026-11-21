# -*- coding: utf-8 -*-
"""账号真实完成状态、IP 门禁与自动补跑预算的端到端回归。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from unittest import mock

from miyouqian.core.config import load_config
from miyouqian.service import runner
from miyouqian.service.scheduler import DailyScheduler
from tests.support import IsolatedConfigTest, base_config


GAME_OK = ["游戏社区签到汇总：成功 1，失败 0，跳过 0"]
GAME_FAILED = [
    "原神 获取绑定角色失败：登录失效(-100)，请重新扫码登录",
    "游戏社区签到汇总：成功 0，失败 1，跳过 0",
]


class RunCompletionTest(IsolatedConfigTest):
    def make_scheduler(
        self,
        *,
        names: tuple[str, ...] = ("A",),
        catch_up: bool = True,
        state: dict | None = None,
        features: dict | None = None,
    ) -> DailyScheduler:
        config = base_config()
        config["accounts"] = [{"name": name} for name in names]
        config["schedule"].update(enable=True, time="00:00", jitter_minutes=0, catch_up=catch_up)
        if features is not None:
            config["features"] = features
        self.write_config(config)
        self.state_path = self.dir / "run_state.json"
        self.state_path.write_text(json.dumps(state or {}), encoding="utf-8")
        self.logs: list[str] = []
        return DailyScheduler(
            load_config(self.config_path), lambda _stop: [], self.logs.append,
            state_path=self.state_path,
        )

    def read_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def run_scheduler(
        self,
        scheduler: DailyScheduler,
        *,
        resume: bool = False,
        game_lines: dict[str, list[str]] | None = None,
        bbs_lines: list[str] | None = None,
        cloud_lines: list[str] | None = None,
        ip_allowed: bool = True,
        stop_in_game: bool = False,
        stream: bool = False,
    ) -> tuple[list[str], list[str]]:
        attempted: list[str] = []
        output: list[str] = []
        finished = threading.Event()
        state_finished = scheduler._state_finished

        def finish(ok):
            state_finished(ok)
            finished.set()

        def run(stop):
            def fake_task(component):
                class Task:
                    def __init__(self, _client, _config, account, emit=None):
                        self.name = account["name"]
                        self.emit = emit

                    def run(self, **_kwargs):
                        attempted.append(f"{component}:{self.name}")
                        if component == "game":
                            lines = (game_lines or {}).get(self.name, GAME_OK)
                            if stop_in_game:
                                stop.set()
                        elif component == "bbs":
                            lines = bbs_lines or []
                        else:
                            lines = cloud_lines or []
                        if self.emit:
                            for line in lines:
                                self.emit(line)
                        return list(lines)

                return Task

            with (
                mock.patch.object(runner, "ApiClient"),
                mock.patch.object(runner, "GameCheckin", fake_task("game")),
                mock.patch.object(runner, "BbsTasks", fake_task("bbs")),
                mock.patch.object(runner, "CloudGameCheckin", fake_task("cloud")),
                mock.patch.object(runner, "ensure_mainland_ip", return_value=ip_allowed),
            ):
                lines = runner.run_tasks(
                    scheduler.config,
                    str(self.config_path),
                    stop_event=stop,
                    skip_accounts=scheduler.resume_skip_accounts(),
                    skip_exhausted_accounts=scheduler.resume_exhausted_accounts(),
                    on_account_done=scheduler.mark_account_done,
                    on_account_failed=scheduler.mark_account_failed,
                    on_account_unfinished=scheduler.mark_account_unfinished,
                    persist=lambda _config: None,
                    emit_component=(lambda _line, _component: None) if stream else None,
                )
                output.extend(lines)
            # WebApp.run_all() 已实时写日志，返回空列表。完成状态必须靠账号回调传回。
            return []

        scheduler.run_fn = run
        with mock.patch.object(scheduler, "_state_finished", side_effect=finish):
            self.assertTrue(scheduler.run_now(resume=resume))
            self.assertTrue(finished.wait(timeout=5), "隔离任务没有按时结束")
        return attempted, output

    def test_game_business_failure_is_not_recorded_as_done(self) -> None:
        for stream in (False, True):
            with self.subTest(stream=stream):
                scheduler = self.make_scheduler(names=("A", "B"))
                attempted, _ = self.run_scheduler(
                    scheduler, game_lines={"A": GAME_FAILED}, stream=stream,
                )
                state = self.read_state()
                self.assertEqual(attempted, ["game:A", "game:B"])
                self.assertEqual(state["accounts_done"], ["2|B"])
                self.assertEqual(state["accounts_attempts"], {"1|A": 1})
                self.assertFalse(state["finished"])
                self.assertIsNone(state["finished_at"])
                self.assertTrue(scheduler._should_catch_up())

    def test_bbs_login_failure_is_not_recorded_as_done(self) -> None:
        scheduler = self.make_scheduler(features={"game_checkin": False, "bbs_tasks": True})
        attempted, _ = self.run_scheduler(
            scheduler, bbs_lines=["任务状态获取失败，请检查 cookie/stoken"],
        )
        state = self.read_state()
        self.assertEqual(attempted, ["bbs:A"])
        self.assertEqual(state["accounts_done"], [])
        self.assertEqual(state["accounts_attempts"], {"1|A": 1})
        self.assertFalse(state["finished"])

    def test_cloud_failure_is_not_recorded_as_done(self) -> None:
        scheduler = self.make_scheduler(features={"game_checkin": False, "cloud_game_checkin": True})
        self.run_scheduler(scheduler, cloud_lines=["云游戏签到汇总：成功 0，失败 1，跳过 0"])
        self.assertEqual(self.read_state()["accounts_attempts"], {"1|A": 1})
        self.assertFalse(self.read_state()["finished"])

    def test_ip_gate_keeps_day_open_without_consuming_an_attempt(self) -> None:
        scheduler = self.make_scheduler()
        attempted, _ = self.run_scheduler(scheduler, ip_allowed=False)
        state = self.read_state()
        self.assertEqual(attempted, [])
        self.assertEqual(state["accounts_done"], [])
        self.assertEqual(state["accounts_attempts"], {})
        self.assertFalse(state["finished"])
        self.assertTrue(scheduler._should_catch_up())
        self.run_scheduler(scheduler, resume=True)
        self.assertTrue(self.read_state()["finished"])
        self.assertEqual(self.read_state()["accounts_done"], ["1|A"])

    def test_stopping_between_components_does_not_mark_account_done(self) -> None:
        scheduler = self.make_scheduler(features={"game_checkin": True, "bbs_tasks": True})
        attempted, output = self.run_scheduler(scheduler, stop_in_game=True)
        state = self.read_state()
        self.assertEqual(attempted, ["game:A"])
        self.assertEqual(state["accounts_done"], [])
        self.assertEqual(state["accounts_attempts"], {})
        self.assertTrue(any("已停止" in line for line in output))
        self.assertTrue(state["finished"], "用户主动停止仍应阻止启动补跑")

    def test_no_bound_role_remains_a_completed_skip(self) -> None:
        scheduler = self.make_scheduler()
        self.run_scheduler(scheduler, game_lines={"A": ["游戏社区签到汇总：成功 0，失败 0，跳过 1"]})
        self.assertTrue(self.read_state()["finished"])
        self.assertEqual(self.read_state()["accounts_done"], ["1|A"])
        self.assertEqual(self.read_state()["accounts_attempts"], {})

    def test_business_failure_exhausts_the_same_daily_retry_budget(self) -> None:
        scheduler = self.make_scheduler()
        self.run_scheduler(scheduler, game_lines={"A": GAME_FAILED})
        self.assertTrue(scheduler._should_catch_up())
        self.run_scheduler(scheduler, resume=True, game_lines={"A": GAME_FAILED})
        self.assertFalse(self.read_state()["finished"])
        self.assertEqual(self.read_state()["accounts_attempts"], {"1|A": 2})
        self.assertFalse(scheduler._should_catch_up())

    def test_exhausted_account_does_not_prevent_another_account_resuming(self) -> None:
        scheduler = self.make_scheduler(names=("A", "B"), state={
            "date": datetime.now().date().isoformat(),
            "finished": False,
            "accounts_done": [],
            "accounts_attempts": {"1|A": 2},
        })
        self.assertTrue(scheduler._should_catch_up())
        attempted, output = self.run_scheduler(scheduler, resume=True)
        state = self.read_state()
        self.assertEqual(attempted, ["game:B"])
        self.assertEqual(state["accounts_done"], ["2|B"])
        self.assertEqual(state["accounts_attempts"], {"1|A": 2})
        self.assertFalse(state["finished"], "用满次数的失败账号仍不算完成")
        self.assertTrue(any("尝试次数已达上限" in line for line in output))
        self.assertNotIn("# 今天所有账号均已完成，无需补跑", output)
        self.assertFalse(scheduler._should_catch_up())

    def test_manual_run_can_retry_an_exhausted_account(self) -> None:
        scheduler = self.make_scheduler(state={
            "date": datetime.now().date().isoformat(), "finished": False,
            "accounts_attempts": {"1|A": 2},
        })
        attempted, _ = self.run_scheduler(scheduler)
        self.assertEqual(attempted, ["game:A"])
        self.assertTrue(self.read_state()["finished"])
        self.assertEqual(self.read_state()["accounts_done"], ["1|A"])

    def test_yesterdays_exhausted_budget_does_not_skip_today(self) -> None:
        scheduler = self.make_scheduler(state={
            "date": (datetime.now() - timedelta(days=1)).date().isoformat(),
            "finished": False, "accounts_attempts": {"1|A": 2},
        })
        attempted, _ = self.run_scheduler(scheduler, resume=True)
        self.assertEqual(attempted, ["game:A"])
        self.assertTrue(self.read_state()["finished"])
        self.assertEqual(self.read_state()["accounts_attempts"], {})

    def test_failure_does_not_enable_unconfigured_catch_up(self) -> None:
        scheduler = self.make_scheduler(catch_up=False)
        self.run_scheduler(scheduler, game_lines={"A": GAME_FAILED})
        self.assertFalse(self.read_state()["finished"])
        self.assertFalse(scheduler._should_catch_up())
