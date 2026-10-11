const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function portal(canEdit) {
  const nodes = new Map();
  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, { value: "", classList: { toggle() {} }, style: { setProperty() {} } });
        return nodes.get(id);
      },
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../student/app.js"), "utf8");
  // 加载实际页面函数，跳过需要浏览器的登录事件绑定与会话恢复。
  const bindings = source.indexOf('\ndocument.getElementById("login-form").addEventListener');
  assert.ok(bindings > 0);
  vm.runInContext(source.slice(0, bindings) + `
    globalThis.portal = { state, registrationLabel, toggleSelection, renderCourseCatalog, renderSchedule, renderOverview };
    renderAll = () => {};
  `, context);
  const api = context.portal;
  api.state.data = {
    semesterId: "TERM", period: { registration_status: canEdit ? "OPEN" : "CLOSED" },
    canEdit, registrationOpen: canEdit, addDropOpen: canEdit, previouslySubmitted: true,
    schedule: { status: "SUBMITTED" }, enrollments: [], offeringCounts: {},
    catalog: {
      courses: [{ courseId: "C1", title: "Course 1", credits: 3 }, { courseId: "C2", title: "Course 2", credits: 3 }],
      faculty: [], departments: [], courseOfferings: [
        { offeringId: "O1", courseId: "C1", semesterId: "TERM", status: "OPEN", capacity: 10, days: [] },
        { offeringId: "O2", courseId: "C2", semesterId: "TERM", status: "OPEN", capacity: 10, days: [] },
      ],
    },
  };
  api.state.primary = ["O1"];
  api.state.alternate = ["O2"];
  return { api, nodes };
}

test("关闭提示优先于加退选日期和课表状态", () => {
  const { api } = portal(false);
  api.state.data.addDropOpen = true;
  api.state.data.registrationOpen = true;
  api.state.data.schedule.status = "CANCELLED";
  assert.equal(api.registrationLabel().text, "注册已关闭");
  assert.equal(api.registrationLabel().closed, true);
});

test("关闭后选择与移除操作均不改变本地课表", () => {
  const { api } = portal(false);
  const before = JSON.stringify([api.state.primary, api.state.alternate]);
  for (const [kind, id] of [["primary", "O1"], ["alternate", "O2"], ["primary", "O3"]]) {
    api.toggleSelection(kind, id);
    assert.equal(JSON.stringify([api.state.primary, api.state.alternate]), before);
  }
});

test("关闭后禁用保存、提交、删除和所有选择与移除按钮", () => {
  const { api, nodes } = portal(false);
  api.renderCourseCatalog();
  api.renderSchedule();
  for (const id of ["save-draft", "submit-schedule", "delete-schedule"]) {
    assert.equal(nodes.get(id).disabled, true, id);
  }
  const picks = nodes.get("course-list").innerHTML.match(/<button[^>]*data-pick-kind[^>]*>/g);
  assert.equal(picks.length, 4);
  for (const button of picks) assert.match(button, /\bdisabled\b/);
  for (const id of ["primary-slots", "alternate-slots"]) {
    assert.match(nodes.get(id).innerHTML, /<button[^>]*data-remove-kind[^>]*\bdisabled\b/);
  }
});

test("开放时保留编辑按钮和满员已选课程的移除能力", () => {
  const { api, nodes } = portal(true);
  api.state.data.catalog.courseOfferings[0].status = "FULL";
  api.renderCourseCatalog();
  api.renderSchedule();
  for (const id of ["save-draft", "submit-schedule", "delete-schedule"]) {
    assert.equal(nodes.get(id).disabled, false, id);
  }
  for (const id of ["primary-slots", "alternate-slots"]) {
    assert.doesNotMatch(nodes.get(id).innerHTML, /\bdisabled\b/);
  }
  const selected = nodes.get("course-list").innerHTML.match(/<button[^>]*active-primary[^>]*>/)[0];
  assert.doesNotMatch(selected, /\bdisabled\b/);
  api.toggleSelection("primary", "O1");
  assert.equal(api.state.primary.length, 0);
});

test("加退选提示允许未补满的课表提交", () => {
  const { api, nodes } = portal(true);
  api.renderOverview();
  assert.match(nodes.get("selection-hint").textContent, /无需补满/);
  assert.match(nodes.get("progress-description").textContent, /无需补满/);
});

test("初次提交仍提示必须选择四门主选和两门备选", () => {
  const { api, nodes } = portal(true);
  api.state.data.previouslySubmitted = false;
  api.renderOverview();
  assert.match(nodes.get("selection-hint").textContent, /初次提交需选择 4 门主选和 2 门备选/);
});
