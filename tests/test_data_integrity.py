"""身份和并发保存回归；只用临时配置和假任务。"""

import copy
import json
import threading
from datetime import datetime, timedelta
from unittest import mock

import yaml

from miyouqian.core.config import account_key, load_config, merge_credentials, save_config
from miyouqian.service import runner, web
from miyouqian.service.scheduler import DailyScheduler
from tests.support import IsolatedConfigTest, base_config


def account(uid, name="相同名称", cookie=None):
    return {"name": name, "stuid": uid, "cookie": cookie or f"cookie-{uid}", "stoken": f"token-{uid}"}


class CredentialIdentityTest(IsolatedConfigTest):
    def test_same_name_accounts_survive_reorder_and_delete(self):
        config = base_config()
        config["accounts"] = [account("101"), account("202")]
        self.write_config(config)
        public = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        public["accounts"].reverse()
        self.config_path.write_text(yaml.safe_dump(public, allow_unicode=True), encoding="utf-8")
        loaded = load_config(self.config_path)
        self.assertEqual([a["cookie"] for a in loaded["accounts"]], ["cookie-202", "cookie-101"])
        public["accounts"].pop()
        self.config_path.write_text(yaml.safe_dump(public, allow_unicode=True), encoding="utf-8")
        self.assertEqual(load_config(self.config_path)["accounts"][0]["cookie"], "cookie-202")

    def test_ambiguous_or_mismatched_identity_never_uses_position(self):
        cases = [
            ([{"name": "same"}, {"name": "same"}], [account("101", "same"), account("202", "same")]),
            ([{"name": "prefix"}], [account("101", "prefix-one"), account("202", "prefix-two")]),
            ([{"name": "A", "stuid": "999"}], [account("101", "A")]),
            ([{"name": "A", "stuid": "101"}], [account("101", "A"), account("101", "B")]),
            ([{"name": "A", "stuid": "101"}, {"name": "B", "stuid": "101"}], [account("101", "A")]),
            ([{"name": "A"}], [{"name": "A", "stuid": "", "cookie": "unknown"}]),
        ]
        for accounts, credentials in cases:
            with self.subTest(accounts=accounts):
                config = {"accounts": accounts}
                merge_credentials(config, {"accounts": credentials})
                self.assertFalse(any(a.get("cookie") for a in config["accounts"]))

    def test_cloud_only_restore_requires_unique_name_and_never_restores_main_cookie(self):
        original = {"name": "cloud", "stuid": "", "cookie": "unbound-cookie",
                    "cloud_games": {"tokens": {"genshin": "fake-cloud"}}}
        for action in ("unique", "duplicate_saved", "duplicate_config", "different_uid"):
            with self.subTest(action=action):
                config = {"accounts": [{"name": "cloud", "stuid": ""}]}
                credentials = {"accounts": [copy.deepcopy(original)]}
                if action == "duplicate_saved":
                    credentials["accounts"].append(copy.deepcopy(original))
                elif action == "duplicate_config":
                    config["accounts"].append({"name": "cloud", "stuid": ""})
                elif action == "different_uid":
                    config["accounts"][0]["stuid"] = "999"
                merge_credentials(config, credentials)
                self.assertFalse(any(a.get("cookie") for a in config["accounts"]))
                tokens = [a["cloud_games"]["tokens"].get("genshin", "") for a in config["accounts"]]
                self.assertEqual(any(tokens), action == "unique")

    def test_unmatched_credentials_prompt_relogin_without_logging_secrets(self):
        from miyouqian.core import config as config_module

        with mock.patch.object(config_module, "logger") as logger:
            merge_credentials({"accounts": [{"name": "unknown"}]}, {"accounts": [account("101")]})
        warning = logger.bind.return_value.warning
        warning.assert_called_once()
        self.assertIn("请重新登录", warning.call_args.args[0])
        self.assertNotIn("cookie-101", str(warning.call_args))
        self.assertNotIn("token-101", str(warning.call_args))

    def test_unambiguous_legacy_name_migrates_once(self):
        config = {"accounts": [{"name": "A"}]}
        merge_credentials(config, {"accounts": [account("101", "A")]})
        self.assertEqual(config["accounts"][0]["stuid"], "101")
        save_config(self.config_path, config)
        self.assertEqual(load_config(self.config_path)["accounts"][0]["cookie"], "cookie-101")

    def test_deleting_all_accounts_does_not_resurrect_credentials(self):
        config = {"accounts": []}
        merge_credentials(config, {"accounts": [account("101")]})
        self.assertEqual(config["accounts"], [])


