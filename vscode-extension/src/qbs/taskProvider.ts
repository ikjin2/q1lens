import type * as vscodeTypes from "vscode";

export interface QbsTimelineTaskProject {
  project: string;
  outputDir: string;
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
    settings: QbsTimelineTaskExecutionSettings,
    scope: vscodeTypes.Task["scope"] = vscode.TaskScope.Workspace,
    name = definition.label,
  ): vscodeTypes.Task {
    const execution = buildTaskExecution(definition, settings);
    const task = new vscode.Task(
      definition,
      scope ?? vscode.TaskScope.Workspace,
      name,
      "qbsTimeline",
      new vscode.ShellExecution(execution.command, execution.args),
    );
    task.problemMatchers = [];
    return task;
  }

  async provideTasks(): Promise<vscodeTypes.Task[]> {
    const vscode = require("vscode") as typeof vscodeTypes;
    const { readSettings } = require("./settings") as typeof import("./settings");
    const settings = readSettings();
    const definitions = buildTaskDefinitions(await this.getProjects(), settings.outputDirOverride);
    return definitions.map((definition) => this.createTask(vscode, definition, settings));
  }

  resolveTask(task: vscodeTypes.Task): vscodeTypes.Task | undefined {
    const vscode = require("vscode") as typeof vscodeTypes;
    const { readSettings } = require("./settings") as typeof import("./settings");
    const settings = readSettings();
    const definition = resolveTaskDefinition(task.definition as Record<string, unknown>, settings.outputDirOverride);
    if (!definition) {
      return undefined;
    }
    const resolved = this.createTask(vscode, definition, settings, task.scope, task.name || definition.label);
    resolved.detail = task.detail;
    resolved.group = task.group;
    resolved.presentationOptions = task.presentationOptions;
    resolved.runOptions = task.runOptions;
    return resolved;
  }
}
