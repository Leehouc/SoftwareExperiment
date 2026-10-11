const state = {
  user: null,
  csrfToken: "",
  data: null,
  page: "home",
  primary: [],
  alternate: [],
  dataVersion: 0,
  refreshInFlight: false,
  refreshTimer: null,
  recentlyFull: [],
  refreshMessage: "",
  refreshError: false,
  transcriptVersion: 0,
  transcriptLoading: false,
};

const AVAILABILITY_REFRESH_MS = 15_000;

const pageTitles = {
  home: "总览",
  register: "课程选择",
  schedule: "我的课表",
  transcript: "成绩单",
};

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

function setPortalVisible(isVisible) {
  document.getElementById("login-screen").hidden = isVisible;
  document.getElementById("portal").hidden = !isVisible;
}

function setUser(user) {
  state.user = user;
  const initial = (user.fullName || "学").slice(0, 1);
  document.getElementById("sidebar-name").textContent = user.fullName || "学生";
  document.getElementById("sidebar-id").textContent = user.studentId || "";
  document.getElementById("sidebar-avatar").textContent = initial;
  document.getElementById("top-avatar").textContent = initial;
  document.getElementById("welcome-name").textContent = `${user.fullName || "同学"}`;
  document.getElementById("transcript-student-name").textContent = user.fullName || "学生";
  document.getElementById("transcript-student-id").textContent = user.studentId || "";
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
  renderTerms();
  clearAlert();
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(date);
}

function semesterName(semesterId) {
  const match = [...(state.data?.semesters || []), ...(state.data?.transcriptSemesters || [])]
    .find((semester) => semester.semesterId === semesterId);
  return match?.name || semesterId || "未设置学期";
}

function getOfferings() {
  return state.data?.catalog?.courseOfferings || [];
}

function offerById(offeringId) {
  return getOfferings().find((offering) => offering.offeringId === offeringId);
}

function courseById(courseId) {
  return state.data?.catalog?.courses?.find((course) => course.courseId === courseId);
}

function currentSelections() {
  const primary = new Set(state.primary);
  const alternate = new Set(state.alternate);
  const choices = (state.data?.choices || []).filter((choice) => choice.selection_status !== "CANCELLED");
  if (choices.length && !primary.size && !alternate.size) {
    for (const choice of choices) {
      if (choice.choice_type === "PRIMARY") primary.add(choice.offering_id);
      if (choice.choice_type === "ALTERNATE") alternate.add(choice.offering_id);
    }
    state.primary = [...primary];
    state.alternate = [...alternate];
  }
}

async function loadData(semesterId = "") {
  const version = ++state.dataVersion;
  const user = state.user;
  const query = [];
  if (semesterId) query.push(`semester=${encodeURIComponent(semesterId)}`);
  if (state.data?.transcriptSemesterId) query.push(`transcriptSemester=${encodeURIComponent(state.data.transcriptSemesterId)}`);
  const url = `/api/student/data${query.length ? `?${query.join("&")}` : ""}`;
  const data = await request(url);
  if (version !== state.dataVersion || user !== state.user) return;
  state.transcriptVersion += 1;
  state.transcriptLoading = false;
  state.data = data;
  state.primary = [];
  state.alternate = [];
  state.recentlyFull = [];
  state.refreshMessage = "每 15 秒自动更新名额与授课信息，保留当前选择。";
  state.refreshError = false;
  currentSelections();
  renderAll();
  if (state.user) startAvailabilityRefresh();
  if (data.catalogError) showAlert(data.catalogError, "error");
}

const TRANSCRIPT_FIELDS = ["transcript", "transcriptSemesterId", "transcriptSemesters", "completedCourseCount"];

async function loadTranscript(semesterId) {
  if (!state.data) return;
  const version = ++state.transcriptVersion;
  const dataVersion = state.dataVersion;
  const user = state.user;
  state.transcriptLoading = true;
  renderTerms();
  renderTranscript();
  try {
    const data = await request(`/api/student/transcript${semesterId ? `?semester=${encodeURIComponent(semesterId)}` : ""}`);
    if (version !== state.transcriptVersion || dataVersion !== state.dataVersion || user !== state.user) return;
    for (const field of TRANSCRIPT_FIELDS) state.data[field] = data[field];
    renderOverview();
  } catch (error) {
    if (version === state.transcriptVersion && dataVersion === state.dataVersion && user === state.user) {
      showAlert(error.message, "error", error.issues);
    }
  } finally {
    if (version === state.transcriptVersion && dataVersion === state.dataVersion && user === state.user) {
      state.transcriptLoading = false;
      renderTerms();
      renderTranscript();
    }
  }
}

