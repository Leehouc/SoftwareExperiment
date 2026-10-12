(() => {
  const $ = (selector) => document.querySelector(selector);
  const state = { courses: [], search: "", filter: "ALL" };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const dayNames = { MON: "周一", TUE: "周二", WED: "周三", THU: "周四", FRI: "周五", SAT: "周六", SUN: "周日" };

  const semesterId = new URLSearchParams(window.location.search).get("semester") || "";
  const stateBox = $("#course-state");
  const content = $("#course-content");
  const errorBox = $("#course-error");

  async function load() {
    if (!semesterId) {
      showError("链接缺少学期编号。请返回注册管理页面后，从对应学期卡片重新打开。", false);
      return;
    }
    try {
      const sessionResponse = await fetch("/api/session", { credentials: "same-origin" });
      const session = await sessionResponse.json();
      if (!session.authenticated) {
        window.location.replace("/");
        return;
      }
      const response = await fetch(`/api/admin/course-status?semester=${encodeURIComponent(semesterId)}`, {
        credentials: "same-origin", headers: { Accept: "application/json" },
      });
      const payload = await response.json().catch(() => ({}));
      if (response.status === 401) {
        window.location.replace("/");
        return;
      }
      if (!response.ok) throw new Error(payload.error || `课程信息读取失败（${response.status}）`);
      state.courses = payload.courses || [];
      $("#semester-name").textContent = payload.semester?.name || semesterId;
      $("#semester-subtitle").textContent = `${semesterId} · 任课教师、班级容量及学生注册情况`;
      $("#period-badge").textContent = `注册状态：${payload.registrationStatus === "OPEN" ? "开放" : payload.registrationStatus === "CLOSED" ? "已关闭" : "未配置"}`;
      renderSummary();
      renderRows();
      stateBox.hidden = true;
      content.hidden = false;
    } catch (error) {
      stateBox.hidden = true;
      showError(error.message, true);
    }
  }

  function showError(message, canRetry) {
    stateBox.hidden = true;
    errorBox.hidden = false;
    errorBox.innerHTML = `${esc(message)}${canRetry ? ` <button class="button subtle" id="retry-courses" type="button">重试</button>` : ""}`;
    $("#retry-courses")?.addEventListener("click", () => {
      errorBox.hidden = true;
      stateBox.hidden = false;
      load();
    });
  }

  function renderSummary() {
    const count = state.courses.length;
    const registered = state.courses.reduce((sum, course) => sum + Number(course.studentCount || 0), 0);
    const pending = state.courses.reduce((sum, course) => sum + Number(course.selectedCount || 0), 0);
    const attention = state.courses.filter((course) => ["教师已撤回", "教师不可用", "未关联教师"].includes(course.status)).length;
    const metrics = [
      ["开课班次", count, "来自课程目录的学期开课记录"],
      ["目录及系统学生", registered, "目录已有与本系统已注册的合计"],
      ["未提交主选", pending, "学生选课意向，不计入已注册人数"],
      ["教师需确认", attention, "未关联或已撤回教师的班次"],
    ];
    $("#course-summary").innerHTML = metrics.map(([label, value, note]) => `<article class="course-metric"><span>${label}</span><strong>${value}</strong><small>${note}</small></article>`).join("");
  }

  function filteredCourses() {
    const term = state.search.trim().toLocaleLowerCase();
    return state.courses.filter((course) => {
      const matchesText = !term || [course.courseId, course.courseTitle, course.section, course.offeringId, course.teacherName, course.room]
        .some((value) => String(value || "").toLocaleLowerCase().includes(term));
      let matchesFilter = true;
      if (state.filter === "OPEN") matchesFilter = course.status === "开放";
      else if (state.filter === "FULL") matchesFilter = course.status === "已满";
      else if (state.filter === "CLOSED") matchesFilter = ["注册已关闭", "教师已撤回", "教师不可用", "已关闭"].includes(course.status);
      else if (state.filter === "UNASSIGNED") matchesFilter = course.status === "未关联教师";
      return matchesText && matchesFilter;
    });
  }

  function statusClass(status) {
    if (status === "开放") return "status-open";
    if (status === "已满") return "status-full";
    if (["未关联教师", "教师已撤回", "教师不可用"].includes(status)) return "status-warning";
    return "status-muted";
  }

  function renderRows() {
    const courses = filteredCourses();
    $("#course-rows").innerHTML = courses.map((course) => {
      const days = (course.days || []).map((day) => dayNames[day] || day).join("、") || "时间待定";
      const time = course.startTime && course.endTime ? `${course.startTime}–${course.endTime}` : "时间待定";
      const room = course.room || "地点待定";
      const capacity = Number(course.capacity || 0);
      const count = Number(course.studentCount || 0);
      const width = capacity ? Math.min(100, Math.round(count / capacity * 100)) : 0;
      const full = Boolean(capacity && count >= capacity);
      const countDetail = `目录 ${Number(course.directoryStudentCount || 0)} · 系统 ${Number(course.systemStudentCount || 0)}`;
      return `<tr>
        <td><div class="course-title"><b>${esc(course.courseTitle)}</b><small>${esc(course.courseId)} · ${Number(course.credits || 0)} 学分 · ${esc(course.section || "班次待定")}<br>${esc(course.offeringId)}</small></div></td>
        <td><div class="course-teacher"><b>${esc(course.teacherName || "未分配")}</b><small>${esc(course.teacherSource || "")} ${course.professorId ? `· ${esc(course.professorId)}` : ""}</small></div></td>
        <td><div class="course-time"><b>${esc(days)} ${esc(time)}</b><small>${esc(room)}</small></div></td>
        <td><div class="course-capacity"><div class="course-capacity-line"><b>${count}</b><span>/ ${capacity || "不限"}</span></div>${capacity ? `<div class="course-meter"><i class="${full ? "full" : ""}" style="width:${width}%"></i></div>` : ""}<small>${esc(countDetail)}</small></div></td>
        <td><span class="course-intent">${Number(course.selectedCount || 0)}</span></td>
        <td><span class="course-status-pill ${statusClass(course.status)}">${esc(course.status)}</span></td>
      </tr>`;
    }).join("");
    $("#course-empty").hidden = courses.length !== 0;
    $("#course-count-label").textContent = `显示 ${courses.length} / ${state.courses.length} 个开课班次`;
  }

  $("#course-search").addEventListener("input", (event) => {
    state.search = event.target.value;
    renderRows();
  });
  $("#course-filter").addEventListener("change", (event) => {
    state.filter = event.target.value;
    renderRows();
  });

  load();
})();
