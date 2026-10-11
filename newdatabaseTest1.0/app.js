const statusPill = document.getElementById("connection-pill");
const connectionLabel = document.getElementById("connection-label");
const tableList = document.getElementById("table-list");
const tableSelect = document.getElementById("table-select");
const errorBanner = document.getElementById("error-banner");
const errorMessage = document.getElementById("error-message");
let csrfToken = "";
let authVersion = 0;

function showLogin(message = "") {
  authVersion += 1;
  csrfToken = "";
  document.getElementById("inspection-panel").hidden = true;
  document.getElementById("login-panel").hidden = false;
  document.getElementById("password").value = "";
  document.getElementById("login-message").textContent = message;
  tableList.replaceChildren();
  tableSelect.replaceChildren();
  tableSelect.disabled = true;
  document.getElementById("preview-head").replaceChildren();
  document.getElementById("preview-body").replaceChildren();
  for (const id of ["database-name", "server-version", "detail-database", "detail-user", "table-total", "record-total", "last-updated", "preview-meta"]) {
    document.getElementById(id).textContent = "—";
  }
}

async function api(path, body) {
  const version = authVersion;
  const headers = { Accept: "application/json" };
  const options = { cache: "no-store", credentials: "same-origin", headers };
  if (body !== undefined) {
    options.method = "POST";
    options.body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
    if (path !== "/api/login") headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(path, options);
  const result = await response.json();
  if (version !== authVersion) throw new Error("登录状态已改变，请重新加载数据。");
  if (response.status === 401 && path !== "/api/login") showLogin(result.error);
  if (!response.ok) throw new Error(result.error || "请求失败。");
  return result;
}

async function enterInspection(csrf) {
  csrfToken = csrf;
  document.getElementById("login-panel").hidden = true;
  document.getElementById("inspection-panel").hidden = false;
  await loadStatus();
}

function showError(message) {
  errorMessage.textContent = message || "请确认 MySQL 正在运行，并检查 MYSQL_* 连接配置。";
  errorBanner.hidden = false;
}

function clearError() {
  errorBanner.hidden = true;
  errorMessage.textContent = "";
}

function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

async function loadStatus() {
  const refreshButton = document.getElementById("refresh-button");
  refreshButton.disabled = true;
  refreshButton.textContent = "检查中…";
  statusPill.dataset.state = "loading";
  connectionLabel.textContent = "正在检查连接";
  clearError();

  try {
    const result = await api("/api/status");
    if (!result.ok) throw new Error(result.error || "数据库连接失败。");

    statusPill.dataset.state = "ok";
    connectionLabel.textContent = "数据库连接正常";
    document.getElementById("database-name").textContent = result.database_name || "未选择数据库";
    document.getElementById("server-version").textContent = result.server_version || "未知";
    document.getElementById("detail-database").textContent = result.database_name || "—";
    document.getElementById("detail-user").textContent = result.mysql_account || "—";
    document.getElementById("page-address").textContent = window.location.host;
    document.getElementById("table-total").textContent = formatNumber(result.tables.length);
    document.getElementById("record-total").textContent = formatNumber(
      result.tables.reduce((sum, table) => sum + table.count, 0),
    );
    document.getElementById("last-updated").textContent = `最近更新 ${new Date().toLocaleTimeString("zh-CN")}`;

    tableList.replaceChildren();
    tableSelect.replaceChildren();
    tableSelect.disabled = false;
    for (const table of result.tables) {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="table-name"></td>
        <td></td>
        <td class="number-cell"><span class="count-value"></span></td>
        <td><button class="preview-action" type="button">预览</button></td>`;
      row.cells[0].textContent = table.name;
      row.cells[1].textContent = table.label;
      row.cells[2].firstElementChild.textContent = formatNumber(table.count);
      row.querySelector("button").addEventListener("click", () => loadPreview(table.name));
      tableList.append(row);

      const option = document.createElement("option");
      option.value = table.name;
      option.textContent = `${table.label} · ${table.name}`;
      tableSelect.append(option);
    }

    if (result.tables.length) await loadPreview(result.tables[0].name);
  } catch (error) {
    statusPill.dataset.state = "error";
    connectionLabel.textContent = "连接失败";
    document.getElementById("database-name").textContent = "无法连接";
    document.getElementById("server-version").textContent = "—";
    document.getElementById("table-total").textContent = "—";
    document.getElementById("record-total").textContent = "—";
    tableList.innerHTML = '<tr><td colspan="4" class="empty-cell">无法读取数据表。</td></tr>';
    tableSelect.disabled = true;
    showError(error.message);
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "刷新状态";
  }
}

async function loadPreview(tableName) {
  if (!tableName) return;
  tableSelect.value = tableName;
  const previewMeta = document.getElementById("preview-meta");
  const previewHead = document.getElementById("preview-head");
  const previewBody = document.getElementById("preview-body");
  previewMeta.textContent = "正在读取…";

  try {
    const result = await api(`/api/preview?table=${encodeURIComponent(tableName)}`);
    if (!result.ok) throw new Error(result.error || "读取数据失败。");

    previewHead.replaceChildren();
    const headerRow = document.createElement("tr");
    for (const column of result.columns) {
      const cell = document.createElement("th");
      cell.textContent = column;
      headerRow.append(cell);
    }
    previewHead.append(headerRow);

    previewBody.replaceChildren();
    if (result.rows.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = result.columns.length || 1;
      cell.className = "empty-cell";
      cell.textContent = "这张表目前没有记录。";
      row.append(cell);
      previewBody.append(row);
    } else {
      for (const record of result.rows) {
        const row = document.createElement("tr");
        for (const column of result.columns) {
          const cell = document.createElement("td");
          const value = record[column];
          cell.textContent = value === null || value === undefined ? "—" : String(value);
          cell.title = cell.textContent;
          row.append(cell);
        }
        previewBody.append(row);
      }
    }
    previewMeta.textContent = `${result.label} · 显示 ${result.rows.length} 条，最多读取 ${result.limit} 条`;
    clearError();
  } catch (error) {
    previewMeta.textContent = "读取失败";
    previewHead.replaceChildren();
    previewBody.innerHTML = '<tr><td class="empty-cell">无法读取所选数据表。</td></tr>';
    showError(error.message);
  }
}

document.getElementById("refresh-button").addEventListener("click", loadStatus);
tableSelect.addEventListener("change", () => loadPreview(tableSelect.value));
document.getElementById("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("login-button");
  button.disabled = true;
  document.getElementById("login-message").textContent = "";
  try {
    const result = await api("/api/login", { username: document.getElementById("username").value,
      password: document.getElementById("password").value });
    document.getElementById("password").value = "";
    await enterInspection(result.csrfToken);
  } catch (error) {
    document.getElementById("login-message").textContent = error.message;
  } finally { button.disabled = false; }
});
document.getElementById("logout-button").addEventListener("click", async () => {
  try { await api("/api/logout", {}); }
  catch { /* 无论服务端会话是否已撤销，都清除本地预览。 */ }
  showLogin();
});
(async () => {
  try {
    const session = await api("/api/session");
    if (session.authenticated) await enterInspection(session.csrfToken);
  } catch (error) { showLogin(error.message); }
})();
