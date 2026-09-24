// 「网页里能改的配置项」必须 render ↔ collect 成对，否则保存即丢失。
//
// 这是本项目踩过多次的坑（MEMORY.md 第 2 条）：
// 新增控件时忘了在 collectConfig() 里采集，改完保存就没了。
// 这里对 account_gap（账号间隔）做静态配对检查。

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const appJs = fs.readFileSync(path.join(root, "miyouqian", "webui", "app.js"), "utf8");
const indexHtml = fs.readFileSync(path.join(root, "miyouqian", "webui", "index.html"), "utf8");

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

console.log("account_gap 控件配对");

const controls = ["accountGapEnable", "accountGapMin", "accountGapMax"];

check("index.html 里有这三个控件", () => {
  for (const id of controls) {
    assert.match(indexHtml, new RegExp(`id="${id}"`), `index.html 缺少 #${id}`);
  }
});

check("控件带 data-autosave（改了才会自动保存）", () => {
  for (const id of controls) {
    const tag = indexHtml.slice(indexHtml.indexOf(`id="${id}"`));
    assert.match(tag.slice(0, 400), /data-autosave/, `#${id} 没有 data-autosave`);
  }
});

check("renderConfig() 把配置写进控件", () => {
  for (const id of controls) {
    assert.match(appJs, new RegExp(`\\$\\("${id}"\\)`), `app.js 没有引用 #${id}`);
  }
  assert.match(appJs, /const gap = config\.account_gap \|\| \{\}/, "renderConfig 没有读 config.account_gap");
});

check("collectConfig() 显式把控件读回配置（漏了就保存即丢失）", () => {
  assert.match(
    appJs,
    /config\.account_gap = \{[\s\S]{0,200}?min_minutes: Number\(\$\("accountGapMin"\)/,
    "collectConfig 没有采集 account_gap.min_minutes",
  );
  assert.match(
    appJs,
    /max_minutes: Number\(\$\("accountGapMax"\)/,
    "collectConfig 没有采集 account_gap.max_minutes",
  );
  assert.match(
    appJs,
    /enable: \$\("accountGapEnable"\)\.checked/,
    "collectConfig 没有采集 account_gap.enable",
  );
});

check("折叠态摘要会显示当前账号间隔", () => {
  assert.match(indexHtml, /id="scheduleSummary"/, "index.html 缺少 #scheduleSummary");
  assert.match(appJs, /function renderScheduleSummary\(\)/, "app.js 缺少 renderScheduleSummary()");
  assert.match(appJs, /renderScheduleSummary\(\)/, "renderScheduleSummary 没有被调用");
});

check("服务端字段名与前端一致", () => {
  for (const key of ["min_minutes", "max_minutes"]) {
    assert.match(appJs, new RegExp(key), `app.js 没有用到 ${key}`);
  }
});

if (failures) {
  console.error(`\n${failures} 个检查未通过`);
  process.exit(1);
}
console.log("\n全部通过");