function offeringAvailability(offering, data = state.data) {
  const occupied = Number(offering.enrolledCount || 0) + Number(data?.offeringCounts?.[offering.offeringId] || 0);
  const capacity = Number(offering.capacity || 0);
  const status = String(offering.status || "").toUpperCase();
  const open = ["OPEN", "开放"].includes(status);
  const full = ["FULL", "已满"].includes(status) || (open && capacity > 0 && occupied >= capacity);
  return {
    full, unavailable: !open && !["FULL", "已满"].includes(status),
    label: full ? "当前已满" : (!open ? "当前未开放" : (capacity > 0 ? `余 ${Math.max(0, capacity - occupied)} 个名额` : "名额不限")),
  };
}

function renderAvailabilityNotice() {
  const registered = new Set((state.data?.enrollments || [])
    .filter((item) => item.enrollment_status === "ENROLLED" && state.primary.includes(item.offering_id))
    .map((item) => item.offering_id));
  const chosen = [...state.primary, ...state.alternate];
  const blocked = chosen.filter((id) => {
    const offering = offerById(id);
    return !registered.has(id) && (!offering || offeringAvailability(offering).full || offeringAvailability(offering).unavailable);
  });
  const changed = state.recentlyFull.filter((id) => !blocked.includes(id) && !registered.has(id));
  const label = (id) => {
    const offering = offerById(id);
    return offering ? `${courseById(offering.courseId)?.title || offering.courseId}（${id}）` : id;
  };
  const messages = [];
  if (blocked.length) messages.push(`所选未注册课程已满或不可用：${blocked.map(label).join("、")}。当前选择已保留，请调整后提交。`);
  if (changed.length) messages.push(`以下课程刚刚满员：${changed.map(label).join("、")}。`);
  if (state.refreshError) messages.push(state.refreshMessage);
  const notice = document.getElementById("catalog-notice");
  notice.textContent = messages.join(" ");
  notice.hidden = messages.length === 0;
  document.getElementById("catalog-refresh-status").textContent = state.refreshMessage;
}

function startAvailabilityRefresh() {
  if (state.refreshTimer !== null) window.clearInterval(state.refreshTimer);
  state.refreshTimer = window.setInterval(refreshAvailability, AVAILABILITY_REFRESH_MS);
}

function stopAvailabilityRefresh() {
  if (state.refreshTimer !== null) window.clearInterval(state.refreshTimer);
  state.refreshTimer = null;
  ++state.dataVersion;
}

async function refreshAvailability() {
  if (!state.user || !state.data || document.hidden || state.refreshInFlight) return;
  const previous = state.data;
  const semesterId = previous.semesterId;
  const version = state.dataVersion;
  const transcriptVersion = state.transcriptVersion;
  const user = state.user;
  state.refreshInFlight = true;
  try {
    const transcriptSemester = previous.transcriptSemesterId;
    const data = await request(`/api/student/data?semester=${encodeURIComponent(semesterId)}${transcriptSemester ? `&transcriptSemester=${encodeURIComponent(transcriptSemester)}` : ""}`);
    if (version !== state.dataVersion || state.user !== user || state.data?.semesterId !== semesterId) return;
    if (!data.catalog || data.catalogError) throw new RequestError(data.catalogError || "课程目录暂不可用。");
    const previousOffers = new Map((previous.catalog?.courseOfferings || []).map((item) => [item.offeringId, item]));
    const newlyFull = data.catalog.courseOfferings.filter((offering) => {
      const old = previousOffers.get(offering.offeringId);
      return offering.semesterId === semesterId && old && !offeringAvailability(old, previous).full
        && offeringAvailability(offering, data).full;
    }).map((offering) => offering.offeringId);
    if (transcriptVersion !== state.transcriptVersion || state.transcriptLoading) {
      for (const field of TRANSCRIPT_FIELDS) data[field] = state.data[field];
    }
    state.data = data;
    // 不调用 currentSelections，也不替换主选/备选，完整保留编辑中的空课表和选择顺序。
    state.recentlyFull = [...new Set([...state.recentlyFull, ...newlyFull])]
      .filter((id) => offerById(id) && offeringAvailability(offerById(id)).full);
    state.refreshError = false;
    state.refreshMessage = "名额与授课信息已更新；每 15 秒自动刷新，当前选择已保留。";
    renderAll();
  } catch (error) {
    if (version !== state.dataVersion || state.user !== user) return;
    state.refreshError = true;
    state.refreshMessage = `自动刷新失败：${error.message} 当前选择已保留，将在下一轮重试。`;
    renderAvailabilityNotice();
  } finally {
    state.refreshInFlight = false;
  }
}

