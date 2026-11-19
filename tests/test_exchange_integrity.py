"""兑换身份、快照和并发回写；仅临时配置与假商城。"""

import copy
import threading
import time
from unittest import mock

from miyouqian.core.config import load_config
from miyouqian.service import web
from miyouqian.service.exchange_scheduler import ExchangeScheduler, PlanWorker
from tests.support import IsolatedConfigTest, base_config


def plan(plan_id="first", **changes):
    return {"plan_id": plan_id, "plan_version": "v1", "account_uid": "101",
            "account_index": 0, "goods_id": "gift", "goods_name": "礼物",
            "device_fp": "fake-fp", "exchange_at": int(time.time()) - 5,
            "enable": True, "auto": True, **changes}


class ExchangeIntegrityTest(IsolatedConfigTest):
    def setUp(self):
        super().setUp()
        self.start_worker = mock.patch.object(PlanWorker, "start").start()
        self.addCleanup(mock.patch.stopall)

    def make_app(self, plans=None):
        config = base_config()
        config["accounts"] = [
            {"name": "A", "stuid": "101", "cookie": "fake-a"},
            {"name": "B", "stuid": "202", "cookie": "fake-b"},
        ]
        config["shop_exchange"].update(enable=True, push=True, plans=plans or [plan()])
        self.write_config(config)
        app = web.WebApp(self.config_path)
        app.log = mock.Mock()
        app._send_exchange_push = mock.Mock()
        return app

    def test_legacy_index_plan_is_paused_and_identity_survives_reload(self):
        legacy = plan()
        for key in ("plan_id", "plan_version", "account_uid"):
            legacy.pop(key)
        app = self.make_app([legacy])
        migrated = app.config["shop_exchange"]["plans"][0]
        self.assertFalse(migrated["enable"])
        self.assertTrue(migrated["needs_account_confirmation"])
        self.assertEqual(load_config(self.config_path)["shop_exchange"]["plans"][0]["plan_id"], migrated["plan_id"])
        with mock.patch.object(app, "_perform_shop_exchange") as request:
            with self.assertRaises(ValueError):
                app.shop_exchange_plan_once(migrated["plan_id"])
            request.assert_not_called()
        payload = app.get_config()
        payload["shop_exchange"]["plans"][0].update(account_uid="202", enable=True)
        app.set_config(payload)
        with mock.patch.object(app, "_perform_shop_exchange", return_value={"ok": True}) as request:
            app.shop_exchange_plan_once(migrated["plan_id"])
        self.assertEqual(request.call_args.args[1]["stuid"], "202")

    def test_binding_uses_uid_after_account_reorder_and_refuses_deleted_account(self):
        app = self.make_app()
        payload = app.get_config()
        payload["accounts"].reverse()
        app.set_config(payload)
        with mock.patch.object(app, "_perform_shop_exchange", return_value={"ok": False}) as request:
            app.shop_exchange_plan_once("first")
            self.assertEqual(request.call_args.args[1]["stuid"], "101")
            payload = app.get_config()
            payload["accounts"].pop()
            app.set_config(payload)
            with self.assertRaises(ValueError):
                app.shop_exchange_plan_once("first")
            self.assertEqual(request.call_count, 1)

    def test_manual_request_rejects_stale_version(self):
        app = self.make_app()
        old_version = app.config["shop_exchange"]["plans"][0]["plan_version"]
        payload = app.get_config()
        payload["shop_exchange"]["plans"][0]["address_id"] = "updated-address"
        app.set_config(payload)
        with mock.patch.object(app, "_perform_shop_exchange") as request:
            with self.assertRaisesRegex(ValueError, "计划已修改"):
                app.shop_exchange_plan_once("first", old_version)
            request.assert_not_called()
        self.assertFalse(app._exchange_tokens)

    def test_failed_initial_persistence_releases_occupancy(self):
        app = self.make_app()
        with mock.patch.object(web, "save_config", side_effect=OSError("fake disk error")), \
             mock.patch.object(app, "_perform_shop_exchange") as request:
            with self.assertRaisesRegex(OSError, "fake disk error"):
                app.shop_exchange_plan_once("first")
            request.assert_not_called()
        self.assertFalse(app._exchange_tokens)
        self.assertEqual(app.exchange_scheduler.status()["running_count"], 0)

    def test_invalid_exchange_response_releases_occupancy(self):
        app = self.make_app()
        with mock.patch.object(app, "_perform_shop_exchange", return_value=None):
            with self.assertRaisesRegex(ValueError, "返回格式错误"):
                app.shop_exchange_plan_once("first")
        self.assertFalse(app._exchange_tokens)
        self.assertEqual(app.exchange_scheduler.status()["running_count"], 0)
        saved = load_config(self.config_path)["shop_exchange"]["plans"][0]
        self.assertIn("返回格式错误", saved["last_result"])
        self.assertTrue(saved["enable"])

    def test_inflight_reorder_delete_and_edit_cannot_corrupt_another_plan(self):
        for action in ("reorder", "delete", "edit"):
            for fail in (False, True):
                with self.subTest(action=action, fail=fail):
                    app = self.make_app([plan(), plan("second", account_uid="202", goods_id="other")])
                    entered, release = threading.Event(), threading.Event()
                    errors, snapshots = [], []

                    def request(config, account, frozen, on_progress=None):
                        snapshots.append((config, account, frozen))
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError("test barrier timeout")
                        if fail:
                            raise RuntimeError("fake exchange error")
                        return {"ok": True, "message": "done", "retcode": 0}

                    def run():
                        try:
                            app.shop_exchange_plan_once("first")
                        except Exception as exc:
                            errors.append(exc)

                    with mock.patch.object(app, "_perform_shop_exchange", side_effect=request):
                        thread = threading.Thread(target=run)
                        thread.start()
                        try:
                            self.assertTrue(entered.wait(5))
                            payload = app.get_config()
                            payload["accounts"].reverse()
                            payload["accounts"][1]["name"] = "renamed"
                            if action == "reorder":
                                payload["shop_exchange"]["plans"].reverse()
                            elif action == "delete":
                                payload["shop_exchange"]["plans"].pop(0)
                            else:
                                payload["shop_exchange"]["plans"][0]["address_id"] = "new-address"
                            app.set_config(payload)
                        finally:
                            release.set()
                            thread.join(5)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(bool(errors), fail)
                    current = {p["plan_id"]: p for p in load_config(self.config_path)["shop_exchange"]["plans"]}
                    self.assertEqual(current["second"]["last_result"], "")
                    if action == "edit":
                        self.assertEqual(current["first"]["last_result"], "")
                        self.assertTrue(current["first"]["enable"])
                    elif action == "reorder":
                        self.assertIn("fake exchange error" if fail else "done", current["first"]["last_result"])
                        self.assertEqual(current["first"]["enable"], fail)
                    self.assertEqual(snapshots[0][1]["name"], "A")
                    self.assertEqual(app._send_exchange_push.call_args.args[2]["account"], "A")
                    self.assertFalse(app._exchange_tokens)
                    self.assertEqual(app.exchange_scheduler.status()["running_count"], 0)

    def test_stale_worker_revalidates_id_version_time_and_enable(self):
        for action in ("delete", "address", "time", "disable", "account", "global"):
            with self.subTest(action=action):
                app = self.make_app()
                old = copy.deepcopy(app.config["shop_exchange"]["plans"][0])
                payload = app.get_config()
                target = payload["shop_exchange"]["plans"][0]
                if action == "delete":
                    payload["shop_exchange"]["plans"].clear()
                elif action == "global":
                    payload["shop_exchange"]["enable"] = False
                else:
                    key, value = {"address": ("address_id", "new"), "time": ("exchange_at", old["exchange_at"] + 1),
                                  "disable": ("enable", False), "account": ("account_uid", "202")}[action]
                    target[key] = value
                app.set_config(payload)
                with mock.patch.object(app, "_perform_shop_exchange") as request:
                    with self.assertRaises(ValueError):
                        app.run_shop_exchange_plan(old["plan_id"], ExchangeScheduler._attempt_key(old, old["exchange_at"]), old["plan_version"])
                    request.assert_not_called()

    def test_worker_server_time_can_trigger_before_local_clock(self):
        app = self.make_app([plan(exchange_at=int(time.time()) + 10)])
        saved = app.config["shop_exchange"]["plans"][0]
        worker = PlanWorker(0, saved, saved["exchange_at"], "fake-worker",
                            app.run_shop_exchange_plan, app.log, ip_check=None)
        with mock.patch("miyouqian.service.exchange_scheduler.sync_server_time_offset", return_value=30), \
             mock.patch.object(app, "_perform_shop_exchange", return_value={"ok": True}) as request:
            worker._loop()
        request.assert_called_once()
        self.assertFalse(app.config["shop_exchange"]["plans"][0]["enable"])
        self.assertFalse(app._exchange_tokens)

    def test_stale_page_save_cannot_reenable_completed_exchange(self):
        for capture_during_run in (False, True):
            with self.subTest(capture_during_run=capture_during_run):
                app = self.make_app()
                stale = app.get_config()

                def perform(*args):
                    nonlocal stale
                    if capture_during_run:
                        stale = app.get_config()
                    return {"ok": True, "message": "fake success"}

                with mock.patch.object(app, "_perform_shop_exchange", side_effect=perform):
                    app.shop_exchange_plan_once("first")
                completed = app.config["shop_exchange"]["plans"][0]
                self.assertFalse(completed["enable"])
                stale["schedule"]["time"] = "12:34"
                app.set_config(stale)
                current = app.config["shop_exchange"]["plans"][0]
                self.assertFalse(current["enable"], "过期整页保存重新启用了已成功的计划")
                self.assertEqual(app.config["schedule"]["time"], "12:34")
                self.assertEqual(current["last_result"], completed["last_result"])
                fresh = app.get_config()
                fresh["shop_exchange"]["plans"][0]["enable"] = True
                app.set_config(fresh)
                self.assertTrue(app.config["shop_exchange"]["plans"][0]["enable"])

    def test_completion_guard_survives_reload_and_is_server_owned(self):
        app = self.make_app()
        stale = app.get_config()
        with mock.patch.object(app, "_perform_shop_exchange", return_value={"ok": True}):
            app.shop_exchange_plan_once("first")
        completed = load_config(self.config_path)["shop_exchange"]["plans"][0]
        self.assertTrue(completed.get("completion_token"))
        reloaded = web.WebApp(self.config_path)
        reloaded.log = mock.Mock()
        reloaded.set_config(stale)
        current = reloaded.config["shop_exchange"]["plans"][0]
        self.assertFalse(current["enable"])
        self.assertEqual(current["completion_token"], completed["completion_token"])
        payload = reloaded.get_config()
        payload["shop_exchange"]["plans"][0]["completion_token"] = "fake-client-token"
        reloaded.set_config(payload)
        self.assertEqual(reloaded.config["shop_exchange"]["plans"][0]["completion_token"], completed["completion_token"])

    def test_manual_and_automatic_execution_share_atomic_occupancy(self):
        for automatic_first in (False, True):
            with self.subTest(automatic_first=automatic_first):
                app = self.make_app()
                saved = app.config["shop_exchange"]["plans"][0]
                occurrence = ExchangeScheduler._attempt_key(saved, saved["exchange_at"])
                version = saved["plan_version"]
                entered, release = threading.Event(), threading.Event()
                errors = []

                def request(*args):
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("test barrier timeout")
                    return {"ok": False, "message": "fake failure"}

                def automatic():
                    return app.run_shop_exchange_plan("first", occurrence, version)

                def manual():
                    return app.shop_exchange_plan_once("first")

                def run():
                    try:
                        (automatic if automatic_first else manual)()
                    except Exception as exc:
                        errors.append(exc)

                with mock.patch.object(app, "_perform_shop_exchange", side_effect=request) as perform:
                    thread = threading.Thread(target=run)
                    thread.start()
                    try:
                        self.assertTrue(entered.wait(5))
                        self.assertEqual(app.exchange_scheduler.status()["running_plan_ids"], ["first"])
                        with self.assertRaises((ValueError, RuntimeError)):
                            (manual if automatic_first else automatic)()
                    finally:
                        release.set()
                        thread.join(5)
                    self.assertEqual(perform.call_count, 1)
                self.assertFalse(errors)
                self.assertFalse(app._exchange_tokens)

    def test_duplicate_scheduler_and_executor_keep_first_plan(self):
        future = int(time.time()) + 3600
        app = self.make_app([plan(exchange_at=future), plan("second", exchange_at=future)])
        app.exchange_scheduler.start()
        self.assertEqual([worker.plan["plan_id"] for worker in app.exchange_scheduler._workers.values()], ["first"])
        app.exchange_scheduler.stop()
        for saved in app.config["shop_exchange"]["plans"]:
            saved["exchange_at"] = future - 4000
        second = app.config["shop_exchange"]["plans"][1]
        with mock.patch.object(app, "_perform_shop_exchange") as request:
            with self.assertRaisesRegex(ValueError, "第一条"):
                app.run_shop_exchange_plan("second", ExchangeScheduler._attempt_key(second, second["exchange_at"]), second["plan_version"])
            request.assert_not_called()

    def test_saved_plan_http_uses_id_and_direct_exchange_still_works(self):
        app = self.make_app()
        handler = web.Handler.__new__(web.Handler)
        handler.app = app
        handler.path = "/api/shop/exchange"
        handler._is_authenticated = lambda: True
        handler.send_json = mock.Mock()
        handler.send_error_json = mock.Mock()
        with mock.patch.object(app, "shop_exchange_plan_once", return_value={"ok": True}) as saved, \
             mock.patch.object(app, "shop_exchange_once", return_value={"ok": True}) as direct:
            handler.read_json = lambda: {"plan_id": "first"}
            handler.do_POST()
            saved.assert_called_once_with("first")
            direct_plan = plan()
            handler.read_json = lambda: {"plan": direct_plan}
            handler.do_POST()
            direct.assert_called_once_with(direct_plan)
            handler.read_json = lambda: {"plan_index": 0}
            handler.do_POST()
            handler.send_error_json.assert_called_once()
