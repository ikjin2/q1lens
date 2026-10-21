import { spawn } from "node:child_process";

export interface ProcessInvocation {
  command: string;
  args: string[];
}

export interface ProcessResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
}

export function buildAnalyzeInvocation(input: {
  pythonPath: string;
  pythonArgs: string[];
  projectFile: string;
  irPath: string;
  outputDir?: string;
}): ProcessInvocation {
  return {
    command: input.pythonPath,
    args: [...input.pythonArgs, "-m", "q1lens", "analyze", "--project", input.projectFile,
      ...(input.outputDir ? ["--out-dir", input.outputDir] : []), "--out", input.irPath],
  };
}

export function buildRenderInvocation(input: {
  pythonPath: string;
  pythonArgs: string[];
  irPath: string;
  htmlPath: string;
}): ProcessInvocation {
  return {
    command: input.pythonPath,
    args: [...input.pythonArgs, "-m", "q1lens", "render", "--ir", input.irPath, "--out", input.htmlPath],
  };
}

export function buildQ1TimelineAnalyzeInvocation(input: {
  pythonPath: string;
  pythonArgs: string[];
  q1timelineCommand?: string | null;
  projectFile: string;
  timelineIrPath: string;
  diagnosticsPath: string;
}): ProcessInvocation {
  const directCommand = typeof input.q1timelineCommand === "string" ? input.q1timelineCommand.trim() : "";
  if (directCommand) {
    return {
      command: directCommand,
      args: q1timelineAnalyzeArgs(input),
    };
  }
  return {
    command: input.pythonPath,
    args: [
      ...input.pythonArgs,
      "-m",
      "q1lens",
      "q1timeline",
      ...q1timelineAnalyzeArgs(input),
    ],
  };
}

function q1timelineAnalyzeArgs(input: {
  projectFile: string;
  timelineIrPath: string;
  diagnosticsPath: string;
}): string[] {
  return [
    "analyze",
    "--project",
    input.projectFile,
    "--out",
    input.timelineIrPath,
    "--diagnostics",
    input.diagnosticsPath,
    "--format",
    "vscode-json",
    "--include-diagnostics",
    "--summary-only",
    "--mode",
    "normal",
    "--no-render",
  ];
}

export function runProcessWithSpawn(
  command: string,
  args: string[],
  options: { cwd: string; onOutput?: (chunk: string) => void; timeoutMs?: number },
): Promise<ProcessResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      shell: false,
      windowsHide: true,
    });

    let stdout = "";
    let stderr = "";
    let settled = false;
    const timeout =
      typeof options.timeoutMs === "number" && options.timeoutMs > 0
        ? setTimeout(() => {
            if (settled) {
              return;
            }
            settled = true;
            child.kill();
            const error = new Error(`Process timed out after ${options.timeoutMs} ms`);
            Object.assign(error, { stdout, stderr, code: "ETIMEDOUT" });
            reject(error);
          }, options.timeoutMs)
        : undefined;
    const settle = (callback: () => void) => {
      if (settled) {
        return;
      }
      settled = true;
      if (timeout) {
        clearTimeout(timeout);
      }
      callback();
    };

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");

    child.stdout.on("data", (text: string) => {
      stdout += text;
      options.onOutput?.(text);
    });

    child.stderr.on("data", (text: string) => {
      stderr += text;
      options.onOutput?.(text);
    });

    child.on("error", (error) => settle(() => reject(error)));
    child.on("close", (exitCode) => settle(() => resolve({ exitCode, stdout, stderr })));
  });
}