function registrationLabel() {
  if (!state.data?.period) return { text: "未配置注册期", closed: true };
  if (String(state.data.period.registration_status).toUpperCase() !== "OPEN") {
    return { text: "注册已关闭", closed: true };
  }
  if (state.data.schedule?.status === "CANCELLED") return { text: "课表已取消", closed: true };
  if (state.data.previouslySubmitted) {
    return state.data.addDropOpen ? { text: "加退选开放", closed: false } : { text: "课表已提交", closed: true };
  }
  if (state.data.addDropOpen) return { text: "加退选开放", closed: false };
  if (state.data.registrationOpen) return { text: "注册开放", closed: false };
  return { text: String(state.data.period.registration_status).toUpperCase() === "OPEN" ? "注册未开放" : "注册已关闭", closed: true };
}

function scheduleDisplayState() {
  const schedule = state.data?.schedule;
  if (!schedule) return { label: "课表草稿", pending: false, submitted: false, cancelled: false };
  if (schedule.status === "CANCELLED") {
    return { label: "课表已取消", pending: false, submitted: false, cancelled: true };
  }
  if (state.data?.previouslySubmitted && schedule.status === "DRAFT") {
    return { label: "待提交变更", pending: true, submitted: false, cancelled: false };
  }
  if (schedule.status === "SUBMITTED" || schedule.status === "FINALIZED") {
    return { label: "课表已提交", pending: false, submitted: true, cancelled: false };
  }
  return { label: "课表草稿", pending: false, submitted: false, cancelled: false };
}

function renderTerms() {
  const select = document.getElementById("semester-select");
  const transcriptPage = state.page === "transcript";
  const semesters = (transcriptPage ? state.data?.transcriptSemesters : state.data?.semesters) || [];
  select.innerHTML = semesters.map((semester) => `<option value="${escapeHtml(semester.semesterId)}">${escapeHtml(semester.name)}</option>`).join("");
  if (!semesters.length) select.innerHTML = `<option value="">${transcriptPage ? "暂无已结束学期" : "暂无学期"}</option>`;
  select.value = (transcriptPage ? state.data?.transcriptSemesterId : state.data?.semesterId) || "";
  select.disabled = semesters.length === 0 || (transcriptPage && state.transcriptLoading);
  select.ariaLabel = transcriptPage ? "选择成绩单学期" : "选择学期";
}

