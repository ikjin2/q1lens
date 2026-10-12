const { app, BrowserWindow, dialog, ipcMain, screen, session } = require("electron");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { analyzeProject, demoProject, detectPython, stopAnalysis } = require("./analysis.cjs");

const pagePath = path.join(__dirname, "renderer/index.html");
const pageUrl = pathToFileURL(pagePath).href;
let window;
let projectPath = null;
let pythonPath = detectPython();
let manualPython = false;
let busy = false;

function getState() {
  return { projectPath, projectName: projectPath ? path.basename(path.dirname(projectPath)) : "", pythonPath };
}

async function openSelectedProject(file) {
  const candidatePython = manualPython ? pythonPath : detectPython(file);
  const result = await analyzeProject(file, candidatePython);
  projectPath = file;
  pythonPath = candidatePython;
  return result;
}

function registerHandler(name, handler, exclusive = true) {
  ipcMain.handle(`q1lens:${name}`, async (event) => {
    if (event.sender !== window?.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== pageUrl) {
      throw new Error("This request did not come from the Q1Lens window.");
    }
    if (!exclusive) return handler();
    if (busy) throw new Error("An operation is already running. Please wait for it to finish.");
    busy = true;
    try {
      return await handler();
    } finally {
      busy = false;
    }
  });
}

registerHandler("getState", getState, false);
registerHandler("analyze", () => analyzeProject(projectPath, pythonPath));
registerHandler("openDemo", () => openSelectedProject(demoProject));
registerHandler("openProject", async () => {
  const result = await dialog.showOpenDialog(window, {
    title: "Open Q1Lens project or TimelineIR",
    properties: ["openFile"],
    filters: [{ name: "Q1Lens projects and timelines", extensions: ["yml", "yaml", "json"] }],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  return openSelectedProject(result.filePaths[0]);
});
registerHandler("selectPython", async () => {
  const result = await dialog.showOpenDialog(window, {
    title: "Select the Python interpreter for this project",
    properties: ["openFile"],
    ...(process.platform === "win32" ? { filters: [{ name: "Python executable", extensions: ["exe"] }] } : {}),
  });
  if (result.canceled || !result.filePaths[0]) return null;
  pythonPath = result.filePaths[0];
  manualPython = true;
  return getState();
});

function createWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  window = new BrowserWindow({
    width: Math.min(1520, width),
    height: Math.min(980, height),
    minWidth: Math.min(960, width),
    minHeight: Math.min(640, height),
    center: true,
    backgroundColor: "#080b12",
    title: "Q1Lens",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.webContents.on("will-attach-webview", (event) => event.preventDefault());
  window.on("closed", () => { window = null; });
  window.loadFile(pagePath);
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", stopAnalysis);
