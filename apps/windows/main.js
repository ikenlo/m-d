"use strict";

const { app, BrowserWindow, Menu, shell, screen } = require("electron");
const fs = require("fs");
const path = require("path");

const APP_URL = process.env.MDS_APP_URL || require("./package.json").appUrl;
const ORIGIN = new URL(APP_URL).origin;
const SAFE_EXTERNAL = new Set(["https:", "http:", "tel:", "mailto:", "tg:"]);

let win = null;

function isOurs(url) {
  try { return new URL(url).origin === ORIGIN; } catch (_) { return false; }
}

function openExternal(url) {
  try {
    if (SAFE_EXTERNAL.has(new URL(url).protocol)) shell.openExternal(url);
  } catch (_) {  }
}

const stateFile = () => path.join(app.getPath("userData"), "window.json");
function loadBounds() {
  try {
    const b = JSON.parse(fs.readFileSync(stateFile(), "utf8"));
    const visible = screen.getAllDisplays().some((d) => {
      const a = d.workArea;
      return b.x >= a.x - 50 && b.y >= a.y - 50 && b.x < a.x + a.width && b.y < a.y + a.height;
    });
    return visible ? b : { width: b.width, height: b.height };
  } catch (_) {
    return { width: 1280, height: 820 };
  }
}
function saveBounds() {
  if (!win || win.isDestroyed() || win.isMinimized()) return;
  try { fs.writeFileSync(stateFile(), JSON.stringify({ ...win.getNormalBounds(), max: win.isMaximized() })); } catch (_) {  }
}

function showOffline() {
  win.loadFile(path.join(__dirname, "offline.html"), { query: { u: APP_URL } });
}

function createWindow() {
  const b = loadBounds();
  win = new BrowserWindow({
    ...b,
    minWidth: 380,
    minHeight: 560,
    title: "MDS Connect",
    backgroundColor: "#14392C",
    icon: path.join(__dirname, "build", "icon.png"),
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });
  if (b.max) win.maximize();
  win.once("ready-to-show", () => win.show());
  win.on("close", saveBounds);

  const wc = win.webContents;
  wc.setUserAgent(`${wc.getUserAgent()} MDSConnectApp/${app.getVersion()} Windows`);
  wc.setWindowOpenHandler(({ url }) => {
    if (isOurs(url)) wc.loadURL(url); else openExternal(url);
    return { action: "deny" };
  });
  wc.on("will-navigate", (e, url) => {
    if (url.startsWith("file:")) return;
    if (!isOurs(url)) { e.preventDefault(); openExternal(url); }
  });
  wc.on("did-fail-load", (_e, code, _desc, url, isMainFrame) => {
    if (isMainFrame && code !== -3 && !String(url).startsWith("file:")) showOffline();
  });
  wc.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown") return;
    const k = input.key.toLowerCase();
    if (k === "f5" || ((input.control || input.meta) && k === "r")) { e.preventDefault(); wc.reload(); }
  });
  wc.setVisualZoomLevelLimits(1, 1);

  win.loadURL(APP_URL);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) { if (win.isMinimized()) win.restore(); win.focus(); }
  });
  app.whenReady().then(() => {
    Menu.setApplicationMenu(null);
    createWindow();
  });
  app.on("window-all-closed", () => app.quit());
}