function renderOverview() {
  const data = state.data;
  const primaryOffers = state.primary.map(offerById).filter(Boolean);
  const selectedOffers = [...state.primary, ...state.alternate].map(offerById).filter(Boolean);
  const enrolled = (data.enrollments || []).filter((item) => item.enrollment_status === "ENROLLED");
  const displayOffers = enrolled.length ? enrolled.map((item) => offerById(item.offering_id) || item) : primaryOffers;
  const credits = enrolled.length
    ? enrolled.reduce((sum, item) => sum + Number(item.credits_snapshot || 0), 0)
    : primaryOffers.reduce((sum, item) => sum + Number(courseById(item.courseId)?.credits || 0), 0);
  const selectedCount = state.primary.length + state.alternate.length;
  const percent = Math.round((Math.min(state.primary.length, 4) + Math.min(state.alternate.length, 2)) / 6 * 100);
  const term = semesterName(data.semesterId);
  const status = registrationLabel();

  document.getElementById("home-semester-name").textContent = term;
  document.getElementById("registration-dates").textContent = data.period
    ? `注册 ${formatDate(data.period.registration_start_at)} - ${formatDate(data.period.registration_end_at)} · 加退选 ${formatDate(data.period.add_drop_start_at)} - ${formatDate(data.period.add_drop_end_at)}`
    : "该学期未配置注册时间。";
  const statusNode = document.getElementById("registration-status");
  statusNode.textContent = status.text;
  statusNode.classList.toggle("closed", status.closed);
  document.getElementById("metric-course-count").textContent = String(enrolled.length || state.primary.length);
  document.getElementById("metric-credits").textContent = `${credits} 学分`;
  document.getElementById("metric-schedule-status").textContent = scheduleDisplayState().label;
  const weeklySlots = new Set(primaryOffers.flatMap((item) => (item.days || []).map((day) => (
    `${day}-${item.startTime || ""}-${item.endTime || ""}`
  ))));
  document.getElementById("metric-time-count").textContent = String(weeklySlots.size || "—");
  const completedCount = data.completedCourseCount ?? data.transcript?.length ?? 0;
  document.getElementById("metric-completed").textContent = String(completedCount);
  document.getElementById("metric-gpa").textContent = completedCount ? "历史成绩已同步" : "暂无历史成绩";
  document.getElementById("progress-value").textContent = `${percent}%`;
  document.getElementById("progress-ring").style.setProperty("--progress", `${percent}%`);
  document.getElementById("progress-title").textContent = selectedCount ? `已选择 ${selectedCount} 门课程` : "还未开始选课";
  const selectionHint = data.previouslySubmitted
    ? "加退选最多保留 4 门主选和 2 门备选，无需补满；提交后同步新增和退选。"
    : "初次提交需选择 4 门主选和 2 门备选，系统会检查先修要求和时间冲突。";
  document.getElementById("progress-description").textContent = selectionHint;
  document.getElementById("selection-hint").textContent = selectionHint;
  document.getElementById("primary-count").textContent = String(state.primary.length);
  document.getElementById("alternate-count").textContent = String(state.alternate.length);
  document.getElementById("schedule-primary-count").textContent = `${state.primary.length} / 4`;
  document.getElementById("schedule-alternate-count").textContent = `${state.alternate.length} / 2`;
  document.getElementById("schedule-credit-count").textContent = String(credits);
  document.getElementById("transcript-course-count").textContent = String(data.transcript?.length || 0);
  document.getElementById("transcript-completed-count").textContent = String(data.transcript?.length || 0);
  document.getElementById("schedule-edit-hint").textContent = data.canEdit
    ? (data.previouslySubmitted ? "当前处于加退选期，可提交课表变更。" : "注册期间可修改并提交课表。")
    : (data.previouslySubmitted ? "已提交课表只能在加退选期间修改。" : "当前不在注册时间内，暂时不能修改课表。");

  const summaryOffers = selectedOffers.length ? selectedOffers.slice(0, 4) : displayOffers.slice(0, 4);
  document.getElementById("home-course-list").innerHTML = summaryOffers.length
    ? summaryOffers.map((offering, index) => {
      const course = courseById(offering.courseId) || {};
      const id = offering.offeringId || "";
      const code = offering.courseId || "";
      const selectedType = state.primary.includes(id) ? "主选" : (state.alternate.includes(id) ? "备选" : "已注册");
      const statusClass = selectedType === "备选" ? "draft" : "";
      return `<div class="mini-course"><div class="mini-course-name"><span class="course-code-mark">${escapeHtml(code.slice(0, 4) || String(index + 1).padStart(2, "0"))}</span><span><strong>${escapeHtml(course.title || offering.course_title_snapshot || code)}</strong><small>${escapeHtml(code)}</small></span></div><span class="mini-course-detail">${escapeHtml((offering.days || []).map(dayName).join(" / ") || "课程目录开课")}</span><span class="mini-course-detail">${escapeHtml(offering.startTime || "")}${offering.endTime ? ` - ${escapeHtml(offering.endTime)}` : ""}</span><span class="course-status ${statusClass}">${selectedType}</span></div>`;
    }).join("")
    : '<div class="schedule-empty"><strong>尚未选择课程</strong><p>进入课程选择页面开始安排课表。</p></div>';
}

function dayName(day) {
  return ({ MON: "周一", TUE: "周二", WED: "周三", THU: "周四", FRI: "周五", SAT: "周六", SUN: "周日" })[String(day).toUpperCase()] || day;
}

