# -*- coding: utf-8 -*-
"""验证码故障复现和现场保留；全部使用假页面、假图片与独立临时目录。"""

from __future__ import annotations

import json
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from PIL import Image

from miyouqian.core import captcha
from miyouqian.core.geetest import nine
from tests.support import make_case_dir


class CaptchaDiagnosticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = make_case_dir()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.root = self.folder / "captcha_debug"
        self.root.mkdir()
        self.patchers = [
            mock.patch.object(captcha, "_debug_dir", return_value=self.root),
            mock.patch.object(captcha, "_local_matcher", return_value=mock.Mock()),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_new_request_does_not_erase_previous_evidence(self) -> None:
        old = self.root / "attempt1-101010-grid.png"
        old.write_bytes(b"previous evidence")
        with mock.patch.object(nine, "solve", return_value=("validate", "passed")):
            self.assertIsNotNone(captcha._solve_local({}, {}, "gt", "challenge", None))
        self.assertEqual(old.read_bytes(), b"previous evidence")

    def test_outer_retries_get_separate_directories_and_private_summaries(self) -> None:
        folders = []

        def solve(gt, challenge, **kwargs):
            folder = kwargs["debug_dir"]
            folders.append(folder)
            (folder / "attempt1-101010-grid.png").write_bytes(b"fake image")
            if len(folders) == 1:
                raise RuntimeError("challenge-secret should not enter the summary")
            return "validate-secret", "challenge-secret"

        with mock.patch.object(nine, "solve", side_effect=solve):
            self.assertIsNone(captcha._solve_local({}, {}, "gt-secret", "challenge-secret", None))
            self.assertIsNotNone(captcha._solve_local({}, {}, "gt-secret", "challenge-secret", None))
        self.assertNotEqual(folders[0], folders[1])
        for folder, status in zip(folders, ("failed", "success")):
            self.assertEqual(folder.parent, self.root)
            self.assertTrue((folder / "attempt1-101010-grid.png").exists())
            raw = (folder / "summary.json").read_text(encoding="utf-8")
            summary = json.loads(raw)
            self.assertEqual(summary["status"], status)
            self.assertNotIn("secret", raw)

    def test_unwritable_debug_root_does_not_fail_the_solver(self) -> None:
        shutil.rmtree(self.root)
        self.root.write_text("not a directory", encoding="utf-8")
        messages = []
        with mock.patch.object(nine, "solve", return_value=("validate", "passed")) as solve:
            result = captcha._solve_local({}, {}, "gt", "challenge", messages.append)
        self.assertIsNotNone(result)
        self.assertIsNone(solve.call_args.kwargs["debug_dir"])
        self.assertTrue(any("不影响识别" in message for message in messages))

    def test_history_count_is_bounded_and_unmanaged_files_are_preserved(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        legacy = self.root / "attempt1-101010.json"
        legacy.write_text("{}", encoding="utf-8")
        notes = self.root / "user-notes"
        notes.mkdir()
        (notes / "keep.txt").write_text("keep", encoding="utf-8")
        with mock.patch.object(captcha, "_DEBUG_KEEP_SESSIONS", 2), \
             mock.patch.object(nine, "solve", return_value=("validate", "passed")):
            # 并发求解不能共用目录，也不能在另一请求写图时清掉它。
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: captcha._solve_local({}, {}, "gt", "ch", None), range(6)))
        self.assertTrue(all(results))
        self.assertEqual(len(list(self.root.glob("session-*/summary.json"))), 2)
        self.assertTrue(legacy.exists())
        self.assertEqual((notes / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_expired_completed_sessions_are_cleaned_but_running_and_notes_are_kept(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        protected = {}
        for index, status in enumerate(("failed", "success", "running", "failed")):
            folder = self.root / f"session-20261101T010101000000Z-{index:012x}"
            folder.mkdir()
            (folder / "summary.json").write_text(json.dumps({
                "kind": captcha._DEBUG_KIND, "version": 1, "status": status,
                "started_at": old, "finished_at": old,
            }), encoding="utf-8")
            (folder / "attempt1-010101-grid.png").write_bytes(b"fake image")
            protected[index] = folder
        (protected[3] / "user-notes.txt").write_text("keep", encoding="utf-8")
        with mock.patch.object(nine, "solve", return_value=("validate", "passed")):
            self.assertIsNotNone(captcha._solve_local({}, {}, "gt", "challenge", None))
        self.assertFalse(protected[0].exists())
        self.assertFalse(protected[1].exists())
        self.assertTrue(protected[2].exists(), "不能清掉仍在写图的请求")
        self.assertTrue((protected[3] / "user-notes.txt").exists())
        self.assertTrue((protected[3] / "attempt1-010101-grid.png").exists())

    def test_broken_summary_does_not_discard_a_successful_result(self) -> None:
        def solve(gt, challenge, **kwargs):
            (kwargs["debug_dir"] / "summary.json").write_text("[]", encoding="utf-8")
            return "validate", "passed"

        messages = []
        with mock.patch.object(nine, "solve", side_effect=solve):
            result = captcha._solve_local({}, {}, "gt", "challenge", messages.append)
        self.assertIsNotNone(result)
        self.assertTrue(any("摘要或历史清理失败" in message for message in messages))

    def test_summary_write_failure_does_not_discard_a_successful_result(self) -> None:
        write_summary = captcha._write_debug_summary
        calls = 0

        def write(folder, summary):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise PermissionError("fake disk error")
            write_summary(folder, summary)

        messages = []
        with mock.patch.object(captcha, "_write_debug_summary", side_effect=write), \
             mock.patch.object(nine, "solve", return_value=("validate", "passed")):
            result = captcha._solve_local({}, {}, "gt", "challenge", messages.append)
        self.assertIsNotNone(result)
        self.assertTrue(any("不影响识别" in message for message in messages))

    def test_active_request_survives_other_requests_history_cleanup(self) -> None:
        def solve(gt, challenge, **kwargs):
            folder = kwargs["debug_dir"]
            marker = folder / "attempt1-010101-grid.png"
            marker.write_bytes(b"active evidence")
            if challenge == "outer":
                self.assertIsNotNone(captcha._solve_local({}, {}, "gt", "inner", None))
                self.assertEqual(marker.read_bytes(), b"active evidence")
                self.assertEqual(json.loads((folder / "summary.json").read_text(encoding="utf-8"))["status"], "running")
            return "validate", "passed"

        with mock.patch.object(captcha, "_DEBUG_KEEP_SESSIONS", 1), \
             mock.patch.object(nine, "solve", side_effect=solve):
            self.assertIsNotNone(captcha._solve_local({}, {}, "gt", "outer", None))
        self.assertEqual(len(list(self.root.glob("session-*/summary.json"))), 1)

    def test_timeout_then_image_errors_matches_historical_pattern(self) -> None:
        """复现前三次等待超时、后两次图片解码失败；不据此猜测实际图片内容。"""
        picture = Image.new("RGB", (344, 384), "white")
        meta = {
            "imgSrc": "https://example.invalid/fake-image",
            "items": [{"x": 0, "y": 0, "w": 10, "h": 10}] * 9,
            "confirm": {"x": 0, "y": 0, "w": 10, "h": 10},
        }
        page = mock.Mock()
        page.evaluate.side_effect = lambda script: json.dumps(meta) if script == nine.PANEL_JS else 1

        def wait_for(script, **kwargs):
            if kwargs.get("timeout") == 12:
                raise TimeoutError("fake callback timeout")
            return True

        page.wait_for.side_effect = wait_for
        browser = mock.Mock()
        browser.start.return_value = page
        matcher = mock.Mock()
        matcher.pick.return_value = ([0], [0.8] * 9)
        messages = []
        error = RuntimeError("九宫格图片获取失败: cannot identify image file <fake BytesIO>")
        with mock.patch.object(captcha, "_local_matcher", return_value=matcher), \
             mock.patch.object(nine, "Browser", return_value=browser), \
             mock.patch.object(nine.time, "sleep"), \
             mock.patch.object(nine, "fetch_image", side_effect=[picture, picture, picture, error, error]):
            result = captcha._solve_local({}, {}, "gt", "challenge", messages.append)
        self.assertIsNone(result)
        self.assertFalse(any("答案被拒绝" in message for message in messages))
        self.assertEqual(sum("12 秒" in message for message in messages), 3)
        summary = json.loads(next(self.root.glob("session-*/summary.json")).read_text(encoding="utf-8"))
        self.assertEqual(summary["counts"]["callback_timeout"], 3)
        self.assertEqual(summary["counts"]["image_fetch_error"], 2)
        self.assertEqual(summary["status"], "failed")
        browser.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
