const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("q1lensDesktop", {
  getState: () => ipcRenderer.invoke("q1lens:getState"),
  openProject: () => ipcRenderer.invoke("q1lens:openProject"),
  selectPython: () => ipcRenderer.invoke("q1lens:selectPython"),
  analyze: () => ipcRenderer.invoke("q1lens:analyze"),
  openDemo: () => ipcRenderer.invoke("q1lens:openDemo"),
});
