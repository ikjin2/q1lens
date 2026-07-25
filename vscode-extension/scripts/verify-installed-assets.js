const { createHash } = require("node:crypto");
const { existsSync, readFileSync } = require("node:fs");
const { homedir } = require("node:os");
const { join } = require("node:path");

const assetFiles = [
  "media/q1lens-icon.png",
  "media/qbs-timeline.svg",
  "out/src/q1timeline/media/timeline.css",
  "out/src/q1timeline/media/timeline.js",
  "out/src/q1timeline/media/timelineAdapter.js",
  "out/src/shared/timeline/renderer.css",
  "out/src/shared/timeline/renderer.js",
  "out/src/qbs/webview/assets/timeline.css",
  "out/src/qbs/webview/assets/timeline.js",
  "out/src/qbs/webview/assets/timelineModel.js",
];

function sha256(filePath) {
  return createHash("sha256").update(readFileSync(filePath)).digest("hex");
}

function compareInstalledAssets({ extensionRoot, installedRoot, files = assetFiles }) {
  const mismatches = [];

  for (const file of files) {
    const sourcePath = join(extensionRoot, file);
    const installedPath = join(installedRoot, file);

    if (!existsSync(sourcePath)) {
      mismatches.push({ file, reason: "source-missing" });
      continue;
    }

    if (!existsSync(installedPath)) {
      mismatches.push({ file, reason: "installed-missing" });
      continue;
    }

    if (sha256(sourcePath) !== sha256(installedPath)) {
      mismatches.push({ file, reason: "hash-mismatch" });
    }
  }

  return {
    ok: mismatches.length === 0,
    mismatches,
  };
}

function readManifest(extensionRoot) {
  return JSON.parse(readFileSync(join(extensionRoot, "package.json"), "utf8"));
}

function defaultInstalledRoot(extensionRoot, extensionsDir) {
  const manifest = readManifest(extensionRoot);
  return join(extensionsDir, `${manifest.publisher}.${manifest.name}-${manifest.version}`);
}

function parseArgs(argv) {
  const result = {
    extensionRoot: join(__dirname, ".."),
    extensionsDir: process.env.VSCODE_EXTENSIONS || join(homedir(), ".vscode", "extensions"),
    installedRoot: null,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const value = argv[index + 1];

    if (arg === "--extension-root") {
      result.extensionRoot = value;
      index += 1;
    } else if (arg === "--extensions-dir") {
      result.extensionsDir = value;
      index += 1;
    } else if (arg === "--installed-root") {
      result.installedRoot = value;
      index += 1;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  return result;
}

function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  const installedRoot = options.installedRoot || defaultInstalledRoot(options.extensionRoot, options.extensionsDir);
  const result = compareInstalledAssets({
    extensionRoot: options.extensionRoot,
    installedRoot,
  });

  if (!result.ok) {
    console.error(`Installed Q1Lens assets are stale or missing: ${installedRoot}`);
    for (const mismatch of result.mismatches) {
      console.error(`- ${mismatch.file}: ${mismatch.reason}`);
    }
    process.exitCode = 1;
    return result;
  }

  console.log(`Installed Q1Lens assets are fresh: ${installedRoot}`);
  return result;
}

if (require.main === module) {
  main();
}

module.exports = {
  assetFiles,
  compareInstalledAssets,
  defaultInstalledRoot,
  main,
  parseArgs,
};
