"""课程目录可视化网页的独立启动器。

先启动 app.py，再运行 python dashboard.py，然后访问 http://127.0.0.1:8001。
此文件提供网页并代理只读 API 请求，不需要修改 app.py 或安装额外依赖。
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


API_BASE = os.environ.get("CATALOG_API_URL", "http://127.0.0.1:8000").rstrip("/")
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.environ.get("DASHBOARD_PORT", "8001"))


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#f5f7f8">
  <title>课程目录 · 学术门户</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #172a3a;
      --muted: #71808c;
      --line: #e4e9ec;
      --paper: #ffffff;
      --canvas: #f5f7f8;
      --navy: #173d5b;
      --navy-dark: #102d46;
      --teal: #188478;
      --teal-soft: #e6f3f1;
      --gold: #d5a348;
      --shadow: 0 12px 35px rgba(22, 48, 67, .06);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--canvas);
      color: var(--ink);
      font-family: Inter, "Segoe UI", "Microsoft YaHei", sans-serif;
      font-size: 14px;
    }
    button, input, select { font: inherit; }
    button { cursor: pointer; }
    .shell { min-height: 100vh; }
    .topbar {
      height: 72px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 5.2vw;
      background: #fff;
      border-bottom: 1px solid var(--line);
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-mark {
      width: 38px; height: 38px; display: grid; place-items: center;
      border-radius: 11px; background: var(--navy); color: #fff;
      font-family: Georgia, serif; font-weight: 700; letter-spacing: -.08em;
    }
    .brand-name { font-family: Georgia, "Times New Roman", serif; font-size: 17px; font-weight: 700; letter-spacing: .01em; }
    .brand-sub { color: #94a0a9; font-size: 10px; letter-spacing: .15em; margin-top: 3px; text-transform: uppercase; }
    .top-actions { display: flex; align-items: center; gap: 18px; }
    .readonly { color: #78858d; font-size: 11px; letter-spacing: .1em; }
    .health { display: flex; align-items: center; gap: 8px; color: #64747e; font-size: 12px; }
    .health-dot { width: 8px; height: 8px; border-radius: 50%; background: #96a3aa; }
    .health-dot.ok { background: #39a58e; box-shadow: 0 0 0 4px #e3f3ee; }
    .health-dot.bad { background: #dc775b; box-shadow: 0 0 0 4px #fff0eb; }
    main { max-width: 1280px; margin: 0 auto; padding: 38px 34px 60px; }
    .eyebrow { color: var(--teal); font-size: 11px; font-weight: 700; letter-spacing: .18em; text-transform: uppercase; }
    .hero { display: flex; align-items: flex-end; justify-content: space-between; gap: 28px; margin-bottom: 28px; }
    h1 { margin: 9px 0 8px; font-family: Georgia, "Times New Roman", serif; font-size: clamp(30px, 4vw, 42px); font-weight: 500; letter-spacing: -.035em; }
    .hero-copy { margin: 0; color: var(--muted); font-size: 14px; }
    .hero-note { color: #87939a; font-size: 12px; padding-bottom: 4px; white-space: nowrap; }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin: 0 0 22px; }
    .stat-card { min-height: 104px; padding: 18px 20px; background: var(--paper); border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow); }
    .stat-label { color: #7d8b94; font-size: 12px; }
    .stat-value { display: flex; align-items: baseline; gap: 8px; margin-top: 11px; color: var(--navy-dark); font-family: Georgia, serif; font-size: 30px; }
    .stat-unit { color: #95a0a7; font-family: inherit; font-size: 12px; }
    .workspace { overflow: hidden; background: var(--paper); border: 1px solid var(--line); border-radius: 14px; box-shadow: var(--shadow); }
    .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 18px 22px 0; }
    .tabs { display: flex; align-items: center; gap: 4px; overflow-x: auto; }
    .tab { flex: 0 0 auto; border: 0; border-bottom: 2px solid transparent; padding: 13px 14px 12px; background: none; color: #788791; font-size: 13px; }
    .tab:hover { color: var(--navy); }
    .tab.active { border-bottom-color: var(--teal); color: var(--navy-dark); font-weight: 700; }
    .filters { display: flex; gap: 9px; padding: 14px 22px 18px; border-bottom: 1px solid var(--line); }
    .search-wrap { position: relative; flex: 1; min-width: 160px; }
    .search-icon { position: absolute; left: 12px; top: 10px; color: #9aa6ac; font-size: 15px; }
    .field { width: 100%; height: 38px; border: 1px solid #e0e6e9; border-radius: 7px; outline: none; background: #fff; color: var(--ink); }
    .field:focus { border-color: #7eb9ae; box-shadow: 0 0 0 3px rgba(24, 132, 120, .09); }
    input.field { padding: 0 12px 0 34px; }
    select.field { width: auto; min-width: 150px; padding: 0 32px 0 11px; }
    .content { min-height: 360px; padding: 22px; }
    .section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 14px; margin: 0 0 15px; }
    .section-heading h2 { margin: 0; font-family: Georgia, serif; font-size: 20px; font-weight: 500; }
    .section-heading p { margin: 5px 0 0; color: #89959c; font-size: 12px; }
    .result-count { color: #89959c; font-size: 12px; white-space: nowrap; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; text-align: left; }
    th { padding: 11px 13px; background: #f7f9fa; color: #89969e; font-size: 10px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; white-space: nowrap; }
    td { padding: 14px 13px; border-bottom: 1px solid #edf0f1; color: #53646e; font-size: 13px; vertical-align: middle; }
    tbody tr:last-child td { border-bottom: 0; }
    tbody tr:hover { background: #fbfcfc; }
    .primary-cell { color: #1d3446; font-weight: 650; }
    .sub-cell { display: block; margin-top: 4px; color: #95a0a6; font-size: 11px; font-weight: 400; }
    .code { color: #607582; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; white-space: nowrap; }
    .pill { display: inline-flex; align-items: center; gap: 5px; padding: 5px 9px; border-radius: 999px; background: #eff4f6; color: #627780; font-size: 11px; white-space: nowrap; }
    .pill.open { background: var(--teal-soft); color: #16796e; }
    .pill.closed { background: #f2f1ef; color: #827d74; }
    .pill-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
    .seat-track { width: 74px; height: 5px; overflow: hidden; border-radius: 9px; background: #e9eef0; }
    .seat-fill { height: 100%; border-radius: inherit; background: #4eaa99; }
    .seat-label { margin-top: 5px; color: #8a969d; font-size: 10px; }
    .course-grid, .entity-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 13px; }
    .course-card, .entity-card { padding: 17px; border: 1px solid var(--line); border-radius: 10px; background: #fff; transition: border-color .15s ease, transform .15s ease; }
    .course-card:hover, .entity-card:hover { border-color: #b4d2cb; transform: translateY(-2px); }
    .card-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    .course-card h3, .entity-card h3 { margin: 9px 0 6px; color: #20384a; font-size: 15px; }
    .course-card p, .entity-card p { min-height: 34px; margin: 0; color: #7b8991; font-size: 12px; line-height: 1.6; }
    .card-meta { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 15px; }
    .prereq-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 11px; }
    .prereq-row { display: grid; grid-template-columns: 1fr 28px 1fr; align-items: center; gap: 10px; padding: 15px; border: 1px solid var(--line); border-radius: 9px; }
    .prereq-course { color: #263d4f; font-size: 13px; font-weight: 650; }
    .prereq-course small { display: block; margin-top: 4px; color: #8b979e; font-size: 11px; font-weight: 400; }
    .arrow { color: var(--teal); text-align: center; font-size: 19px; }
    .overview-grid { display: grid; grid-template-columns: 1.15fr .85fr; gap: 18px; }
    .panel { padding: 18px; border: 1px solid var(--line); border-radius: 10px; }
    .panel h3 { margin: 0 0 15px; color: #2a4152; font-family: Georgia, serif; font-size: 17px; font-weight: 500; }
    .dept-row { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: center; margin: 14px 0; }
    .dept-name { color: #566872; font-size: 12px; }
    .dept-count { color: #8b979e; font-size: 11px; }
    .bar { height: 5px; margin-top: 7px; overflow: hidden; border-radius: 9px; background: #edf1f2; }
    .bar span { display: block; height: 100%; border-radius: inherit; background: #58a99b; }
    .upcoming-item { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 0; border-bottom: 1px solid #edf0f1; }
    .upcoming-item:last-child { border-bottom: 0; }
    .upcoming-name { color: #334b5b; font-size: 12px; font-weight: 600; }
    .upcoming-meta { margin-top: 4px; color: #8b979e; font-size: 11px; }
    .empty, .loading, .error { display: grid; min-height: 240px; place-items: center; color: #87939a; text-align: center; }
    .error { color: #a15f4c; }
    .empty strong, .error strong { display: block; margin-bottom: 6px; color: #4e626d; font-size: 14px; }
    .footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px 2px 0; color: #9aa4a9; font-size: 11px; }
    .footer a { color: #6e8c91; text-decoration: none; }
    .skeleton { height: 13px; margin: 10px 0; border-radius: 8px; background: linear-gradient(90deg,#edf1f2,#f8f9fa,#edf1f2); background-size: 200% 100%; animation: shimmer 1.3s infinite; }
    @keyframes shimmer { to { background-position: -200% 0; } }
    @media (max-width: 850px) {
      main { padding: 27px 20px 44px; }
      .stats { grid-template-columns: repeat(2, 1fr); }
      .course-grid, .entity-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .toolbar { align-items: flex-start; flex-direction: column; gap: 5px; }
      .tabs { width: 100%; }
      .overview-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      .topbar { height: 62px; padding: 0 17px; }
      .readonly { display: none; }
      main { padding: 23px 14px 36px; }
      .hero { align-items: flex-start; flex-direction: column; gap: 13px; }
      .hero-note { white-space: normal; }
      .stats { gap: 9px; }
      .stat-card { min-height: 90px; padding: 14px; }
      .stat-value { font-size: 25px; }
      .filters { flex-wrap: wrap; padding: 12px 14px 14px; }
      .search-wrap { flex-basis: 100%; }
      select.field { flex: 1; min-width: 0; }
      .content { padding: 17px 14px; }
      .course-grid, .entity-grid, .prereq-grid { grid-template-columns: 1fr; }
      .footer { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">CC</div>
        <div><div class="brand-name">Course Catalog</div><div class="brand-sub">Academic Services</div></div>
      </div>
      <div class="top-actions">
        <span class="readonly">READ ONLY · CATALOG</span>
        <div class="health"><span id="healthDot" class="health-dot"></span><span id="healthText">连接中</span></div>
      </div>
    </header>

    <main>
      <section class="hero">
        <div>
          <div class="eyebrow">ACADEMIC YEAR 2026—27</div>
          <h1>课程目录</h1>
          <p class="hero-copy">探索课程、开课安排与先修关系，规划你的下一学期。</p>
        </div>
        <div id="snapshotNote" class="hero-note">正在连接目录服务…</div>
      </section>

      <section id="stats" class="stats">
        <div class="stat-card"><div class="stat-label">目录课程</div><div class="skeleton" style="width:45%"></div></div>
        <div class="stat-card"><div class="stat-label">开课班次</div><div class="skeleton" style="width:38%"></div></div>
        <div class="stat-card"><div class="stat-label">院系</div><div class="skeleton" style="width:24%"></div></div>
        <div class="stat-card"><div class="stat-label">授课教师</div><div class="skeleton" style="width:30%"></div></div>
      </section>

      <section class="workspace">
        <div class="toolbar">
          <nav id="tabs" class="tabs" aria-label="目录视图">
            <button class="tab active" data-view="overview">总览</button>
            <button class="tab" data-view="courses">课程</button>
            <button class="tab" data-view="offerings">开课安排</button>
            <button class="tab" data-view="people">教师与学期</button>
            <button class="tab" data-view="prerequisites">先修关系</button>
          </nav>
        </div>
        <div class="filters">
          <div class="search-wrap"><span class="search-icon">⌕</span><input id="search" class="field" type="search" placeholder="搜索课程名称、编号或教师…" autocomplete="off"></div>
          <select id="departmentFilter" class="field" aria-label="选择院系"><option value="">全部院系</option></select>
          <select id="semesterFilter" class="field" aria-label="选择学期"><option value="">全部学期</option></select>
        </div>
        <div id="content" class="content"><div class="loading">正在读取课程目录…</div></div>
      </section>

      <footer class="footer">
        <span>课程目录数据 · 只读预览</span>
        <span>API 服务状态：<a href="/health" target="_blank" rel="noreferrer">查看健康状态 ↗</a></span>
      </footer>
    </main>
  </div>

  <script>
    const state = { catalog: null, view: "overview", courses: [], offerings: [], loading: false };
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
    const entityName = (list, key, value, labelKey) => (list || []).find((item) => item[key] === value)?.[labelKey] || value || "—";
    const courseById = (id) => (state.catalog?.courses || []).find((item) => item.courseId === id);
    const facultyName = (id) => entityName(state.catalog?.faculty, "facultyId", id, "name");
    const departmentName = (id) => entityName(state.catalog?.departments, "departmentId", id, "name");
    const semesterName = (id) => entityName(state.catalog?.semesters, "semesterId", id, "name");
    const dayNames = { MON:"周一", TUE:"周二", WED:"周三", THU:"周四", FRI:"周五", SAT:"周六", SUN:"周日" };
    const daysText = (days) => (days || []).map((day) => dayNames[day] || day).join(" / ") || "时间待定";
    const statusText = (status) => ({ OPEN:"开放", CLOSED:"已满", "开放":"开放", "已满":"已满" })[status] || status || "未知";
    const statusStyle = (status) => (status === "OPEN" || status === "开放") ? "open" : "closed";

    async function api(path) {
      const response = await fetch(path, { headers: { Accept: "application/json" } });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.error?.detail || body?.error?.message || `请求失败（${response.status}）`);
      return body.data ?? body;
    }

    function showError(message) {
      $("content").innerHTML = `<div class="error"><div><strong>暂时无法读取课程目录</strong>${esc(message)}<div style="margin-top:12px"><button class="pill" onclick="loadCatalog()">重新连接</button></div></div></div>`;
      $("healthDot").className = "health-dot bad";
      $("healthText").textContent = "连接异常";
    }

    async function loadCatalog() {
      $("content").innerHTML = '<div class="loading">正在读取课程目录…</div>';
      try {
        const [catalog, health] = await Promise.all([api("/api/v1/catalog"), api("/health")]);
        state.catalog = catalog;
        state.courses = catalog.courses || [];
        state.offerings = catalog.courseOfferings || [];
        $("departmentFilter").innerHTML = '<option value="">全部院系</option>' + (catalog.departments || []).map((item) => `<option value="${esc(item.departmentId)}">${esc(item.name)}</option>`).join("");
        $("semesterFilter").innerHTML = '<option value="">全部学期</option>' + (catalog.semesters || []).map((item) => `<option value="${esc(item.semesterId)}">${esc(item.name)}</option>`).join("");
        const ready = health.status === "ok" || health.status === "degraded";
        $("healthDot").className = `health-dot ${health.status === "degraded" ? "bad" : ready ? "ok" : ""}`;
        $("healthText").textContent = health.status === "degraded" ? "使用缓存数据" : ready ? "目录服务正常" : (health.status || "已连接");
        $("snapshotNote").textContent = health.cacheAgeSeconds != null ? `数据快照更新于 ${Math.round(health.cacheAgeSeconds)} 秒前` : "数据源已连接 · 实时读取";
        renderStats();
        await refreshFilteredData();
        render();
      } catch (error) {
        showError(error.message || "请确认 app.py 正在运行。环回地址默认是 127.0.0.1:8000。");
      }
    }

    async function refreshFilteredData() {
      if (!state.catalog) return;
      const department = $("departmentFilter").value;
      const semester = $("semesterFilter").value;
      const courseParams = new URLSearchParams();
      const offeringParams = new URLSearchParams();
      if (department) { courseParams.set("department", department); offeringParams.set("department", department); }
      if (semester) offeringParams.set("semester", semester);
      const courseUrl = "/api/v1/courses" + (courseParams.size ? `?${courseParams}` : "");
      const offeringUrl = "/api/v1/course-offerings" + (offeringParams.size ? `?${offeringParams}` : "");
      try {
        const [courses, offerings] = await Promise.all([api(courseUrl), api(offeringUrl)]);
        state.courses = courses;
        state.offerings = offerings;
        render();
      } catch (error) {
        showError(error.message || "筛选数据读取失败。");
      }
    }

    function renderStats() {
      const catalog = state.catalog;
      const values = [
        ["目录课程", (catalog.courses || []).length, "门"],
        ["开课班次", (catalog.courseOfferings || []).length, "个"],
        ["院系", (catalog.departments || []).length, "个"],
        ["授课教师", (catalog.faculty || []).length, "位"]
      ];
      $("stats").innerHTML = values.map(([label, value, unit]) => `<div class="stat-card"><div class="stat-label">${label}</div><div class="stat-value">${value}<span class="stat-unit">${unit}</span></div></div>`).join("");
    }

    function filteredCourses() {
      const query = $("search").value.trim().toLowerCase();
      return state.courses.filter((course) => `${course.courseId} ${course.title} ${course.description} ${departmentName(course.departmentId)}`.toLowerCase().includes(query));
    }

    function filteredOfferings() {
      const query = $("search").value.trim().toLowerCase();
      return state.offerings.filter((offering) => {
        const course = courseById(offering.courseId) || {};
        return `${offering.offeringId} ${offering.courseId} ${course.title || ""} ${facultyName(offering.facultyId)} ${offering.room || ""}`.toLowerCase().includes(query);
      });
    }

    function heading(title, subtitle, count) {
      return `<div class="section-heading"><div><h2>${title}</h2><p>${subtitle}</p></div>${count == null ? "" : `<span class="result-count">${count} 条记录</span>`}</div>`;
    }

    function renderOverview() {
      const catalog = state.catalog;
      const departments = catalog.departments || [];
      const maxCourses = Math.max(1, ...(departments.map((department) => catalog.courses.filter((course) => course.departmentId === department.departmentId).length)));
      const deptRows = departments.map((department) => {
        const count = catalog.courses.filter((course) => course.departmentId === department.departmentId).length;
        return `<div class="dept-row"><div><div class="dept-name">${esc(department.name)}</div><div class="bar"><span style="width:${Math.round(count / maxCourses * 100)}%"></span></div></div><span class="dept-count">${count} 门</span></div>`;
      }).join("");
      const upcoming = [...(catalog.courseOfferings || [])].slice(0, 5).map((offering) => {
        const course = courseById(offering.courseId) || {};
        return `<div class="upcoming-item"><div><div class="upcoming-name">${esc(course.title || offering.courseId)}</div><div class="upcoming-meta">${esc(offering.courseId)} · ${esc(semesterName(offering.semesterId))}</div></div><span class="pill ${statusStyle(offering.status)}">${esc(statusText(offering.status))}</span></div>`;
      }).join("");
      return `${heading("目录概览", "按院系浏览课程，查看本学年的开课安排。")}
        <div class="overview-grid">
          <section class="panel"><h3>院系课程分布</h3>${deptRows || '<div class="empty">暂无院系数据</div>'}</section>
          <section class="panel"><h3>开课预览</h3>${upcoming || '<div class="empty">暂无开课数据</div>'}</section>
        </div>`;
    }

    function renderCourses() {
      const courses = filteredCourses();
      if (!courses.length) return `${heading("课程目录", "浏览课程简介、所属院系和学分。", 0)}<div class="empty"><div><strong>没有找到匹配课程</strong>试试其他关键词或院系筛选。</div></div>`;
      const cards = courses.map((course) => {
        const prereqs = (state.catalog.prerequisites || []).filter((item) => item.courseId === course.courseId).map((item) => item.prerequisiteCourseId);
        return `<article class="course-card"><div class="card-top"><span class="code">${esc(course.courseId)}</span><span class="pill">${esc(course.credits)} 学分</span></div><h3>${esc(course.title)}</h3><p>${esc(course.description)}</p><div class="card-meta"><span class="pill">${esc(departmentName(course.departmentId))}</span>${prereqs.length ? `<span class="pill">先修：${prereqs.map(esc).join("、")}</span>` : '<span class="pill">无先修要求</span>'}</div></article>`;
      }).join("");
      return `${heading("课程目录", "浏览课程简介、所属院系和学分。", courses.length)}<div class="course-grid">${cards}</div>`;
    }

    function renderOfferings() {
      const offerings = filteredOfferings();
      if (!offerings.length) return `${heading("开课安排", "按学期和院系筛选教学班次。", 0)}<div class="empty"><div><strong>没有找到匹配的开课</strong>调整筛选条件或搜索关键词。</div></div>`;
      const rows = offerings.map((offering) => {
        const course = courseById(offering.courseId) || {};
        const pct = offering.capacity ? Math.min(100, Math.round(offering.enrolledCount / offering.capacity * 100)) : 0;
        return `<tr><td><span class="code">${esc(offering.courseId)}</span><span class="sub-cell">班次 ${esc(offering.section)}</span></td><td class="primary-cell">${esc(course.title || "课程信息缺失")}<span class="sub-cell">${esc(departmentName(course.departmentId))}</span></td><td>${esc(semesterName(offering.semesterId))}</td><td>${esc(facultyName(offering.facultyId))}</td><td>${esc(daysText(offering.days))}<span class="sub-cell">${esc(offering.startTime)}–${esc(offering.endTime)} · ${esc(offering.room)}</span></td><td><div class="seat-track"><div class="seat-fill" style="width:${pct}%"></div></div><div class="seat-label">${esc(offering.enrolledCount)} / ${esc(offering.capacity)} 人</div></td><td><span class="pill ${statusStyle(offering.status)}"><span class="pill-dot"></span>${esc(statusText(offering.status))}</span></td></tr>`;
      }).join("");
      return `${heading("开课安排", "按学期和院系筛选教学班次。", offerings.length)}<div class="table-wrap"><table><thead><tr><th>课程编号</th><th>课程</th><th>学期</th><th>授课教师</th><th>上课时间与地点</th><th>选课人数</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function renderPeople() {
      const faculty = state.catalog.faculty || [];
      const semesters = state.catalog.semesters || [];
      const query = $("search").value.trim().toLowerCase();
      const facultyCards = faculty.filter((person) => `${person.name} ${person.facultyId}`.toLowerCase().includes(query)).map((person) => {
        const count = (state.catalog.courseOfferings || []).filter((item) => item.facultyId === person.facultyId).length;
        return `<article class="entity-card"><span class="code">${esc(person.facultyId)}</span><h3>${esc(person.name)}</h3><p>负责 ${count} 个课程开课班次</p></article>`;
      }).join("");
      const semesterCards = semesters.filter((semester) => `${semester.name} ${semester.semesterId}`.toLowerCase().includes(query)).map((semester) => {
        const count = (state.catalog.courseOfferings || []).filter((item) => item.semesterId === semester.semesterId).length;
        return `<article class="entity-card"><span class="code">${esc(semester.semesterId)}</span><h3>${esc(semester.name)}</h3><p>${esc(semester.startDate)} — ${esc(semester.endDate)}<br>${count} 个开课班次</p></article>`;
      }).join("");
      return `${heading("教师与学期", "查看目录中的授课教师和学期信息。")}
        <div class="section-heading"><div><h2 style="font-size:16px">授课教师</h2></div><span class="result-count">${faculty.length} 位</span></div><div class="entity-grid">${facultyCards || '<div class="empty">没有匹配教师</div>'}</div>
        <div class="section-heading" style="margin-top:27px"><div><h2 style="font-size:16px">学期</h2></div><span class="result-count">${semesters.length} 个</span></div><div class="entity-grid">${semesterCards || '<div class="empty">没有匹配学期</div>'}</div>`;
    }

    function renderPrerequisites() {
      const query = $("search").value.trim().toLowerCase();
      const relations = (state.catalog.prerequisites || []).filter((relation) => {
        const course = courseById(relation.courseId) || {};
        const prerequisite = courseById(relation.prerequisiteCourseId) || {};
        return `${relation.courseId} ${relation.prerequisiteCourseId} ${course.title || ""} ${prerequisite.title || ""}`.toLowerCase().includes(query);
      });
      if (!relations.length) return `${heading("先修关系", "箭头从先修课程指向需要完成该课程的后续课程。", 0)}<div class="empty"><div><strong>没有找到先修关系</strong>当前目录没有匹配的先修课程。</div></div>`;
      const rows = relations.map((relation) => {
        const course = courseById(relation.courseId) || {};
        const prerequisite = courseById(relation.prerequisiteCourseId) || {};
        return `<div class="prereq-row"><div class="prereq-course">${esc(prerequisite.title || relation.prerequisiteCourseId)}<small>${esc(relation.prerequisiteCourseId)} · 先修课程</small></div><div class="arrow">→</div><div class="prereq-course">${esc(course.title || relation.courseId)}<small>${esc(relation.courseId)} · 后续课程</small></div></div>`;
      }).join("");
      return `${heading("先修关系", "箭头从先修课程指向需要完成该课程的后续课程。", relations.length)}<div class="prereq-grid">${rows}</div>`;
    }

    function render() {
      if (!state.catalog) return;
      const renderers = { overview: renderOverview, courses: renderCourses, offerings: renderOfferings, people: renderPeople, prerequisites: renderPrerequisites };
      document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === state.view));
      $("content").innerHTML = renderers[state.view]();
    }

    document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => { state.view = tab.dataset.view; render(); }));
    $("search").addEventListener("input", render);
    $("departmentFilter").addEventListener("change", refreshFilteredData);
    $("semesterFilter").addEventListener("change", refreshFilteredData);
    loadCatalog();
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    """提供网页，并将允许的只读请求转发给课程目录 API。"""

    server_version = "CourseCatalogDashboard/1.0"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - 按 BaseHTTPRequestHandler 的接口约定命名
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path != "/health" and not path.startswith("/api/v1/"):
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return

        request = Request(
            f"{API_BASE}{self.path}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=12) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "application/json; charset=utf-8")
                self._send(response.status, body, content_type)
        except HTTPError as error:
            self._send(error.code, error.read(), error.headers.get("Content-Type", "application/json; charset=utf-8"))
        except (URLError, TimeoutError) as error:
            message = json.dumps(
                {
                    "error": {
                        "code": "CATALOG_API_UNAVAILABLE",
                        "message": "无法连接课程目录 API，请确认 app.py 已启动。",
                        "detail": str(error),
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self._send(502, message, "application/json; charset=utf-8")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"课程目录可视化页面：http://{HOST}:{PORT}")
    print(f"目录 API 地址：{API_BASE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止可视化页面服务。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
