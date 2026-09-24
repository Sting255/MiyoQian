// 「最多抢几秒 / 隔几秒发一次」这两个设置的说明必须说人话、并且会跟着数值更新。
//
// 背景：用户直接说「我都看不懂」——光给两个数字没有解释。
// 这里守住两件事：
//   1. shopRetryAttemptRange() 的估算不胡说（边界、单调性、不会返回 0 次或负数）；
//   2. 界面上确实有解释文字 + 动态提示，而且提示被 render/input 触发刷新。

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const appJs = fs.readFileSync(path.join(root, "miyouqian", "webui", "app.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(root, "miyouqian", "webui", "index.html"), "utf8");

function extractFunction(name) {
  const start = appJs.search(new RegExp(`function\\s+${name}\\s*\\(`));
  assert.ok(start >= 0, `app.js 里找不到 ${name}()`);
  const open = appJs.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < appJs.length; i += 1) {
    if (appJs[i] === "{") depth += 1;
    else if (appJs[i] === "}") {
      depth -= 1;
      if (depth === 0) return appJs.slice(start, i + 1);
    }
  }
  throw new Error(`无法解析 ${name}() 的函数体`);
}

const range = new Function(
  `${extractFunction("shopRetryAttemptRange")}
   return shopRetryAttemptRange;`,
)();

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

console.log("自动兑换设置的可读性");

check("窗口为 0 或无效时表示「不重试」", () => {
  assert.equal(range(0, 0.3), null);
  assert.equal(range("0", 0.3), null);
  assert.equal(range(-5, 0.3), null);
  assert.equal(range("abc", 0.3), null);
  assert.equal(range(undefined, 0.3), null);
});

check("正常取值给出合理的次数范围", () => {
  const r = range(5.5, 0.3);
  assert.ok(r, "应该返回估算");
  for (const key of ["typical", "fewest", "most"]) {
    assert.ok(Number.isInteger(r[key]) && r[key] >= 1, `${key} 应为 >=1 的整数，实际 ${r[key]}`);
  }
  assert.ok(r.fewest <= r.typical && r.typical <= r.most,
    `范围顺序不对: fewest=${r.fewest} typical=${r.typical} most=${r.most}`);
  assert.equal(r.maxGap, 0.3, "maxGap 应该就是填进去的那个数");
  assert.ok(r.typical >= 5 && r.typical <= 40, `5.5 秒/0.3 秒的估算次数不合理: ${r.typical}`);
});

check("填的数就是间隔上限（不是 ±0.5 那种）", () => {
  // 平均间隔 = 上限/2 → 上限翻倍，次数应明显减少，但不会归零
  const tight = range(10, 0.1).typical;
  const loose = range(10, 1).typical;
  assert.ok(tight > loose, `上限变大次数没变少: ${tight} -> ${loose}`);
  // 上限取最大时（每次都等满）就是 fewest
  assert.equal(range(10, 0.5).fewest, Math.max(1, Math.floor(10 / 0.7)));
});

check("窗口越长次数越多；间隔越大次数越少（单调性）", () => {
  const short = range(5, 0.3).typical;
  const long = range(20, 0.3).typical;
  assert.ok(long > short, `窗口变长次数没变多: ${short} -> ${long}`);
  const dense = range(20, 0.2).typical;
  const sparse = range(20, 2).typical;
  assert.ok(sparse < dense, `间隔变大次数没变少: ${dense} -> ${sparse}`);
});

check("间隔为 0 / 负数 / 非数字时用兜底值，不会算出离谱结果", () => {
  for (const bad of [0, -1, "abc", null, undefined]) {
    const r = range(10, bad);
    assert.ok(r && r.typical >= 1 && r.typical <= 100, `间隔=${bad} 时估算异常: ${JSON.stringify(r)}`);
  }
});

check("界面里有解释文字，而不是只给两个数字", () => {
  assert.match(indexHtml, /最多抢几秒/, "缺少「最多抢几秒」标签");
  assert.match(indexHtml, /最多隔几秒发一次/, "缺少「最多隔几秒发一次」标签");
  assert.match(indexHtml, /id="shopRetryHint"/, "缺少动态提示节点 #shopRetryHint");
  // 旧的、看不懂的措辞应当已经消失
  assert.doesNotMatch(indexHtml, /到点后重试秒数/, "旧的「到点后重试秒数」还留着");
  assert.doesNotMatch(indexHtml, /失败重试间隔<\/span>/, "旧的「失败重试间隔」还留着");
  assert.match(indexHtml, /抢到就立刻停/, "解释里应说明抢到就停");
  assert.match(indexHtml, /最长不超过/, "解释里应说明填的数是间隔上限");
});

check("提示会随输入更新（render + change + input 都要触发）", () => {
  assert.match(appJs, /function renderShopRetryHint\(\)/, "缺少 renderShopRetryHint()");
  const renderShopConfig = extractFunction("renderShopConfig");
  assert.match(
    renderShopConfig,
    /renderShopRetryHint\(\)/,
    "renderShopConfig 里没有调用 renderShopRetryHint()，切换/刷新后提示不会更新",
  );
  const calls = (appJs.match(/renderShopRetryHint\(\)/g) || []).length;
  assert.ok(calls >= 4, `renderShopRetryHint 的调用点太少（${calls}），改数字时提示不会跟着变`);
  assert.match(appJs, /开抢后最多坚持/, "提示文案缺少人话描述");
  assert.match(appJs, /0~/, "提示应说明间隔是从 0 到上限之间随机");
});

if (failures) {
  console.error(`\n${failures} 个检查未通过`);
  process.exit(1);
}
console.log("\n全部通过");
