import type * as vscodeTypes from "vscode";

export interface SidebarItemDefinition {
  label: string;
  command: string;
  description: string;
}

export function buildSidebarItems(): SidebarItemDefinition[] {
  return [
    {
      label: "Analyze and Open",
      command: "qbsTimeline.analyzeAndOpen",
      description: "Run Q1Lens analysis and show the timeline webview.",
    },
    {
      label: "Refresh",
      command: "qbsTimeline.refresh",
      description: "Re-run analysis for the selected project.",
    },
    {
      label: "Open QBS IR",
      command: "qbsTimeline.openIr",
      description: "Open the generated qbs_ir.json after analysis.",
    },
    {
      label: "Open Rendered HTML",
      command: "qbsTimeline.openRenderedHtml",
      description: "Open the generated static HTML page.",
    },
    {
      label: "Open Q1ASM Folder",
      command: "qbsTimeline.openQ1asmFolder",
      description: "Reveal generated Q1ASM files.",
    },
    {
      label: "Open Q1ASM Timeline",
      command: "qbsTimeline.openQ1Timeline",
      description: "Open the generated Q1ASM in the Q1Lens preview.",
    },
    {
      label: "Open Current Folder Q1ASM",
      command: "qbsTimeline.openCurrentFolderQ1Timeline",
      description: "Open Q1ASM files from the current folder in Q1Lens.",
    },
  ];
}

export function registerSidebar(context: vscodeTypes.ExtensionContext): void {
  const vscode = require("vscode") as typeof vscodeTypes;
  class SidebarItem extends vscode.TreeItem {
    constructor(definition: SidebarItemDefinition) {
      super(definition.label, vscode.TreeItemCollapsibleState.None);
      this.description = definition.description;
      this.command = {
        command: definition.command,
        title: definition.label,
      };
      this.contextValue = "qbsTimelineAction";
    }
  }

  const provider: vscodeTypes.TreeDataProvider<InstanceType<typeof SidebarItem>> = {
    getTreeItem: (element) => element,
    getChildren: async () => buildSidebarItems().map((item) => new SidebarItem(item)),
  };

  context.subscriptions.push(vscode.window.createTreeView("qbsTimeline.sidebar", { treeDataProvider: provider }));
}