function renderDepartmentFilter() {
  const select = document.getElementById("department-filter");
  const selected = select.value;
  const departments = state.data?.catalog?.departments || [];
  select.innerHTML = '<option value="">全部院系</option>' + departments.map((department) => `<option value="${escapeHtml(department.departmentId)}">${escapeHtml(department.name)}</option>`).join("");
  if (departments.some((department) => department.departmentId === selected)) select.value = selected;
}

function renderSlots(containerId, values, kind, total) {
  const container = document.getElementById(containerId);
  const className = kind === "alternate" ? "alternate-slots" : "";
  const entries = values.map((id, index) => {
    const offering = offerById(id) || {};
    const course = courseById(offering.courseId) || {};
    return `<div class="selected-slot ${className}"><span class="slot-index">${index + 1}</span><span class="selected-slot-text"><strong>${escapeHtml(course.title || offering.courseId || id)}</strong><small>${escapeHtml(offering.courseId || id)}</small></span><button class="remove-slot" type="button" data-remove-kind="${kind}" data-remove-id="${escapeHtml(id)}" aria-label="移除课程" ${!state.data?.canEdit ? "disabled" : ""}>×</button></div>`;
  });
  for (let index = values.length; index < total; index += 1) {
    entries.push(`<div class="slot-empty">${kind === "primary" ? "选择主选课程" : "选择备选课程"} · ${index + 1}</div>`);
  }
  container.innerHTML = entries.join("");
}

