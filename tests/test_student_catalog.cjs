const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function portal() {
  const nodes = new Map();
  const timers = new Map();
  let timerId = 0;
  const context = vm.createContext({
    document: {
      hidden: false,
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, { value: "", classList: { toggle() {} }, style: { setProperty() {} } });
        return nodes.get(id);
      },
    },
    window: {
      setInterval(fn, delay) { timers.set(++timerId, { fn, delay }); return timerId; },
      clearInterval(id) { timers.delete(id); },
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../student/app.js"), "utf8");
  const bindings = source.indexOf('\ndocument.getElementById("login-form").addEventListener');
  assert.ok(bindings > 0);
  vm.runInContext(source.slice(0, bindings) + `
    globalThis.api = { state, refreshAvailability, startAvailabilityRefresh, stopAvailabilityRefresh,
      loadData, renderCourseCatalog, renderAvailabilityNotice };
    renderAll = () => renderCourseCatalog();
  `, context);
  const api = context.api;
  api.state.user = { studentId: "S" };
  api.state.data = {
    semesterId: "TERM", canEdit: true, enrollments: [], choices: [], offeringCounts: {},
    teachingAssignments: { O1: { fullName: "Actual <Teacher>" } },
    catalog: {
      courses: [{ courseId: "C1", title: "Advanced", departmentId: "CS" },
        { courseId: "C2", title: "Prerequisite <Basics>", departmentId: "CS" }],
      faculty: [{ facultyId: "LEGACY", name: "Wrong teacher" }], departments: [{ departmentId: "CS", name: "Computer Science" }],
      prerequisites: [{ courseId: "C1", prerequisiteCourseId: "C2" }, { courseId: "C1", prerequisiteCourseId: "MISSING" }],
      courseOfferings: [
        { offeringId: "O1", semesterId: "TERM", courseId: "C1", status: "OPEN", capacity: 2, facultyId: "LEGACY", days: [] },
        { offeringId: "O2", semesterId: "TERM", courseId: "C2", status: "OPEN", capacity: 2, days: [] },
      ],
    },
  };
  api.state.primary = ["O2", "O1"];
  nodes.set("course-search", { value: "advanced" });
  nodes.set("department-filter", { value: "CS" });
  const next = () => JSON.parse(JSON.stringify(api.state.data));
  const respond = (fn) => { context.response = fn; vm.runInContext("request = response", context); };
  return { api, nodes, timers, next, respond, context };
}

test("课程卡片展示院系、实际教师和完整先修编号及名称，正确转义", () => {
  const { api, nodes } = portal();
  api.renderCourseCatalog();
  const html = nodes.get("course-list").innerHTML;
  assert.match(html, /Computer Science/);
  assert.match(html, /Actual &lt;Teacher&gt;/);
  assert.doesNotMatch(html, /Wrong teacher/);
  assert.match(html, /C2 · Prerequisite &lt;Basics&gt;；MISSING/);
  api.state.data.teachingAssignments = {};
  api.renderCourseCatalog();
  assert.match(nodes.get("course-list").innerHTML, /尚未安排/);
  nodes.get("course-search").value = "";
  api.renderCourseCatalog();
  assert.match(nodes.get("course-list").innerHTML, /无先修要求/);
});

test("自动刷新通知满员并保留未保存选择、顺序和筛选条件", async () => {
  const { api, nodes, next, respond } = portal();
  const updated = next();
  updated.offeringCounts.O1 = 2;
  updated.choices = [{ offering_id: "O2", choice_type: "ALTERNATE" }];
  updated.teachingAssignments.O1.fullName = "New teacher";
  respond(async () => updated);
  const selected = JSON.stringify([api.state.primary, api.state.alternate]);
  await api.refreshAvailability();
  assert.equal(JSON.stringify([api.state.primary, api.state.alternate]), selected);
  assert.equal(nodes.get("course-search").value, "advanced");
  assert.equal(nodes.get("department-filter").value, "CS");
  assert.match(nodes.get("course-list").innerHTML, /New teacher/);
  assert.match(nodes.get("catalog-notice").textContent, /O1/);
  assert.match(nodes.get("catalog-notice").textContent, /已满或不可用/);
  assert.equal(nodes.get("catalog-notice").hidden, false);
});

test("刷新期间继续编辑或移除全部课程也不会被服务端旧选择覆盖", async () => {
  const { api, next, respond } = portal();
  const updated = next();
  updated.choices = [{ offering_id: "O1", choice_type: "PRIMARY" }];
  let resolve;
  respond(() => new Promise((done) => { resolve = done; }));
  const pending = api.refreshAvailability();
  api.state.primary = [];
  api.state.alternate = [];
  resolve(updated);
  await pending;
  assert.equal(api.state.primary.length, 0);
  assert.equal(api.state.alternate.length, 0);
});

test("未选课程从开放变满员也会通知；恢复名额后移除通知", async () => {
  const { api, nodes, next, respond } = portal();
  api.state.primary = [];
  const updated = next();
  updated.catalog.courseOfferings[0].status = "FULL";
  respond(async () => updated);
  await api.refreshAvailability();
  assert.match(nodes.get("catalog-notice").textContent, /刚刚满员/);
  const reopened = next();
  reopened.catalog.courseOfferings[0].status = "OPEN";
  respond(async () => reopened);
  await api.refreshAvailability();
  assert.equal(nodes.get("catalog-notice").hidden, true);
});

test("已注册满员主选不会被要求更换，移除后可恢复保留", async () => {
  const { api, nodes, next, respond } = portal();
  const updated = next();
  updated.offeringCounts.O1 = 2;
  updated.enrollments = [{ offering_id: "O1", enrollment_status: "ENROLLED" }];
  respond(async () => updated);
  await api.refreshAvailability();
  assert.equal(nodes.get("catalog-notice").hidden, true);
  api.state.primary = [];
  api.renderCourseCatalog();
  const button = nodes.get("course-list").innerHTML.match(/<button[^>]*data-pick-kind="primary"[^>]*>/)[0];
  assert.doesNotMatch(button, /\bdisabled\b/);
});

test("请求失败保留所有数据和选择并能在下一轮恢复", async () => {
  const { api, nodes, next, respond } = portal();
  const before = JSON.stringify([api.state.data, api.state.primary]);
  respond(async () => { throw new Error("Offline"); });
  await api.refreshAvailability();
  assert.equal(JSON.stringify([api.state.data, api.state.primary]), before);
  assert.match(nodes.get("catalog-notice").textContent, /Offline/);
  respond(async () => next());
  await api.refreshAvailability();
  assert.equal(api.state.refreshError, false);
  assert.equal(nodes.get("catalog-notice").hidden, true);
});

test("后台页面和已有刷新请求均不产生重叠请求", async () => {
  const { api, context, respond } = portal();
  let count = 0;
  let resolve;
  respond(() => { count++; return new Promise((done) => { resolve = done; }); });
  context.document.hidden = true;
  await api.refreshAvailability();
  assert.equal(count, 0);
  context.document.hidden = false;
  const pending = api.refreshAvailability();
  await api.refreshAvailability();
  assert.equal(count, 1);
  resolve(api.state.data);
  await pending;
});

test("切换学期或退出时丢弃旧刷新请求且清理定时器", async () => {
  const { api, next, respond, timers } = portal();
  api.startAvailabilityRefresh();
  api.startAvailabilityRefresh();
  assert.equal(timers.size, 1);
  assert.equal([...timers.values()][0].delay, 15_000);
  let resolve;
  respond(() => new Promise((done) => { resolve = done; }));
  const old = next();
  const pending = api.refreshAvailability();
  api.stopAvailabilityRefresh();
  api.state.data = { semesterId: "NEW_TERM" };
  api.state.primary = ["NEW_COURSE"];
  resolve(old);
  await pending;
  assert.equal(api.state.data.semesterId, "NEW_TERM");
  assert.equal(api.state.primary[0], "NEW_COURSE");
  assert.equal(timers.size, 0);
});

test("刷新得知注册关闭后禁用编辑并保留当前选择", async () => {
  const { api, nodes, next, respond } = portal();
  const updated = next();
  updated.canEdit = false;
  respond(async () => updated);
  await api.refreshAvailability();
  assert.equal(nodes.get("save-draft").disabled, true);
  assert.equal(nodes.get("submit-schedule").disabled, true);
  assert.equal(api.state.primary.length, 2);
});
