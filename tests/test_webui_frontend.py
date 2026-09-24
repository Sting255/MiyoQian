# -*- coding: utf-8 -*-
"""前端检查的统一入口。

Python 侧的 `unittest discover` 只负责把 `tests/*.test.mjs` 都调起来；
真正的断言写在那些 .mjs 里（直接抽 app.js 的真实源码来跑，不是另写一份等价逻辑）。
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def run_node(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


@unittest.skipUnless(NODE, "未安装 node，跳过前端检查")
class WebuiFrontendTest(unittest.TestCase):
    def test_all_webui_scripts(self) -> None:
        scripts = sorted((ROOT / "tests").glob("*.test.mjs"))
        self.assertTrue(scripts, "没有找到任何前端检查脚本")
        for script in scripts:
            with self.subTest(script=script.name):
                result = run_node([str(script)])
                if result.returncode != 0:
                    self.fail(f"{script.name} 未通过:\n{result.stdout}\n{result.stderr}")

    def test_app_js_syntax(self) -> None:
        result = run_node(["--check", str(ROOT / "miyouqian" / "webui" / "app.js")])
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