function renderCourseCatalog() {
  const list = document.getElementById("course-list");
  const catalog = state.data?.catalog;
  if (!catalog) {
    list.innerHTML = "";
    document.getElementById("catalog-course-count").textContent = "0";
    document.getElementById("no-courses").hidden = false;
    document.getElementById("no-courses").textContent = state.data?.catalogError || "课程目录暂不可用。";
    return;
  }

  const departments = Object.fromEntries((catalog.departments || []).map((item) => [item.departmentId, item.name]));
  const courses = Object.fromEntries((catalog.courses || []).map((item) => [item.courseId, item]));
  const search = document.getElementById("course-search").value.trim().toLowerCase();
  const departmentId = document.getElementById("department-filter").value;
  const selectedSemester = state.data.semesterId;
  const allOffers = (catalog.courseOfferings || []).filter((item) => item.semesterId === selectedSemester);
  const filtered = allOffers.filter((offering) => {
    const course = courses[offering.courseId] || {};
    const text = `${offering.courseId} ${course.title || ""} ${course.description || ""}`.toLowerCase();
    return (!search || text.includes(search)) && (!departmentId || course.departmentId === departmentId);
  });
  document.getElementById("catalog-course-count").textContent = String(filtered.length);
  document.getElementById("no-courses").hidden = filtered.length > 0;

  list.innerHTML = filtered.map((offering) => {
    const course = courses[offering.courseId] || {};
    const isPrimary = state.primary.includes(offering.offeringId);
    const isAlternate = state.alternate.includes(offering.offeringId);
    const selectedClass = isPrimary ? "selected-primary" : (isAlternate ? "selected-alternate" : "");
    const availability = offeringAvailability(offering);
    const full = availability.full || availability.unavailable;
    const seatLabel = availability.label;
    const teacher = state.data.teachingAssignments?.[offering.offeringId];
    const prerequisiteIds = [...new Set((catalog.prerequisites || [])
      .filter((item) => item.courseId === offering.courseId).map((item) => item.prerequisiteCourseId))];
    const prerequisites = prerequisiteIds.length
      ? prerequisiteIds.map((id) => `${id}${courses[id]?.title ? ` · ${courses[id].title}` : ""}`).join("；")
      : "无先修要求";
    const registered = (state.data.enrollments || []).some((item) => (
      item.offering_id === offering.offeringId && item.enrollment_status === "ENROLLED"
    ));
    const primaryDisabled = !state.data.canEdit || (full && !isPrimary && !(availability.full && registered));
    const alternateDisabled = !state.data.canEdit || (full && !isAlternate);
    const days = (offering.days || []).map(dayName).join(" / ");
    return `<article class="course-card ${selectedClass}">
      <div class="course-card-top"><span class="course-dept">${escapeHtml(departments[course.departmentId] || course.departmentId || "课程")}</span><span class="course-code">${escapeHtml(offering.courseId)} · ${escapeHtml(offering.section || "01")}</span></div>
      <h3 title="${escapeHtml(course.title || offering.courseId)}">${escapeHtml(course.title || offering.courseId)}</h3>
      <p class="course-card-description">${escapeHtml(course.description || "课程目录提供的开课信息。")}</p>
      <div class="course-facts"><span title="${escapeHtml(teacher?.fullName || "尚未安排授课教师")}"><b>教师</b>${escapeHtml(teacher?.fullName || "尚未安排")}</span><span><b>学分</b>${escapeHtml(course.credits || 0)}</span><span><b>时间</b>${escapeHtml(days)} ${escapeHtml(offering.startTime || "")}</span><span><b>地点</b>${escapeHtml(offering.room || "待定")}</span><div class="course-prerequisites"><b>先修课程</b><span>${escapeHtml(prerequisites)}</span></div></div>
      <div class="course-card-bottom"><span class="seat-text ${full ? "full" : ""}">${escapeHtml(seatLabel)}</span><div class="course-actions"><button class="course-pick ${isPrimary ? "active-primary" : ""}" type="button" data-pick-kind="primary" data-offering-id="${escapeHtml(offering.offeringId)}" ${primaryDisabled ? "disabled" : ""}>${isPrimary ? "主选 ✓" : "设为主选"}</button><button class="course-pick ${isAlternate ? "active-alternate" : ""}" type="button" data-pick-kind="alternate" data-offering-id="${escapeHtml(offering.offeringId)}" ${alternateDisabled ? "disabled" : ""}>${isAlternate ? "备选 ✓" : "设为备选"}</button></div></div>
    </article>`;
  }).join("");
  document.getElementById("selection-total").innerHTML = `${state.primary.length + state.alternate.length} <small>/ 6</small>`;
  document.getElementById("primary-slots-label").textContent = `${state.primary.length} / 4`;
  document.getElementById("alternate-slots-label").textContent = `${state.alternate.length} / 2`;
  renderSlots("primary-slots", state.primary, "primary", 4);
  renderSlots("alternate-slots", state.alternate, "alternate", 2);
  const badge = document.getElementById("draft-badge");
  const scheduleState = scheduleDisplayState();
  badge.textContent = scheduleState.pending ? "待提交变更" : (scheduleState.submitted ? "已提交" : "草稿");
  badge.classList.toggle("submitted", scheduleState.submitted);
  badge.classList.toggle("pending", scheduleState.pending);
  const editable = Boolean(state.data.canEdit);
  document.getElementById("save-draft").disabled = !editable;
  document.getElementById("submit-schedule").disabled = !editable;
  document.getElementById("register-hint").textContent = editable
    ? (state.data.previouslySubmitted ? "加退选期内，保存只更新草稿；提交后才会同步增退选。" : "可以先保存草稿，稍后再提交。")
    : "当前不在允许修改课表的时间段内。";
  renderAvailabilityNotice();
}

function renderSchedule() {
  const data = state.data;
  const schedule = data.schedule;
  const statusTag = document.getElementById("schedule-status-tag");
  const scheduleState = scheduleDisplayState();
  statusTag.textContent = scheduleState.pending ? "待提交变更" : (scheduleState.submitted ? "已提交" : (scheduleState.cancelled ? "已取消" : "草稿"));
  statusTag.classList.toggle("submitted", scheduleState.submitted);
  statusTag.classList.toggle("pending", scheduleState.pending);
  document.getElementById("schedule-banner-title").textContent = scheduleState.pending
    ? "课表变更尚未提交"
    : (scheduleState.submitted ? "课表已提交" : (scheduleState.cancelled ? "课表已取消" : "课表草稿"));
  document.getElementById("schedule-banner-copy").textContent = scheduleState.pending
    ? "已注册课程保持不变；提交变更后才会同步新增或退选。"
    : (scheduleState.submitted
      ? "当前显示已经保存到系统的注册记录。"
      : (scheduleState.cancelled
        ? "本学期课表已取消，历史注册记录仍予保留。"
        : "当前显示已保存的选课单；提交后主选课程才会登记为已注册。"));
  const rows = (data.enrollments || []).filter((item) => ["ENROLLED", "DROPPED", "CANCELLED"].includes(item.enrollment_status));
  document.getElementById("class-total").textContent = `${rows.filter((item) => item.enrollment_status === "ENROLLED").length} 门已注册`;
  document.getElementById("schedule-rows").innerHTML = rows.length ? rows.map((item) => {
    const offering = offerById(item.offering_id) || {};
    const course = courseById(item.course_id) || {};
    const active = item.enrollment_status === "ENROLLED";
    return `<tr><td><span class="schedule-course"><strong>${escapeHtml(course.title || item.course_title_snapshot || item.course_id)}</strong><small>${escapeHtml(item.course_id)} · ${escapeHtml(item.offering_id)}</small></span></td><td>${escapeHtml((offering.days || []).map(dayName).join(" / ") || "—")} ${escapeHtml(offering.startTime || "")}${offering.endTime ? ` - ${escapeHtml(offering.endTime)}` : ""}</td><td>${escapeHtml(offering.room || "—")}</td><td><span class="status-pill ${active ? "" : "dropped"}">${active ? "已注册" : "已退选"}</span></td></tr>`;
  }).join("") : "";
  document.getElementById("schedule-empty").hidden = rows.some((item) => item.enrollment_status === "ENROLLED");
  document.getElementById("delete-schedule").disabled = !schedule || !data.canEdit;
}

