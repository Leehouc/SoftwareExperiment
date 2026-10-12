const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function portal(period) {
  const context = vm.createContext({ document: { querySelector() { return {}; } } });
  const source = fs.readFileSync(path.join(__dirname, "../administer/app.js"), "utf8");
  const end = source.indexOf('  $("#main-nav").addEventListener');
  vm.runInContext(source.slice(0, end) + '\nglobalThis.api = { state, renderRegistration };})();', context);
  context.api.state.data = { periods: [{ semesterId: "TERM", name: "Term <name>", status: "OPEN", ...period }],
    minimumEnrollment: 3, feePerCreditCents: 15000 };
  return context.api.renderRegistration();
}

test("未到关闭阶段禁用按钮，显示服务端原因和最早日期", () => {
  const html = portal({ canClose: false, closeAvailableOn: "2026-01-26", closeReason: "加退选尚未结束" });
  assert.match(html, /data-action="close-registration"[^>]*disabled/);
  assert.match(html, /加退选尚未结束/);
  assert.match(html, /最早关闭日期/);
  assert.match(html, /Term &lt;name&gt;/);
  assert.match(html, /data-action="refresh-registration"/);
});

test("窗口均已结束后允许关闭，已 CLOSED 时移除操作入口", () => {
  const ready = portal({ canClose: true, closeReason: "可以关闭并结算" });
  const button = ready.match(/<button[^>]*data-action="close-registration"[^>]*>/)[0];
  assert.doesNotMatch(button, /disabled/);
  assert.match(ready, /可以关闭并结算/);
  const closed = portal({ status: "CLOSED", canClose: false, closeReason: "不能重复结算" });
  assert.doesNotMatch(closed, /data-action="close-registration"/);
  assert.match(closed, /不能重复结算/);
});

test("缺失权限字段不因 OPEN 误放行，也不信任 CLOSED 的异常 canClose", () => {
  assert.match(portal({}), /data-action="close-registration"[^>]*disabled/);
  assert.doesNotMatch(portal({ status: "CLOSED", canClose: true }), /data-action="close-registration"/);
});
