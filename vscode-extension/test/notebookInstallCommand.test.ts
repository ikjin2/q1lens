import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { notebookInstallCommand } from "../src/notebookInstallCommand";

describe("notebook installation commands", () => {
  it("targets the notebook Python with pip on Windows", () => {
    assert.equal(
      notebookInstallCommand("C:\\My Environment\\python.exe", "0.2.2", "pip", "win32"),
      "& 'C:\\My Environment\\python.exe' -m pip install 'q1lens==0.2.2'"
    );
  });

  it("targets the notebook Python with uv on Windows", () => {
    assert.equal(
      notebookInstallCommand("C:\\My Environment\\python.exe", "0.2.2", "uv", "win32"),
      "uv pip install --python 'C:\\My Environment\\python.exe' 'q1lens==0.2.2'"
    );
  });

  for (const platform of ["linux", "darwin"] as const) {
    it(`targets the notebook Python with pip and uv on ${platform}`, () => {
      assert.equal(
        notebookInstallCommand("/home/user/notebook env/bin/python", "0.2.2", "pip", platform),
        "'/home/user/notebook env/bin/python' -m pip install 'q1lens==0.2.2'"
      );
      assert.equal(
        notebookInstallCommand("/home/user/notebook env/bin/python", "0.2.2", "uv", platform),
        "uv pip install --python '/home/user/notebook env/bin/python' 'q1lens==0.2.2'"
      );
    });
  }

  it("quotes apostrophes and shell metacharacters for both shell families", () => {
    const executable = "/env/a'b $HOME `whoami`; python";
    assert.equal(
      notebookInstallCommand(executable, "0.2.2", "pip", "win32"),
      "& '/env/a''b $HOME `whoami`; python' -m pip install 'q1lens==0.2.2'"
    );
    assert.equal(
      notebookInstallCommand(executable, "0.2.2", "pip", "linux"),
      "'/env/a'\"'\"'b $HOME `whoami`; python' -m pip install 'q1lens==0.2.2'"
    );
  });

  it("escapes typographic apostrophes recognized as quotes by PowerShell", () => {
    assert.equal(
      notebookInstallCommand("C:/env/\u2018a\u2019b\u201ac\u201b/python", "0.2.2", "uv", "win32"),
      "uv pip install --python 'C:/env/\u2018\u2018a\u2019\u2019b\u201a\u201ac\u201b\u201b/python' 'q1lens==0.2.2'"
    );
  });

  it("rejects control characters that could alter or execute a pasted command", () => {
    for (const code of [...Array.from({ length: 32 }, (_, index) => index), 127]) {
      const character = String.fromCharCode(code);
      for (const platform of ["win32", "linux"] as const) {
        assert.throws(() => notebookInstallCommand(`/env/${character}/python`, "0.2.2", "pip", platform));
        assert.throws(() => notebookInstallCommand("/env/python", `0.2.2${character}`, "uv", platform));
      }
    }
  });

  it("passes literal arguments through the native shell without installing anything", () => {
    const directory = mkdtempSync(join(tmpdir(), "q1lens-command-"));
    const windows = process.platform === "win32";
    const pythonPath = join(directory, `python env'\u2018\u2019\u201a\u201b$HOME\`;stub${windows ? ".ps1" : ""}`);
    const version = "0.2.2'\u2019$HOME`;literal";
    try {
      writeFileSync(pythonPath, windows
        ? "ConvertTo-Json -Compress -InputObject @($args)"
        : "#!/bin/sh\nprintf '%s\\n' \"$@\"\n");
      if (!windows) chmodSync(pythonPath, 0o700);
      for (const installer of ["pip", "uv"] as const) {
        const prefix = installer === "uv"
          ? windows
            ? "function uv { ConvertTo-Json -Compress -InputObject @($args) }\n"
            : "uv() { printf '%s\\n' \"$@\"; }\n"
          : "";
        const command = prefix + notebookInstallCommand(pythonPath, version, installer);
        const result = spawnSync(
          windows ? "powershell.exe" : "/bin/sh",
          windows ? ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command] : ["-c", command],
          { encoding: "utf8", timeout: 10000 }
        );
        assert.equal(result.status, 0, result.stderr || result.error?.message);
        const args = windows ? JSON.parse(result.stdout) : result.stdout.trimEnd().split("\n");
        assert.deepEqual(args, installer === "uv"
          ? ["pip", "install", "--python", pythonPath, `q1lens==${version}`]
          : ["-m", "pip", "install", `q1lens==${version}`]);
      }
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  });
});
