# -*- coding: utf-8 -*-
"""测试夹具。

硬规则：所有测试都必须在临时目录里读写配置。
`run_tasks()` / `set_config()` 结束时会调 `save_config()`，
直接拿真实 `config.yaml` 测会把内存里的改动写回用户配置（历史上踩过两次）。
"""

from __future__ import annotations

import copy
import pathlib
import shutil
import unittest
import uuid

from miyouqian.core.config import DEFAULT_CONFIG, save_config

# 测试目录放在仓库内：DSH 的文件沙箱允许在工作区建子目录，
# 但系统 %TEMP% 下的某些目录会拒绝 mkdir（save_config 要建 data/ 子目录）。
ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = ROOT / ".tmp-tests"

# 测试里固定设备指纹，避免 normalize_config 每次随机挑机型导致断言不稳定
TEST_DEVICE = {
    "id": "TEST-DEVICE-ID",
    "fp": "0123456789abc",
    "name": "Test Phone",
    "model": "TEST-0001",
}


def base_config() -> dict:
    """一份干净、可预测、默认不联网、**不会真等**的配置。"""
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["device"].update(TEST_DEVICE)
    config["features"] = {"game_checkin": True, "cloud_game_checkin": False, "bbs_tasks": False}
    config["accounts"] = []
    config["schedule"]["enable"] = False
    config["schedule"]["run_on_start"] = False
    # 账号间等待默认关掉：默认值是 60~120 分钟，跑 run_tasks 的测试会真的睡 1~2 小时。
    # 要测这个行为的用例自己设 account_gap。
    config["account_gap"] = {"enable": False, "min_minutes": 0, "max_minutes": 0}
    config["ip_guard"]["enable"] = False
    config["captcha"]["enable"] = False
    config["push"]["channels"] = []
    config["shop_exchange"]["enable"] = False
    config["shop_exchange"]["plans"] = []
    config["web"] = {"host": "127.0.0.1", "port": 5890, "password": ""}
    return config


def make_case_dir() -> pathlib.Path:
    """建一个唯一的用例目录。

    这里刻意不使用 `tempfile.mkdtemp()`：它用 `mode=0o700` 建目录，
    而文件沙箱会拒绝这种不可被其他用户访问的目录——建出来的目录
    连 `os.listdir()` 都抛 PermissionError，导致 `save_config()` 里
    「创建 data/ 子目录」这一步必然失败。
    用默认权限的 `Path.mkdir()` 没有这个问题。

    另注：在这个沙箱里用例目录用完可能删不掉（`shutil.rmtree` 被拒绝），
    `.tmp-tests/` 已经在 .gitignore 里，残留的无非是假凭证，可以直接忽略。
    """
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        candidate = TEST_TMP_ROOT / f"case-{uuid.uuid4().hex[:10]}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError(f"无法在 {TEST_TMP_ROOT} 下创建测试目录")


class IsolatedConfigTest(unittest.TestCase):
    """基类：在临时目录里准备一份配置（+ 凭证），用完自动清理。"""

    def setUp(self) -> None:
        self.dir = make_case_dir()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.config_path = self.dir / "config.yaml"

    @property
    def credentials_path(self) -> pathlib.Path:
        return self.dir / "data" / "credentials.yaml"

    def write_config(self, config: dict) -> pathlib.Path:
        """落盘配置；账号凭证会被 save_config 自动拆到 data/credentials.yaml。"""
        save_config(self.config_path, config)
        return self.config_path

    def write_config_yaml(self, text: str) -> pathlib.Path:
        """直接写原始 YAML 文本，用来测畸形/手改配置。"""
        self.config_path.write_text(text, encoding="utf-8")
        return self.config_path
