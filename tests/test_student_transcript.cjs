const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function portal() {
  const nodes = new Map();
  const context = vm.createContext({
    document: {
      hidden: false,
      querySelectorAll() { return []; },
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, { value: "", classList: { toggle() {}, remove() {} } });
        return nodes.get(id);
      },
    },
    window: { clearInterval() {}, setInterval() { return 1; } },
  });
  const source = fs.readFileSync(path.join(__dirname, "../student/app.js"), "utf8");
  const bindings = source.indexOf('\ndocument.getElementById("login-form").addEventListener');
  vm.runInContext(source.slice(0, bindings) + `
    globalThis.api = { state, loadTranscript, refreshAvailability, renderTerms, renderTranscript, switchPage };
    renderOverview = () => {};
    renderAll = () => { renderTerms(); renderTranscript(); };
    renderAvailabilityNotice = () => {};
    showAlert = (message) => { globalThis.alertMessage = message; };
  `, context);
  const api = context.api;
  const terms = [{ semesterId: "RECENT", name: "上一学期" }, { semesterId: "OLD", name: "旧 <学期>" }];
  const transcript = (term) => ({
    transcriptSemesterId: term, transcriptSemesters: terms, completedCourseCount: 2,
    transcript: [{ semester_id: term, course_id: term, course_title_snapshot: `${term} <course>`,
      credits_snapshot: 3, letter_grade: "A", professor_id: "P" }],
  });
  api.state.user = { studentId: "S" };
  api.state.page = "transcript";
  api.state.data = {
    semesterId: "CURRENT", semesters: [{ semesterId: "CURRENT", name: "当前学期" }],
    catalog: { courseOfferings: [], faculty: [] }, enrollments: [], choices: [], ...transcript("RECENT"),
  };
  api.state.primary = ["UNSAVED"];
  api.state.alternate = ["ALT"];
  nodes.set("course-search", { value: "search" });
  nodes.set("department-filter", { value: "CS" });
  const respond = (fn) => { context.response = fn; vm.runInContext("request = response", context); };
  return { api, nodes, context, respond, transcript };
}

test("历史成绩加载只替换成绩字段，保留当前学期、草稿顺序和筛选条件", async () => {
  const { api, nodes, respond, transcript } = portal();
  let url;
  respond(async (path) => { url = path; return transcript("OLD"); });
  const saved = JSON.stringify([api.state.primary, api.state.alternate, api.state.data.choices]);
  await api.loadTranscript("OLD");
  assert.equal(url, "/api/student/transcript?semester=OLD");
  assert.equal(api.state.data.semesterId, "CURRENT");
  assert.equal(JSON.stringify([api.state.primary, api.state.alternate, api.state.data.choices]), saved);
  assert.equal(nodes.get("course-search").value, "search");
  assert.equal(nodes.get("department-filter").value, "CS");
  assert.match(nodes.get("transcript-rows").innerHTML, /OLD &lt;course&gt;/);
  assert.doesNotMatch(nodes.get("transcript-rows").innerHTML, /RECENT/);
  assert.equal(nodes.get("transcript-course-count").textContent, "1");
  assert.equal(nodes.get("semester-select").value, "OLD");
  assert.equal(nodes.get("transcript-semester-select").value, "OLD");
});

test("切换页面时顶部选择器使用对应学期集合，并保留历史学期选择", () => {
  const { api, nodes } = portal();
  api.renderTerms();
  assert.equal(nodes.get("semester-select").value, "RECENT");
  assert.doesNotMatch(nodes.get("semester-select").innerHTML, /CURRENT/);
  api.switchPage("register");
  assert.equal(nodes.get("semester-select").value, "CURRENT");
  assert.doesNotMatch(nodes.get("semester-select").innerHTML, /RECENT/);
  api.switchPage("transcript");
  assert.equal(nodes.get("semester-select").value, "RECENT");
});

test("请求加载及失败有反馈，失败保留原成绩并恢复两个筛选器", async () => {
  const { api, nodes, context, respond } = portal();
  let reject;
  respond(() => new Promise((_, fail) => { reject = fail; }));
  const pending = api.loadTranscript("OLD");
  assert.equal(nodes.get("semester-select").disabled, true);
  assert.equal(nodes.get("transcript-semester-select").disabled, true);
  assert.match(nodes.get("transcript-period-summary").textContent, /正在加载/);
  reject(new Error("Offline"));
  await pending;
  assert.equal(context.alertMessage, "Offline");
  assert.equal(api.state.data.transcriptSemesterId, "RECENT");
  assert.equal(nodes.get("semester-select").disabled, false);
  assert.equal(nodes.get("transcript-semester-select").disabled, false);
});

test("较早的成绩请求后返回不会覆盖新的历史筛选", async () => {
  const { api, respond, transcript } = portal();
  const resolve = [];
  respond(() => new Promise((done) => resolve.push(done)));
  const first = api.loadTranscript("OLD");
  const second = api.loadTranscript("RECENT");
  resolve[1](transcript("RECENT"));
  await second;
  resolve[0](transcript("OLD"));
  await first;
  assert.equal(api.state.data.transcriptSemesterId, "RECENT");
  assert.equal(api.state.transcriptLoading, false);
});

test("选课定时刷新携带历史学期，较早的刷新结果不会覆盖后来选择的成绩", async () => {
  const { api, respond, transcript } = portal();
  const updated = JSON.parse(JSON.stringify(api.state.data));
  let resolve;
  let url;
  respond((path) => {
    if (path.startsWith("/api/student/data")) {
      url = path;
      return new Promise((done) => { resolve = done; });
    }
    return Promise.resolve(transcript("OLD"));
  });
  const polling = api.refreshAvailability();
  await api.loadTranscript("OLD");
  resolve(updated);
  await polling;
  assert.match(url, /transcriptSemester=RECENT/);
  assert.equal(api.state.data.transcriptSemesterId, "OLD");
  assert.equal(api.state.primary[0], "UNSAVED");
});

test("切换用户或重新加载课程后丢弃旧成绩请求", async () => {
  for (const invalidate of [(state) => { state.user = { studentId: "OTHER" }; }, (state) => { state.dataVersion++; }]) {
    const { api, respond, transcript } = portal();
    let resolve;
    respond(() => new Promise((done) => { resolve = done; }));
    const pending = api.loadTranscript("OLD");
    invalidate(api.state);
    resolve(transcript("OLD"));
    await pending;
    assert.equal(api.state.data.transcriptSemesterId, "RECENT");
  }
});

test("空成绩学期保留筛选入口，无已结束学期时显示空态并禁用选择", () => {
  const { api, nodes } = portal();
  api.state.data.transcript = [];
  api.renderTranscript();
  assert.equal(nodes.get("transcript-empty").hidden, false);
  assert.match(nodes.get("transcript-empty-copy").textContent, /上一学期暂未录入成绩/);
  assert.equal(nodes.get("transcript-semester-select").disabled, false);
  api.state.data.transcriptSemesters = [];
  api.state.data.transcriptSemesterId = null;
  api.renderTerms();
  api.renderTranscript();
  assert.equal(nodes.get("semester-select").disabled, true);
  assert.equal(nodes.get("transcript-semester-select").disabled, true);
  assert.match(nodes.get("transcript-period-summary").textContent, /没有可查看/);
});
