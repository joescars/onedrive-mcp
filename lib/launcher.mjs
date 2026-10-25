import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import os from "node:os";
const packageJson = JSON.parse(
  readFileSync(new URL("../package.json", import.meta.url), "utf8"),
);
const PYTHON_DISTRIBUTION = "joescars-onedrive-mcp";
const ALLOWED_ENTRY_POINTS = new Set(["onedrive-mcp", "onedrive-mcp-setup"]);

export function buildUvxArgs(entryPoint, forwardedArgs = [], version = packageJson.version) {
  if (!ALLOWED_ENTRY_POINTS.has(entryPoint)) {
    throw new Error(`Unsupported OneDrive MCP entry point: ${entryPoint}`);
  }
  return ["--from", `${PYTHON_DISTRIBUTION}==${version}`, entryPoint, ...forwardedArgs];
}

export function launch(entryPoint, forwardedArgs = [], spawnProcess = spawn) {
  const child = spawnProcess("uvx", buildUvxArgs(entryPoint, forwardedArgs), {
    stdio: "inherit",
  });

  child.on("error", (error) => {
    if (error.code === "ENOENT") {
      process.stderr.write(
        "OneDrive MCP needs uv (the uvx command). Install uv from https://docs.astral.sh/uv/getting-started/installation/ and retry.\n",
      );
      process.exitCode = 127;
      return;
    }
    process.stderr.write(`Could not start uvx: ${error.message}\n`);
    process.exitCode = 1;
  });

  child.on("exit", (code, signal) => {
    process.exitCode = signal
      ? 128 + (os.constants.signals[signal] ?? 0)
      : (code ?? 1);
  });

  return child;
}
