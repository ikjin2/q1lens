import { delimiter, resolve } from "node:path";
import { runTests } from "@vscode/test-electron";

async function main(): Promise<void> {
  const extensionDevelopmentPath = resolve(__dirname, "..", "..");
  const extensionTestsPath = resolve(__dirname, "suite", "index");
  const workspacePath = resolve(__dirname, "..", "..", "..");
  const pythonSourcePath = resolve(workspacePath, "src");
  process.env.PYTHONPATH = process.env.PYTHONPATH ? `${pythonSourcePath}${delimiter}${process.env.PYTHONPATH}` : pythonSourcePath;

  await runTests({
    extensionDevelopmentPath,
    extensionTestsPath,
    launchArgs: ["--disable-extensions", workspacePath],
  });
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
