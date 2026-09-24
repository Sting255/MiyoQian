// 「关掉总开关会清空已选项目」的回归测试。
//
// 事故经过（2026-09-23）：用户在页面上操作「云游戏签到」总开关，
// config.yaml 里的 cloud_games.enabled（全局 + 账号级）被静默清成 []。
// 原因是 collectConfig() 纯靠 DOM 的 :checked 重建列表：
// 总开关关着时 updateTaskDependencyState() 会把 chips 全部 disabled，
// 而面板被重新渲染过之后 checked 也不复存在 —— 于是「一个都没勾」
// 被当成「用户全部取消」，保存后选择就丢了。
//
// 这里直接抽 app.js 的 collectChipSelection 真实源码来跑。

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, "..", "miyouqian", "webui", "app.js"), "utf8");

function extractFunction(name) {
  const start = src.search(new RegExp(`function\\s+${name}\\s*\\(`));
  assert.ok(start >= 0, `app.js 里找不到 ${name}()`);
  const open = src.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === "{") depth += 1;
    else if (src[i] === "}") {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`无法解析 ${name}() 的函数体`);
}

const collect = new Function(
  `${extractFunction("collectChipSelection")}
   return collectChipSelection;`,
)();

const keyOf = (input) => input.dataset.cloudGame;
const chip = (key, { checked = false, disabled = false } = {}) => ({
  checked,
  disabled,
  dataset: { cloudGame: key },
});
const keys = (...items) => items.map(([k, opts]) => chip(k, opts));

let failures = 0;
function check(label, fn) {
  try {
    fn();
    console.log(`  ok   ${label}`);
  } catch (error) {
    failures += 1;
    console.log(`  FAIL ${label}\n       ${error.message}`);
  }
}

console.log("关掉总开关不能清空已选项目");

const SAVED = ["genshin", "zzz"];

check("面板没渲染出来时保持原值", () => {
  assert.deepEqual(collect([], keyOf, SAVED), SAVED);
  assert.deepEqual(collect([], keyOf, undefined), []);
});

check("chips 全部被禁用且没勾选时保持原值（就是这次事故）", () => {
  const chips = keys(["genshin", { disabled: true }], ["zzz", { disabled: true }]);
  assert.deepEqual(collect(chips, keyOf, SAVED), SAVED, "已选云游戏被清空了");
});

check("（对照）旧的 :checked 写法在同一份 DOM 上确实会清空", () => {
  const chips = keys(["genshin", { disabled: true }], ["zzz", { disabled: true }]);
  const oldWay = chips.filter((input) => input.checked).map(keyOf);
  assert.deepEqual(oldWay, [], "旧写法本应得到空数组——若不然说明这组用例没复现事故");
  assert.deepEqual(collect(chips, keyOf, SAVED), SAVED);
});

check("面板可交互、用户确实全部取消 → 允许清空", () => {
  const chips = keys(["genshin"], ["zzz"]);
  assert.deepEqual(collect(chips, keyOf, SAVED), []);
});

check("正常勾选时按 DOM 取值", () => {
  const chips = keys(["genshin", { checked: true }], ["zzz", { checked: true }], ["starrail", { disabled: true }]);
  assert.deepEqual(collect(chips, keyOf, []), ["genshin", "zzz"]);
});

check("只勾了一个就用那一个", () => {
  const chips = keys(["genshin", { checked: true }], ["zzz"]);
  assert.deepEqual(collect(chips, keyOf, SAVED), ["genshin"]);
});

check("返回的是新数组，不会污染传入的 fallback", () => {
  const fallback = [...SAVED];
  const out = collect([], keyOf, fallback);
  out.push("starrail");
  assert.deepEqual(fallback, SAVED, "fallback 被就地改写了");
});

check("collectConfig 不再直接用 :checked 重建云游戏/游戏列表", () => {
  assert.doesNotMatch(
    src,
    /querySelectorAll\("\[data-cloud-game\]:checked"\)/,
    "全局云游戏列表仍在只用 :checked 重建",
  );
  assert.doesNotMatch(
    src,
    /querySelectorAll\("\[data-game\]:checked"\)/,
    "全局游戏列表仍在只用 :checked 重建",
  );
  assert.doesNotMatch(
    src,
    /querySelectorAll\("\[data-account-cloud-game\]:checked"\)/,
    "账号级云游戏列表仍在只用 :checked 重建",
  );
  assert.match(src, /collectChipSelection\(/);
});

if (failures) {
  console.error(`\n${failures} 个检查未通过`);
  process.exit(1);
}
console.log("\n全部通过");
