const path = require("node:path");
const esbuild = require("esbuild");

esbuild.build({
  entryPoints: [path.join(__dirname, "../renderer/app.js")],
  outfile: path.join(__dirname, "../dist/renderer.js"),
  bundle: true,
  platform: "browser",
  format: "iife",
  target: "chrome130",
  sourcemap: true,
  logLevel: "info",
}).catch(() => process.exit(1));
