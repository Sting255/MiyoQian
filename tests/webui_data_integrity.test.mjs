// 直接运行 app.js 的真实函数，假 DOM / 假 API / 可控响应，不访问服务。
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync(new URL("../miyouqian/webui/app.js", import.meta.url), "utf8");
function extract(name) {
  const start = src.search(new RegExp(`^(?:async )?function ${name}\\(`, "m"));
  assert.ok(start >= 0, `缺少函数 ${name}`);
  const rest = src.slice(start + 1);
  const next = rest.search(/^(?:async )?function \w+\(/m);
  return src.slice(start, next < 0 ? src.length : start + 1 + next);
}
const clone = (value) => JSON.parse(JSON.stringify(value));
const tick = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const queueFunctions = ["hasPendingConfigEdits", "enqueueConfigSave", "autoSaveConfig", "saveConfig", "scheduleAutoSave"];
function context(names = queueFunctions, overrides = {}) {
  const timers = new Map();
  let timerId = 0;
  const c = vm.createContext({
    config: { accounts: [], schedule: { catch_up: true }, shop_exchange: { plans: [] } },
    configSaveQueue: Promise.resolve(), configEditVersion: 0, configSavedVersion: 0,
    pendingConfigSaves: 0, isSavingConfig: false, autoSaveTimer: null, configLoadSeq: 0,
    showToast() {}, loadConfig() {}, collectConfig() {}, validateUniqueAccountUids() {},
    serverConfig() { return clone(c.config); },
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    flushTimers() { const pending = [...timers.values()]; timers.clear(); pending.forEach((fn) => fn()); },
    document: { querySelectorAll: () => [], activeElement: null },
    $: () => ({ checked: false, value: "", querySelector: () => null }),
    ...overrides,
  });
  vm.runInContext(names.map(extract).join("\n"), c);
  return c;
}
let passed = 0;
async function check(label, fn) {
  await fn();
  passed += 1;
  console.log(`  ok   ${label}`);
}

await check("两个快速保存串行提交，最终服务端保留两项新值", async () => {
  const first = deferred(), requests = [];
  let server;
  const c = context(queueFunctions, { api: async (_url, options) => {
    const payload = JSON.parse(options.body);
    requests.push(payload);
    if (requests.length === 1) await first.promise;
    server = payload;
    return { ok: true, config: payload };
  } });
  c.config.schedule.time = "10:00";
  const a = c.autoSaveConfig();
  await tick();
  c.config.schedule.jitter_minutes = 12;
  const b = c.autoSaveConfig();
  await tick();
  assert.equal(requests.length, 1, "并发发出了第二个 POST");
  first.resolve();
  await Promise.all([a, b]);
  assert.equal(server.schedule.time, "10:00");
  assert.equal(server.schedule.jitter_minutes, 12);
  assert.equal(c.isSavingConfig, false);
  assert.equal(c.hasPendingConfigEdits(), false);
});

await check("保存尚未返回时发生的新编辑仍会触发自动保存", async () => {
  const first = deferred(), requests = [];
  const c = context(queueFunctions, { api: async (_url, options) => {
    requests.push(JSON.parse(options.body));
    if (requests.length === 1) await first.promise;
    return { ok: true };
  } });
  const a = c.autoSaveConfig();
  await tick();
  c.config.schedule.time = "11:30";
  c.scheduleAutoSave();
  c.flushTimers();
  await tick();
  assert.equal(requests.length, 1);
  first.resolve();
  await a;
  await c.configSaveQueue;
  assert.equal(requests.length, 2);
  assert.equal(requests[1].schedule.time, "11:30");
});

await check("某次保存失败不会堵塞后续编辑，失败期间保留 dirty 状态", async () => {
  let calls = 0;
  const c = context(queueFunctions, { api: async () => {
    if (++calls === 1) throw new Error("fake offline");
    return { ok: true };
  } });
  await assert.rejects(c.autoSaveConfig(), /fake offline/);
  assert.equal(c.hasPendingConfigEdits(), true);
  await c.autoSaveConfig();
  assert.equal(c.hasPendingConfigEdits(), false);
});

await check("显式保存后的慢 GET 不覆盖期间刚编辑的控件", async () => {
  const get = deferred();
  const c = context([...queueFunctions, "loadConfig"], {
    api: async (_url, options) => options ? { ok: true } : get.promise,
    renderConfig() { throw new Error("不应重绘过时配置"); },
  });
  const saved = c.saveConfig();
  await tick();
  c.config.schedule.time = "23:45";
  c.scheduleAutoSave();
  get.resolve({ accounts: [], schedule: { time: "09:00" } });
  await saved;
  assert.equal(c.config.schedule.time, "23:45");
  assert.equal(c.hasPendingConfigEdits(), true);
});

await check("状态请求返回前已编辑并保存，也不能用旧状态覆盖计划", async () => {
  const status = deferred();
  let renders = 0;
  const c = context([...queueFunctions, "refreshStatus"], {
    api: async (url) => url === "/api/status" ? status.promise : { ok: true },
    shopRequestInFlight: false, lastLoginStatus: "", formatTime: () => "", latestResultText: () => "",
    updateLogs() {}, renderLoginSlot() {}, isEditingShopConfig: () => false,
    renderShopConfig() { renders += 1; },
  });
  const refresh = c.refreshStatus();
  c.config.shop_exchange.plans = [{ plan_id: "new" }];
  await c.autoSaveConfig();
  status.resolve({ shop_exchange: { plans: [{ plan_id: "old" }] } });
  await refresh;
  assert.equal(c.config.shop_exchange.plans[0].plan_id, "new");
  assert.equal(renders, 0);
});

await check("采集普通表单字段保留 schedule.catch_up", () => {
  const c = context(["collectConfig", "collectChipSelection"], {
    renderScheduleSummary() {}, collectShopExchange: () => ({ plans: [] }),
    collectPushChannel() {}, cleanPushChannel() {}, shouldSavePushChannel() {}, collectCaptchaChannel() {},
  });
  c.collectConfig();
  assert.equal(c.config.schedule.catch_up, true);
});

await check("计划按钮先保存，再发送 plan_id 与服务端版本；商品直兑仍可用", async () => {
  const requests = [];
  const c = context([...queueFunctions, "shopPlanAccountIndex", "exchangePlanNow"], {
    refreshStatus: async () => {},
    api: async (url, options) => {
      const body = JSON.parse(options.body);
      requests.push({ url, body });
      if (url === "/api/config") {
        body.shop_exchange.plans[0].plan_version = "server-v2";
        return { ok: true, config: body };
      }
      return { result: { message: "fake done" } };
    },
  });
  c.config.accounts = [{ stuid: "101", cookie: "masked" }];
  c.config.shop_exchange.plans = [{ plan_id: "stable", account_uid: "101" }];
  await c.exchangePlanNow(0);
  assert.deepEqual(requests.map((r) => r.url), ["/api/config", "/api/shop/exchange"]);
  assert.deepEqual(requests[1].body, { plan_id: "stable", plan_version: "server-v2" });
  await c.exchangePlanNow({ account_uid: "101", goods_id: "direct" });
  assert.equal(requests[2].body.plan.goods_id, "direct");
  await assert.rejects(c.exchangePlanNow({ account_index: 0 }), /选择已登录账号/);
});

await check("旧计划要求明确选择 UID，账号重排后仍选原绑定账号", () => {
  const c = context(["shopPlanAccountIndex", "shopPlanRow", "shopPlanStatus"], {
    shopOpenPlans: new Set(), shopRoleDisplay: () => "", shopServerDisplay: () => "",
    accountLabel: (a) => a.stuid, escapeHtml: String, escapeAttr: String,
    timestampToLocalInput: () => "", shopPlanAddressSelectHtml: () => "",
  });
  c.config.accounts = [{ stuid: "202", cookie: "masked" }, { stuid: "101", cookie: "masked" }];
  const legacy = c.shopPlanRow({ goods_id: "gift", account_index: 0, enable: false }, 0);
  assert.match(legacy, /请重新选择并确认账号/);
  assert.match(legacy, /data-shop-plan-now="0"[^>]*disabled/);
  assert.match(legacy, /data-shop-plan-field="enable"[^>]*disabled/);
  assert.equal(c.shopPlanAccountIndex({ account_uid: "101", account_index: 0 }), 1);
  const bound = c.shopPlanRow({ goods_id: "gift", account_uid: "101", plan_id: "stable", plan_version: "v1" }, 0);
  assert.match(bound, /value="101" selected/);
  assert.match(bound, /data-shop-plan-field="plan_id"[^>]*value="stable"/);
});

await check("角色与地址的过期响应不能污染改绑后的计划", async () => {
  for (const kind of ["Role", "Addresses"]) {
    const response = deferred();
    const select = { isConnected: true };
    const c = context(["shopPlanAccountIndex", `refreshShopPlan${kind}`], {
      api: () => response.promise, document: { querySelector: () => select },
      escapeAttr: String,
      shopPlanAddressCache: new Map(), renderShopPlans() { throw new Error("过期响应触发了重绘"); },
    });
    c.config.accounts = [{ stuid: "101", cookie: "masked" }, { stuid: "202", cookie: "masked" }];
    const plan = { plan_id: "stable", account_uid: "101", game_biz: "game", address_id: "", uid: "", type: kind === "Role" ? 2 : 0 };
    c.config.shop_exchange.plans = [plan];
    const pending = c[`refreshShopPlan${kind}`](0);
    plan.account_uid = "202";
    response.resolve({ roles: [{ uid: "wrong-role" }], addresses: [{ id: "wrong-address" }] });
    await pending;
    assert.equal(plan.uid, "");
    assert.equal(plan.address_id, "");
  }
});

await check("保存复制对象并重排后，慢角色响应仍更新原计划且保留新编辑", async () => {
  const response = deferred();
  let saves = 0;
  const c = context(["shopPlanAccountIndex", "refreshShopPlanRole"], {
    api: () => response.promise,
    renderShopPlans() {},
    collectConfig() { c.config.shop_exchange.plans = clone(c.config.shop_exchange.plans); },
    autoSaveConfig: async () => { saves += 1; },
  });
  c.config.accounts = [{ stuid: "101", cookie: "masked" }];
  c.config.shop_exchange.plans = [
    { plan_id: "stable", account_uid: "101", game_biz: "game", uid: "", exchange_at: 1 },
    { plan_id: "other", account_uid: "101", game_biz: "other", uid: "unchanged" },
  ];
  const pending = c.refreshShopPlanRole(0);
  c.config.shop_exchange.plans = clone(c.config.shop_exchange.plans).reverse();
  c.config.shop_exchange.plans[1].exchange_at = 42;
  response.resolve({ roles: [{ uid: "correct-role", region: "server", nickname: "role" }] });
  await pending;
  assert.equal(c.config.shop_exchange.plans[1].uid, "correct-role");
  assert.equal(c.config.shop_exchange.plans[1].exchange_at, 42);
  assert.equal(c.config.shop_exchange.plans[0].uid, "unchanged");
  assert.equal(saves, 1);
});

await check("地址加载中保留旧值，保存复制计划后仍可更新下拉框", async () => {
  const response = deferred();
  const select = { isConnected: true };
  const c = context(["shopPlanAccountIndex", "refreshShopPlanAddresses", "shopPlanAddressSelectHtml", "buildAddressOptions"], {
    api: () => response.promise,
    escapeAttr: String, escapeHtml: String,
    document: { querySelector: () => select },
    shopPlanAddressCache: new Map(),
    collectConfig() { c.config.shop_exchange.plans = clone(c.config.shop_exchange.plans); },
  });
  c.config.accounts = [{ stuid: "101", cookie: "masked" }];
  c.config.shop_exchange.plans = [{ plan_id: "stable", account_uid: "101", address_id: "saved-address", type: 0 }];
  assert.match(c.shopPlanAddressSelectHtml(c.config.shop_exchange.plans[0]), /value="saved-address"/);
  const pending = c.refreshShopPlanAddresses(0);
  assert.match(select.innerHTML, /value="saved-address"/);
  assert.equal(select.disabled, true);
  c.config.shop_exchange.plans = clone(c.config.shop_exchange.plans);
  response.resolve({ addresses: [{ id: "saved-address", name: "my address" }] });
  await pending;
  assert.match(select.innerHTML, /value="saved-address" selected/);
  assert.equal(c.config.shop_exchange.plans[0].address_id, "saved-address");
  assert.equal(select.disabled, false);
});

await check("新增计划全程按 UID 校验，登录检查期间重排账号不会错绑或丢表单编辑", async () => {
  const login = deferred(), urls = [];
  let pendingEdit = false;
  const c = context(["addShopPlan", "buildShopPlan", "checkShopAccountLogin", "shopPlanAccountIndex"], {
    canShopGoodAddPlan: () => true, chooseShopPlanAccount: async () => "101",
    accountLabel: (a) => a.stuid, crypto: { randomUUID: () => "new-plan" },
    renderShopPlans() {}, refreshShopPlanAddresses: async () => {}, saveConfig: async () => {},
    collectConfig() {
      if (pendingEdit) c.config.shop_exchange.plans[0].exchange_at = 42;
    },
    api: async (url) => {
      urls.push(url);
      if (url.startsWith("/api/shop/account-meta?")) return login.promise;
      if (url === "/api/shop/device-fp") return { device_fp: "fake-fp" };
      return { goods_id: "gift", type: 0 };
    },
  });
  c.config.accounts = [{ stuid: "101", cookie: "masked" }, { stuid: "202", cookie: "masked" }];
  c.config.shop_exchange.plans = [{ plan_id: "other", account_uid: "202", goods_id: "other", exchange_at: 1 }];
  const adding = c.addShopPlan({ goods_id: "gift" });
  await tick();
  c.config.accounts.reverse();
  pendingEdit = true;
  login.resolve({});
  await adding;
  assert.equal(urls[0], "/api/shop/account-meta?account_uid=101");
  assert.equal(c.config.shop_exchange.plans[1].account_uid, "101");
  assert.equal(c.config.shop_exchange.plans[1].account_index, 1);
  assert.equal(c.config.shop_exchange.plans[0].exchange_at, 42);
});

await check("构造计划期间删除账号必须拒绝，不能改用当前位置的账号", async () => {
  const fp = deferred();
  const c = context(["buildShopPlan", "shopPlanAccountIndex"], {
    crypto: { randomUUID: () => "new-plan" },
    api: async (url) => url === "/api/shop/device-fp" ? fp.promise : { goods_id: "gift", type: 0 },
  });
  c.config.accounts = [{ stuid: "101", cookie: "masked" }, { stuid: "202", cookie: "masked" }];
  const building = c.buildShopPlan({ goods_id: "gift" }, "101");
  const rejected = assert.rejects(building, /账号/);
  c.config.accounts.shift();
  fp.resolve({ device_fp: "fake-fp" });
  await rejected;
});

await check("两个同时添加的请求返回时重新去重，只保留一条同账号商品计划", async () => {
  const login = deferred();
  let seq = 0;
  const c = context(["addShopPlan", "buildShopPlan", "checkShopAccountLogin", "shopPlanAccountIndex"], {
    canShopGoodAddPlan: () => true, chooseShopPlanAccount: async () => "101",
    accountLabel: (a) => a.stuid, crypto: { randomUUID: () => `new-${++seq}` },
    renderShopPlans() {}, refreshShopPlanAddresses: async () => {}, saveConfig: async () => {},
    api: async (url) => {
      if (url.startsWith("/api/shop/account-meta?")) return login.promise;
      return url === "/api/shop/device-fp" ? { device_fp: "fake-fp" } : { goods_id: "gift", type: 0 };
    },
  });
  c.config.accounts = [{ stuid: "101", cookie: "masked" }];
  const a = c.addShopPlan({ goods_id: "gift" }), b = c.addShopPlan({ goods_id: "gift" });
  await tick();
  login.resolve({});
  await Promise.all([a, b]);
  assert.equal(c.config.shop_exchange.plans.length, 1);
});

await check("两个账号选择框返回稳定 UID，确认前重排账号仍保留原选择", async () => {
  for (const name of ["chooseShopExchangeAccount", "chooseShopPlanAccount"]) {
    let html = "", confirm;
    const select = { value: "101", focus() {} };
    const overlay = {
      set innerHTML(value) { html = value; },
      querySelector: (selector) => selector.startsWith("#") ? select : {
        addEventListener(_event, callback) { confirm = callback; },
      },
      querySelectorAll: () => [], addEventListener() {}, remove() {},
    };
    const c = context(["loggedInAccountChoices", name], {
      accountLabel: (a) => a.stuid, escapeHtml: String, escapeAttr: String,
      document: { createElement: () => overlay, body: { insertBefore() {} }, querySelector() {} },
    });
    c.config.accounts = [{ stuid: "101", cookie: "masked" }, { stuid: "202", cookie: "masked" }];
    const choosing = c[name]({ goods_name: "gift" });
    c.config.accounts.reverse();
    confirm();
    assert.match(html, /option value="101"/);
    assert.equal(await choosing, "101");
  }
});

await check("商品直兑在选择框等待期间重排账号，仍用原账号获取角色并执行", async () => {
  const choice = deferred(), requests = [], exchanged = [];
  const c = context(["exchangeGoodNow", "buildShopPlan", "shopPlanAccountIndex"], {
    isShopGoodSoldOut: () => false, canShopGoodExchangeNow: () => true,
    chooseShopExchangeAccount: () => choice.promise,
    crypto: { randomUUID: () => "direct-plan" },
    shopRequestInFlight: false, shopExchangeNowGoodsId: "", renderShopGoods() {},
    exchangePlanNow: async (plan) => exchanged.push(plan),
    api: async (url) => {
      requests.push(url);
      if (url === "/api/shop/device-fp") return { device_fp: "fake-fp" };
      if (url.startsWith("/api/shop/good-detail?")) return { goods_id: "gift", type: 2, game_biz: "fake-game" };
      return { roles: [{ uid: "correct-role", region: "fake-server" }] };
    },
  });
  c.config.accounts = [{ stuid: "101", cookie: "masked" }, { stuid: "202", cookie: "masked" }];
  const exchanging = c.exchangeGoodNow({ goods_id: "gift" });
  c.config.accounts.reverse();
  choice.resolve("101");
  await exchanging;
  assert.equal(exchanged[0].account_uid, "101");
  assert.equal(exchanged[0].account_index, 1);
  assert.equal(exchanged[0].uid, "correct-role");
  assert.ok(requests.includes("/api/shop/account-meta?account_uid=101&game_biz=fake-game"));
  assert.equal(c.shopRequestInFlight, false);
});

await check("地址列表不含旧地址时，后续重绘仍保留已失效的选中值", async () => {
  const select = { isConnected: true };
  const c = context(["shopPlanAccountIndex", "refreshShopPlanAddresses", "shopPlanAddressSelectHtml", "buildAddressOptions"], {
    api: async () => ({ addresses: [{ id: "other", name: "new" }] }),
    escapeAttr: String, escapeHtml: String, document: { querySelector: () => select },
    shopPlanAddressCache: new Map(),
  });
  c.config.accounts = [{ stuid: "101", cookie: "masked" }];
  const plan = { plan_id: "stable", account_uid: "101", address_id: "saved-address", type: 0 };
  c.config.shop_exchange.plans = [plan];
  await c.refreshShopPlanAddresses(0);
  assert.match(c.shopPlanAddressSelectHtml(plan), /value="saved-address" selected/);
  assert.equal(plan.address_id, "saved-address");
});

await check("地址接口报错不清空缓存或当前地址，错误返回前的重排也不会覆盖其他计划", async () => {
  for (const reorder of [false, true]) {
    const response = deferred();
    const selects = [{ isConnected: true }, { isConnected: true }];
    const c = context(["shopPlanAccountIndex", "refreshShopPlanAddresses", "buildAddressOptions"], {
      api: () => response.promise, escapeAttr: String, escapeHtml: String,
      document: { querySelector: (selector) => selects[Number(selector.match(/data-shop-plan="(\d+)"/)[1])] },
      shopPlanAddressCache: new Map([["101", [{ id: "saved-address" }]]]),
    });
    c.config.accounts = [{ stuid: "101", cookie: "masked" }];
    c.config.shop_exchange.plans = [
      { plan_id: "stable", account_uid: "101", address_id: "saved-address", type: 0 },
      { plan_id: "other", account_uid: "101", address_id: "other-address", type: 0 },
    ];
    const pending = c.refreshShopPlanAddresses(0);
    if (reorder) {
      c.config.shop_exchange.plans.reverse();
      selects[0].innerHTML = "other-plan";
    }
    response.resolve({ addresses_error: "fake temporary error" });
    await pending;
    assert.equal(c.shopPlanAddressCache.get("101")[0].id, "saved-address");
    if (reorder) assert.equal(selects[0].innerHTML, "other-plan");
    assert.equal(c.config.shop_exchange.plans[reorder ? 1 : 0].address_id, "saved-address");
  }
});

await check("草稿登录按唯一草稿 ID 回填，删除、重排或缺失身份时不会填错卡片", () => {
  for (const action of ["reorder", "delete", "missing", "duplicate"]) {
    const c = context(["applyDraftLogin", "loginAccountIndex"], {
      activeLoginIndex: 1, editingAccountIndex: null, renderAccounts() {},
      document: { querySelector() {} },
    });
    const target = { _draft: true, _draft_id: "target", stuid: "", cookie: "" };
    const other = { _draft: true, _draft_id: "other", stuid: "", cookie: "" };
    c.config.accounts = [other, target];
    const login = { account_index: 1, draft_id: "target", account_data: { stuid: "303", cookie: "draft-cookie" } };
    if (action === "reorder") c.config.accounts.reverse();
    if (action === "delete") c.config.accounts = [other];
    if (action === "missing") delete login.draft_id;
    if (action === "duplicate") other._draft_id = "target";
    c.applyDraftLogin(login);
    assert.equal(other.stuid, "");
    assert.equal(target.stuid, action === "reorder" ? "303" : "");
    if (action === "reorder") assert.equal(c.editingAccountIndex, 0);
  }
});

await check("打开小号登录面板不会重放大号上一次的登录成功状态", async () => {
  const toasts = [];
  const node = { textContent: "", disabled: false, querySelector: () => null };
  const c = context(["showLoginMethods", "refreshStatus"], {
    config: { accounts: [{ name: "大号" }, { name: "小号" }] },
    lastLoginStatus: "success",
    activeLoginIndex: null,
    localLoginPanel: null,
    configEditVersion: 0,
    hasPendingConfigEdits: () => false,
    api: async () => ({
      scheduler: {}, exchange_scheduler: {}, logs: [],
      login: { running: false, status: "success", account_index: 0, message: "账号 大号 登录成功" },
    }),
    collectConfig() {}, clearLoginCountdown() {},
    renderLoginSlot() {}, updateLogs() {},
    showToast: (message) => toasts.push(message),
    loadConfig: async () => {},
    formatTime: () => "", latestResultText: () => "",
    document: { querySelectorAll: () => [] },
    $: () => node,
  });

  await c.showLoginMethods(1);
  await c.refreshStatus();

  assert.equal(c.localLoginPanel.account_index, 1);
  assert.deepEqual(toasts, []);
  assert.equal(c.lastLoginStatus, "success");
});

await check("小号扫码请求完成前的状态轮询忽略大号旧成功，随后处理小号新结果", async () => {
  const startRequest = deferred();
  const toasts = [];
  const node = { textContent: "", disabled: false, querySelector: () => null };
  let statusCount = 0;
  const c = context(["startLogin", "refreshStatus", "stripClientAccount"], {
    config: { accounts: [{ name: "大号" }, { name: "小号" }] },
    lastLoginStatus: "success",
    activeLoginIndex: null,
    localLoginPanel: { account_index: 1, mode: "methods" },
    configEditVersion: 0,
    hasPendingConfigEdits: () => false,
    api: async (url) => {
      if (url === "/api/login/start") return startRequest.promise;
      statusCount += 1;
      const isOld = statusCount === 1;
      return {
        scheduler: {}, exchange_scheduler: {}, logs: [],
        login: {
          running: false, status: "success", account_index: isOld ? 0 : 1,
          message: isOld ? "账号 大号 登录成功" : "账号 小号 登录成功",
        },
      };
    },
    collectConfig() {}, clearLoginCountdown() {},
    renderLoginSlot() {}, updateLogs() {},
    showToast: (message) => toasts.push(message),
    loadConfig: async () => {},
    formatTime: () => "", latestResultText: () => "",
    document: { querySelectorAll: () => [] },
    $: () => node,
  });

  const starting = c.startLogin(1);
  await tick();
  await c.refreshStatus();
  assert.deepEqual(toasts, []);
  assert.equal(c.lastLoginStatus, "success");

  startRequest.resolve({ ok: true });
  await starting;
  assert.deepEqual(toasts, ["登录流程已启动", "账号 小号 登录成功"]);
});

await check("草稿身份随表单采集保留，仅登录请求携带，保存配置时移除", () => {
  let seq = 0;
  const c = context(["emptyAccount", "emptyAccountCloudGames", "stripClientAccount", "serverConfig", "collectConfig", "collectChipSelection"], {
    crypto: { randomUUID: () => `draft-${++seq}` },
    renderScheduleSummary() {}, collectShopExchange: () => ({ plans: [] }),
    collectPushChannel() {}, cleanPushChannel() {}, shouldSavePushChannel() {}, collectCaptchaChannel() {},
    collectAccountCloudGames: () => ({}), collectAccountTasks: () => null,
  });
  const first = c.emptyAccount("A"), second = c.emptyAccount("B");
  assert.ok(first._draft_id);
  assert.notEqual(first._draft_id, second._draft_id);
  c.config.accounts = [first];
  c.document.querySelectorAll = (selector) => selector === ".account-row" ? [{
    dataset: { draft: "1", draftId: first._draft_id },
    querySelectorAll: () => [],
  }] : [];
  c.collectConfig();
  assert.equal(c.config.accounts[0]._draft_id, first._draft_id);
  assert.equal(c.stripClientAccount(c.config.accounts[0])._draft_id, first._draft_id);
  assert.equal(c.serverConfig().accounts[0]._draft_id, undefined);
});

console.log(`\n${passed} 项数据完整性前端检查通过`);
