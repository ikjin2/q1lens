// @ts-nocheck
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const Module = require("node:module");

function createUri(fsPath: string) {
  return {
    fsPath,
    toString() {
      return `file://${fsPath}`;
    },
  };
}

function createMockVscode() {
  const vscode: any = {
    Uri: {
      file: (fsPath: string) => createUri(fsPath),
      parse: (value: string) => createUri(value.replace(/^file:\/\//, "")),
      joinPath: (base: any, ...parts: string[]) => createUri([base.fsPath, ...parts].join("\\")),
    },
    ViewColumn: { Active: -1, Beside: 2 },
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
    window: {
      activeTextEditor: undefined,
      visibleTextEditors: [],
      tabGroups: { all: [] },
      createOutputChannel: () => ({
        appendLine: () => {},
        show: () => {},
        dispose: () => {},
      }),
      createTextEditorDecorationType: () => ({ dispose: () => {} }),
      onDidChangeTextEditorSelection: () => ({ dispose: () => {} }),
      onDidChangeActiveTextEditor: () => ({ dispose: () => {} }),
      showWarningMessage: async () => undefined,
      showInformationMessage: async () => undefined,
      showErrorMessage: async () => undefined,
      showQuickPick: async (items: any[]) => items[0],
      createWebviewPanel: () => ({
        webview: {
          html: "",
          onDidReceiveMessage: () => ({ dispose: () => {} }),
          asWebviewUri: (uri: any) => uri,
        },
        reveal: () => {},
        onDidDispose: () => ({ dispose: () => {} }),
        dispose: () => {},
      }),
    },
    workspace: {
      workspaceFolders: [],
      textDocuments: [],
      getConfiguration: () => ({ get: (_key: string, fallback: any) => fallback }),
      getWorkspaceFolder: () => undefined,
      findFiles: async () => [],
      asRelativePath: (uri: any) => uri.fsPath,
      createFileSystemWatcher: () => ({
        onDidChange: () => ({ dispose: () => {} }),
        onDidCreate: () => ({ dispose: () => {} }),
        onDidDelete: () => ({ dispose: () => {} }),
        dispose: () => {},
      }),
      onDidSaveTextDocument: () => ({ dispose: () => {} }),
      onDidChangeTextDocument: () => ({ dispose: () => {} }),
    },
    languages: {
      createDiagnosticCollection: () => ({
        clear: () => {},
        set: () => {},
        dispose: () => {},
      }),
    },
    commands: {
      executeCommand: async () => undefined,
    },
    Range: class {
      constructor(public startLine: any, public startColumn: any, public endLine?: any, public endColumn?: any) {}
    },
    Position: class {
      constructor(public line: number, public character: number) {}
    },
    Selection: class {
      constructor(public anchor: any, public active: any) {}
    },
    TextEditorRevealType: { InCenter: 1 },
    Diagnostic: class {
      constructor(public range: any, public message: string, public severity: number) {}
    },
    Location: class {
      constructor(public uri: any, public range: any) {}
    },
    DiagnosticRelatedInformation: class {
      constructor(public location: any, public message: string) {}
    },
    MarkdownString: class {
      value = "";
      appendMarkdown(text: string) {
        this.value += text;
      }
    },
    Hover: class {
      constructor(public markdown: any, public range: any) {}
    },
  };
  return vscode;
}

function createContext(storedProjectFile?: string) {
  return {
    subscriptions: [],
    extensionPath: "C:\\extension",
    extensionUri: createUri("C:\\extension"),
    workspaceState: {
      get: () => storedProjectFile,
      update: async () => undefined,
    },
  };
}

async function withMockedVscode(vscode: any, callback: (controllerModule: any) => Promise<void>) {
  const originalLoad = Module._load;
  Module._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
    if (request === "vscode") {
      return vscode;
    }
    return originalLoad.call(this, request, parent, isMain);
  };
  const modulePath = require.resolve("../../src/q1timeline/controller");
  delete require.cache[modulePath];
  try {
    await callback(require("../../src/q1timeline/controller"));
  } finally {
    delete require.cache[modulePath];
    Module._load = originalLoad;
  }
}

describe("q1timeline controller", () => {
  it("cache-busts q1timeline webview assets when injecting them", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ injectWebviewAssetTags }) => {
      const webview = {
        asWebviewUri: (uri: any) => ({
          toString: () => `vscode-resource://${uri.fsPath.split("\\").pop()}`,
        }),
      };
      const html = injectWebviewAssetTags(
        "<html><head></head><body></body></html>",
        webview,
        createContext(),
        "abc123"
      );

      assert.match(html, /renderer\.css\?v=abc123/);
      assert.match(html, /timeline\.css\?v=abc123/);
      assert.match(html, /renderer\.js\?v=abc123/);
      assert.match(html, /timelineAdapter\.js\?v=abc123/);
      assert.match(html, /timeline\.js\?v=abc123/);
      assert.ok(html.indexOf("renderer.css?v=abc123") < html.indexOf("timeline.css?v=abc123"));
      assert.ok(html.indexOf("renderer.js?v=abc123") < html.indexOf("timelineAdapter.js?v=abc123"));
      assert.ok(html.indexOf("timelineAdapter.js?v=abc123") < html.indexOf("timeline.js?v=abc123"));
    });
  });

  it("clears legacy q1timeline SVG while keeping the rich diagnostics panel", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ injectWebviewAssetTags }) => {
      const webview = {
        asWebviewUri: (uri: any) => ({
          toString: () => `vscode-resource://${uri.fsPath.split("\\").pop()}`,
        }),
      };
      const html = injectWebviewAssetTags(
        '<html><head></head><body><section id="timeline-root" class="timeline"><svg class="timeline-svg"><g class="loop-bracket"></g></svg></section><section class="diagnostics" aria-label="Diagnostics"><ol><li>warning duplicate</li></ol></section><aside id="event-inspector"></aside></body></html>',
        webview,
        createContext(),
        "abc123"
      );

      assert.match(html, /<section id="timeline-root" class="timeline"><\/section>/);
      assert.doesNotMatch(html, /loop-bracket/);
      assert.doesNotMatch(html, /timeline-svg/);
      assert.match(html, /warning duplicate/);
      assert.match(html, /<section class="diagnostics"/);
      assert.match(html, /<aside id="event-inspector"><\/aside>/);
      assert.match(html, /renderer\.js\?v=abc123/);
    });
  });

  it("allows q1timeline webview scripts from the VS Code webview origin", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ contentSecurityPolicy }) => {
      const csp = contentSecurityPolicy({ cspSource: "vscode-webview://q1lens" }, "abc123");

      assert.match(csp, /script-src 'nonce-abc123' vscode-webview:\/\/q1lens/);
    });
  });

  it("prefers an active editor project over a stored project in another workspace root", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-controller-"));
    const projectA = join(root, "project-a");
    const projectB = join(root, "project-b");
    mkdirSync(projectA);
    mkdirSync(projectB);
    const storedProject = join(projectA, "q1timeline.yml");
    const activeProject = join(projectB, "q1timeline.yml");
    writeFileSync(storedProject, "sequencers: []\n", "utf8");
    writeFileSync(activeProject, "sequencers: []\n", "utf8");
    vscode.workspace.workspaceFolders = [
      { uri: vscode.Uri.file(projectA) },
      { uri: vscode.Uri.file(projectB) },
    ];
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(join(projectB, "q1asm", "seq0.q1asm")) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext(vscode.Uri.file(storedProject).toString()));
      controller.config = () => ({ get: () => "q1timeline.yml" });
      controller.findProjectFileUpward = () => vscode.Uri.file(activeProject);

      const project = await controller.findProjectFile();

      assert.equal(project.fsPath, activeProject);
      controller.dispose();
    });
  });

  it("uses the configured project file name in the manual project picker", async () => {
    const vscode = createMockVscode();
    let searchedProjectFile = "";

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.config = () => ({
        get: (key: string, fallback: any) => (key === "projectFile" ? "q1timeline.yaml" : fallback),
      });
      controller.findWorkspaceProjectFiles = async (projectFile: string) => {
        searchedProjectFile = projectFile;
        return [vscode.Uri.file("C:\\repo\\q1timeline.yaml")];
      };
      controller.pickProjectFile = async (matches: any[]) => matches[0];
      controller.openPreview = async () => undefined;

      await controller.selectProjectFile();

      assert.equal(searchedProjectFile, "q1timeline.yaml");
      controller.dispose();
    });
  });

  it("rediscovers the active editor project when opening an existing preview", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-open-preview-"));
    const projectA = join(root, "project-a");
    const projectB = join(root, "project-b");
    mkdirSync(projectA);
    mkdirSync(projectB);
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(join(projectB, "q1asm", "seq0.q1asm")) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(projectA, "q1timeline.yml"));
      controller.outputDir = join(projectA, ".q1timeline");
      controller.panel = { reveal: () => {}, webview: { postMessage: () => {} }, dispose: () => {} };
      controller.config = () => ({ get: () => "q1timeline.yml" });
      controller.findProjectFileUpward = () => vscode.Uri.file(join(projectB, "q1timeline.yml"));
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview();

      assert.equal(controller.projectUri.fsPath, join(projectB, "q1timeline.yml"));
      controller.dispose();
    });
  });

  it("opens a new q1timeline preview above the current editor group", async () => {
    const vscode = createMockVscode();
    const commands: string[] = [];
    let createdViewColumn: any = undefined;
    vscode.commands.executeCommand = async (command: string) => {
      commands.push(command);
    };
    vscode.window.createWebviewPanel = (_viewType: string, _title: string, viewColumn: any) => {
      createdViewColumn = viewColumn;
      return {
        webview: {
          html: "",
          onDidReceiveMessage: () => ({ dispose: () => {} }),
          asWebviewUri: (uri: any) => uri,
        },
        reveal: () => {},
        onDidDispose: () => ({ dispose: () => {} }),
        dispose: () => {},
      };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ preserveProject: true });

      assert.deepEqual(commands, ["workbench.action.newGroupAbove"]);
      assert.equal(createdViewColumn, vscode.ViewColumn.Active);
      controller.dispose();
    });
  });

  it("reuses an existing q1timeline preview without moving or duplicating it", async () => {
    const vscode = createMockVscode();
    const commands: string[] = [];
    const revealArgs: any[][] = [];
    let createPanelCount = 0;
    vscode.commands.executeCommand = async (command: string) => {
      commands.push(command);
    };
    vscode.window.createWebviewPanel = () => {
      createPanelCount += 1;
      return {};
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.panel = {
        reveal: (...args: any[]) => {
          revealArgs.push(args);
        },
        webview: { postMessage: () => {} },
        dispose: () => {},
      };
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ preserveProject: true });

      assert.deepEqual(commands, []);
      assert.equal(createPanelCount, 0);
      assert.deepEqual(revealArgs, [[]]);
      controller.dispose();
    });
  });

  it("finds active q1timeline projects for uppercase Q1ASM editor paths", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-uppercase-"));
    const projectDir = join(root, "ProjectA");
    const q1asmDir = join(projectDir, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const projectFile = join(projectDir, "q1timeline.yml");
    writeFileSync(projectFile, "sequencers: []\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const sourceUri = vscode.Uri.file(join(q1asmDir.toLowerCase(), "SEQ0.Q1ASM"));

      const discovered = controller.findProjectFileUpward(sourceUri, "q1timeline.yml");

      assert.equal(discovered?.fsPath.toLowerCase(), projectFile.toLowerCase());
      controller.dispose();
    });
  });

  it("rebuilds single-file fallback projects when the active Q1ASM file changes", async () => {
    const vscode = createMockVscode();
    const oldFile = "C:\\repo\\old\\a.q1asm";
    const newFile = "C:\\repo\\new\\b.q1asm";
    const newProject = "C:\\repo\\new\\.q1timeline\\single-file.q1timeline.yml";
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(newFile) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.singleFileMode = true;
      controller.singleFileUri = vscode.Uri.file(oldFile);
      controller.projectUri = vscode.Uri.file("C:\\repo\\old\\.q1timeline\\single-file.q1timeline.yml");
      controller.outputDir = "C:\\repo\\old\\.q1timeline";
      controller.panel = { webview: { postMessage: () => {} }, reveal: () => {}, dispose: () => {} };
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;
      let created = false;
      controller.createSingleFileProject = async () => {
        created = true;
        controller.singleFileUri = vscode.Uri.file(newFile);
        controller.singleFileMode = true;
        controller.outputDir = "C:\\repo\\new\\.q1timeline";
        return vscode.Uri.file(newProject);
      };

      await controller.openPreview();

      assert.equal(created, true);
      assert.equal(controller.projectUri.fsPath, newProject);
      assert.equal(controller.singleFileUri.fsPath, newFile);
      controller.dispose();
    });
  });

  it("uses single-file fallback for an active orphan Q1ASM instead of a cached project", async () => {
    const vscode = createMockVscode();
    const sourceFile = "C:\\repo\\scratch\\orphan.q1asm";
    const singleProject = "C:\\repo\\scratch\\.q1timeline\\single-file.q1timeline.yml";
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(sourceFile) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\project-a\\q1timeline.yml");
      controller.singleFileMode = false;
      controller.panel = { webview: { postMessage: () => {} }, reveal: () => {}, dispose: () => {} };
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;
      let created = false;
      controller.createSingleFileProject = async () => {
        created = true;
        controller.singleFileUri = vscode.Uri.file(sourceFile);
        controller.singleFileMode = true;
        controller.outputDir = "C:\\repo\\scratch\\.q1timeline";
        return vscode.Uri.file(singleProject);
      };

      await controller.openPreview();

      assert.equal(created, true);
      assert.equal(controller.projectUri.fsPath, singleProject);
      assert.equal(controller.singleFileUri.fsPath, sourceFile);
      controller.dispose();
    });
  });

  it("uses an explicit Q1ASM URI for single-file fallback instead of the active editor", async () => {
    const vscode = createMockVscode();
    const activeFile = "C:\\repo\\active\\active.q1asm";
    const requestedFile = "C:\\repo\\clicked\\clicked.q1asm";
    const singleProject = "C:\\repo\\clicked\\.q1timeline\\single-file.q1timeline.yml";
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file(activeFile) },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;
      let createdFor: string | undefined;
      controller.createSingleFileProject = async (sourceUri: any) => {
        createdFor = sourceUri.fsPath;
        controller.singleFileUri = sourceUri;
        controller.singleFileMode = true;
        controller.outputDir = "C:\\repo\\clicked\\.q1timeline";
        return vscode.Uri.file(singleProject);
      };

      await controller.openPreview({ sourceUri: vscode.Uri.file(requestedFile) });

      assert.equal(createdFor, requestedFile);
      assert.equal(controller.projectUri.fsPath, singleProject);
      assert.equal(controller.singleFileUri.fsPath, requestedFile);
      controller.dispose();
    });
  });

  it("auto-generates from the clicked Q1ASM folder instead of using a parent project", async () => {
    const vscode = createMockVscode();
    const root = mkdtempSync(join(tmpdir(), "q1-clicked-folder-"));
    const projectDir = join(root, "project");
    const q1asmDir = join(projectDir, "child");
    mkdirSync(q1asmDir, { recursive: true });
    const parentProject = join(projectDir, "q1timeline.yml");
    const clickedFile = join(q1asmDir, "clicked.q1asm");
    writeFileSync(parentProject, "sequencers: []\n", "utf8");
    writeFileSync(clickedFile, "wait_sync 4\nstop\n", "utf8");
    vscode.workspace.workspaceFolders = [{ uri: vscode.Uri.file(projectDir) }];
    vscode.workspace.getWorkspaceFolder = () => ({ uri: vscode.Uri.file(projectDir) });

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ sourceUri: vscode.Uri.file(clickedFile) });

      const generatedProject = join(q1asmDir, ".q1timeline", "auto-generated.q1timeline.yml");
      assert.equal(controller.projectUri.fsPath, generatedProject);
      assert.equal(controller.singleFileMode, true);
      assert.equal(existsSync(generatedProject), true);
      controller.dispose();
    });
  });

  it("tells the user to open or select Q1ASM when no project or Q1ASM source is available", async () => {
    const vscode = createMockVscode();
    const warnings: string[] = [];
    vscode.window.showWarningMessage = async (message: string) => {
      warnings.push(message);
      return undefined;
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview();

      assert.deepEqual(warnings, [
        "Open or select a .q1asm file, or create q1timeline.yml, before opening a Q1ASM timeline.",
      ]);
      controller.dispose();
    });
  });

  it("creates an auto-generated folder project from every sibling Q1ASM file", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-session-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    const notes = join(folder, "notes.txt");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(beta, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(notes, "ignore me\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ sourceUri: vscode.Uri.file(beta) });

      const projectPath = join(folder, ".q1timeline", "auto-generated.q1timeline.yml");
      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.equal(controller.singleFileMode, true);
      assert.equal(existsSync(projectPath), true);
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.match(project, /id: "alpha"/);
      assert.match(project, /name: "alpha"/);
      assert.match(project, /file: "\.\.\/alpha\.q1asm"/);
      assert.match(project, /id: "beta"/);
      assert.match(project, /name: "beta"/);
      assert.match(project, /file: "\.\.\/beta\.q1asm"/);
      assert.equal(project.includes("notes.txt"), false);
      controller.dispose();
    });
  });

  it("creates inferred placeholder params for auto-generated folder projects", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-params-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    writeFileSync(
      alpha,
      ".DEF T_TOTAL {T_TOTAL}\n.DEF START_ALIGN {START_ALIGN}\nwait $START_ALIGN\nplay 0,1,$T_TOTAL\nstop\n",
      "utf8",
    );
    writeFileSync(
      beta,
      ".DEF GAIN_ID {GAIN_ID}\n.DEF IQ_SHIFT {IQ_SHIFT}\nfb_pop_data $GAIN_ID,R0\nfb_acq_iq_shift $IQ_SHIFT,4\nstop\n",
      "utf8",
    );

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ sourceUri: vscode.Uri.file(alpha) });

      const projectPath = join(folder, ".q1timeline", "auto-generated.q1timeline.yml");
      const paramsPath = join(folder, ".q1timeline", "auto-generated.params.json");
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      const params = JSON.parse(readFileSync(paramsPath, "utf8"));

      assert.match(project, /params:\n  file: "auto-generated\.params\.json"/);
      assert.equal(params.T_TOTAL, 4000);
      assert.equal(params.START_ALIGN, 40);
      assert.equal(params.GAIN_ID, 0);
      assert.equal(params.IQ_SHIFT, 0);
      controller.dispose();
    });
  });

  it("reports auto-generated folder project contents in the output channel", async () => {
    const vscode = createMockVscode();
    const outputLines: string[] = [];
    vscode.window.createOutputChannel = () => ({
      appendLine: (message: string) => {
        outputLines.push(message);
      },
      show: () => {},
      dispose: () => {},
    });
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-output-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    writeFileSync(alpha, ".DEF T_TOTAL {T_TOTAL}\nwait $T_TOTAL\nstop\n", "utf8");
    writeFileSync(beta, "wait_sync 4\nstop\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.findProjectFile = async () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openPreview({ sourceUri: vscode.Uri.file(alpha) });

      assert.ok(outputLines.some((line) => line.includes("Auto-generated q1timeline fallback includes 2 Q1ASM file(s): alpha.q1asm, beta.q1asm")));
      assert.ok(outputLines.some((line) => line.includes("Auto-generated q1timeline fallback params: auto-generated.params.json")));
      controller.dispose();
    });
  });

  it("selects a subset of folder Q1ASM files for the transient project", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-subset-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    const gamma = join(folder, "gamma.q1asm");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(beta, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(gamma, "wait_sync 4\nstop\n", "utf8");
    let quickPickOptions: any;
    vscode.window.showQuickPick = async (items: any[], options: any) => {
      quickPickOptions = options;
      return items.filter((item) => item.label !== "gamma.q1asm");
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.findProjectFileUpward = () => undefined;
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.selectQ1asmFilesInFolder(vscode.Uri.file(alpha));

      const projectPath = join(folder, ".q1timeline", "auto-generated.q1timeline.yml");
      assert.equal(quickPickOptions.canPickMany, true);
      assert.equal(controller.projectUri.fsPath, projectPath);
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.match(project, /file: "\.\.\/alpha\.q1asm"/);
      assert.match(project, /file: "\.\.\/beta\.q1asm"/);
      assert.equal(project.includes("gamma.q1asm"), false);
      controller.dispose();
    });
  });

  it("opens every Q1ASM file in a folder without prompting", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-all-"));
    const alpha = join(folder, "alpha.q1asm");
    const beta = join(folder, "beta.q1asm");
    const gamma = join(folder, "gamma.q1asm");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(beta, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(gamma, "wait_sync 4\nstop\n", "utf8");
    let quickPickCalled = false;
    const warnings: string[] = [];
    vscode.window.showQuickPick = async () => {
      quickPickCalled = true;
      return [];
    };
    vscode.window.showWarningMessage = async (message: string) => {
      warnings.push(message);
      return undefined;
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      const projectPath = join(folder, ".q1timeline", "auto-generated.q1timeline.yml");
      assert.equal(quickPickCalled, false);
      assert.deepEqual(warnings, []);
      assert.equal(controller.projectUri.fsPath, projectPath);
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.match(project, /file: "\.\.\/alpha\.q1asm"/);
      assert.match(project, /file: "\.\.\/beta\.q1asm"/);
      assert.match(project, /file: "\.\.\/gamma\.q1asm"/);
      controller.dispose();
    });
  });

  it("opens an existing folder q1timeline project instead of auto-generating one", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-existing-project-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const projectPath = join(folder, "q1timeline.yml");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(join(folder, "params.json"), "{\"T_TOTAL\": 4}\n", "utf8");
    writeFileSync(
      projectPath,
      "sequencers:\n  - id: alpha\n    name: alpha\n    file: q1asm/alpha.q1asm\nparams:\n  file: params.json\n",
      "utf8",
    );

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.equal(existsSync(join(q1asmDir, ".q1timeline", "auto-generated.q1timeline.yml")), false);
      controller.dispose();
    });
  });

  it("opens an existing .q1timeline project next to current-folder Q1ASM files", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-dot-project-"));
    const dotQ1timeline = join(folder, ".q1timeline");
    mkdirSync(dotQ1timeline, { recursive: true });
    const alpha = join(folder, "alpha.q1asm");
    const projectPath = join(dotQ1timeline, "q1timeline.yml");
    const generatedProject = join(dotQ1timeline, "auto-generated.q1timeline.yml");
    let quickPickCalled = false;
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(projectPath, "sequencers:\n  - id: alpha\n    file: ../alpha.q1asm\n", "utf8");
    writeFileSync(generatedProject, "sequencers:\n  - id: stale\n    file: ../stale.q1asm\n", "utf8");
    vscode.window.showQuickPick = async (items: any[]) => {
      quickPickCalled = true;
      return items[0];
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(quickPickCalled, false);
      assert.equal(controller.projectUri.fsPath, projectPath);
      controller.dispose();
    });
  });

  it("opens an existing custom q1timeline project instead of auto-generating one", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-custom-project-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const projectPath = join(folder, "experiment.q1timeline.yml");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(projectPath, "sequencers:\n  - id: alpha\n    file: q1asm/alpha.q1asm\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.equal(existsSync(join(q1asmDir, ".q1timeline", "auto-generated.q1timeline.yml")), false);
      controller.dispose();
    });
  });

  it("uses the default canonical q1timeline.yml when multiple projects are in the same folder", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-multiple-projects-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const canonicalProject = join(folder, "q1timeline.yml");
    const customProject = join(folder, "experiment.q1timeline.yml");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(canonicalProject, "sequencers:\n  - id: alpha\n    file: q1asm/alpha.q1asm\n", "utf8");
    writeFileSync(customProject, "sequencers:\n  - id: beta\n    file: q1asm/beta.q1asm\n", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(controller.projectUri.fsPath, canonicalProject);
      controller.dispose();
    });
  });

  it("lets the user choose a project when multiple q1timeline yml candidates exist", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-choose-project-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const canonicalProject = join(folder, "q1timeline.yml");
    const customProject = join(folder, "experiment.q1timeline.yml");
    const quickPickPlaceholders: string[] = [];
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(canonicalProject, "sequencers:\n  - id: alpha\n    file: q1asm/alpha.q1asm\n", "utf8");
    writeFileSync(customProject, "sequencers:\n  - id: beta\n    file: q1asm/beta.q1asm\n", "utf8");
    vscode.window.showQuickPick = async (items: any[], options: any) => {
      quickPickPlaceholders.push(options.placeHolder);
      return items.find((item) => item.uri.fsPath === customProject);
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      assert.equal(controller.projectUri.fsPath, customProject);
      assert.deepEqual(quickPickPlaceholders, ["Select q1timeline project"]);
      controller.dispose();
    });
  });

  it("lets the user choose params for an auto-generated q1timeline project", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-choose-params-"));
    const q1asmDir = join(folder, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const alpha = join(q1asmDir, "alpha.q1asm");
    const defaultParams = join(folder, "params.json");
    const customParams = join(folder, "experiment.params.json");
    const quickPickPlaceholders: string[] = [];
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(defaultParams, "{\"T_TOTAL\": 4}\n", "utf8");
    writeFileSync(customParams, "{\"T_TOTAL\": 8}\n", "utf8");
    vscode.window.showQuickPick = async (items: any[], options: any) => {
      quickPickPlaceholders.push(options.placeHolder);
      return items.find((item) => item.uri?.fsPath === customParams);
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      const projectPath = join(q1asmDir, ".q1timeline", "auto-generated.q1timeline.yml");
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.deepEqual(quickPickPlaceholders, ["Select params file"]);
      assert.match(project, /params:\n  file: "\.\.\/\.\.\/experiment\.params\.json"/);
      assert.equal(existsSync(join(q1asmDir, ".q1timeline", "auto-generated.params.json")), false);
      controller.dispose();
    });
  });

  it("lets the user choose params from a sibling .q1timeline directory", async () => {
    const vscode = createMockVscode();
    const folder = mkdtempSync(join(tmpdir(), "q1-folder-dot-params-"));
    const dotQ1timeline = join(folder, ".q1timeline");
    mkdirSync(dotQ1timeline, { recursive: true });
    const alpha = join(folder, "alpha.q1asm");
    const paramsFile = join(dotQ1timeline, "params.json");
    writeFileSync(alpha, "wait_sync 4\nstop\n", "utf8");
    writeFileSync(paramsFile, "{\"T_TOTAL\": 8}\n", "utf8");
    vscode.window.showQuickPick = async (items: any[], options: any) => {
      assert.equal(options.placeHolder, "Select params file");
      return items.find((item) => item.uri?.fsPath === paramsFile);
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      controller.runAnalysis = async () => undefined;

      await controller.openQ1asmFilesInFolder(vscode.Uri.file(alpha));

      const projectPath = join(dotQ1timeline, "auto-generated.q1timeline.yml");
      const project = readFileSync(projectPath, "utf8").replace(/\r\n/g, "\n");
      assert.equal(controller.projectUri.fsPath, projectPath);
      assert.match(project, /params:\n  file: "params\.json"/);
      controller.dispose();
    });
  });

  it("clears cached analyzer diff state when opening a different project", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.lastAnalyzerTimelineIr = { events: [{ id: "old" }] };
      controller.openPreview = async () => undefined;
      controller.revealPendingTarget = async () => undefined;

      await controller.openTarget({
        projectFile: "C:\\repo\\project-b\\q1timeline.yml",
        q1asmFile: "C:\\repo\\project-b\\q1asm\\seq0.q1asm",
        sequencer: "seq0",
        line: 1,
      });

      assert.equal(controller.lastAnalyzerTimelineIr, undefined);
      controller.dispose();
    });
  });

  it("clears pending open-target highlights after one reveal attempt", async () => {
    const vscode = createMockVscode();
    const posted: any[] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, reveal: () => {}, dispose: () => {} };
      controller.timelineIr = {
        source_map: {
          by_source: { "q1asm/seq0.q1asm:3": ["seq0:e3"] },
        },
        events: [{ id: "seq0:e3" }],
      };
      controller.pendingTarget = {
        projectFile: "C:\\repo\\q1timeline.yml",
        q1asmFile: "q1asm/seq0.q1asm",
        sequencer: "seq0",
        line: 3,
      };

      await controller.revealPendingTarget();

      assert.deepEqual(posted[0], { type: "highlightEventIds", highlightEventIds: ["seq0:e3"] });
      assert.equal(controller.pendingTarget, undefined);
      controller.dispose();
    });
  });

  it("reuses a hidden already-open Q1ASM tab when a timeline event is clicked", async () => {
    const vscode = createMockVscode();
    const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
    const existingDocument = { uri: vscode.Uri.file(sourcePath) };
    let openedDocument = false;
    let shownDocument: any = undefined;
    let shownOptions: any = undefined;
    vscode.workspace.textDocuments = [existingDocument];
    vscode.window.visibleTextEditors = [];
    vscode.window.tabGroups.all = [
      {
        viewColumn: 1,
        tabs: [{ input: { uri: vscode.Uri.file(sourcePath) } }],
      },
    ];
    vscode.workspace.openTextDocument = async () => {
      openedDocument = true;
      return { uri: vscode.Uri.file(sourcePath) };
    };
    vscode.window.showTextDocument = async (document: any, options: any) => {
      shownDocument = document;
      shownOptions = options;
      return {
        document,
        viewColumn: 1,
        setDecorations: () => {},
        revealRange: () => {},
      };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.timelineIr = {
        project: { root: "C:\\repo" },
        events: [
          {
            id: "seq0:e3",
            source: { file: "q1asm/seq0.q1asm", line: 3, column: 1 },
          },
        ],
      };

      await controller.openSourceForEvent("seq0:e3");

      assert.equal(openedDocument, false);
      assert.equal(shownDocument, existingDocument);
      assert.deepEqual(shownOptions, { viewColumn: 1, preview: false });
      controller.dispose();
    });
  });

  it("opens new Q1ASM sources in an existing Q1ASM tab group", async () => {
    const vscode = createMockVscode();
    const openTabPath = "C:\\repo\\q1asm\\seq0.q1asm";
    const targetPath = "C:\\repo\\q1asm\\seq1.q1asm";
    const targetDocument = { uri: vscode.Uri.file(targetPath) };
    let openedUri: any = undefined;
    let shownDocument: any = undefined;
    let shownOptions: any = undefined;
    vscode.window.visibleTextEditors = [];
    vscode.window.tabGroups.all = [
      {
        viewColumn: 3,
        tabs: [{ input: { uri: vscode.Uri.file(openTabPath) } }],
      },
    ];
    vscode.workspace.openTextDocument = async (uri: any) => {
      openedUri = uri;
      return targetDocument;
    };
    vscode.window.showTextDocument = async (document: any, options: any) => {
      shownDocument = document;
      shownOptions = options;
      return {
        document,
        viewColumn: 3,
        setDecorations: () => {},
        revealRange: () => {},
      };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.timelineIr = {
        project: { root: "C:\\repo" },
        events: [
          {
            id: "seq1:e3",
            source: { file: "q1asm/seq1.q1asm", line: 3, column: 1 },
          },
        ],
      };

      await controller.openSourceForEvent("seq1:e3");

      assert.equal(openedUri.fsPath, targetPath);
      assert.equal(shownDocument, targetDocument);
      assert.deepEqual(shownOptions, { viewColumn: 3, preview: false });
      controller.dispose();
    });
  });

  it("opens new Q1ASM sources in a below editor group when no source tab exists", async () => {
    const vscode = createMockVscode();
    const targetPath = "C:\\repo\\q1asm\\seq1.q1asm";
    const targetDocument = { uri: vscode.Uri.file(targetPath) };
    const commands: string[] = [];
    let shownDocument: any = undefined;
    let shownOptions: any = undefined;
    vscode.window.visibleTextEditors = [];
    vscode.window.tabGroups.all = [];
    vscode.commands.executeCommand = async (command: string) => {
      commands.push(command);
    };
    vscode.workspace.openTextDocument = async () => targetDocument;
    vscode.window.showTextDocument = async (document: any, options: any) => {
      shownDocument = document;
      shownOptions = options;
      return {
        document,
        viewColumn: vscode.ViewColumn.Active,
        setDecorations: () => {},
        revealRange: () => {},
      };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.timelineIr = {
        project: { root: "C:\\repo" },
        events: [
          {
            id: "seq1:e3",
            source: { file: "q1asm/seq1.q1asm", line: 3, column: 1 },
          },
        ],
      };

      await controller.openSourceForEvent("seq1:e3");

      assert.deepEqual(commands, ["workbench.action.newGroupBelow"]);
      assert.equal(shownDocument, targetDocument);
      assert.deepEqual(shownOptions, { viewColumn: vscode.ViewColumn.Active, preview: false });
      controller.dispose();
    });
  });

  it("queues reveal current line when opening the preview first", async () => {
    const vscode = createMockVscode();
    const posted: any[] = [];
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file("C:\\repo\\q1asm\\seq0.q1asm") },
      selection: { active: { line: 2 } },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.panel = undefined;
      controller.openPreview = async () => {
        controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, dispose: () => {} };
        controller.timelineIr = {
          source_map: {
            by_source: { "q1asm/seq0.q1asm:3": ["seq0:e3"] },
          },
          events: [{ id: "seq0:e3" }],
        };
        await controller.revealPendingTarget();
      };

      await controller.revealCurrentLineInTimeline();

      assert.deepEqual(posted[0], { type: "highlightEventIds", highlightEventIds: ["seq0:e3"] });
      controller.dispose();
    });
  });

  it("highlights the timeline block when a related Q1ASM editor becomes active", async () => {
    const vscode = createMockVscode();
    const posted: any[] = [];
    let activeEditorHandler: ((editor: any) => void) | undefined;
    const sourcePath = "C:\\repo\\q1asm\\seq0.q1asm";
    const editor = {
      document: { uri: vscode.Uri.file(sourcePath) },
      selection: { active: { line: 2 } },
      viewColumn: 2,
    };
    vscode.window.onDidChangeActiveTextEditor = (handler: (editor: any) => void) => {
      activeEditorHandler = handler;
      return { dispose: () => {} };
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.projectRelatedPaths = new Set([sourcePath]);
      controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, dispose: () => {} };
      controller.timelineIr = {
        source_map: {
          by_source: { "q1asm/seq0.q1asm:3": ["seq0:e3"] },
        },
        events: [{ id: "seq0:e3" }],
      };

      activeEditorHandler?.(editor);

      assert.deepEqual(posted[0], { type: "highlightEventIds", highlightEventIds: ["seq0:e3"] });
      controller.dispose();
    });
  });

  it("rediscovers the active project before revealing the current line in an existing preview", async () => {
    const vscode = createMockVscode();
    vscode.window.activeTextEditor = {
      document: { uri: vscode.Uri.file("C:\\repo\\project-b\\q1asm\\seq0.q1asm") },
      selection: { active: { line: 4 } },
    };

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\project-a\\q1timeline.yml");
      controller.panel = { webview: { postMessage: () => {} }, reveal: () => {}, dispose: () => {} };
      controller.timelineIr = {
        source_map: {
          by_source: { "q1asm/seq0.q1asm:5": ["old:e5"] },
        },
        events: [{ id: "old:e5" }],
      };
      controller.findProjectFileUpward = () => vscode.Uri.file("C:\\repo\\project-b\\q1timeline.yml");
      controller.refreshProjectRelatedPaths = async () => undefined;
      controller.startWatchers = () => undefined;
      let analysisRan = false;
      controller.runAnalysis = async () => {
        analysisRan = true;
      };

      await controller.revealCurrentLineInTimeline();

      assert.equal(controller.projectUri.fsPath, "C:\\repo\\project-b\\q1timeline.yml");
      assert.equal(controller.pendingTarget?.q1asmFile, "C:\\repo\\project-b\\q1asm\\seq0.q1asm");
      assert.equal(controller.pendingTarget?.line, 5);
      assert.equal(analysisRan, true);
      controller.dispose();
    });
  });

  it("keeps explicit open targets queued while a fresher analysis is pending", async () => {
    const vscode = createMockVscode();
    const posted: any[] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\q1timeline.yml");
      controller.outputDir = "C:\\repo\\.q1timeline";
      controller.panel = { webview: { postMessage: (message: any) => posted.push(message) }, reveal: () => {}, dispose: () => {} };
      controller.timelineIr = {
        source_map: {
          by_source: { "q1asm/seq0.q1asm:3": ["old:e3"] },
        },
        events: [{ id: "old:e3" }],
      };
      controller.analysisInFlight = true;

      await controller.openTarget({
        projectFile: "C:\\repo\\q1timeline.yml",
        q1asmFile: "q1asm/seq0.q1asm",
        sequencer: "seq0",
        line: 3,
      });

      assert.equal(posted.some((message) => message.type === "highlightEventIds"), false);
      assert.equal(controller.pendingTarget?.line, 3);
      assert.equal(controller.analysisQueued, true);
      controller.dispose();
    });
  });

  it("refreshes related paths for case-equivalent project watcher events", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\Repo\\q1timeline.yml");
      controller.projectRelatedPaths = new Set(["C:\\Repo\\old.q1asm"]);
      controller.runAnalysis = () => undefined;
      let refreshed = false;
      controller.refreshProjectRelatedPaths = async () => {
        refreshed = true;
        controller.projectRelatedPaths = new Set(["C:\\Repo\\new.q1asm"]);
      };

      await controller.onWatchedFile(vscode.Uri.file("c:\\repo\\q1timeline.yml"));

      assert.equal(refreshed, true);
      assert.equal(controller.isProjectRelated(vscode.Uri.file("C:\\Repo\\new.q1asm")), true);
      controller.dispose();
    });
  });

  it("clears stale preview HTML when analyzer failure handling runs", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const webview = { html: "<html>old event</html>" };
      controller.panel = { webview, dispose: () => {} };
      controller.timelineIr = { events: [{ id: "old" }] };
      controller.lastPreviewHtml = "<html>old event</html>";

      controller.clearPreviewAfterAnalyzerFailure("Analyzer failed.");

      assert.equal(controller.timelineIr, undefined);
      assert.equal(controller.lastPreviewHtml, undefined);
      assert.equal(webview.html.includes("old event"), false);
      controller.dispose();
    });
  });

  it("does not reuse cached HTML when timeline.html is missing", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-missing-html-"));
    writeFileSync(join(outputDir, "timeline_ir.json"), "{\"events\":[]}", "utf8");
    writeFileSync(join(outputDir, "diagnostics.json"), "[]", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const webview = {
        html: "",
        cspSource: "vscode-resource:",
        asWebviewUri: (uri: any) => uri,
        postMessage: () => {},
      };
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.lastPreviewHtml = "<html>old project</html>";
      controller.panel = { webview, dispose: () => {} };

      await controller.refreshWebview();

      assert.equal(webview.html.includes("old project"), false);
      assert.equal(webview.html.includes("No timeline.html generated."), true);
      controller.dispose();
    });
  });

  it("clears stale standalone analyzer outputs before running analysis", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-stale-outputs-"));
    writeFileSync(join(outputDir, "timeline_ir.json"), "{\"events\":[{\"id\":\"old\"}]}", "utf8");
    writeFileSync(join(outputDir, "diagnostics.json"), "[]", "utf8");
    writeFileSync(join(outputDir, "timeline.html"), "<html>old</html>", "utf8");

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.execFile = async () => ({ stdout: "{}", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.runAnalysis();

      const fs = require("node:fs");
      assert.equal(fs.existsSync(join(outputDir, "timeline_ir.json")), false);
      assert.equal(fs.existsSync(join(outputDir, "diagnostics.json")), false);
      assert.equal(fs.existsSync(join(outputDir, "timeline.html")), false);
      controller.dispose();
    });
  });

  it("adds branch and loop timeline chip overrides to analyzer args", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-control-overrides-"));
    const capturedArgs: string[][] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.config = () => ({
        get: (key: string, fallback: any) => {
          if (key === "analyzer.extraArgs") {
            return ["--strict"];
          }
          return fallback;
        },
      });
      controller.execFile = async (_pythonPath: string, args: string[]) => {
        capturedArgs.push(args);
        return { stdout: "{}", stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.handleWebviewMessage({
        type: "setBranchAssumption",
        branchId: "seq0:branch:main.q1asm:4:jge:target",
        path: "both",
      });
      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 3,
      });

      const latestArgs = capturedArgs[capturedArgs.length - 1];
      assert.ok(latestArgs);
      assert.deepEqual(
        latestArgs.slice(latestArgs.indexOf("--branch-assumption"), latestArgs.indexOf("--strict")),
        [
          "--branch-assumption",
          "seq0:branch:main.q1asm:4:jge:target=both",
          "--loop-preview",
          "seq0:loop:main.q1asm:2-8=3",
        ],
      );
      controller.dispose();
    });
  });

  it("opens direct source locations from timeline control messages", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      let openedSource: any = undefined;
      controller.openSourceLocation = async (source: any) => {
        openedSource = source;
      };

      await controller.handleWebviewMessage({
        type: "sourceClick",
        file: "q1asm/seq0.q1asm",
        line: 15,
        column: 1,
      });

      assert.deepEqual(openedSource, { file: "q1asm/seq0.q1asm", line: 15, column: 1 });
      controller.dispose();
    });
  });

  it("clears a loop timeline override when reset requests one visible iteration", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-control-reset-"));
    const capturedArgs: string[][] = [];

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file(join(outputDir, "q1timeline.yml"));
      controller.outputDir = outputDir;
      controller.execFile = async (_pythonPath: string, args: string[]) => {
        capturedArgs.push(args);
        return { stdout: "{}", stderr: "", exitCode: 0, elapsedMs: 1 };
      };
      controller.runRender = async () => ({ stdout: "", stderr: "", exitCode: 0, elapsedMs: 1 });
      controller.refreshWebview = async () => undefined;

      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 3,
      });
      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 1,
      });

      const latestArgs = capturedArgs[capturedArgs.length - 1];
      assert.ok(latestArgs);
      assert.equal(latestArgs.includes("--loop-preview"), false);
      assert.deepEqual(controller.analyzerOverrideArgs(), []);
      controller.dispose();
    });
  });

  it("clears timeline chip overrides when switching projects", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.projectUri = vscode.Uri.file("C:\\repo\\project-a\\q1timeline.yml");
      controller.outputDir = "C:\\repo\\project-a\\.q1timeline";
      controller.runAnalysis = async () => undefined;

      await controller.handleWebviewMessage({
        type: "setBranchAssumption",
        branchId: "seq0:branch:a.q1asm:4:jge:target",
        path: "taken",
      });
      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:a.q1asm:2-8",
        visibleIterations: 2,
      });

      assert.notDeepEqual(controller.analyzerOverrideArgs(), []);
      controller.setProjectUri(vscode.Uri.file("C:\\repo\\project-b\\q1timeline.yml"));
      assert.deepEqual(controller.analyzerOverrideArgs(), []);
      controller.dispose();
    });
  });

  it("clears analyzer diff baseline before timeline control reruns", async () => {
    const vscode = createMockVscode();

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      const baselinesDuringRun: any[] = [];
      controller.runAnalysis = async () => {
        baselinesDuringRun.push(controller.lastAnalyzerTimelineIr);
      };

      controller.lastAnalyzerTimelineIr = { events: [{ id: "previous-path" }] };
      await controller.handleWebviewMessage({
        type: "setBranchAssumption",
        branchId: "seq0:branch:main.q1asm:4:jge:target",
        path: "fallthrough",
      });

      controller.lastAnalyzerTimelineIr = { events: [{ id: "previous-loop-preview" }] };
      await controller.handleWebviewMessage({
        type: "setLoopPreview",
        loopKey: "seq0:loop:main.q1asm:2-8",
        visibleIterations: 3,
      });

      assert.deepEqual(baselinesDuringRun, [undefined, undefined]);
      controller.dispose();
    });
  });

  it("marks same-line raw Q1ASM event changes as changed", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-diff-change-"));
    const timelinePath = join(outputDir, "timeline_ir.json");
    writeFileSync(
      timelinePath,
      JSON.stringify({
        events: [
          {
            id: "seq0:e10:new",
            sequencer_id: "seq0",
            kind: "set_mrk",
            lane: "rt.path0",
            source: { file: "seq0.q1asm", line: 10, raw: "set_mrk 2" },
            t0: { value: 20 },
            t1: { value: 20 },
            duration: { value: 0 },
          },
        ],
      }),
      "utf8",
    );

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.outputDir = outputDir;
      controller.lastAnalyzerTimelineIr = {
        events: [
          {
            id: "seq0:e10:old",
            sequencer_id: "seq0",
            kind: "set_mrk",
            lane: "rt.path0",
            source: { file: "seq0.q1asm", line: 10, raw: "set_mrk 1" },
            t0: { value: 20 },
            t1: { value: 20 },
            duration: { value: 0 },
          },
        ],
      };

      await controller.prepareTimelineIrForRender(timelinePath);

      const annotated = JSON.parse(require("node:fs").readFileSync(timelinePath, "utf8"));
      assert.equal(annotated.events[0].meta.diff_status, "changed");
      controller.dispose();
    });
  });

  it("keeps diff-removed event ids unique after Q1ASM line insertions", async () => {
    const vscode = createMockVscode();
    const outputDir = mkdtempSync(join(tmpdir(), "q1-diff-ids-"));
    const timelinePath = join(outputDir, "timeline_ir.json");
    writeFileSync(
      timelinePath,
      JSON.stringify({
        events: [
          { id: "seq0:e0", sequencer_id: "seq0", kind: "wait", lane: "rt.wait", source: { file: "seq0.q1asm", line: 1 }, t0: { value: 0 }, t1: { value: 4 }, duration: { value: 4 } },
          { id: "seq0:e1", sequencer_id: "seq0", kind: "wait", lane: "rt.wait", source: { file: "seq0.q1asm", line: 2 }, t0: { value: 4 }, t1: { value: 8 }, duration: { value: 4 } },
          { id: "seq0:e2", sequencer_id: "seq0", kind: "play", lane: "rt.path0", source: { file: "seq0.q1asm", line: 3 }, t0: { value: 8 }, t1: { value: 16 }, duration: { value: 8 } },
        ],
      }),
      "utf8",
    );

    await withMockedVscode(vscode, async ({ TimelineController }) => {
      const controller = new TimelineController(createContext());
      controller.outputDir = outputDir;
      controller.lastAnalyzerTimelineIr = {
        events: [
          { id: "seq0:e0", sequencer_id: "seq0", kind: "wait", lane: "rt.wait", source: { file: "seq0.q1asm", line: 1 }, t0: { value: 0 }, t1: { value: 4 }, duration: { value: 4 } },
          { id: "seq0:e1", sequencer_id: "seq0", kind: "play", lane: "rt.path0", source: { file: "seq0.q1asm", line: 2 }, t0: { value: 4 }, t1: { value: 12 }, duration: { value: 8 } },
        ],
      };

      await controller.prepareTimelineIrForRender(timelinePath);

      const annotated = JSON.parse(require("node:fs").readFileSync(timelinePath, "utf8"));
      const ids = annotated.events.map((event: { id: string }) => event.id);
      assert.equal(new Set(ids).size, ids.length);
      assert.equal(annotated.events.some((event: { id: string; meta?: { diff_status?: string } }) => event.id !== "seq0:e1" && event.meta?.diff_status === "removed"), true);
      controller.dispose();
    });
  });
});
