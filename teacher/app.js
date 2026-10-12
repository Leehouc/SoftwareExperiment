const state = {
  user: null,
  csrfToken: "",
  data: null,
  page: "home",
  selectedOfferingIds: [],
  activeOfferingId: "",
  rosterData: null,
  rosterLoading: false,
};

const pageTitles = { home: "工作概览", teaching: "授课选择", grades: "班级名册与成绩" };

class RequestError extends Error {
  constructor(message, issues = []) {
    super(message);
    this.issues = issues;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

async function request(path, options = {}) {
  const user = state.user;
  const method = options.method || "GET";
  const headers = { Accept: "application/json" };
  const init = { method, headers, credentials: "same-origin", cache: "no-store" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  if (method !== "GET" && path !== "/api/login") headers["X-CSRF-Token"] = state.csrfToken;
  const response = await fetch(path, init);
  let payload;
  try { payload = await response.json(); }
  catch { throw new RequestError("服务器返回了无法识别的数据。"); }
  if (response.status === 401 && path !== "/api/login" && user && user === state.user) {
    clearLocalSession();
    document.getElementById("login-message").textContent = payload.error || "登录状态已失效，请重新登录。";
  }
  if (!response.ok) throw new RequestError(payload.error || "请求失败。", payload.issues || []);
  if (user && user !== state.user) throw new RequestError("登录状态已改变，请重新加载数据。");
  return payload;
}

function showAlert(message, type = "warning", issues = []) {
  const alert = document.getElementById("page-alert");
  alert.className = `page-alert ${type}`;
  alert.replaceChildren();
  const messageNode = document.createElement("span");
  messageNode.textContent = message;
  alert.append(messageNode);
  if (issues.length) {
    const list = document.createElement("ul");
    for (const issue of issues) {
      const item = document.createElement("li");
      item.textContent = issue;
      list.append(item);
    }
    alert.append(list);
  }
  alert.hidden = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function clearAlert() {
  const alert = document.getElementById("page-alert");
  alert.hidden = true;
  alert.textContent = "";
}

function setPortalVisible(visible) {
  document.getElementById("login-screen").hidden = visible;
  document.getElementById("portal").hidden = !visible;
}

function setUser(user) {
  state.user = user;
  const initial = (user.fullName || "师").slice(0, 1);
  document.getElementById("sidebar-name").textContent = user.fullName || "教师";
  document.getElementById("sidebar-id").textContent = user.professorId || "";
  document.getElementById("sidebar-avatar").textContent = initial;
  document.getElementById("top-avatar").textContent = initial;
  document.getElementById("welcome-name").textContent = user.fullName || "老师";
}

function switchPage(page) {
  if (!pageTitles[page]) return;
  state.page = page;
  document.querySelectorAll(".page-section").forEach((section) => {
    section.classList.toggle("active", section.id === `page-${page}`);
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === page);
  });
  document.getElementById("breadcrumb-title").textContent = pageTitles[page];
  clearAlert();
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(date);
}

function courseById(courseId) {
  return state.data?.catalog?.courses?.find((course) => course.courseId === courseId) || {};
}

function offeringById(offeringId) {
  return state.data?.catalog?.courseOfferings?.find((offering) => offering.offeringId === offeringId) || {};
}

function currentSemesterName() {
  return state.data?.semester?.name || state.data?.semesterId || "未设置学期";
}

function rosterCoursesForSelectedSemester() {
  const semesterId = state.data?.semesterId;
  return (state.data?.rosterCourses || []).filter((course) => course.semesterId === semesterId);
}

function offeringTime(offering) {
  const dayLabels = { MON: "周一", TUE: "周二", WED: "周三", THU: "周四", FRI: "周五", SAT: "周六", SUN: "周日" };
  const days = Array.isArray(offering.days) ? offering.days : String(offering.days || "").split(",");
  const labels = days.map((day) => dayLabels[String(day).trim().toUpperCase()] || String(day).trim()).filter(Boolean);
  const time = offering.startTime && offering.endTime ? `${offering.startTime}-${offering.endTime}` : "时间待定";
  return `${labels.join(" / ") || "日期待定"} · ${time}`;
}

function isActivePeriod() {
  return Boolean(state.data?.canEditTeaching);
}

function registrationLabel() {
  if (!state.data?.period) return { label: "未配置注册期", closed: true };
  if (state.data.canEditTeaching) return { label: "授课选择开放", closed: false };
  if (String(state.data.period.registration_status).toUpperCase() === "OPEN") {
    return { label: "开放日期外", closed: true };
  }
  return { label: "注册已关闭", closed: true };
}

async function loadData(semesterId = "") {
  const user = state.user;
  const path = semesterId
    ? `/api/teacher/data?semester=${encodeURIComponent(semesterId)}`
    : "/api/teacher/data";
  const data = await request(path);
  if (user !== state.user) return;
  state.data = data;
  state.selectedOfferingIds = [...(data.selectedOfferingIds || [])];
  const rosterCourses = (data.rosterCourses || []).filter((course) => course.semesterId === data.semesterId);
  if (!rosterCourses.some((course) => course.offeringId === state.activeOfferingId)) {
    state.activeOfferingId = rosterCourses[0]?.offeringId || "";
  }
  state.rosterData = null;
  state.rosterLoading = Boolean(state.activeOfferingId);
  renderAll();
  if (state.activeOfferingId) await loadRoster(state.activeOfferingId);
}

function renderTerms() {
  const select = document.getElementById("semester-select");
  const semesters = state.data?.semesters || [];
  select.innerHTML = semesters.map((semester) => (
    `<option value="${escapeHtml(semester.semesterId)}">${escapeHtml(semester.name)}</option>`
  )).join("");
  if (state.data?.semesterId) select.value = state.data.semesterId;
  select.disabled = semesters.length === 0;
}

function renderOverview() {
  const data = state.data;
  const selected = (data.eligibleOfferings || []).filter((offering) => state.selectedOfferingIds.includes(offering.offeringId));
  const uniqueSlots = new Set(selected.flatMap((offering) => {
    const days = Array.isArray(offering.days) ? offering.days : String(offering.days || "").split(",");
    return days.map((day) => `${day}-${offering.startTime || ""}-${offering.endTime || ""}`);
  }));
  const gradeCourses = data.gradeCourses || [];
  const gradeCount = gradeCourses.reduce((sum, course) => sum + Number(course.rosterCount || 0), 0);
  const periodStatus = registrationLabel();

  document.getElementById("home-semester-name").textContent = currentSemesterName();
  document.getElementById("registration-dates").textContent = data.period
    ? `注册 ${formatDate(data.period.registration_start_at)} - ${formatDate(data.period.registration_end_at)}`
    : "该学期未配置注册时间。";
  const statusNode = document.getElementById("registration-status");
  statusNode.textContent = periodStatus.label;
  statusNode.classList.toggle("closed", periodStatus.closed);
  document.getElementById("metric-teaching-count").textContent = String(selected.length);
  document.getElementById("metric-eligible-count").textContent = `${(data.eligibleOfferings || []).length} 个符合资格`;
  document.getElementById("metric-weekly-slots").textContent = String(uniqueSlots.size || "—");
  document.getElementById("metric-grade-courses").textContent = String(gradeCourses.length);
  document.getElementById("metric-grade-pending").textContent = gradeCount ? `${gradeCount} 名学生在名册中` : "暂无可录入名册";

  const list = document.getElementById("home-teaching-list");
  list.innerHTML = selected.length
    ? selected.slice(0, 5).map((offering) => {
      const course = offering.course || courseById(offering.courseId);
      return `<div class="preview-row"><div class="preview-course"><span class="course-code-mark">${escapeHtml(String(offering.courseId || "").slice(0, 4))}</span><span><strong>${escapeHtml(course.title || offering.courseId)}</strong><small>${escapeHtml(offering.courseId)} · ${escapeHtml(offering.offeringId)}</small></span></div><span class="preview-detail">${escapeHtml(offeringTime(offering))}</span><span class="preview-tag">已选择</span></div>`;
    }).join("")
    : '<div class="empty-state compact"><strong>当前学期还没有授课选择</strong><p>进入授课选择页面查看符合资格的开课。</p></div>';
}

function renderTeachingSelection() {
  const data = state.data;
  const offers = data.eligibleOfferings || [];
  const list = document.getElementById("eligible-course-list");
  document.getElementById("no-eligible-courses").hidden = offers.length > 0;
  document.getElementById("selection-total").textContent = String(state.selectedOfferingIds.length);
  document.getElementById("teaching-period-title").textContent = `${currentSemesterName()} · 授课计划`;
  const hint = document.getElementById("teaching-period-hint");
  const badge = document.getElementById("teaching-state-badge");
  if (isActivePeriod()) {
    hint.textContent = "注册开放期间可以增选或取消授课班次。保存时会统一检查时间冲突。";
    badge.textContent = "开放编辑";
    badge.className = "draft-badge open";
  } else {
    hint.textContent = "当前不在注册开放期间，只能查看已有授课选择。";
    badge.textContent = "只读";
    badge.className = "draft-badge closed";
  }
  list.innerHTML = offers.map((offering) => {
    const course = offering.course || courseById(offering.courseId);
    const selected = state.selectedOfferingIds.includes(offering.offeringId);
    const reserved = offering.reservedByOther;
    const status = String(offering.status || "").toUpperCase();
    const statusOpen = ["OPEN", "开放", "FULL", "已满"].includes(status);
    const disabled = !isActivePeriod() || reserved || (!statusOpen && !selected);
    const stateLabel = reserved
      ? (["SELECTED", "CONFIRMED"].includes(offering.assignmentStatus) ? "已由其他教师选择" : "此前由其他教师登记")
      : (selected ? "我的授课课程" : (statusOpen ? "可选择" : "当前未开放"));
    const statusClass = reserved ? "reserved-label" : (!statusOpen ? "closed" : "");
    const departments = Object.fromEntries((data.catalog.departments || []).map((item) => [item.departmentId, item.name]));
    const faculty = Object.fromEntries((data.catalog.faculty || []).map((item) => [item.facultyId, item.name]));
    return `<article class="course-card ${selected ? "selected" : ""} ${reserved ? "reserved" : ""}">
      <div class="course-card-top"><span class="course-dept">${escapeHtml(departments[course.departmentId] || course.departmentId || "课程")}</span><span class="course-code">${escapeHtml(offering.courseId)} · ${escapeHtml(offering.section || "01")}</span></div>
      <h3>${escapeHtml(course.title || offering.courseId)}</h3>
      <p class="course-description">${escapeHtml(course.description || "课程目录提供的开课信息。")}</p>
      <div class="course-facts"><span><b>资格</b>${offering.retainedWithoutEligibility ? "资格已撤销，仅保留已有授课" : escapeHtml(offering.courseId)}</span><span><b>教师</b>${escapeHtml(faculty[offering.facultyId] || offering.facultyId || "待安排")}</span><span><b>时间</b>${escapeHtml(offeringTime(offering))}</span><span><b>地点</b>${escapeHtml(offering.room || "待定")}</span></div>
      <div class="course-card-bottom"><span class="course-status ${statusClass}">${escapeHtml(stateLabel)}</span><button class="course-toggle" type="button" data-offering-id="${escapeHtml(offering.offeringId)}" ${disabled ? "disabled" : ""}>${selected ? "取消选择" : "选择授课"}</button></div>
    </article>`;
  }).join("");
  document.getElementById("save-teaching").disabled = !isActivePeriod();
}

function renderGradeCourses() {
  const courses = rosterCoursesForSelectedSemester();
  const list = document.getElementById("grade-course-list");
  document.getElementById("grade-course-total").textContent = String(courses.length);
  document.getElementById("no-grade-courses").hidden = courses.length > 0;
  list.innerHTML = courses.map((course) => {
    const active = course.offeringId === state.activeOfferingId;
    return `<button type="button" class="grade-course-button ${active ? "active" : ""}" data-grade-offering="${escapeHtml(course.offeringId)}"><span class="course-code-mark">${escapeHtml(course.courseId.slice(0, 4))}</span><span class="grade-course-text"><strong>${escapeHtml(course.courseTitle)}</strong><small>${escapeHtml(course.semesterName)} · ${escapeHtml(course.courseId)} · ${course.canEditGrades ? "可录成绩" : "仅查看名册"}</small></span><span class="grade-course-count">${Number(course.rosterCount || 0)}人</span></button>`;
  }).join("");
}

function renderRoster() {
  const title = document.getElementById("roster-title");
  const subtitle = document.getElementById("roster-subtitle");
  const count = document.getElementById("roster-count");
  const tbody = document.getElementById("roster-rows");
  const empty = document.getElementById("roster-empty");
  const saveButton = document.getElementById("save-grades");
  if (state.rosterLoading) {
    title.textContent = "正在读取名册";
    subtitle.textContent = "请稍候…";
    count.textContent = "…";
    tbody.innerHTML = "";
    empty.hidden = true;
    saveButton.disabled = true;
    return;
  }
  if (!state.rosterData) {
    title.textContent = "选择一门课程";
    subtitle.textContent = "名册只包含当前教师所授课程的学生。";
    count.textContent = "—";
    tbody.innerHTML = "";
    empty.hidden = true;
    saveButton.disabled = true;
    return;
  }
  const course = rosterCoursesForSelectedSemester().find((item) => item.offeringId === state.rosterData.offeringId);
  const rows = state.rosterData.roster || [];
  const canEditGrades = state.rosterData.canEditGrades === true;
  document.getElementById("roster-grade-hint").textContent = canEditGrades
    ? "成绩范围：A、B、C、D、F、I。留空表示跳过，不会清除已有成绩。"
    : "当前学期尚未结束，可查看名册；学期结束后才能录入或修改成绩。";
  title.textContent = course?.courseTitle || course?.courseId || state.rosterData.offeringId;
  subtitle.textContent = `${course?.semesterName || state.rosterData.semesterId} · ${course?.courseId || "开课"} · ${state.rosterData.offeringId}`;
  count.textContent = `${rows.length} 名学生`;
  empty.hidden = rows.length > 0;
  tbody.innerHTML = rows.map((student) => {
    const currentGrade = student.letter_grade || "";
    const options = ["", "A", "B", "C", "D", "F", "I"].map((grade) => {
      const label = grade || "留空";
      return `<option value="${grade}" ${grade === currentGrade ? "selected" : ""}>${label}</option>`;
    }).join("");
    const status = student.enrollment_status === "COMPLETED" ? "已完成" : "已注册";
    return `<tr><td>${escapeHtml(student.full_name)}${student.letter_grade ? `<small>上次录入：${escapeHtml(student.graded_at || "已有成绩")}</small>` : ""}</td><td>${escapeHtml(student.student_id)}</td><td><span class="status-pill ${student.enrollment_status === "COMPLETED" ? "" : "registered"}">${status}</span></td><td><select class="grade-select" data-enrollment-id="${Number(student.enrollment_id)}" aria-label="${escapeHtml(student.full_name)}的成绩" ${canEditGrades ? "" : "disabled"}>${options}</select></td></tr>`;
  }).join("");
  saveButton.disabled = !canEditGrades || rows.length === 0;
}

function renderAll() {
  renderTerms();
  renderOverview();
  renderTeachingSelection();
  renderGradeCourses();
  renderRoster();
}

async function loadRoster(offeringId) {
  const user = state.user;
  state.activeOfferingId = offeringId;
  state.rosterData = null;
  state.rosterLoading = true;
  renderGradeCourses();
  renderRoster();
  try {
    const data = await request(`/api/teacher/grades/roster?offering=${encodeURIComponent(offeringId)}`);
    if (user !== state.user || offeringId !== state.activeOfferingId) return;
    state.rosterData = data;
  } catch (error) {
    if (user === state.user && offeringId === state.activeOfferingId) showAlert(error.message, "error", error.issues);
  } finally {
    if (user === state.user && offeringId === state.activeOfferingId && state.data) {
      state.rosterLoading = false;
      renderGradeCourses();
      renderRoster();
    }
  }
}

function toggleTeachingOffering(offeringId) {
  const offering = (state.data.eligibleOfferings || []).find((item) => item.offeringId === offeringId);
  if (!offering || offering.reservedByOther || !isActivePeriod()) return;
  const index = state.selectedOfferingIds.indexOf(offeringId);
  if (index >= 0) state.selectedOfferingIds.splice(index, 1);
  else state.selectedOfferingIds.push(offeringId);
  clearAlert();
  renderOverview();
  renderTeachingSelection();
}

async function saveTeachingSelection() {
  const button = document.getElementById("save-teaching");
  button.disabled = true;
  try {
    const result = await request("/api/teacher/selections/save", {
      method: "POST",
      body: { semesterId: state.data.semesterId, offeringIds: state.selectedOfferingIds },
    });
    showAlert(result.message, "success");
    await loadData(state.data.semesterId);
  } catch (error) {
    showAlert(error.message, "error", error.issues);
    button.disabled = !isActivePeriod();
  }
}

async function saveGrades() {
  if (!state.rosterData?.canEditGrades) return;
  const selects = document.querySelectorAll(".grade-select[data-enrollment-id]");
  const grades = {};
  for (const select of selects) {
    if (select.value) grades[select.dataset.enrollmentId] = select.value;
  }
  const button = document.getElementById("save-grades");
  button.disabled = true;
  try {
    const result = await request("/api/teacher/grades/save", {
      method: "POST",
      body: { offeringId: state.rosterData.offeringId, grades },
    });
    showAlert(result.message, "success");
    await loadData(state.data.semesterId);
    switchPage("grades");
  } catch (error) {
    showAlert(error.message, "error", error.issues);
    button.disabled = !state.rosterData?.canEditGrades || !state.rosterData?.roster?.length;
  }
}

async function login(event) {
  event.preventDefault();
  const button = document.getElementById("login-button");
  const message = document.getElementById("login-message");
  const formData = new FormData(event.currentTarget);
  button.disabled = true;
  button.textContent = "正在验证…";
  message.textContent = "";
  try {
    const result = await request("/api/login", {
      method: "POST",
      body: { username: formData.get("username"), password: formData.get("password") },
    });
    state.csrfToken = result.user.csrfToken;
    setUser(result.user);
    setPortalVisible(true);
    try { await loadData(); }
    catch (dataError) { showAlert(dataError.message, "error", dataError.issues); }
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    button.innerHTML = '登录教师工作台 <span>→</span>';
  }
}

async function logout() {
  try { await request("/api/logout", { method: "POST", body: {} }); }
  catch { /* 会话失效时仍清除当前网页状态。 */ }
  clearLocalSession();
}

function clearLocalSession() {
  state.user = null;
  state.data = null;
  state.csrfToken = "";
  state.selectedOfferingIds = [];
  state.activeOfferingId = "";
  state.rosterData = null;
  state.rosterLoading = false;
  document.getElementById("password").value = "";
  document.getElementById("roster-rows").innerHTML = "";
  setPortalVisible(false);
}

async function restoreSession() {
  try {
    const session = await request("/api/session");
    if (!session.authenticated) return;
    state.csrfToken = session.csrfToken;
    setUser(session.user);
    setPortalVisible(true);
    await loadData();
  } catch {
    setPortalVisible(false);
  }
}

document.getElementById("login-form").addEventListener("submit", login);
document.getElementById("toggle-password").addEventListener("click", (event) => {
  const input = document.getElementById("password");
  input.type = input.type === "password" ? "text" : "password";
  event.currentTarget.textContent = input.type === "password" ? "显示" : "隐藏";
});
document.querySelectorAll(".demo-account").forEach((button) => button.addEventListener("click", () => {
  document.getElementById("username").value = button.dataset.username;
  document.getElementById("password").value = button.dataset.password;
  document.getElementById("login-message").textContent = "";
}));
document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => switchPage(button.dataset.page)));
document.querySelectorAll("[data-go-page]").forEach((button) => button.addEventListener("click", () => switchPage(button.dataset.goPage)));
document.getElementById("logout-button").addEventListener("click", logout);
document.getElementById("semester-select").addEventListener("change", async (event) => {
  try { await loadData(event.target.value); }
  catch (error) { showAlert(error.message, "error", error.issues); }
});
document.getElementById("eligible-course-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-offering-id]");
  if (button && !button.disabled) toggleTeachingOffering(button.dataset.offeringId);
});
document.getElementById("save-teaching").addEventListener("click", saveTeachingSelection);
document.getElementById("grade-course-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-grade-offering]");
  if (button) loadRoster(button.dataset.gradeOffering);
});
document.getElementById("save-grades").addEventListener("click", saveGrades);
document.getElementById("today-label").textContent = new Intl.DateTimeFormat("en", { weekday: "long", month: "long", day: "numeric" }).format(new Date()).toUpperCase();
restoreSession();