class TaskPersistenceTest(IsolatedConfigTest):
    def test_finishing_task_merges_cookie_without_overwriting_new_config(self):
        for action in ("settings", "relogin", "delete", "reorder"):
            with self.subTest(action=action):
                config = base_config()
                config["accounts"] = [account("101", "A"), account("202", "B")]
                self.write_config(config)
                app = web.WebApp(self.config_path)
                app.log = mock.Mock()
                entered, release = threading.Event(), threading.Event()
                errors = []

                class FakeGame:
                    def __init__(self, client, config, target, emit=None):
                        self.target = target

                    def run(self, only_games=None):
                        if self.target["stuid"] == "101":
                            entered.set()
                            if not release.wait(5):
                                raise RuntimeError("test barrier timeout")
                            self.target["cookie"] = "refreshed-cookie"
                        return []

                def run():
                    try:
                        app.run_all()
                    except Exception as exc:
                        errors.append(exc)

                with mock.patch.object(runner, "GameCheckin", FakeGame), mock.patch.object(runner, "ApiClient"), \
                     mock.patch.object(runner, "ensure_mainland_ip", return_value=True):
                    thread = threading.Thread(target=run)
                    thread.start()
                    try:
                        self.assertTrue(entered.wait(5))
                        payload = app.get_config()
                        payload["schedule"]["time"] = "12:34"
                        if action == "relogin":
                            payload["accounts"][0]["cookie"] = "new-login"
                        elif action == "delete":
                            payload["accounts"].pop(0)
                        elif action == "reorder":
                            payload["accounts"].reverse()
                        app.set_config(payload)
                    finally:
                        release.set()
                        thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertFalse(errors)
                saved = load_config(self.config_path)
                self.assertEqual(saved["schedule"]["time"], "12:34")
                by_uid = {a["stuid"]: a for a in saved["accounts"]}
                if action == "delete":
                    self.assertNotIn("101", by_uid)
                else:
                    self.assertEqual(by_uid["101"]["cookie"], "new-login" if action == "relogin" else "refreshed-cookie")
                self.assertEqual(by_uid["202"]["cookie"], "cookie-202")


