// 通过当前入口的主机名跳转，方便在本机或校园局域网使用。
const hostname = window.location.hostname || "127.0.0.1";
document.querySelectorAll("a[data-port]").forEach((link) => {
  const target = new URL(`http://${hostname}/`);
  target.port = link.dataset.port;
  link.href = target.href;
});
