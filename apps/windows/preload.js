// Ізольований preload: сторінці нічого не відкривається з Node.js.
// Лише позначка, що панель працює в застосунку для Windows (можна використати в інтерфейсі).
"use strict";
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("mdsApp", { platform: "windows", version: "0.1.0" });