class DailyBudgetTest(IsolatedConfigTest):
    def test_yesterdays_attempt_limit_does_not_block_today(self):
        config = base_config()
        config["schedule"].update(enable=True, catch_up=True, time="00:00")
        config["accounts"] = [{"name": "A"}]
        state_path = self.dir / "state.json"
        state = {
            "date": (datetime.now() - timedelta(days=1)).date().isoformat(),
            "accounts_done": [], "accounts_attempts": {account_key(1, "A"): 2}, "finished": False,
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        scheduler = DailyScheduler(config, lambda: [], lambda _: None, state_path=state_path)
        self.assertEqual(scheduler._state, state)
        self.assertTrue(scheduler._should_catch_up())


class LoginConcurrencyTest(IsolatedConfigTest):
    def make_app(self):
        config = base_config()
        config["accounts"] = [account("101", "A"), account("202", "B")]
        self.write_config(config)
        app = web.WebApp(self.config_path)
        app.log = mock.Mock()
        return app

    def test_draft_qr_and_captcha_results_echo_stable_client_identity(self):
        for kind in ("qr", "captcha"):
            with self.subTest(kind=kind):
                app = self.make_app()
                draft = {"name": "draft", "_draft_id": "stable-draft-id"}
                result_data = account("303", cookie="draft-cookie")
                with mock.patch.object(web, "ApiClient"), \
                     mock.patch.object(app, "_complete_login_data", return_value=result_data):
                    if kind == "qr":
                        with mock.patch.object(web.threading, "Thread") as thread, \
                             mock.patch.object(web, "QRLogin") as login, \
                             mock.patch.object(web, "make_qr_data_uri", return_value="fake-qr"), \
                             mock.patch.object(app, "_wait_with_refresh", return_value={}):
                            login.return_value.fetch.return_value = ("fake-url", "fake-ticket")
                            app.start_login(2, 5, draft, True)
                            self.assertEqual(app.login_state.get("draft_id"), "stable-draft-id")
                            thread.call_args.kwargs["target"](*thread.call_args.kwargs["args"])
                            result = app.login_state
                    else:
                        with mock.patch.object(web, "CaptchaLogin"):
                            result = app.complete_login_captcha(2, "13800000000", "123456", "fake-action", draft, True)
                self.assertEqual(result.get("draft_id"), "stable-draft-id")
                self.assertEqual(result["account_data"]["stuid"], "303")
                self.assertEqual([a["stuid"] for a in load_config(self.config_path)["accounts"]], ["101", "202"])

    def test_old_qr_fetch_cannot_replace_restarted_login(self):
        app = self.make_app()
        first_fetch, release_first = threading.Event(), threading.Event()
        second_wait, release_second = threading.Event(), threading.Event()
        threads, logins = [], []
        real_thread = threading.Thread

        class FakeLogin:
            def __init__(self, *args):
                logins.append(self)
                self.number = len(logins)

            def fetch(self):
                if self.number == 1:
                    first_fetch.set()
                    if not release_first.wait(5):
                        raise RuntimeError("test barrier timeout")
                return f"fake:{self.number}", str(self.number)

            def wait(self, *args, **kwargs):
                if self.number == 1:
                    raise AssertionError("old login must not start waiting")
                second_wait.set()
                if not release_second.wait(5):
                    raise RuntimeError("test barrier timeout")
                return {}

        def capture_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            threads.append(thread)
            return thread

        with mock.patch.object(web, "ApiClient"), mock.patch.object(web, "QRLogin", FakeLogin), \
             mock.patch.object(web, "make_qr_data_uri", side_effect=lambda url: f"qr:{url}"), \
             mock.patch.object(app, "_complete_login_data") as complete, \
             mock.patch.object(web.threading, "Thread", side_effect=capture_thread):
            try:
                app.start_login(0, 5)
                self.assertTrue(first_fetch.wait(5))
                app.cancel_login()
                app.start_login(1, 5)
                self.assertTrue(second_wait.wait(5))
                release_first.set()
                threads[0].join(5)
                self.assertFalse(threads[0].is_alive())
                self.assertEqual(app.login_state["qr"], "qr:fake:2")
                self.assertEqual(app.login_state["qr_url"], "fake:2")
                self.assertEqual(app.login_state["account_index"], 1)
                self.assertEqual(app.login_state["status"], "waiting")
                self.assertTrue(app.login_state["running"])
                complete.assert_not_called()
            finally:
                app.cancel_login()
                release_first.set()
                release_second.set()
                for thread in threads:
                    thread.join(5)
        self.assertEqual([a["cookie"] for a in load_config(self.config_path)["accounts"]],
                         ["cookie-101", "cookie-202"])

    def test_deleted_empty_uid_target_cannot_write_to_replacement(self):
        config = base_config()
        config["accounts"] = [{"name": "pending"}, account("202", "B")]
        self.write_config(config)
        app = web.WebApp(self.config_path)
        app.log = mock.Mock()
        snapshot, _name, target = app._login_account_snapshot(0, None, False)
        payload = app.get_config()
        payload["accounts"].pop(0)
        app.set_config(payload)
        with self.assertRaisesRegex(ValueError, "目标账号已删除"):
            app._save_login_account(0, snapshot, account("101", cookie="old-worker"), False, target=target)
        self.assertEqual([a["stuid"] for a in load_config(self.config_path)["accounts"]], ["202"])
        self.assertEqual(app.config["accounts"][0]["cookie"], "cookie-202")

    def test_cancel_and_restart_cannot_save_or_publish_old_login(self):
        app = self.make_app()
        entered, release, second_wait = threading.Event(), threading.Event(), threading.Event()
        threads, counter = [], []
        real_thread = threading.Thread

        class FakeLogin:
            def __init__(self, *args):
                counter.append(1)
                self.number = len(counter)

            def fetch(self):
                return f"fake:{self.number}", str(self.number)

            def wait(self, *args, **kwargs):
                if self.number == 2:
                    second_wait.set()
                    release.wait(5)
                return {"number": self.number}

        def complete(login, scan):
            if scan["number"] == 1:
                entered.set()
                release.wait(5)
            return account("101" if scan["number"] == 1 else "202", cookie=f"login-{scan['number']}")

        def capture_thread(*args, **kwargs):
            thread = real_thread(*args, **kwargs)
            threads.append(thread)
            return thread

        with mock.patch.object(web, "ApiClient"), mock.patch.object(web, "QRLogin", FakeLogin), \
             mock.patch.object(app, "_complete_login_data", side_effect=complete), \
             mock.patch.object(web.threading, "Thread", side_effect=capture_thread):
            try:
                app.start_login(0, 5)
                self.assertTrue(entered.wait(5))
                app.cancel_login()
                app.start_login(1, 5)
                self.assertTrue(second_wait.wait(5))
            finally:
                release.set()
                for thread in threads:
                    thread.join(5)
        saved = load_config(self.config_path)
        self.assertEqual(saved["accounts"][0]["cookie"], "cookie-101")
        self.assertEqual(saved["accounts"][1]["cookie"], "login-2")
        self.assertEqual(app.login_state["account_index"], 1)
        self.assertEqual(app.login_state["status"], "success")

    def test_login_writeback_follows_uid_and_preserves_current_fields(self):
        for action in ("delete", "reorder", "relogin"):
            with self.subTest(action=action):
                app = self.make_app()
                snapshot, _name, target = app._login_account_snapshot(0, None, False)
                payload = app.get_config()
                if action == "delete":
                    payload["accounts"].pop(0)
                elif action == "reorder":
                    payload["accounts"].reverse()
                    payload["accounts"][1]["name"] = "renamed"
                else:
                    payload["accounts"][0]["cookie"] = "newer-login"
                app.set_config(payload)
                if action == "reorder":
                    app._save_login_account(0, snapshot, account("101", cookie="new-cookie"), False, target=target)
                    self.assertEqual(app.config["accounts"][1]["cookie"], "new-cookie")
                    self.assertEqual(app.config["accounts"][1]["name"], "renamed")
                    self.assertEqual(app.config["accounts"][0]["cookie"], "cookie-202")
                else:
                    with self.assertRaises(ValueError):
                        app._save_login_account(0, snapshot, account("101", cookie="old-worker"), False, target=target)
