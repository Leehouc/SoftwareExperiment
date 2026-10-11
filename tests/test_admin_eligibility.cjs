const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function portal() {
  const context = vm.createContext({ document: { querySelector() { return {}; } } });
  const source = fs.readFileSync(path.join(__dirname, "../administer/app.js"), "utf8");
  const end = source.indexOf('  $("#main-nav").addEventListener');
  assert.ok(end > 0);
  vm.runInContext(source.slice(0, end) + '\nglobalThis.api = { state, eligibilityFields, renderPeople };})();', context);
  context.api.state.data = {
    professors: [{ id: "P", fullName: "Teacher", username: "teacher", status: "ACTIVE", accountActive: 1,
      eligibleCourseIds: ["C1", "ARCHIVE"] }], students: [],
    catalog: { departments: [], courses: [{ courseId: "C1", title: "Course <one>" }, { courseId: "C2", title: "Two" }] },
  };
  return context.api;
}

test("教师资格表单回显当前和归档资格，对课程名称正确转义", () => {
  const { eligibilityFields } = portal();
  const html = eligibilityFields(["C1", "ARCHIVE"]);
  assert.match(html, /value="C1" checked/);
  assert.match(html, /value="ARCHIVE" checked/);
  assert.match(html, /归档课程/);
  assert.match(html, /Course &lt;one&gt;/);
  assert.doesNotMatch(html, /value="C2" checked/);
  assert.match(html, /暂不能新增授课/);
});

test("教师列表提供资格数量与维护入口，学生列表没有该入口", () => {
  const { renderPeople } = portal();
  assert.match(renderPeople("professor"), /授课资格（2）/);
  assert.match(renderPeople("professor"), /data-action="eligibility" data-id="P"/);
  assert.doesNotMatch(renderPeople("student"), /data-action="eligibility"/);
});

test("空课程目录明确提示，仍允许查看已有归档资格", () => {
  const { state, eligibilityFields } = portal();
  state.data.catalog.courses = [];
  assert.match(eligibilityFields([]), /目录暂无课程/);
  assert.match(eligibilityFields(["ARCHIVE"]), /value="ARCHIVE" checked/);
});
