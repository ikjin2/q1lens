import type * as vscodeTypes from "vscode";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { resolveFilePythonEnvironment } from "../pythonEnvironment";

export interface QbsTimelineTaskProject {
  project: string;
  outputDir: string;
  resource?: vscodeTypes.Uri;
}

export interface QbsTimelineTaskDefinition {
  type: "qbs-timeline";
  command: "analyze" | "render" | "analyzeAndRender";
  project: string;
  label: string;
  irPath: string;
  htmlPath: string;
  outputDir?: string;
}

export interface QbsTimelineTaskExecutionSettings {
  pythonPath: string;
  pythonArgs: string[];
}

function projectLabel(project: string): string {
  const label = project.replace(/[\\/]qbstimeline\.ya?ml$/i, "").replace(/\\/g, "/");
  return label.length > 0 ? label : ".";
}

function normalizeTaskPath(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+/g, "/");
}

function isAbsoluteTaskPath(path: string): boolean {
  const normalized = normalizeTaskPath(path);
  return /^[A-Za-z]:\//.test(normalized) || normalized.startsWith("/");
}

function joinTaskPath(...parts: string[]): string {
  return normalizeTaskPath(parts.filter((part) => part.length > 0).join("/"));
}

function taskProject(input: string | QbsTimelineTaskProject): QbsTimelineTaskProject {
  return typeof input === "string" ? { project: input, outputDir: ".qbs_timeline" } : input;
}

function buildArtifactPaths(project: string, configuredOutputDir: string, outputDirOverride: string | null): { irPath: string; htmlPath: string } {
  const outputDir = normalizeTaskPath(outputDirOverride ?? configuredOutputDir);
  const outputBase = isAbsoluteTaskPath(outputDir) ? outputDir : joinTaskPath(projectLabel(project), outputDir);
  return {
    irPath: joinTaskPath(outputBase, "qbs_ir.json"),
    htmlPath: joinTaskPath(outputBase, "index.html"),
  };
}

function taskLabel(command: QbsTimelineTaskDefinition["command"], label: string): string {
  if (command === "analyze") {
    return `Q1Lens: Analyze ${label}`;
  }
  if (command === "render") {
    return `Q1Lens: Render ${label}`;
  }
  return `Q1Lens: Analyze and Render ${label}`;
}

function isQbsTimelineCommand(command: unknown): command is QbsTimelineTaskDefinition["command"] {
  return command === "analyze" || command === "render" || command === "analyzeAndRender";
}

export function buildTaskDefinitions(
  projects: Array<string | QbsTimelineTaskProject>,
  outputDirOverride: string | null = null,
): QbsTimelineTaskDefinition[] {
  return projects.flatMap((project) => {
    const taskProjectInfo = taskProject(project);
    const label = projectLabel(taskProjectInfo.project);
    const paths = buildArtifactPaths(taskProjectInfo.project, taskProjectInfo.outputDir, outputDirOverride);
    return [
      {
        type: "qbs-timeline",
        command: "analyze",
        project: taskProjectInfo.project,
        label: taskLabel("analyze", label),
        outputDir: taskProjectInfo.outputDir,
        ...paths,
      },
      {
        type: "qbs-timeline",
        command: "render",
        project: taskProjectInfo.project,
        label: taskLabel("render", label),
        outputDir: taskProjectInfo.outputDir,
        ...paths,
      },
      {
        type: "qbs-timeline",
        command: "analyzeAndRender",
        project: taskProjectInfo.project,
        label: taskLabel("analyzeAndRender", label),
        outputDir: taskProjectInfo.outputDir,
        ...paths,
      },
    ];
  });
}

export function resolveTaskDefinition(
  rawDefinition: Record<string, unknown>,
  outputDirOverride: string | null = null,
): QbsTimelineTaskDefinition | undefined {
  if (
    rawDefinition.type !== "qbs-timeline" ||
    !isQbsTimelineCommand(rawDefinition.command) ||
    typeof rawDefinition.project !== "string" ||
    rawDefinition.project.length === 0
  ) {
    return undefined;
  }

  const outputDir = typeof rawDefinition.outputDir === "string" && rawDefinition.outputDir.length > 0 ? rawDefinition.outputDir : ".qbs_timeline";
  const paths = buildArtifactPaths(rawDefinition.project, outputDir, outputDirOverride);
  const label = typeof rawDefinition.label === "string" && rawDefinition.label.length > 0
    ? rawDefinition.label
    : taskLabel(rawDefinition.command, projectLabel(rawDefinition.project));

  return {
    type: "qbs-timeline",
    command: rawDefinition.command,
    project: rawDefinition.project,
    label,
    outputDir,
    irPath: typeof rawDefinition.irPath === "string" && rawDefinition.irPath.length > 0 ? rawDefinition.irPath : paths.irPath,
    htmlPath: typeof rawDefinition.htmlPath === "string" && rawDefinition.htmlPath.length > 0 ? rawDefinition.htmlPath : paths.htmlPath,
  };
}

export function buildTaskExecution(
  definition: QbsTimelineTaskDefinition,
  settings: QbsTimelineTaskExecutionSettings,
): { command: string; args: string[] } {
  const analyzeArgs = ["analyze", "--project", definition.project, "--out", definition.irPath];
  const renderArgs = ["render", "--ir", definition.irPath, "--out", definition.htmlPath];

  if (definition.command === "analyze") {
    return { command: settings.pythonPath, args: [...settings.pythonArgs, "-m", "q1lens", ...analyzeArgs] };
  }
  if (definition.command === "render") {
    return { command: settings.pythonPath, args: [...settings.pythonArgs, "-m", "q1lens", ...renderArgs] };
  }

  const script = [
    "import sys",
    "from q1lens.cli import main",
    `code = main(${JSON.stringify(analyzeArgs)})`,
    `sys.exit(code if code else main(${JSON.stringify(renderArgs)}))`,
  ].join("; ");
  return { command: settings.pythonPath, args: [...settings.pythonArgs, "-c", script] };
}

