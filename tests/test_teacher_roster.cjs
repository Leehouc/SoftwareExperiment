const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function portal(canEditGrades) {
  const nodes = new Map();
  const requests = [];
  const course = {
    offeringId: "O1", semesterId: "TERM", semesterName: "Current term",
    courseId: "C1", courseTitle: "Course 1", rosterCount: 1, canEditGrades,
  };
  const data = { rosterCourses: [course], gradeCourses: canEditGrades ? [course] : [] };
  const rosterData = {
    offeringId: "O1", semesterId: "TERM", canEditGrades,
    roster: [{ enrollment_id: 1, student_id: "S", full_name: "Student", enrollment_status: "ENROLLED" }],
  };
  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, {});
        return nodes.get(id);
      },
      querySelectorAll() { throw new Error("只读名册不应收集成绩"); },
    },
    mockRequest: async (url) => {
      requests.push(url);
      if (url.startsWith("/api/teacher/data")) return data;
      if (url.startsWith("/api/teacher/grades/roster")) return rosterData;
      throw new Error(`Unexpected request: ${url}`);
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../teacher/app.js"), "utf8");
  const bindings = source.indexOf('\ndocument.getElementById("login-form").addEventListener');
  assert.ok(bindings > 0);
  vm.runInContext(source.slice(0, bindings) + `
    request = mockRequest;
    renderAll = () => {};
    globalThis.portal = { state, loadData, renderGradeCourses, renderRoster, saveGrades };
  `, context);
  context.portal.state.data = data;
  context.portal.state.rosterData = rosterData;
  return { api: context.portal, nodes, requests };
}

test("当前学期没有可录成绩课程时仍能从页面加载名册", async () => {
  const { api, nodes, requests } = portal(false);
  await api.loadData("TERM");
  assert.equal(api.state.activeOfferingId, "O1");
  assert.equal(requests.length, 2);
  assert.match(requests[1], /grades\/roster\?offering=O1/);
  assert.equal(nodes.get("no-grade-courses").hidden, true);
  assert.match(nodes.get("grade-course-list").innerHTML, /仅查看名册/);
  assert.match(nodes.get("roster-rows").innerHTML, /Student/);
});

test("当前学期成绩控件只读且直接调用保存也不会发出写请求", async () => {
  const { api, nodes, requests } = portal(false);
  api.renderRoster();
  assert.equal(nodes.get("save-grades").disabled, true);
  assert.match(nodes.get("roster-rows").innerHTML, /<select[^>]*\bdisabled\b/);
  assert.match(nodes.get("roster-grade-hint").textContent, /学期结束后才能录入或修改成绩/);
  await api.saveGrades();
  assert.equal(requests.length, 0);
});

test("已结束学期保留成绩编辑与保存入口", () => {
  const { api, nodes } = portal(true);
  api.renderGradeCourses();
  api.renderRoster();
  assert.match(nodes.get("grade-course-list").innerHTML, /可录成绩/);
  assert.equal(nodes.get("save-grades").disabled, false);
  assert.doesNotMatch(nodes.get("roster-rows").innerHTML, /\bdisabled\b/);
  assert.match(nodes.get("roster-grade-hint").textContent, /成绩范围/);
});

test("可录成绩但没有学生的班级不能保存成绩", () => {
  const { api, nodes } = portal(true);
  api.state.rosterData.roster = [];
  api.renderRoster();
  assert.equal(nodes.get("save-grades").disabled, true);
  assert.equal(nodes.get("roster-empty").hidden, false);
});