function renderTranscript() {
  const data = state.data;
  const select = document.getElementById("transcript-semester-select");
  const semesters = data.transcriptSemesters || [];
  select.innerHTML = semesters.length
    ? semesters.map((semester) => `<option value="${escapeHtml(semester.semesterId)}">${escapeHtml(semester.name)}</option>`).join("")
    : '<option value="">暂无已结束学期</option>';
  select.value = data.transcriptSemesterId || "";
  select.disabled = semesters.length === 0 || state.transcriptLoading;
  const term = semesters.find((semester) => semester.semesterId === data.transcriptSemesterId);
  document.getElementById("transcript-period-summary").textContent = state.transcriptLoading
    ? "正在加载成绩单…"
    : (term ? `当前查看：${term.name} · 可切换其他历史学期` : "目前没有可查看的已结束学期。");
  const faculty = Object.fromEntries((data.catalog?.faculty || []).map((item) => [item.facultyId, item.name]));
  const rows = data.transcript || [];
  document.getElementById("transcript-rows").innerHTML = rows.map((record) => `<tr><td class="semester-cell">${escapeHtml(semesterName(record.semester_id))}</td><td>${escapeHtml(record.course_title_snapshot)}</td><td>${escapeHtml(record.course_id)}</td><td>${escapeHtml(record.credits_snapshot)}</td><td><span class="grade-mark">${escapeHtml(record.letter_grade)}</span></td><td>${escapeHtml(faculty[record.professor_id] || record.professor_id)}</td></tr>`).join("");
  document.getElementById("transcript-empty").hidden = rows.length > 0;
  document.getElementById("transcript-course-count").textContent = String(rows.length);
  document.getElementById("transcript-completed-count").textContent = String(rows.length);
  document.getElementById("transcript-empty-copy").textContent = term
    ? `${term.name}暂未录入成绩，可切换其他历史学期查看。`
    : "学期结束并录入成绩后，可以在这里查看。";
}

function renderAll() {
  renderTerms();
  renderDepartmentFilter();
  renderOverview();
  renderCourseCatalog();
  renderSchedule();
  renderTranscript();
}

function toggleSelection(kind, offeringId) {
  if (!state.data?.canEdit) return;
  const own = kind === "primary" ? state.primary : state.alternate;
  const other = kind === "primary" ? state.alternate : state.primary;
  const index = own.indexOf(offeringId);
  if (index >= 0) own.splice(index, 1);
  else if (other.includes(offeringId)) {
    showAlert("这门开课已在另一组中，请先移除后再选择。", "warning");
    return;
  } else {
    const courseId = offerById(offeringId)?.courseId;
    const duplicateCourse = [...state.primary, ...state.alternate].some((selectedId) => (
      selectedId !== offeringId && offerById(selectedId)?.courseId === courseId
    ));
    if (duplicateCourse) {
      showAlert("同一门课程不能重复选择不同班次，也不能同时设为主选和备选。", "warning");
      return;
    }
    const max = kind === "primary" ? 4 : 2;
    if (own.length >= max) {
      showAlert(`${kind === "primary" ? "主选" : "备选"}最多选择 ${max} 门，请先移除一门再添加。`, "warning");
      return;
    }
    own.push(offeringId);
  }
  clearAlert();
  renderAll();
}

