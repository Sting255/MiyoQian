// P4 的前端侧验证：凭证被脱敏成占位符后，界面行为必须和拿到真值时完全一致。
//
// 关键风险是「保存一次就丢配置」：如果某个推送渠道因为 token 变短/变化
// 被判成「没配置」，shouldSavePushChannel 会把它丢掉，用户下次保存时
// 这个渠道就消失了。这里直接抽 app.js 的真实函数来跑，
// 并且断言 app.js 里根本没有针对占位符的特殊分支（脱敏对前端应当完全透明）。

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const appJsPath = path.join(here, "..", "miyouqian", "webui", "app.js");
const src = fs.readFileSync(appJsPath, "utf8");

const MASK = "__MYQ_MASKED__";

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

const body = [
  "pushChannelFieldNames",
  "hasPushChannelConfig",
  "cleanPushChannel",
  "shouldSavePushChannel",
]
  .map(extractFunction)
  .join("\n");

const api = new Function(
  `${body}
   return { hasPushChannelConfig, cleanPushChannel, shouldSavePushChannel };`,
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

console.log("P4 脱敏对前端透明");

// 注意：这里必须是假值。这个夹具曾经抄过真实的 pushplus token，差点跟着仓库公开。
const realChannel = { provider: "pushplus", enable: false, token: "0123456789abcdef0123456789abcdef", topic: "" };
const maskedChannel = { provider: "pushplus", enable: false, token: MASK, topic: "" };
const emptyChannel = { provider: "pushplus", enable: false, token: "", topic: "" };

check("已配置的推送渠道（未启用）脱敏后仍被认为已配置", () => {
  assert.equal(api.hasPushChannelConfig(realChannel), true);
  assert.equal(api.hasPushChannelConfig(maskedChannel), true);
});

check("脱敏后保存时不会被丢弃", () => {
  assert.equal(api.shouldSavePushChannel(realChannel), true);
  assert.equal(api.shouldSavePushChannel(maskedChannel), true, "渠道会被静默删除");
});

check("cleanPushChannel 原样保留占位符，交给服务端还原", () => {
  assert.equal(api.cleanPushChannel(realChannel).token, realChannel.token);
  assert.equal(api.cleanPushChannel(maskedChannel).token, MASK);
});

check("空渠道的行为没有变化（仍然不显示、不保存）", () => {
  assert.equal(api.hasPushChannelConfig(emptyChannel), false);
  assert.equal(api.shouldSavePushChannel(emptyChannel), false);
});

check("带 secret / webhook / smtp_password 的渠道同样成立", () => {
  for (const provider of ["dingrobot", "feishubot", "email", "telegram", "qq"]) {
    const masked = { provider, enable: false };
    const real = { provider, enable: false };
    for (const [key, value] of Object.entries({
      token: MASK, webhook: MASK, secret: MASK, access_token: MASK,
      smtp_password: MASK, push_url: "http://127.0.0.1:5700", send_id: "1", msg_type: "private",
      chat_id: "1", smtp_host: "smtp.example.com", smtp_user: "u", mail_from: "a@b.c", mail_to: "d@e.f",
    })) {
      masked[key] = value;
      real[key] = value === MASK ? "REAL-VALUE" : value;
    }
    assert.equal(
      api.shouldSavePushChannel(masked),
      api.shouldSavePushChannel(real),
      `${provider} 在脱敏前后的保存判定不一致`,
    );
  }
});

check("app.js 里没有针对占位符的特殊分支（脱敏应当完全透明）", () => {
  assert.doesNotMatch(src, /__MYQ_MASKED__/, "前端不该知道占位符的具体内容");
});

if (failures) {
  console.error(`\n${failures} 个检查未通过`);
  process.exit(1);
}
console.log("\n全部通过");
