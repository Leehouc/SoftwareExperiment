(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const state = { user: null, csrf: "", data: null, view: "overview", query: "", statusFilter: "ALL", edit: null };
  const returnView = new URLSearchParams(window.location.search).get("view");
  if (["overview", "students", "professors", "registration", "billing"].includes(returnView)) {
    state.view = returnView;
  }
  const loginScreen = $("#login-screen");
  const shell = $("#app-shell");
  const page = $("#page-content");
  const loading = $("#loading-state");
  const dialog = $("#record-dialog");

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
  const dateText = (value) => {
    if (!value) return "—";
    const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
    return Number.isNaN(date.getTime()) ? String(value).slice(0, 10) : new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "short", day: "numeric" }).format(date);
  };
  const money = (cents) => new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(Number(cents || 0) / 100);
  const statusLabel = (status) => ({ ACTIVE: "在职 / 在读", INACTIVE: "已停用", GRADUATED: "已毕业", OPEN: "注册开放", CLOSED: "已关闭", PENDING: "待发送", RETRY: "等待重试", SENT: "已发送", FAILED: "发送失败" }[String(status || "").toUpperCase()] || status || "未知");
  const statusClass = (status) => ({ ACTIVE: "green", OPEN: "green", SENT: "green", PENDING: "amber", RETRY: "amber", FAILED: "red", INACTIVE: "gray", GRADUATED: "gray", CLOSED: "gray" }[String(status || "").toUpperCase()] || "gray");

  async function api(path, options = {}) {
    const user = state.user;
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (options.method && options.method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
    const response = await fetch(path, { credentials: "same-origin", ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== "/api/login" && user && user === state.user) showLogin();
    if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
    if (user && user !== state.user) throw new Error("登录状态已改变，请重新加载数据。");
    return payload;
  }

  function toast(message, kind = "") {
    const item = document.createElement("div");
    item.className = `toast ${kind}`;
    item.textContent = message;
    $("#toast-region").append(item);
    window.setTimeout(() => item.remove(), 3600);
  }

  function showLogin() {
    state.user = null;
    state.csrf = "";
    state.data = null;
    state.edit = null;
    page.replaceChildren();
    if (dialog.open) dialog.close();
    shell.hidden = true;
    loginScreen.hidden = false;
  }

  async function enterApp(user, csrf) {
    state.user = user;
    state.csrf = csrf;
    loginScreen.hidden = true;
    shell.hidden = false;
    $("#user-name").textContent = user.fullName || "教务管理员";
    $("#user-avatar").textContent = (user.fullName || "教").slice(0, 1);
    $("#today-label").textContent = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(new Date());
    await refreshData();
  }

  async function refreshData() {
    loading.hidden = false;
    page.innerHTML = "";
    try {
      state.data = await api("/api/admin/data");
      loading.hidden = true;
      $("#student-nav-count").textContent = state.data.students.length;
      $("#professor-nav-count").textContent = state.data.professors.length;
      const queueCount = Number(state.data.billingCounts?.PENDING || 0) + Number(state.data.billingCounts?.RETRY || 0);
      $("#billing-nav-count").hidden = !queueCount;
      render();
    } catch (error) {
      loading.hidden = true;
      page.innerHTML = `<section class="panel empty-state"><div class="empty-symbol">⌁</div><b>暂时无法读取工作台</b><p>${esc(error.message)}</p><button class="button subtle" id="retry-load">重新加载</button></section>`;
      $("#retry-load")?.addEventListener("click", refreshData);
    }
  }

  function heading(title, description, actions = "") {
    return `<div class="page-heading"><div><p class="eyebrow">REGISTRAR CONSOLE</p><h1>${title}</h1><p>${description}</p></div><div class="heading-actions">${actions}</div></div>`;
  }
  function pill(status) { return `<span class="pill ${statusClass(status)}">${esc(statusLabel(status))}</span>`; }

  function render() {
    if (!state.data) return;
    const titles = { overview: "总览", students: "学生档案", professors: "教师档案", registration: "注册期管理", billing: "账单队列" };
    $("#page-crumb").textContent = titles[state.view] || "总览";
    document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
    if (state.view === "students") page.innerHTML = renderPeople("student");
    else if (state.view === "professors") page.innerHTML = renderPeople("professor");
    else if (state.view === "registration") page.innerHTML = renderRegistration();
    else if (state.view === "billing") page.innerHTML = renderBilling();
    else page.innerHTML = renderOverview();
    bindPage();
  }

  function renderOverview() {
    const data = state.data;
    const activeStudents = data.students.filter((item) => item.status === "ACTIVE").length;
    const activeProfessors = data.professors.filter((item) => item.status === "ACTIVE").length;
    const openPeriods = data.periods.filter((item) => item.status === "OPEN").length;
    const pending = Number(data.billingCounts?.PENDING || 0) + Number(data.billingCounts?.RETRY || 0);
    const recentPeriods = data.periods.slice(0, 4);
    const openPeriod = data.periods.find((item) => item.status === "OPEN");
    return `${heading(`欢迎回来，${esc(state.user?.fullName || "管理员")}`, "管理学生与教师档案，查看并处理学期注册工作。", `<button class="button primary" data-view-link="students">＋ 新增学生</button>`)}
      <section class="stats-grid">
        <article class="stat-card"><div class="stat-top"><span>在读学生</span><span class="stat-icon">♙</span></div><div class="stat-value">${activeStudents}</div><div class="stat-foot">共 ${data.students.length} 份学生档案</div></article>
        <article class="stat-card"><div class="stat-top"><span>在职教师</span><span class="stat-icon">♧</span></div><div class="stat-value">${activeProfessors}</div><div class="stat-foot">共 ${data.professors.length} 份教师档案</div></article>
        <article class="stat-card"><div class="stat-top"><span>开放注册期</span><span class="stat-icon">▦</span></div><div class="stat-value">${openPeriods}</div><div class="stat-foot">${openPeriod ? `当前：${esc(openPeriod.name)}` : "当前没有开放注册期"}</div></article>
        <article class="stat-card"><div class="stat-top"><span>待处理账单</span><span class="stat-icon">＄</span></div><div class="stat-value">${pending}</div><div class="stat-foot">已发送 ${Number(data.billingCounts?.SENT || 0)} 笔</div></article>
      </section>
      <section class="dashboard-grid">
        <article class="panel"><div class="panel-head"><div><h2>学期注册</h2><p>注册期状态与开放时间</p></div><button class="text-link" data-view-link="registration">管理注册期 →</button></div>
          <div class="period-list">${recentPeriods.length ? recentPeriods.map((period) => `<div class="period-row"><span class="period-symbol">▦</span><div class="period-main"><b>${esc(period.name)}</b><small>${dateText(period.registrationStartAt)} — ${dateText(period.registrationEndAt)}</small></div>${pill(period.status)}</div>`).join("") : `<div class="empty-state">尚无注册期数据</div>`}</div>
        </article>
        <div class="overview-side"><article class="welcome-card"><p class="eyebrow">ADMINISTRATOR</p><h2>档案与注册，一站管理</h2><p>新增人员会同步建立登录账户。每项变更都会记入审计记录。</p></article>
          <article class="panel"><div class="panel-head"><div><h2>快捷操作</h2><p>常用管理入口</p></div></div><div class="quick-list">
            <button class="quick-action" data-view-link="students"><span class="quick-icon">♙</span><span class="quick-copy"><b>维护学生档案</b><small>新增、编辑或停用学生账户</small></span><span class="quick-arrow">→</span></button>
            <button class="quick-action" data-view-link="professors"><span class="quick-icon">♧</span><span class="quick-copy"><b>维护教师档案</b><small>更新教师信息与所属院系</small></span><span class="quick-arrow">→</span></button>
            <button class="quick-action" data-view-link="billing"><span class="quick-icon">＄</span><span class="quick-copy"><b>查看账单队列</b><small>${pending ? `${pending} 笔等待处理` : "查看注册关闭后生成的账单"}</small></span><span class="quick-arrow">→</span></button>
          </div></article>
        </div>
      </section>`;
  }

  function filteredPeople(kind) {
    const people = kind === "student" ? state.data.students : state.data.professors;
    const query = state.query.trim().toLowerCase();
    return people.filter((item) => {
      const matchesQuery = !query || [item.id, item.fullName, item.username, item.departmentId].some((value) => String(value || "").toLowerCase().includes(query));
      const matchesStatus = state.statusFilter === "ALL" || item.status === state.statusFilter;
      return matchesQuery && matchesStatus;
    });
  }

  function renderPeople(kind) {
    const isStudent = kind === "student";
    const title = isStudent ? "学生档案" : "教师档案";
    const description = isStudent ? "建立并维护学生身份、账户与预计毕业信息。" : "建立并维护教师身份、账户与所属院系。";
    const list = filteredPeople(kind);
    const rows = list.map((person) => {
      const active = person.status === "ACTIVE" && Number(person.accountActive) === 1;
      const department = state.data.catalog.departments.find((item) => item.departmentId === person.departmentId)?.name || person.departmentId;
      return `<tr><td><div class="person-cell"><span class="person-avatar ${isStudent ? "" : "purple"}">${esc((person.fullName || "?").slice(0, 1))}</span><span class="person-name"><b>${esc(person.fullName)}</b><small>${esc(person.username)}</small></span></div></td>
        <td><span>${esc(person.id)}</span></td><td>${isStudent ? dateText(person.graduationDate) : esc(department)}</td><td>${dateText(person.dateOfBirth)}</td><td>${pill(active ? "ACTIVE" : person.status)}</td>
        <td><div class="table-actions"><button class="row-action" data-action="edit" data-kind="${kind}" data-id="${esc(person.id)}">编辑</button>${!isStudent ? `<button class="row-action" data-action="eligibility" data-id="${esc(person.id)}">授课资格（${(person.eligibleCourseIds || []).length}）</button>` : ""}${active ? `<button class="row-action delete" data-action="deactivate" data-kind="${kind}" data-id="${esc(person.id)}">停用</button>` : ""}</div></td></tr>`;
    }).join("");
    return `${heading(title, description, `<button class="button primary" data-action="add" data-kind="${kind}">＋ 新增${isStudent ? "学生" : "教师"}</button>`)}
      <article class="table-panel"><div class="panel-head"><div><h2>人员列表</h2><p>共 ${list.length} 条记录 · 停用账户不能登录</p></div><div class="table-tools"><label class="searchbox"><input id="people-search" type="search" value="${esc(state.query)}" placeholder="搜索姓名、编号或用户名" aria-label="搜索人员"></label><select id="status-filter" class="filter-select" aria-label="筛选状态"><option value="ALL" ${state.statusFilter === "ALL" ? "selected" : ""}>全部状态</option><option value="ACTIVE" ${state.statusFilter === "ACTIVE" ? "selected" : ""}>在读 / 在职</option><option value="INACTIVE" ${state.statusFilter === "INACTIVE" ? "selected" : ""}>已停用</option>${isStudent ? `<option value="GRADUATED" ${state.statusFilter === "GRADUATED" ? "selected" : ""}>已毕业</option>` : ""}</select></div></div>
        <div class="table-scroll"><table><thead><tr><th>${isStudent ? "学生" : "教师"}</th><th>编号</th><th>${isStudent ? "预计毕业" : "所属院系"}</th><th>出生日期</th><th>档案状态</th><th>操作</th></tr></thead><tbody>${rows || `<tr><td colspan="6"><div class="empty-state"><div class="empty-symbol">⌕</div>没有匹配的档案</div></td></tr>`}</tbody></table></div>
        <div class="table-foot"><span>敏感证件号不会在列表中显示</span><span>共 ${list.length} 条</span></div></article>`;
  }

  function renderRegistration() {
    const periods = state.data.periods;
    const openCount = periods.filter((item) => item.status === "OPEN").length;
    const cards = periods.map((period) => {
      const canClose = period.status === "OPEN";
      const canReopen = period.status === "CLOSED";
      return `<article class="registration-card"><div class="registration-card-top"><div><h3>${esc(period.name)}</h3><p>学期编号：${esc(period.semesterId)}</p></div>${pill(period.status)}</div>
        <div class="date-row"><div class="date-pair"><small>选课注册</small><b>${dateText(period.registrationStartAt)} — ${dateText(period.registrationEndAt)}</b></div><div class="date-pair"><small>加退选</small><b>${dateText(period.addDropStartAt)} — ${dateText(period.addDropEndAt)}</b></div></div>
        ${period.closedAt ? `<div class="date-row"><div class="date-pair"><small>关闭时间</small><b>${dateText(period.closedAt)}</b></div></div>` : ""}
        <div class="card-actions"><button class="button subtle" data-action="course-status" data-semester="${esc(period.semesterId)}">查看课程情况 ↗</button>${canClose ? `<button class="button primary" data-action="close-registration" data-semester="${esc(period.semesterId)}" data-name="${esc(period.name)}">关闭本学期注册 →</button>` : canReopen ? `<button class="button subtle" data-action="reopen-registration" data-semester="${esc(period.semesterId)}" data-name="${esc(period.name)}">重新开启注册</button>` : ""}</div></article>`;
    }).join("");
    return `${heading("注册期管理", "核对注册窗口，并在选课结束后结算当学期注册。", `<span class="pill ${openCount ? "green" : "gray"}">${openCount} 个开放</span>`)}
      <section class="registration-grid"><div>${cards || `<article class="panel empty-state">数据库中没有注册期记录。</article>`}</div>
        <aside><article class="registration-card"><div class="eyebrow">注册期管理</div><h3>关闭与重新开放</h3><p>重新开放不会撤销既有结算；已结算学期再次关闭只切回关闭状态，不重复处理课表或账单。学生仍受注册时间范围限制。</p><div class="date-row"><div class="date-pair"><small>最低开课人数</small><b>${state.data.minimumEnrollment} 名学生</b></div><div class="date-pair"><small>每份课表上限</small><b>4 门课程</b></div></div></article>
          <div class="info-callout"><b>替代课与开课检查</b>优先保留有效主选，再按顺序尝试备选；检查授课教师、容量、先修课和时间冲突。低于最低人数或未分配教师的课程会被取消。</div>
          <div class="info-callout warning"><b>账单处理</b>关闭注册时按每学分 ¥${(state.data.feePerCreditCents / 100).toFixed(2)} 计算金额并写入队列；配置账单接口后，服务会发送并重试。</div>
        </aside></section>`;
  }

  function renderBilling() {
    const counts = state.data.billingCounts || {};
    const items = state.data.billing || [];
    const rows = items.map((item) => `<tr><td><span class="person-name"><b>${esc(item.studentName)}</b><small>${esc(item.studentId)}</small></span></td><td>${esc(item.semesterId)}</td><td class="amount">${money(item.amountCents)}</td><td>${pill(item.status)}</td><td>${Number(item.attemptCount || 0)}</td><td>${dateText(item.createdAt)}</td></tr>`).join("");
    return `${heading("账单队列", "注册期关闭后生成的账单记录；当前状态反映待发送的模拟队列。", "")}
      <section class="billing-summary"><article class="billing-stat"><small>待发送</small><b>${Number(counts.PENDING || 0)}</b></article><article class="billing-stat"><small>等待重试</small><b>${Number(counts.RETRY || 0)}</b></article><article class="billing-stat"><small>已发送</small><b>${Number(counts.SENT || 0)}</b></article><article class="billing-stat"><small>发送失败</small><b>${Number(counts.FAILED || 0)}</b></article></section>
      <div class="info-callout warning" style="margin-bottom:14px"><b>账单发送方式</b>当前记录保存在数据库队列中。配置外部账单接口后，服务会定时发送并在失败时继续重试；未配置接口时保持 PENDING，不会伪报为“已发送”。</div>
      <article class="table-panel"><div class="panel-head"><div><h2>最近账单交易</h2><p>最多显示最近 100 条</p></div></div><div class="table-scroll"><table><thead><tr><th>学生</th><th>学期</th><th>金额</th><th>状态</th><th>尝试次数</th><th>创建日期</th></tr></thead><tbody>${rows || `<tr><td colspan="6"><div class="empty-state"><div class="empty-symbol">＄</div>目前没有账单交易</div></td></tr>`}</tbody></table></div><div class="table-foot"><span>金额以人民币显示，数据库按分保存</span><span>${items.length} 条记录</span></div></article>`;
  }

  function field(label, name, value = "", options = {}) {
    const span = options.span ? " span-2" : "";
    const hint = options.hint ? `<small class="field-hint">${options.hint}</small>` : "";
    if (options.select) {
      return `<label class="${span}">${label}<select name="${name}" ${options.required ? "required" : ""}>${options.select.map(([v, text]) => `<option value="${esc(v)}" ${String(value) === String(v) ? "selected" : ""}>${esc(text)}</option>`).join("")}</select>${hint}</label>`;
    }
    return `<label class="${span}">${label}<input name="${name}" type="${options.type || "text"}" value="${esc(value)}" ${options.required ? "required" : ""} ${options.minLength ? `minlength="${options.minLength}"` : ""} ${options.placeholder ? `placeholder="${options.placeholder}"` : ""} ${options.autocomplete ? `autocomplete="${options.autocomplete}"` : ""}>${hint}</label>`;
  }

  function openRecord(kind, person = null) {
    const isStudent = kind === "student";
    state.edit = { kind, id: person?.id || null };
    $("#dialog-eyebrow").textContent = isStudent ? "STUDENT PROFILE" : "FACULTY PROFILE";
    $("#dialog-title").textContent = person ? `编辑${isStudent ? "学生" : "教师"}档案` : `新增${isStudent ? "学生" : "教师"}`;
    const departments = state.data.catalog.departments.map((item) => [item.departmentId, item.name]);
    const statuses = isStudent ? [["ACTIVE", "在读"], ["INACTIVE", "停用"], ["GRADUATED", "已毕业"]] : [["ACTIVE", "在职"], ["INACTIVE", "停用"]];
    let content = `${field("姓名", "fullName", person?.fullName || "", { required: true })}${field("出生日期", "dateOfBirth", person?.dateOfBirth || "", { type: "date", required: true })}`;
    content += isStudent
      ? field("预计毕业日期", "graduationDate", person?.graduationDate || "", { type: "date", required: true })
      : field("所属院系", "departmentId", person?.departmentId || "", { select: departments, required: true });
    content += field("档案状态", "status", person?.status || "ACTIVE", { select: statuses });
    content += field("登录用户名", "username", person?.username || "", { required: true, autocomplete: "username" });
    if (!isStudent) content += eligibilityFields(person?.eligibleCourseIds || []);
    content += field(person ? "重置密码（可留空）" : "初始密码", "password", "", { type: "password", required: !person, minLength: 10, autocomplete: person ? "new-password" : "new-password", placeholder: person ? "不修改请留空" : "至少 10 位" });
    content += field("社会安全号码（可选）", "ssn", "", { span: true, hint: "系统使用 AES-GCM 加密保存，不会在档案列表中显示。编辑时留空表示保留现有值。" });
    if (person) content += `<label class="checkline span-2"><input type="checkbox" name="clearSsn">清除已保存的加密号码</label>`;
    $("#dialog-fields").innerHTML = content;
    $("#record-form").dataset.kind = kind;
    dialog.showModal();
  }

  function eligibilityFields(selectedIds) {
    const selected = new Set(selectedIds);
    const courses = state.data.catalog.courses || [];
    const availableIds = new Set(courses.map((course) => course.courseId));
    const choices = [...courses, ...selectedIds.filter((id) => !availableIds.has(id))
      .map((id) => ({ courseId: id, title: "归档课程（可保留或移除）" }))];
    return `<fieldset class="eligibility-fields span-2" id="eligibility-fields"><legend>授课资格</legend>
      <p class="field-hint">勾选教师可讲授的课程，可跨院系选择。撤销资格不撤回已有授课；已有班次撤回后不能再次选择。</p>
      <div class="eligibility-list">${choices.map((course) => `<label class="checkline"><input type="checkbox" name="eligibleCourseIds" value="${esc(course.courseId)}" ${selected.has(course.courseId) ? "checked" : ""}><span>${esc(course.courseId)} · ${esc(course.title || course.courseId)}</span></label>`).join("") || '<p class="field-hint">课程目录暂无课程，请补齐目录后配置资格。</p>'}</div>
      <p class="field-hint">未勾选任何课程时，教师暂不能新增授课班次。</p></fieldset>`;
  }

  function bindPage() {
    document.querySelectorAll("[data-view-link]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.viewLink)));
    document.querySelectorAll("[data-action='add']").forEach((button) => button.addEventListener("click", () => openRecord(button.dataset.kind)));
    document.querySelectorAll("[data-action='edit']").forEach((button) => button.addEventListener("click", () => {
      const group = button.dataset.kind === "student" ? state.data.students : state.data.professors;
      const person = group.find((item) => item.id === button.dataset.id);
      if (person) openRecord(button.dataset.kind, person);
    }));
    document.querySelectorAll("[data-action='eligibility']").forEach((button) => button.addEventListener("click", () => {
      const person = state.data.professors.find((item) => item.id === button.dataset.id);
      if (!person) return;
      openRecord("professor", person);
      $("#eligibility-fields").scrollIntoView({ block: "nearest" });
      $("#eligibility-fields input")?.focus();
    }));
    document.querySelectorAll("[data-action='deactivate']").forEach((button) => button.addEventListener("click", async () => {
      const kindName = button.dataset.kind === "student" ? "学生" : "教师";
      if (!window.confirm(`停用${kindName} ${button.dataset.id}？账户将无法登录，课程、成绩和审计历史会保留。`)) return;
      try {
        await api(`/api/admin/${button.dataset.kind === "student" ? "students" : "professors"}/${encodeURIComponent(button.dataset.id)}`, { method: "DELETE" });
        toast("档案已停用，历史数据已保留。", "success");
        await refreshData();
      } catch (error) { toast(error.message, "error"); }
    }));
    document.querySelectorAll("[data-action='course-status']").forEach((button) => button.addEventListener("click", () => {
      window.location.assign(`/course-status?semester=${encodeURIComponent(button.dataset.semester)}`);
    }));
    document.querySelectorAll("[data-action='close-registration']").forEach((button) => button.addEventListener("click", async () => {
      const message = `确定关闭“${button.dataset.name}”注册期吗？\n\n首次关闭会执行选课校验、备选课填充、课程取消、课表结算和账单入队。若该学期此前已关闭并结算过，本次只会关闭注册状态，不会重复处理课表或账单。`;
      if (!window.confirm(message)) return;
      button.disabled = true;
      try {
        const payload = await api("/api/admin/registration/close", { method: "POST", body: JSON.stringify({ semesterId: button.dataset.semester, confirm: true }) });
        const result = payload.result;
        toast(result.alreadyFinalized
          ? "注册已关闭；本次没有重复结算课表、取消课程或生成账单。"
          : `注册已关闭：取消 ${result.cancelledOfferings.length} 门课程、${result.cancelledSchedules} 份课表，生成 ${result.billingTransactionsQueued} 笔待发送账单。`, "success");
        await refreshData();
      } catch (error) {
        button.disabled = false;
        toast(error.message, "error");
      }
    }));
    document.querySelectorAll("[data-action='reopen-registration']").forEach((button) => button.addEventListener("click", async () => {
      const message = `确定重新开启“${button.dataset.name}”的注册吗？\n\n这只会将注册状态改为开放，不会撤销已经完成的课表结算、课程取消或账单记录。学生能否修改课表仍由注册时间与加退选时间范围决定。`;
      if (!window.confirm(message)) return;
      button.disabled = true;
      try {
        await api("/api/admin/registration/reopen", { method: "POST", body: JSON.stringify({ semesterId: button.dataset.semester, confirm: true }) });
        toast("注册状态已重新开放；既有课表结算、课程取消和账单记录未更改。", "success");
        await refreshData();
      } catch (error) {
        button.disabled = false;
        toast(error.message, "error");
      }
    }));
    const search = $("#people-search");
    search?.addEventListener("input", () => {
      state.query = search.value;
      const start = search.selectionStart;
      render();
      const next = $("#people-search");
      next?.focus();
      next?.setSelectionRange(start, start);
    });
    $("#status-filter")?.addEventListener("change", (event) => { state.statusFilter = event.target.value; render(); });
  }

  function navigate(view) {
    state.view = view;
    state.query = "";
    state.statusFilter = "ALL";
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  $("#main-nav").addEventListener("click", (event) => {
    const button = event.target.closest("[data-view]");
    if (button) navigate(button.dataset.view);
  });

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const button = event.currentTarget.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const result = await api("/api/login", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) });
      const session = await api("/api/session");
      await enterApp(result.user, session.csrfToken);
    } catch (error) { toast(error.message, "error"); }
    finally { button.disabled = false; }
  });

  $("#load-demo-account").addEventListener("click", () => {
    const form = $("#login-form");
    form.elements.username.value = "registrar.demo";
    form.elements.password.value = "RegistrarDemo1!";
    form.elements.password.focus();
  });

  $("#logout-button").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST", body: "{}" }); } catch (_) { /* 退出时本地也清理登录页面状态。 */ }
    showLogin();
  });

  $("#record-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const body = Object.fromEntries(form.entries());
    body.clearSsn = form.get("clearSsn") === "on";
    const current = state.edit;
    if (!current) return;
    if (current.kind === "professor") body.eligibleCourseIds = form.getAll("eligibleCourseIds");
    const endpoint = current.kind === "student" ? "students" : "professors";
    const url = current.id ? `/api/admin/${endpoint}/${encodeURIComponent(current.id)}` : `/api/admin/${endpoint}`;
    try {
      await api(url, { method: current.id ? "PUT" : "POST", body: JSON.stringify(body) });
      dialog.close();
      toast(current.id ? "档案已保存。" : "档案已创建。", "success");
      if (current.kind === "professor" && !body.eligibleCourseIds.length) toast("尚未配置授课资格；教师暂不能新增授课班次。");
      await refreshData();
    } catch (error) { toast(error.message, "error"); }
  });

  document.querySelectorAll(".close-dialog").forEach((button) => button.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });

  (async () => {
    try {
      const session = await api("/api/session");
      if (session.authenticated) await enterApp(session.user, session.csrfToken);
      else showLogin();
    } catch (_) { showLogin(); }
  })();
})();
