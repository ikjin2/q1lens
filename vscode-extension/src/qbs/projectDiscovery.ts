import { dirname, isAbsolute, join, normalize, resolve } from "node:path";
import { parse } from "yaml";

export interface ProjectConfigLite {
  scheduleFile?: string;
  scheduleNotebook?: string;
  sourceNotebook?: string;
  setupTags?: string[];
  scheduleTag?: string;
  scheduleVariable?: string;
  compilerVariable?: string;
  outputDir: string;
}

export interface OutputPaths {
  projectFile: string;
  projectDir: string;
  schedulePath?: string;
  scheduleNotebookPath?: string;
  sourceNotebookPath?: string;
  outputDir: string;
  irPath: string;
  htmlPath: string;
  q1asmDir: string;
}

export interface ProjectChoice {
  label: string;
  description: string;
  path: string;
}

function resolveFromProject(projectDir: string, value: string): string {
  return isAbsolute(value) ? normalize(value) : resolve(projectDir, value);
}

export function isProjectFile(filePath: string): boolean {
  const normalized = normalize(filePath).replace(/\\/g, "/").toLowerCase();
  return normalized.endsWith("/qbstimeline.yml") || normalized.endsWith("/qbstimeline.yaml");
}

export async function chooseProjectFile(options: {
  activeFile: string | null;
  discoveredFiles: string[];
  choose: (items: ProjectChoice[]) => Promise<string | undefined>;
}): Promise<string | undefined> {
  if (options.activeFile && isProjectFile(options.activeFile)) {
    return options.activeFile;
  }

  const uniqueFiles = [...new Set(options.discoveredFiles)].sort();
  if (uniqueFiles.length === 0) {
    return undefined;
  }
  if (uniqueFiles.length === 1) {
    return uniqueFiles[0];
  }

  return options.choose(
    uniqueFiles.map((path) => ({
      label: path.split(/[\\/]/).slice(-2).join("/"),
      description: path,
      path,
    })),
  );
}

export function parseProjectConfigLite(text: string): ProjectConfigLite {
  const parsed = parse(text) as {
    schedule?: {
      file?: unknown;
      notebook?: unknown;
      setup_tags?: unknown;
      schedule_tag?: unknown;
      schedule_variable?: unknown;
      compiler_variable?: unknown;
    };
    source?: { notebook?: unknown };
    outputs?: { dir?: unknown };
  } | null;

  const scheduleFile = parsed?.schedule?.file;
  const scheduleNotebook = parsed?.schedule?.notebook;
  const scheduleFileValue = typeof scheduleFile === "string" && scheduleFile.length > 0 ? scheduleFile : undefined;
  const scheduleNotebookValue = typeof scheduleNotebook === "string" && scheduleNotebook.length > 0 ? scheduleNotebook : undefined;
  if (!scheduleFileValue && !scheduleNotebookValue) {
    throw new Error("qbstimeline.yml must contain schedule.file or schedule.notebook");
  }

  const outputDir = parsed?.outputs?.dir;
  const sourceNotebook = typeof parsed?.source?.notebook === "string" && parsed.source.notebook.length > 0
    ? parsed.source.notebook
    : scheduleNotebookValue
      ? scheduleNotebookValue
      : undefined;
  const setupTags = Array.isArray(parsed?.schedule?.setup_tags)
    ? parsed.schedule.setup_tags.filter((tag): tag is string => typeof tag === "string" && tag.length > 0)
    : [];
  return {
    ...(scheduleFileValue ? { scheduleFile: scheduleFileValue } : {}),
    ...(scheduleNotebookValue ? { scheduleNotebook: scheduleNotebookValue } : {}),
    ...(sourceNotebook ? { sourceNotebook } : {}),
    setupTags,
    ...(typeof parsed?.schedule?.schedule_tag === "string" && parsed.schedule.schedule_tag.length > 0
      ? { scheduleTag: parsed.schedule.schedule_tag }
      : {}),
    ...(typeof parsed?.schedule?.schedule_variable === "string" && parsed.schedule.schedule_variable.length > 0
      ? { scheduleVariable: parsed.schedule.schedule_variable }
      : {}),
    ...(typeof parsed?.schedule?.compiler_variable === "string" && parsed.schedule.compiler_variable.length > 0
      ? { compilerVariable: parsed.schedule.compiler_variable }
      : {}),
    outputDir: typeof outputDir === "string" && outputDir.length > 0 ? outputDir : ".qbs_timeline",
  };
}

export function deriveOutputPaths(input: {
  projectFile: string;
  scheduleFile?: string;
  scheduleNotebook?: string;
  sourceNotebook?: string;
  outputDir: string;
  overrideOutputDir: string | null;
}): OutputPaths {
  const projectFile = normalize(input.projectFile);
  const projectDir = dirname(projectFile);
  const outputDir = resolveFromProject(projectDir, input.overrideOutputDir ?? input.outputDir);

  return {
    projectFile,
    projectDir,
    ...(input.scheduleFile ? { schedulePath: resolveFromProject(projectDir, input.scheduleFile) } : {}),
    ...(input.scheduleNotebook ? { scheduleNotebookPath: resolveFromProject(projectDir, input.scheduleNotebook) } : {}),
    ...(input.sourceNotebook ? { sourceNotebookPath: resolveFromProject(projectDir, input.sourceNotebook) } : {}),
    outputDir,
    irPath: join(outputDir, "qbs_ir.json"),
    htmlPath: join(outputDir, "index.html"),
    q1asmDir: join(outputDir, "q1asm"),
  };
}
