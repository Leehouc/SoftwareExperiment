const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function portal(name) {
  const nodes = new Map();
  const context = vm.createContext({
    fetch: async () => ({ status: 401, ok: false, json: async () => ({ error: "登录状态已失效" }) }),
    document: { getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, { value: "", hidden: false, innerHTML: "Private data" });
      return nodes.get(id);
    } },
    window: { clearInterval() {} },
  });
  const source = fs.readFileSync(path.join(__dirname, `../${name}/app.js`), "utf8");
  const end = source.indexOf('\ndocument.getElementById("login-form").addEventListener');
  vm.runInContext(source.slice(0, end) + '\nglobalThis.api = { state, request };', context);
  const { state } = context.api;
  state.user = { userId: "U" };
  state.data = { transcript: [{ letter_grade: "A" }] };
  state.csrfToken = "csrf";
  return { context, nodes, ...context.api };
}

for (const name of ["student", "teacher"]) {
  test(`${name} 会话失效后清除状态并回到登录页`, async () => {
    const { state, request, nodes } = portal(name);
    await assert.rejects(request("/api/protected"), /登录状态已失效/);
    assert.equal(state.user, null);
    assert.equal(state.data, null);
    assert.equal(state.csrfToken, "");
    assert.equal(nodes.get("portal").hidden, true);
    assert.equal(nodes.get("login-screen").hidden, false);
    assert.equal(nodes.get(name === "student" ? "transcript-rows" : "roster-rows").innerHTML, "");
  });
  test(`${name} 旧用户请求的 401 不清除后来登录的新用户`, async () => {
    const { context, state, request } = portal(name);
    let resolve;
    context.fetch = () => new Promise((done) => { resolve = done; });
    const pending = request("/api/protected");
    state.user = { userId: "NEW" };
    resolve({ status: 401, ok: false, json: async () => ({ error: "expired" }) });
    await assert.rejects(pending, /expired/);
    assert.equal(state.user.userId, "NEW");
  });
  test(`${name} 会话改变后丢弃旧请求成功返回的数据`, async () => {
    const { context, state, request } = portal(name);
    let resolve;
    context.fetch = () => new Promise((done) => { resolve = done; });
    const pending = request("/api/protected");
    state.user = null;
    resolve({ status: 200, ok: true, json: async () => ({ privateData: "old user" }) });
    await assert.rejects(pending, /登录状态已改变/);
  });
}
