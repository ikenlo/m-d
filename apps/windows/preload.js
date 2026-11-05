"use strict";
const { contextBridge } = require("electron");

contextBridge.exposeInMainWorld("mdsApp", { platform: "windows", version: "0.1.0" });
