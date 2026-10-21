import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createQ1TimelineControllerState, q1timelineProjectOutputDir } from "../../src/q1timeline/register";
import { lookupEventIdsForSourceLine, lookupEventIdsForSourceRange, lookupSourceForEvent } from "../../src/q1timeline/sourceMapLookup";
import {
  collectProjectRelatedPaths,
  isProjectRelatedPath,
} from "../../src/q1timeline/projectRelatedFiles";
import {
  fallbackAnalyzerDiagnostic,
  normalizeAnalyzerDiagnostics,
} from "../../src/q1timeline/diagnosticFallback";
import {
  mapAnalyzerDiagnostic,
} from "../../src/q1timeline/diagnosticMapper";
import {
  findProjectFileCandidatesUpward,
  findProjectFileUpward,
  usableStoredProjectFile,
} from "../../src/q1timeline/projectDiscovery";
import {
  prependPythonArgs,
} from "../../src/q1timeline/invocation";

describe("q1timeline internal API", () => {
  it("computes q1timeline output next to an explicit project file", () => {
    assert.equal(
      q1timelineProjectOutputDir("C:\\repo\\.qbs_timeline\\q1timeline.yml").replace(/\\/g, "/"),
      "C:/repo/.qbs_timeline/.q1timeline",
    );
  });

  it("stores explicit open target state without project discovery", () => {
    const state = createQ1TimelineControllerState();

    state.setExplicitTarget({
      projectFile: "C:\\repo\\.qbs_timeline\\q1timeline.yml",
      q1asmFile: "C:\\repo\\.qbs_timeline\\q1asm\\seq0.q1asm",
      sequencer: "seq0",
      line: 3,
    });

    assert.equal(state.projectFile, "C:\\repo\\.qbs_timeline\\q1timeline.yml");
    assert.equal(state.pendingTarget?.line, 3);
  });

  it("matches source-map by_source keys relative to the q1timeline project root", () => {
    const timelineIr: any = {
      project: { root: "C:\\repo" },
      source_map: {
        by_source: {
          "q1asm/seq0.q1asm:7": ["seq0:e7"],
        },
      },
    };

    assert.deepEqual(
      lookupEventIdsForSourceLine(timelineIr, "C:\\repo\\q1asm\\seq0.q1asm", 7),
      ["seq0:e7"],
    );
  });

  it("does not match same-basename source files from other directories", () => {
    const timelineIr: any = {
      project: { root: "C:\\repo" },
      source_map: {
        by_event_id: {
          "a:e3": { file: "a/seq0.q1asm", line: 3 },
          "b:e3": { file: "b/seq0.q1asm", line: 3 },
        },
      },
    };

    assert.deepEqual(
      lookupEventIdsForSourceLine(timelineIr, "C:\\repo\\a\\seq0.q1asm", 3),
      ["a:e3"],
    );
  });

  it("keeps UNC project roots intact while resolving relative source maps", () => {
    const roots = process.platform === "win32"
      ? [String.raw`\\q1lens-host\share\project`, "//q1lens-host/share/project"]
      : ["/q1lens-host/share/project"];
    for (const root of roots) {
      for (const project of [{ project: { root } }, { project_root: root }]) {
        for (const mapping of [
          { source_map: { by_source: { "q1asm/seq0.q1asm:7": ["expected"] } } },
          { source_map: { by_event_id: { expected: { file: "q1asm/seq0.q1asm", line: 7 } } } },
          { events: [{ id: "expected", source: { file: "q1asm/seq0.q1asm", line: 7 } }] },
        ]) {
          const timelineIr = { ...project, ...mapping };
          assert.deepEqual(lookupEventIdsForSourceLine(timelineIr, join(root, "q1asm", "seq0.q1asm"), 7), ["expected"]);
          assert.deepEqual(lookupEventIdsForSourceLine(timelineIr, join(root, "other", "seq0.q1asm"), 7), []);
        }
      }
    }
  });

  it("keeps root-level and nested source files with the same basename separate", () => {
    const root = join(tmpdir(), "q1lens-source-map");
    const sources = {
      root: { file: "seq0.q1asm", line: 3 },
      nested: { file: "nested/seq0.q1asm", line: 3 },
    };
    for (const mapping of [
      { source_map: { by_source: { "seq0.q1asm:3": ["root"], "nested/seq0.q1asm:3": ["nested"] } } },
      { source_map: { by_event_id: sources } },
      { events: Object.entries(sources).map(([id, source]) => ({ id, source })) },
    ]) {
      const timelineIr = { project: { root }, ...mapping };
      assert.deepEqual(lookupEventIdsForSourceLine(timelineIr, join(root, "nested", "seq0.q1asm"), 3), ["nested"]);
      assert.deepEqual(lookupEventIdsForSourceLine(timelineIr, join(root, "seq0.q1asm"), 3), ["root"]);
      assert.deepEqual(lookupEventIdsForSourceLine(timelineIr, join(root, "other", "nested", "seq0.q1asm"), 3), []);
      assert.deepEqual(lookupEventIdsForSourceLine(timelineIr, "nested/seq0.q1asm", 3), ["nested"]);
    }
  });

  it("matches relative source paths by suffix when no project root is available", () => {
    const timelineIr = {
      source_map: {
        by_event_id: {
          root: { file: "seq0.q1asm", line: 3 },
          nested: { file: "nested/seq0.q1asm", line: 3 },
        },
      },
    };
    assert.deepEqual(lookupEventIdsForSourceLine(timelineIr, join(tmpdir(), "nested", "seq0.q1asm"), 3), ["root", "nested"]);
  });

  it("matches source-map paths case-insensitively on Windows", () => {
    const timelineIr: any = {
      source_map: {
        by_event_id: {
          "seq0:e7": { file: "C:/Repo/q1asm/seq0.q1asm", line: 7 },
        },
      },
    };

    assert.deepEqual(
      lookupEventIdsForSourceLine(timelineIr, "c:\\repo\\q1asm\\seq0.q1asm", 7),
      ["seq0:e7"],
    );
  });

  it("falls back to event.source when event source_map entries are absent", () => {
    const timelineIr: any = {
      project: { root: "C:\\repo" },
      events: [
        { id: "seq0:e4", source: { file: "q1asm/seq0.q1asm", line: 4 } },
      ],
    };

    assert.deepEqual(lookupSourceForEvent(timelineIr, "seq0:e4"), { file: "q1asm/seq0.q1asm", line: 4 });
    assert.deepEqual(
      lookupEventIdsForSourceLine(timelineIr, "C:\\repo\\q1asm\\seq0.q1asm", 4),
      ["seq0:e4"],
    );
  });

  it("omits diff-removed events from active source-line lookup", () => {
    const timelineIr: any = {
      source_map: {
        by_source: {
          "q1asm/seq0.q1asm:10": ["seq0:removed", "seq0:current"],
        },
        by_event_id: {
          "seq0:removed": { file: "q1asm/seq0.q1asm", line: 10 },
          "seq0:current": { file: "q1asm/seq0.q1asm", line: 10 },
        },
      },
      events: [
        { id: "seq0:removed", meta: { diff_status: "removed" } },
        { id: "seq0:current" },
      ],
    };

    assert.deepEqual(
      lookupEventIdsForSourceLine(timelineIr, "q1asm/seq0.q1asm", 10),
      ["seq0:current"],
    );
  });

  it("omits diff-removed events from event source lookup", () => {
    const timelineIr: any = {
      source_map: {
        by_event_id: {
          "seq0:removed": { file: "q1asm/seq0.q1asm", line: 2 },
        },
      },
      events: [
        {
          id: "seq0:removed",
          source: { file: "q1asm/seq0.q1asm", line: 2 },
          meta: { diff_status: "removed" },
        },
      ],
    };

    assert.equal(lookupSourceForEvent(timelineIr, "seq0:removed"), undefined);
  });

  it("scopes watcher refreshes to files collected from the active project", () => {
    const projectFile = "C:\\repo\\project-a\\q1timeline.yml";
    const related = collectProjectRelatedPaths(
      projectFile,
      "sequencers:\n  - file: q1asm/seq0.q1asm\nparams: params.json\ndisplay: display.yml\n",
    );

    assert.equal(
      isProjectRelatedPath({
        filePath: "C:\\repo\\project-a\\q1asm\\seq0.q1asm",
        projectPath: projectFile,
        projectRelatedPaths: related,
      }),
      true,
    );
    assert.equal(
      isProjectRelatedPath({
        filePath: "C:\\repo\\project-b\\q1asm\\seq0.q1asm",
        projectPath: projectFile,
        projectRelatedPaths: related,
      }),
      false,
    );
    assert.equal(related.some((item) => item.endsWith("params.json")), true);
  });

  it("collects flow-style q1timeline YAML file references", () => {
    const projectFile = "C:\\repo\\project-a\\q1timeline.yml";
    const related = collectProjectRelatedPaths(
      projectFile,
      "sequencers: [{id: s0, file: q1asm/s0.q1asm}]\n",
    );

    assert.equal(
      isProjectRelatedPath({
        filePath: "C:\\repo\\project-a\\q1asm\\s0.q1asm",
        projectPath: projectFile,
        projectRelatedPaths: related,
      }),
      true,
    );
  });

  it("builds webview-visible fallback diagnostics for invalid analyzer JSON", () => {
    assert.deepEqual(
      fallbackAnalyzerDiagnostic("C:\\repo\\q1timeline.yml", "Invalid analyzer diagnostics JSON"),
      {
        severity: "error",
        category: "invalid_analyzer_json",
        message: "Invalid analyzer diagnostics JSON",
        source: { file: "C:\\repo\\q1timeline.yml", line: 1, column: 1 },
        confidence: "error",
      },
    );
  });

  it("normalizes non-array analyzer diagnostics JSON to a fallback diagnostic", () => {
    const diagnostics = normalizeAnalyzerDiagnostics(
      { diagnostics: [] },
      "C:\\repo\\q1timeline.yml",
      "Invalid analyzer diagnostics JSON: expected array",
    );

    assert.equal(diagnostics[0].category, "invalid_analyzer_json");
  });

  it("maps malformed diagnostic source lines to a finite fallback range", () => {
    const diagnostic = mapAnalyzerDiagnostic(
      {
        severity: "warning",
        category: "schema",
        message: "bad line",
        source: { file: "seq0.q1asm", line: "abc", column: "def" },
      },
      (file: string) => `C:\\repo\\${file}`,
    );

    assert.equal(Number.isFinite(diagnostic?.range.startLine), true);
    assert.equal(diagnostic?.range.startLine, 0);
    assert.equal(diagnostic?.range.startColumn, 0);
  });

  it("ignores malformed related diagnostic entries", () => {
    const diagnostic = mapAnalyzerDiagnostic(
      {
        severity: "warning",
        category: "timing",
        message: "bad timing",
        source: { file: "seq0.q1asm", line: 4 },
        related: [
          null,
          {},
          { source: { file: "schedule.py", line: 12 }, message: "source pulse" },
        ],
      },
      (file: string) => `C:\\repo\\${file}`,
    );

    assert.equal(diagnostic?.relatedInformation.length, 1);
    assert.equal(diagnostic?.relatedInformation[0].file, "C:\\repo\\schedule.py");
  });

  it("maps analyzer-suggested edits only inside the diagnosed Q1ASM range", () => {
    const diagnostic = mapAnalyzerDiagnostic(
      {
        severity: "warning",
        category: "future_safe_fix",
        message: "A future analyzer can offer a safe edit.",
        source: { file: "seq0.q1asm", line: 4, column: 1 },
        details: {
          suggested_edit: {
            title: "Apply analyzer suggestion",
            file: "seq0.q1asm",
            range: {
              start: { line: 4, column: 1 },
              end: { line: 4, column: 1 },
            },
            new_text: "upd_param 4\n",
          },
        },
      },
      (file: string) => `C:\\repo\\${file}`,
    );

    assert.deepEqual(diagnostic?.suggestedEdit, {
      title: "Apply analyzer suggestion",
      file: "C:\\repo\\seq0.q1asm",
      range: { start: { line: 3, column: 0 }, end: { line: 3, column: 0 } },
      newText: "upd_param 4\n",
    });
  });

  it("rejects analyzer-suggested edits targeting another file", () => {
    const diagnostic = mapAnalyzerDiagnostic(
      {
        severity: "warning",
        category: "future_safe_fix",
        message: "Do not edit another program.",
        source: { file: "seq0.q1asm", line: 4, column: 1 },
        details: {
          suggested_edit: {
            title: "Unsafe cross-file edit",
            file: "seq1.q1asm",
            range: {
              start: { line: 4, column: 1 },
              end: { line: 4, column: 1 },
            },
            new_text: "upd_param 4\n",
          },
        },
      },
      (file: string) => `C:\\repo\\${file}`,
    );

    assert.equal(diagnostic?.suggestedEdit, undefined);
  });

  it("rejects malformed and out-of-range analyzer-suggested edits", () => {
    for (const range of [
      { start: { line: 0, column: 1 }, end: { line: 0, column: 1 } },
      { start: { line: 4, column: 3 }, end: { line: 4, column: 3 } },
      { start: { line: 4, column: 2 }, end: { line: 4, column: 1 } },
    ]) {
      const diagnostic = mapAnalyzerDiagnostic(
        {
          severity: "warning",
          category: "future_safe_fix",
          message: "Reject an invalid edit.",
          source: { file: "seq0.q1asm", line: 4, column: 1 },
          details: {
            suggested_edit: {
              title: "Invalid edit",
              file: "seq0.q1asm",
              range,
              new_text: "upd_param 4\n",
            },
          },
        },
        (file: string) => `C:\\repo\\${file}`,
      );

      assert.equal(diagnostic?.suggestedEdit, undefined);
    }
  });

  it("rejects stale stored project files outside the current workspace", () => {
    assert.equal(
      usableStoredProjectFile({
        storedProjectFile: "C:\\old\\q1timeline.yml",
        workspaceFolders: ["C:\\repo"],
        exists: () => true,
      }),
      false,
    );
  });

  it("finds projects inside workspace child directories whose names start with two dots", () => {
    const root = mkdtempSync(join(tmpdir(), "q1lens-dot-prefix-project-"));
    try {
      const projectDir = join(root, "..experiment");
      mkdirSync(projectDir);
      const projectFile = join(projectDir, "q1timeline.yml");
      writeFileSync(projectFile, "sequencers: []\n", "utf8");

      assert.equal(findProjectFileUpward(join(projectDir, "seq0.q1asm"), "q1timeline.yml", root), projectFile);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("retains stored projects in two-dot-prefixed child directories without accepting parent paths", () => {
    const root = join(tmpdir(), "q1lens-stored-project-workspace");
    for (const [storedProjectFile, expected] of [
      [join(root, "..experiment", "q1timeline.yml"), true],
      [join(root, "..", "other", "q1timeline.yml"), false],
      [join(root, "..other", "q1timeline.yml"), true],
    ] as const) {
      assert.equal(usableStoredProjectFile({ storedProjectFile, workspaceFolders: [root], exists: () => true }), expected);
    }
  });

  it("finds the active q1timeline project for mixed-case Windows Q1ASM paths", () => {
    const root = join(tmpdir(), `qbstimeline-mixed-case-${process.pid}`);
    const projectDir = join(root, "ProjectA");
    const q1asmDir = join(projectDir, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const projectFile = join(projectDir, "q1timeline.yml");
    writeFileSync(projectFile, "sequencers: []\n", "utf8");
    const sourceFile = join(q1asmDir.toLowerCase(), "SEQ0.Q1ASM");

    const discovered = findProjectFileUpward(sourceFile, "q1timeline.yml", root.toUpperCase());

    assert.equal(discovered?.toLowerCase(), projectFile.toLowerCase());
  });

  it("finds custom q1timeline project candidates upward from a Q1ASM file", () => {
    const root = join(tmpdir(), `qbstimeline-custom-project-${process.pid}`);
    const projectDir = join(root, "ProjectA");
    const q1asmDir = join(projectDir, "q1asm");
    mkdirSync(q1asmDir, { recursive: true });
    const customProject = join(projectDir, "experiment.q1timeline.yml");
    writeFileSync(customProject, "sequencers: []\n", "utf8");

    const discovered = findProjectFileCandidatesUpward(
      join(q1asmDir, "seq0.q1asm"),
      ["q1timeline.yml", "*.q1timeline.yml"],
      root,
    );

    assert.deepEqual(discovered, [customProject]);
  });

  it("finds q1timeline projects in a sibling .q1timeline directory", () => {
    const root = join(tmpdir(), `qbstimeline-dot-q1timeline-project-${process.pid}`);
    const projectDir = join(root, "ProjectA");
    mkdirSync(join(projectDir, ".q1timeline"), { recursive: true });
    const sourceFile = join(projectDir, "seq0.q1asm");
    const projectFile = join(projectDir, ".q1timeline", "q1timeline.yml");
    writeFileSync(sourceFile, "stop\n", "utf8");
    writeFileSync(projectFile, "sequencers: []\n", "utf8");

    const discovered = findProjectFileCandidatesUpward(
      sourceFile,
      [".q1timeline/q1timeline.yml", ".q1timeline/*.q1timeline.yml"],
      root,
    );

    assert.deepEqual(discovered, [projectFile]);
  });

  it("prefers the nearest q1timeline project directory before higher-level candidates", () => {
    const root = join(tmpdir(), `qbstimeline-nearest-project-${process.pid}`);
    const projectDir = join(root, "ProjectA");
    const nestedDir = join(projectDir, "nested", "q1asm");
    mkdirSync(nestedDir, { recursive: true });
    const rootProject = join(projectDir, "q1timeline.yml");
    const nestedProject = join(projectDir, "nested", "experiment.q1timeline.yml");
    writeFileSync(rootProject, "sequencers: []\n", "utf8");
    writeFileSync(nestedProject, "sequencers: []\n", "utf8");

    const discovered = findProjectFileCandidatesUpward(
      join(nestedDir, "seq0.q1asm"),
      ["q1timeline.yml", "*.q1timeline.yml"],
      root,
    );

    assert.deepEqual(discovered, [nestedProject]);
  });

  it("continues upward when only ignored generated q1timeline projects are nearby", () => {
    const root = join(tmpdir(), `qbstimeline-ignore-generated-project-${process.pid}`);
    const projectDir = join(root, "ProjectA");
    const q1asmDir = join(projectDir, "q1asm");
    mkdirSync(join(q1asmDir, ".q1timeline"), { recursive: true });
    const sourceFile = join(q1asmDir, "seq0.q1asm");
    const generatedProject = join(q1asmDir, ".q1timeline", "auto-generated.q1timeline.yml");
    const parentProject = join(projectDir, "q1timeline.yml");
    writeFileSync(sourceFile, "stop\n", "utf8");
    writeFileSync(generatedProject, "sequencers: []\n", "utf8");
    writeFileSync(parentProject, "sequencers: []\n", "utf8");

    const discovered = findProjectFileCandidatesUpward(
      sourceFile,
      ["q1timeline.yml", ".q1timeline/*.q1timeline.yml"],
      root,
      ["auto-generated.q1timeline.yml"],
    );

    assert.deepEqual(discovered, [parentProject]);
  });

  it("continues upward when only ignored generated q1timeline params are nearby", () => {
    const root = join(tmpdir(), `qbstimeline-ignore-generated-params-${process.pid}`);
    const projectDir = join(root, "ProjectA");
    const q1asmDir = join(projectDir, "q1asm");
    mkdirSync(join(q1asmDir, ".q1timeline"), { recursive: true });
    const sourceFile = join(q1asmDir, "seq0.q1asm");
    const generatedParams = join(q1asmDir, ".q1timeline", "auto-generated.params.json");
    const parentParams = join(projectDir, "params.json");
    writeFileSync(sourceFile, "stop\n", "utf8");
    writeFileSync(generatedParams, "{}\n", "utf8");
    writeFileSync(parentParams, "{}\n", "utf8");

    const discovered = findProjectFileCandidatesUpward(
      sourceFile,
      ["params.json", ".q1timeline/*.params.json"],
      root,
      ["auto-generated.params.json"],
    );

    assert.deepEqual(discovered, [parentParams]);
  });

  it("prepends configured Python launcher arguments before q1timeline module args", () => {
    assert.deepEqual(
      prependPythonArgs(["-X", "utf8"], ["-m", "q1lens", "q1timeline"]),
      ["-X", "utf8", "-m", "q1lens", "q1timeline"],
    );
  });

  it("looks up every selected source line across map formats without duplicates or removed events", () => {
    const file = "q1asm/seq0.q1asm";
    const timelineIr = {
      project: { root: "C:\\repo" },
      source_map: {
        by_source: { [`${file}:2`]: ["a", "a", "removed"], [`${file}:4`]: ["b"], [`${file}:5`]: ["outside"] },
        by_event_id: {
          a: { file, line: 2 }, b: [{ file, line: 4 }], c: { sources: [{ file, line: 3 }] },
          d: { source: { file, line: 4 } }, removed: { file, line: 3 },
          other: { file: "other/seq0.q1asm", line: 3 },
        },
      },
      events: [
        { id: "a", source: { file, line: 2 } }, { id: "e", source: { file, line: 3 } },
        { id: "removed", source: { file, line: 3 }, meta: { diff_status: "removed" } },
        { id: "before", source: { file, line: 1 } },
      ],
    };
    const before = JSON.stringify(timelineIr);
    assert.deepEqual(lookupEventIdsForSourceRange(timelineIr, "C:\\repo\\q1asm\\seq0.q1asm", 2, 4), ["a", "b", "c", "d", "e"]);
    assert.deepEqual(lookupEventIdsForSourceRange(timelineIr, file, 4, 4), lookupEventIdsForSourceLine(timelineIr, file, 4));
    assert.deepEqual(lookupEventIdsForSourceRange(timelineIr, "missing.q1asm", 2, 4), []);
    assert.deepEqual(lookupEventIdsForSourceRange(timelineIr, file, 4, 2), []);
    assert.deepEqual(lookupEventIdsForSourceRange(timelineIr, file, Number.NaN, 4), []);
    assert.deepEqual(lookupEventIdsForSourceRange({}, file, 1, 100_000), []);
    assert.equal(JSON.stringify(timelineIr), before);
  });
});