export class QbsTimelineTaskProvider implements vscodeTypes.TaskProvider {
  constructor(private readonly getProjects: () => Promise<Array<string | QbsTimelineTaskProject>>) {}

  private createTask(
    vscode: typeof vscodeTypes,
    definition: QbsTimelineTaskDefinition,
    resource?: vscodeTypes.Uri,
    scope: vscodeTypes.Task["scope"] = vscode.TaskScope.Workspace,
    name = definition.label,
  ): vscodeTypes.Task {
    const task = new vscode.Task(
      definition,
      scope ?? vscode.TaskScope.Workspace,
      name,
      "qbsTimeline",
      new vscode.CustomExecution(async (resolvedDefinition) => {
        const write = new vscode.EventEmitter<string>();
        const exit = new vscode.EventEmitter<number>();
        let child: ReturnType<typeof spawn> | undefined;
        let closed = false;
        const print = (text: string) => write.fire(text.replace(/\r?\n/g, "\r\n"));
        const finish = (code: number) => {
          if (closed) return;
          closed = true;
          exit.fire(code);
          write.dispose();
          exit.dispose();
        };
        return {
          onDidWrite: write.event,
          onDidClose: exit.event,
          open: () => {
            void (async () => {
              try {
                const resolved = resolveTaskDefinition({ ...definition, ...resolvedDefinition });
                if (!resolved) throw new Error("Invalid Q1Lens task definition.");
                const target = resource ?? taskResource(vscode, resolved.project, scope);
                if (!target) throw new Error("Set this Q1Lens task's workspace folder or use an absolute project path.");
                const environment = await resolveFilePythonEnvironment(vscode, target, "qbs");
                if (closed) return;
                const workspaceDir = vscode.workspace.getWorkspaceFolder(target)?.uri.fsPath ?? dirname(target.fsPath);
                const execution = buildTaskExecution({
                  ...resolved,
                  project: target.fsPath,
                  irPath: resolve(workspaceDir, resolved.irPath),
                  htmlPath: resolve(workspaceDir, resolved.htmlPath),
                }, environment);
                print(`Python: ${environment.pythonPath}\nSource: ${environment.sourceLabel}\nCommand: ${JSON.stringify([execution.command, ...execution.args])}\n\n`);
                child = spawn(execution.command, execution.args, {
                  cwd: dirname(target.fsPath),
                  shell: false,
                  windowsHide: true,
                  stdio: ["ignore", "pipe", "pipe"],
                });
                child.stdout?.setEncoding("utf8").on("data", print);
                child.stderr?.setEncoding("utf8").on("data", print);
                child.on("error", (error) => { print(`Q1Lens task failed: ${error.message}\n`); finish(1); });
                child.on("close", (code) => finish(code ?? 1));
              } catch (error) {
                if (!closed) print(`Q1Lens task failed: ${error instanceof Error ? error.message : String(error)}\n`);
                finish(1);
              }
            })();
          },
          close: () => {
            if (closed) return;
            closed = true;
            child?.kill();
            write.dispose();
            exit.dispose();
          },
        };
      }),
    );
    task.problemMatchers = [];
    return task;
  }

  async provideTasks(): Promise<vscodeTypes.Task[]> {
    const vscode = require("vscode") as typeof vscodeTypes;
    const { readSettings } = require("./settings") as typeof import("./settings");
    return (await this.getProjects()).flatMap((project) => {
      const info = taskProject(project);
      const resource = info.resource ?? taskResource(vscode, info.project);
      const settings = readSettings(resource);
      const scope = resource ? vscode.workspace.getWorkspaceFolder(resource) : undefined;
      return buildTaskDefinitions([info], settings.outputDirOverride)
        .map((definition) => this.createTask(vscode, definition, resource, scope));
    });
  }

  resolveTask(task: vscodeTypes.Task): vscodeTypes.Task | undefined {
    const vscode = require("vscode") as typeof vscodeTypes;
    const { readSettings } = require("./settings") as typeof import("./settings");
    const resource = typeof task.definition.project === "string"
      ? taskResource(vscode, task.definition.project, task.scope)
      : undefined;
    const settings = readSettings(resource);
    const definition = resolveTaskDefinition(task.definition as Record<string, unknown>, settings.outputDirOverride);
    if (!definition) {
      return undefined;
    }
    const resolved = this.createTask(vscode, definition, resource, task.scope, task.name || definition.label);
    resolved.definition = task.definition;
    resolved.detail = task.detail;
    resolved.group = task.group;
    resolved.presentationOptions = task.presentationOptions;
    resolved.runOptions = task.runOptions;
    return resolved;
  }
}

function taskResource(
  vscode: typeof vscodeTypes,
  project: string,
  scope?: vscodeTypes.Task["scope"],
): vscodeTypes.Uri | undefined {
  if (project.includes("${")) return undefined;
  if (isAbsoluteTaskPath(project)) return vscode.Uri.file(project);
  const folder = scope && typeof scope === "object"
    ? scope
    : vscode.workspace.workspaceFolders?.length === 1 ? vscode.workspace.workspaceFolders[0] : undefined;
  return folder ? vscode.Uri.joinPath(folder.uri, project) : undefined;
}
