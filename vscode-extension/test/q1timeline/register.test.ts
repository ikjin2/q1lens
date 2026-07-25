import assert from "node:assert/strict";

const Module = require("node:module");

function createUri(fsPath: string) {
  return {
    fsPath,
    toString() {
      return `file://${fsPath}`;
    },
  };
}

describe("q1timeline registration", () => {
  it("passes a selected Q1ASM URI from the open preview command to the controller", async () => {
    const registeredCommands = new Map<string, (...args: any[]) => any>();
    const previewCalls: any[] = [];
    const vscode = {
      commands: {
        registerCommand: (name: string, callback: (...args: any[]) => any) => {
          registeredCommands.set(name, callback);
          return { dispose: () => undefined };
        },
      },
    };

    const originalLoad = Module._load;
    Module._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") {
        return vscode;
      }
      if (request === "./controller") {
        return {
          TimelineController: class {
            openPreview(options?: any) {
              previewCalls.push(options);
            }
            refreshPreview() {}
            selectProjectFile() {}
            selectQ1asmFilesInFolder() {}
            openQ1asmFilesInFolder() {}
            revealCurrentLineInTimeline() {}
            openTarget() {}
          },
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../../src/q1timeline/register");
    delete require.cache[modulePath];
    try {
      const { registerQ1Timeline } = require("../../src/q1timeline/register");
      registerQ1Timeline({ subscriptions: [] });
      const selectedUri = createUri("C:\\repo\\clicked.q1asm");

      await registeredCommands.get("q1timeline.openPreview")?.(selectedUri);

      assert.equal(previewCalls[0].sourceUri.fsPath, selectedUri.fsPath);
    } finally {
      delete require.cache[modulePath];
      Module._load = originalLoad;
    }
  });

  it("passes a selected Q1ASM URI from the folder selection command to the controller", async () => {
    const registeredCommands = new Map<string, (...args: any[]) => any>();
    const selectionCalls: any[] = [];
    const vscode = {
      commands: {
        registerCommand: (name: string, callback: (...args: any[]) => any) => {
          registeredCommands.set(name, callback);
          return { dispose: () => undefined };
        },
      },
    };

    const originalLoad = Module._load;
    Module._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") {
        return vscode;
      }
      if (request === "./controller") {
        return {
          TimelineController: class {
            openPreview() {}
            refreshPreview() {}
            selectProjectFile() {}
            selectQ1asmFilesInFolder(sourceUri?: any) {
              selectionCalls.push(sourceUri);
            }
            revealCurrentLineInTimeline() {}
            openTarget() {}
          },
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../../src/q1timeline/register");
    delete require.cache[modulePath];
    try {
      const { registerQ1Timeline } = require("../../src/q1timeline/register");
      registerQ1Timeline({ subscriptions: [] });
      const selectedUri = createUri("C:\\repo\\seq0.q1asm");

      await registeredCommands.get("q1timeline.selectQ1asmFilesInFolder")?.(selectedUri);

      assert.equal(selectionCalls[0].fsPath, selectedUri.fsPath);
    } finally {
      delete require.cache[modulePath];
      Module._load = originalLoad;
    }
  });

  it("passes a selected Q1ASM URI from the open-folder command to the controller", async () => {
    const registeredCommands = new Map<string, (...args: any[]) => any>();
    const openFolderCalls: any[] = [];
    const vscode = {
      commands: {
        registerCommand: (name: string, callback: (...args: any[]) => any) => {
          registeredCommands.set(name, callback);
          return { dispose: () => undefined };
        },
      },
    };

    const originalLoad = Module._load;
    Module._load = function patchedLoad(request: string, parent: any, isMain: boolean) {
      if (request === "vscode") {
        return vscode;
      }
      if (request === "./controller") {
        return {
          TimelineController: class {
            openPreview() {}
            refreshPreview() {}
            selectProjectFile() {}
            selectQ1asmFilesInFolder() {}
            openQ1asmFilesInFolder(sourceUri?: any) {
              openFolderCalls.push(sourceUri);
            }
            revealCurrentLineInTimeline() {}
            openTarget() {}
          },
        };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    const modulePath = require.resolve("../../src/q1timeline/register");
    delete require.cache[modulePath];
    try {
      const { registerQ1Timeline } = require("../../src/q1timeline/register");
      registerQ1Timeline({ subscriptions: [] });
      const selectedUri = createUri("C:\\repo\\seq0.q1asm");

      await registeredCommands.get("q1timeline.openQ1asmFilesInFolder")?.(selectedUri);

      assert.equal(openFolderCalls[0].fsPath, selectedUri.fsPath);
    } finally {
      delete require.cache[modulePath];
      Module._load = originalLoad;
    }
  });
});
