import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../miyouqian/core/geetest/harness.html', import.meta.url), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match => match[1]).join('\n');

function boot(getValidate = () => ({ geetest_validate: 'fake-validate', geetest_challenge: 'fake-challenge' })) {
  const handlers = {};
  const captchaObj = { appendTo() {}, getValidate };
  for (const event of ['Ready', 'Success', 'Error', 'Close']) {
    captchaObj[`on${event}`] = handler => { handlers[event] = handler; };
  }
  const window = { location: { search: '?gt=fake-gt&challenge=fake-challenge' } };
  const context = vm.createContext({
    window,
    document: { readyState: 'complete' },
    initGeetest: (_options, callback) => callback(captchaObj),
  });
  vm.runInContext(script, context);
  handlers.Ready();
  return { state: window.__gt, handlers };
}

test('关闭回调进入现有 Python 等待条件支持的 error 状态', () => {
  const { state, handlers } = boot();
  handlers.Close();
  assert.equal(state.status, 'error');
  assert.match(state.message, /onClose/);
  assert.equal(state.result, null);
});

test('成功后的自动关闭保留成功结果', () => {
  const { state, handlers } = boot();
  handlers.Success();
  handlers.Close();
  assert.equal(state.status, 'success');
  assert.equal(state.result.geetest_validate, 'fake-validate');
  assert.equal(state.message, '');
});

test('成功事件没有验证结果时给出明确错误', () => {
  for (const getValidate of [() => null, () => ({}), () => { throw Error('fake failure'); }]) {
    const { state, handlers } = boot(getValidate);
    handlers.Success();
    assert.equal(state.status, 'error');
    assert.match(state.message, /onSuccess/);
    assert.equal(state.result, null);
  }
});

test('组件报错清除旧结果并保留诊断', () => {
  const { state, handlers } = boot();
  handlers.Success();
  handlers.Error({ msg: 'fake network error' });
  assert.equal(state.status, 'error');
  assert.equal(state.result, null);
  assert.match(state.message, /onError: fake network error/);
});
