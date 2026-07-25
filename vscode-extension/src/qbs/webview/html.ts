export interface WebviewHtmlInput {
  title: string;
  sharedCssUri?: string;
  cssUri: string;
  cspSource: string;
  sharedScriptUri?: string;
  modelScriptUri: string;
  scriptUri: string;
  nonce: string;
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    };
    return map[char];
  });
}

export function buildWebviewHtml(input: WebviewHtmlInput): string {
  const title = escapeHtml(input.title);
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src ${input.cspSource}; script-src 'nonce-${input.nonce}';">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  ${input.sharedCssUri ? `<link rel="stylesheet" href="${input.sharedCssUri}">` : ""}
  <link rel="stylesheet" href="${input.cssUri}">
  <title>${title}</title>
</head>
<body>
  <header class="topbar">
    <h1>${title}</h1>
    <button id="refresh-button" type="button" aria-label="Refresh timeline">Refresh</button>
  </header>
  <main id="qbs-root" class="timeline-shell" aria-live="polite"></main>
  ${input.sharedScriptUri ? `<script nonce="${input.nonce}" src="${input.sharedScriptUri}"></script>` : ""}
  <script nonce="${input.nonce}" src="${input.modelScriptUri}"></script>
  <script nonce="${input.nonce}" src="${input.scriptUri}"></script>
</body>
</html>`;
}
