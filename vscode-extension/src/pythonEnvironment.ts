import type * as vscodeTypes from "vscode";
import { normalize } from "node:path";
import { explicitConfigValue, readSharedPythonSettings } from "./qbs/settingsCore";
import { q1timelineConfigValue } from "./q1timeline/settings";

export type PythonEnvironmentKind = "qbs" | "q1asm";

const selections = new WeakMap<object, { targets: Map<string, { requestId: number }>; updates: Promise<void> }>();

export function beginPythonEnvironmentSelection(vscode: typeof vscodeTypes, resource: vscodeTypes.Uri) {
  let state = selections.get(vscode.workspace);
  if (!state) { state = { targets: new Map(), updates: Promise.resolve() }; selections.set(vscode.workspace, state); }
  const folder = vscode.workspace.getWorkspaceFolder(resource);
  const key = folder ? `folder:${folder.uri.fsPath}` : "unscoped";
  const selection = state.targets.get(key) ?? { requestId: 0 };
  state.targets.set(key, selection);
  const requestId = ++selection.requestId;
  const isCurrent = () => selection.requestId === requestId;
  return {
    isCurrent,
    updateSettings: async (update: () => Promise<void>) => {
      // A global mode write can change the effective selection in another folder.
      const pending = state.updates.then(async () => { if (isCurrent()) await update(); });
      state.updates = pending.catch(() => {});
      await pending;
      return isCurrent();
    },
  };
}

export interface ResolvedPythonEnvironment {
  pythonPath: string;
  pythonArgs: string[];
  source: "vscode" | "manual";
  sourceLabel: string;
  resource: vscodeTypes.Uri;
  manualSetting?: string;
  assertCurrentSelection?: () => void;
}

interface PythonExtensionApi {
  environments?: {
    getActiveEnvironmentPath(resource: vscodeTypes.Uri): { id: string; path: string } | undefined;
    resolveEnvironment(environment: { id: string; path: string }): Promise<{
      executable?: { uri?: vscodeTypes.Uri };
    } | undefined>;
  };
}

export async function selectVSCodeInterpreter(vscode: typeof vscodeTypes, resource: vscodeTypes.Uri, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return;
  if (!vscode.workspace.workspaceFolders?.some((folder) => folder.uri.fsPath === resource.fsPath)) {
    await vscode.window.showTextDocument(resource);
  }
  if (signal?.aborted) return;
  await vscode.commands.executeCommand("python.setInterpreter");
}

export async function resolveFilePythonEnvironment(
  vscode: typeof vscodeTypes,
  resource: vscodeTypes.Uri,
  kind: PythonEnvironmentKind,
): Promise<ResolvedPythonEnvironment> {
  const q1lens = vscode.workspace.getConfiguration("q1lens", resource);
  const qblox = vscode.workspace.getConfiguration("qbloxTimeline", resource);
  const qbs = vscode.workspace.getConfiguration("qbsTimeline", resource);
  const legacy = vscode.workspace.getConfiguration("q1timeline", resource);
  const sources = kind === "qbs"
    ? [
      { config: q1lens, prefix: "", name: "q1lens" },
      { config: qblox, prefix: "", name: "qbloxTimeline" },
      { config: qbs, prefix: "", name: "qbsTimeline" },
    ]
    : [
      { config: q1lens, prefix: "q1timeline.", name: "q1lens.q1timeline" },
      { config: qblox, prefix: "q1timeline.", name: "qbloxTimeline.q1timeline" },
      { config: legacy, prefix: "", name: "q1timeline" },
      { config: q1lens, prefix: "", name: "q1lens" },
      { config: qblox, prefix: "", name: "qbloxTimeline" },
    ];
  const pathSource = sources.find(({ config, prefix }) =>
    explicitConfigValue<string>(config, `${prefix}pythonPath`) !== undefined);
  const argsSource = sources.find(({ config, prefix }) =>
    explicitConfigValue<string[]>(config, `${prefix}pythonArgs`) !== undefined);
  const settings = kind === "qbs"
    ? readSharedPythonSettings(q1lens, qblox, qbs)
    : {
      pythonPath: q1timelineConfigValue(qblox, legacy, "pythonPath", "python", q1lens),
      pythonArgs: q1timelineConfigValue<string[]>(qblox, legacy, "pythonArgs", [], q1lens),
    };
  const primaryCommand = q1lens.get<string | null>("q1timelineCommand");
  const customCommand = primaryCommand ?? qbs.get<string | null>("q1timelineCommand");
  const commandSetting = kind === "qbs" && customCommand
    ? primaryCommand != null ? "q1lens.q1timelineCommand" : "qbsTimeline.q1timelineCommand"
    : undefined;
  const manualSetting = pathSource
    ? `${pathSource.name}.pythonPath`
    : settings.pythonArgs.length && argsSource
      ? `${argsSource.name}.pythonArgs`
      : commandSetting;
  if (manualSetting && q1lens.get<"auto" | "vscode">("pythonEnvironment", "auto") !== "vscode") {
    return {
      ...settings,
      source: "manual",
      sourceLabel: `Manual setting: ${manualSetting}`,
      resource,
      manualSetting,
    };
  }

  const extension = vscode.extensions.getExtension<PythonExtensionApi>("ms-python.python");
  if (!extension) {
    throw new Error("Install or enable the Microsoft Python extension, then use Python: Select Interpreter for this file's project. You can also configure q1lens.pythonPath manually.");
  }
  const api = await extension.activate();
  const environments = api?.environments;
  if (typeof environments?.getActiveEnvironmentPath !== "function"
    || typeof environments.resolveEnvironment !== "function") {
    throw new Error("The Microsoft Python extension's environment API is unavailable. Update or enable the extension, then use Python: Select Interpreter.");
  }
  const selected = environments.getActiveEnvironmentPath(resource);
  const selectedId = selected?.id;
  const selectedPath = selected && normalize(selected.path);
  const assertCurrentSelection = () => {
    const current = environments.getActiveEnvironmentPath(resource);
    const currentPath = current && normalize(current.path);
    const samePath = process.platform === "win32"
      ? selectedPath?.toLowerCase() === currentPath?.toLowerCase()
      : selectedPath === currentPath;
    if (selectedId !== current?.id || !samePath) {
      throw new Error("The VS Code Python selection changed while resolving this file's environment. Please try again.");
    }
  };
  const environment = selected ? await environments.resolveEnvironment(selected).finally(assertCurrentSelection) : undefined;
  const pythonPath = environment?.executable?.uri?.fsPath;
  if (!pythonPath) {
    throw new Error("The VS Code Python environment selected for this file has no usable Python executable. Use Python: Select Interpreter for this file's project, then try again.");
  }
  return {
    pythonPath,
    pythonArgs: [],
    source: "vscode",
    sourceLabel: "VS Code selected interpreter",
    resource,
    assertCurrentSelection,
  };
}
