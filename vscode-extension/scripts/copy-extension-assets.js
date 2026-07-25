const { copyFileSync, mkdirSync, readdirSync, statSync } = require("node:fs");
const { dirname, join } = require("node:path");

function copyDir(sourceDir, targetDir) {
  mkdirSync(targetDir, { recursive: true });
  for (const entry of readdirSync(sourceDir, { withFileTypes: true })) {
    const source = join(sourceDir, entry.name);
    const target = join(targetDir, entry.name);
    if (entry.isDirectory()) {
      copyDir(source, target);
    } else if (entry.isFile()) {
      mkdirSync(dirname(target), { recursive: true });
      copyFileSync(source, target);
    }
  }
}

const copies = [
  ["src/shared/timeline", "out/src/shared/timeline"],
  ["src/qbs/webview/assets", "out/src/qbs/webview/assets"],
  ["src/q1timeline/media", "out/src/q1timeline/media"],
  ["src/q1timeline/syntaxes", "out/src/q1timeline/syntaxes"],
];

for (const [source, target] of copies) {
  const sourcePath = join(__dirname, "..", source);
  if (statSync(sourcePath, { throwIfNoEntry: false })) {
    copyDir(sourcePath, join(__dirname, "..", target));
  }
}