async function saveDraft() {
  try {
    const data = await request("/api/student/schedule/save", {
      method: "POST",
      body: { semesterId: state.data.semesterId, primary: state.primary, alternate: state.alternate },
    });
    showAlert(state.data.previouslySubmitted ? "变更草稿已保存；当前已注册课程保持不变，提交后才会同步调整。" : data.message, "success");
    await loadData(state.data.semesterId);
  } catch (error) {
    showAlert(error.message, "error", error.issues);
  }
}

async function submitSchedule() {
  const message = state.data.previouslySubmitted
    ? "提交变更后，未保留的已注册课程会被退选，所选主选课程会更新。继续吗？"
    : "提交后，4 门主选课程会登记为已注册。请确认课程、先修要求和时间安排。继续吗？";
  if (!window.confirm(message)) return;
  try {
    const data = await request("/api/student/schedule/submit", {
      method: "POST",
      body: { semesterId: state.data.semesterId, primary: state.primary, alternate: state.alternate },
    });
    showAlert(data.message, "success");
    await loadData(state.data.semesterId);
    switchPage("schedule");
  } catch (error) {
    showAlert(error.message, "error", error.issues);
  }
}

async function deleteSchedule() {
  if (!window.confirm("确定取消本学期课表吗？已注册课程将标记为退选，历史记录会保留。")) return;
  try {
    const data = await request("/api/student/schedule/delete", {
      method: "POST",
      body: { semesterId: state.data.semesterId, confirm: true },
    });
    showAlert(data.message, "success");
    await loadData(state.data.semesterId);
  } catch (error) {
    showAlert(error.message, "error", error.issues);
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
    try {
      await loadData();
    } catch (dataError) {
      showAlert(dataError.message, "error", dataError.issues);
    }
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
    button.innerHTML = '登录学生门户 <span>→</span>';
  }
}

async function logout() {
  stopAvailabilityRefresh();
  state.transcriptVersion += 1;
  state.transcriptLoading = false;
  try { await request("/api/logout", { method: "POST", body: {} }); }
  catch { /* 本地会话若已过期，仍清除网页状态并返回登录页。 */ }
  clearLocalSession();
}

function clearLocalSession() {
  stopAvailabilityRefresh();
  state.transcriptVersion += 1;
  state.transcriptLoading = false;
  state.user = null;
  state.data = null;
  state.csrfToken = "";
  state.primary = [];
  state.alternate = [];
  document.getElementById("password").value = "";
  document.getElementById("transcript-rows").innerHTML = "";
  document.getElementById("schedule-rows").innerHTML = "";
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
  try {
    if (state.page === "transcript") await loadTranscript(event.target.value);
    else await loadData(event.target.value);
  }
  catch (error) { showAlert(error.message, "error", error.issues); }
});
document.getElementById("transcript-semester-select").addEventListener("change", (event) => loadTranscript(event.target.value));
document.getElementById("course-search").addEventListener("input", renderCourseCatalog);
document.getElementById("department-filter").addEventListener("change", renderCourseCatalog);
document.getElementById("clear-filters").addEventListener("click", () => {
  document.getElementById("course-search").value = "";
  document.getElementById("department-filter").value = "";
  renderCourseCatalog();
});
document.getElementById("course-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-pick-kind]");
  if (button && !button.disabled) toggleSelection(button.dataset.pickKind, button.dataset.offeringId);
});
document.getElementById("primary-slots").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-kind]");
  if (button) toggleSelection(button.dataset.removeKind, button.dataset.removeId);
});
document.getElementById("alternate-slots").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-kind]");
  if (button) toggleSelection(button.dataset.removeKind, button.dataset.removeId);
});
document.getElementById("save-draft").addEventListener("click", saveDraft);
document.getElementById("submit-schedule").addEventListener("click", submitSchedule);
document.getElementById("delete-schedule").addEventListener("click", deleteSchedule);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshAvailability();
});
document.getElementById("today-label").textContent = new Intl.DateTimeFormat("en", { weekday: "long", month: "long", day: "numeric" }).format(new Date()).toUpperCase();
restoreSession();
