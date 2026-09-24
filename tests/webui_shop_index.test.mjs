// P2：商品卡片必须按「在 shopGoods 里的真实下标」定位，而不是过滤后的下标。
//
// 现状：renderShopGoods 用 filtered.slice(...) 得到 pageGoods，
// 却把 start + index（过滤/分页后的位置）写进 data-shop-add / data-shop-now；
// 点击时 bindShopGoodsEvents 回查的是未过滤的 shopGoods[...]。
// 默认筛选是「全部」时两者恰好相等，所以一直没被发现；
// 一旦切到「已售罄 / 可兑换」，就会兑换到错误的商品。
//
// 这个测试直接从 app.js 里抽出真实的 shopGoodCardIndex 源码来跑，
// 不是另写一份等价逻辑。

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const appJsPath = path.join(here, "..", "miyouqian", "webui", "app.js");
const src = fs.readFileSync(appJsPath, "utf8");

function extractFunction(name) {
  assert.match(
    src,
    new RegExp(`function\\s+${name}\\s*\\(`),
    `app.js 里找不到 ${name}()`,
  );
  const start = src.search(new RegExp(`function\\s+${name}\\s*\\(`));
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

const fnSource = extractFunction("shopGoodCardIndex");
const factory = new Function(
  "shopGoods",
  "good",
  `"use strict";\n${fnSource}\nreturn shopGoodCardIndex(good);`,
);

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

const shopGoods = [
  { goods_id: "1", goods_name: "A" },
  { goods_id: "2", goods_name: "B" },
  { goods_id: "3", goods_name: "C" },
  { goods_id: "4", goods_name: "D" },
  { goods_id: "5", goods_name: "E" },
];

console.log("P2 商品下标定位");

check("全部商品时下标就是自身位置", () => {
  const mapped = shopGoods.map((good) => factory(shopGoods, good));
  assert.deepEqual(mapped, [0, 1, 2, 3, 4]);
});

check("过滤子集必须映射回 shopGoods 的真实下标", () => {
  const filtered = shopGoods.filter((good) => Number(good.goods_id) % 2 === 0);
  assert.equal(filtered.length, 2);
  const mapped = filtered.map((good) => factory(shopGoods, good));
  assert.deepEqual(mapped, [1, 3], `过滤后下标错位：${JSON.stringify(mapped)}`);
  // 现在踩的坑：用过滤后的位置去查未过滤的数组
  assert.notDeepEqual(mapped, [0, 1]);
});

check("分页后也必须映射回真实下标", () => {
  const pageGoods = shopGoods.slice(2, 4);
  const mapped = pageGoods.map((good) => factory(shopGoods, good));
  assert.deepEqual(mapped, [2, 3]);
});

check("渲染调用点不再使用 start + index 当商品标识", () => {
  assert.doesNotMatch(
    src,
    /shopGoodCard\(good,\s*start\s*\+\s*index\)/,
    "renderShopGoods 仍在把过滤/分页后的下标写进卡片",
  );
  assert.match(
    src,
    /shopGoodCard\(good,\s*shopGoodCardIndex\(good\)\)/,
    "renderShopGoods 没有改用 shopGoodCardIndex",
  );
});

const guardSource = extractFunction("shopGoodByCardIndex");
const guardFactory = new Function(
  "shopGoods",
  "rawIndex",
  `"use strict";\n${guardSource}\nreturn shopGoodByCardIndex(rawIndex);`,
);

check("越界/非法下标返回 null，不会拿错商品", () => {
  assert.equal(guardFactory(shopGoods, 0), shopGoods[0]);
  assert.equal(guardFactory(shopGoods, 4), shopGoods[4]);
  assert.equal(guardFactory(shopGoods, "2"), shopGoods[2]);
  assert.equal(guardFactory(shopGoods, -1), null);
  assert.equal(guardFactory(shopGoods, 5), null);
  assert.equal(guardFactory(shopGoods, "abc"), null);
  assert.equal(guardFactory(shopGoods, undefined), null);
  assert.equal(guardFactory(shopGoods, 1.5), null);
});

check("点击处理走 guard，不再直接 shopGoods[下标]", () => {
  assert.doesNotMatch(
    src,
    /shopGoods\[Number\(button\.dataset\.shop(Add|Now)\)\]/,
    "点击处理仍在直接按下标取商品",
  );
  assert.match(src, /shopGoodByCardIndex\(button\.dataset\.shopAdd\)/);
  assert.match(src, /shopGoodByCardIndex\(button\.dataset\.shopNow\)/);
});

if (failures) {
  console.error(`\n${failures} 个检查未通过`);
  process.exit(1);
}
console.log("\n全部通过");
