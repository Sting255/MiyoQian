# -*- coding: utf-8 -*-
"""P8：配置段写成 null（YAML 里把键留空）不能让加载直接崩。

`accounts:` / `device:` / `captcha:` 这种留空写法在 YAML 里得到 None，
而 load_config 的 merge_dict 会把 None 原样盖到默认值上，
normalize_config 里的 setdefault 又因为「键已存在」而返回 None，
最终在 enumerate(None) / None.get(...) 处崩掉，用户只看到一句 traceback。
"""

from __future__ import annotations

import unittest

from miyouqian.core.config import DEFAULT_CONFIG, load_config
from tests.support import IsolatedConfigTest


class NullSectionTest(IsolatedConfigTest):
    def test_every_top_level_section_tolerates_null(self) -> None:
        for key, default in DEFAULT_CONFIG.items():
            with self.subTest(section=key):
                path = self.write_config_yaml(f"enable: true\n{key}:\n")
                config = load_config(path)  # 不能抛异常
                self.assertIsNotNone(config.get(key), f"{key}: null 没有被兜底")
                if isinstance(default, bool):
                    self.assertIsInstance(config[key], bool, f"{key} 应为布尔")
                else:
                    self.assertIsInstance(
                        config[key], type(default), f"{key} 应为 {type(default).__name__}"
                    )

    def test_accounts_null_becomes_empty_list(self) -> None:
        path = self.write_config_yaml("enable: true\naccounts:\n")
        config = load_config(path)
        self.assertEqual(config["accounts"], [])

    def test_enable_null_is_not_treated_as_disabled(self) -> None:
        """`enable:` 留空时不能静默跳过全部签到。"""
        path = self.write_config_yaml("enable:\naccounts:\n  - name: A\n")
        config = load_config(path)
        self.assertTrue(config["enable"], "enable 为 null 时被判成了关闭，任务会整天不跑")

    def test_null_section_then_save_round_trips(self) -> None:
        """兜底之后必须能正常保存并再次加载（save_config 会建 data/ 子目录）。"""
        from miyouqian.core.config import save_config

        path = self.write_config_yaml("enable: true\ndevice:\ncaptcha:\npush:\n")
        config = load_config(path)
        save_config(path, config)
        again = load_config(path)
        self.assertIsInstance(again["device"], dict)
        self.assertIsInstance(again["captcha"], dict)
        self.assertIsInstance(again["push"], dict)
        self.assertEqual(again["device"]["name"], config["device"]["name"])

    def test_wrong_type_section_does_not_crash(self) -> None:
        path = self.write_config_yaml('enable: true\ngames: "genshin"\nbbs: 3\npush: []\n')
        config = load_config(path)
        self.assertIsInstance(config["games"], dict)
        self.assertIsInstance(config["bbs"], dict)
        self.assertIsInstance(config["push"], dict)


if __name__ == "__main__":
    unittest.main()
