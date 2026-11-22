import type * as vscodeTypes from "vscode";

export interface SidebarItemDefinition {
  label: string;
  /** Shown on hover; the row itself carries no explanatory sentence. */
  tooltip: string;
  command?: string;
  /** Short state shown beside the label, such as ".venv · 3.12". */
  description?: string;
  /** Codicon id. */
  icon?: string;
  /** Theme color id for the icon. */
  iconColor?: string;
  children?: SidebarItemDefinition[];
}

export interface SidebarEnvironment {
  name: string;
  /** Full Python version; the row shows major.minor. */
  version?: string;
  state: "ready" | "error" | "checking";
  /** Executable, source and error text for the tooltip. */
  detail?: string;
}

/** Analyze and Refresh are view title actions (package.json `view/title`), not tree rows. */
export const SIDEBAR_TITLE_COMMANDS = ["qbsTimeline.analyzeAndOpen", "qbsTimeline.refresh"] as const;

const ENVIRONMENT_TOOLTIP = "Check Python and Q1Lens installation, or select another environment.";

function environmentItem(environment?: SidebarEnvironment): SidebarItemDefinition {
  if (!environment) {
    return { label: "Python environment", command: "q1lens.checkEnvironment", icon: "python", tooltip: ENVIRONMENT_TOOLTIP };
  }
  const version = environment.version?.match(/^\d+\.\d+/)?.[0];
  const icons = {
    ready: { icon: "pass", iconColor: "testing.iconPassed" },
    error: { icon: "error", iconColor: "testing.iconFailed" },
    checking: { icon: "sync" },
  } as const;
  return {
    label: "Python",
    command: "q1lens.checkEnvironment",
    description: [environment.name, version].filter(Boolean).join(" · "),
    ...icons[environment.state],
    tooltip: [environment.detail, ENVIRONMENT_TOOLTIP].filter(Boolean).join("\n"),
  };
}

export function buildSidebarItems(environment?: SidebarEnvironment): SidebarItemDefinition[] {
  return [
    environmentItem(environment),
    {
      label: "Open",
      tooltip: "Open analysis results and Q1ASM previews.",
      children: [
        {
          label: "Q1ASM timeline",
          command: "qbsTimeline.openQ1Timeline",
          tooltip: "Open the generated Q1ASM in the Q1Lens preview.",
        },
        {
          label: "Current folder Q1ASM",
          command: "qbsTimeline.openCurrentFolderQ1Timeline",
          tooltip: "Open Q1ASM files from the current folder in Q1Lens.",
        },
        {
          label: "QBS IR",
          command: "qbsTimeline.openIr",
          tooltip: "Open the generated qbs_ir.json after analysis.",
        },
        {
          label: "Rendered HTML",
          command: "qbsTimeline.openRenderedHtml",
          tooltip: "Open the generated static HTML page.",
        },
        {
          label: "Q1ASM folder",
          command: "qbsTimeline.openQ1asmFolder",
          tooltip: "Reveal generated Q1ASM files.",
        },
      ],
    },
  ];
}

export function registerSidebar(context: vscodeTypes.ExtensionContext): void {
  const vscode = require("vscode") as typeof vscodeTypes;
  // Loaded here so buildSidebarItems stays usable without the VS Code runtime.
  const status = require("../pythonEnvironmentStatus") as typeof import("../pythonEnvironmentStatus");

  class SidebarItem extends vscode.TreeItem {
    constructor(readonly definition: SidebarItemDefinition) {
      super(definition.label, definition.children
        ? vscode.TreeItemCollapsibleState.Expanded
        : vscode.TreeItemCollapsibleState.None);
      this.description = definition.description;
      this.tooltip = definition.tooltip;
      if (definition.icon) {
        this.iconPath = new vscode.ThemeIcon(definition.icon,
          definition.iconColor ? new vscode.ThemeColor(definition.iconColor) : undefined);
      }
      if (definition.command) {
        this.command = { command: definition.command, title: definition.label };
      }
      this.contextValue = definition.children ? "qbsTimelineGroup" : "qbsTimelineAction";
    }
  }

  const currentEnvironment = (): SidebarEnvironment | undefined => {
    const environment = status.lastPythonEnvironment();
    if (!environment) return undefined;
    const error = status.pythonEnvironmentError(environment);
    const report = environment.report;
    return {
      name: status.pythonEnvironmentName(environment),
      version: report?.version,
      state: error ? "error" : report ? "ready" : "checking",
      detail: [
        error,
        environment.sourceLabel,
        report?.executable ?? environment.pythonPath,
        report ? `Python ${report.version}${report.packageVersion ? ` · Q1Lens ${report.packageVersion}` : ""}` : undefined,
      ].filter(Boolean).join("\n"),
    };
  };

  const changed = new vscode.EventEmitter<void>();
  const provider: vscodeTypes.TreeDataProvider<InstanceType<typeof SidebarItem>> = {
    onDidChangeTreeData: changed.event,
    getTreeItem: (element) => element,
    getChildren: async (element) => (element ? element.definition.children ?? [] : buildSidebarItems(currentEnvironment()))
      .map((item) => new SidebarItem(item)),
  };

  context.subscriptions.push(
    changed,
    status.onPythonEnvironmentChanged(() => changed.fire()),
    vscode.window.createTreeView("qbsTimeline.sidebar", { treeDataProvider: provider }),
  );
}
