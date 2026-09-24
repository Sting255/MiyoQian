# -*- coding: utf-8 -*-
"""极验三代「九宫格点选」本地求解。

不逆向 w 参数的加密与行为轨迹，而是用本机 Chrome 加载极验自己的 JS，
由极验前端生成校验数据，我们只负责用视觉模型判断该点哪几格。
仅供学习与个人使用。
"""

from __future__ import annotations

from . import nine
from .nine import TileMatcher, ensure_model, model_path, solve

__all__ = ["nine", "TileMatcher", "ensure_model", "model_path", "solve"]
