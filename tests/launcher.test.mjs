import assert from "node:assert/strict";
import test from "node:test";

import { buildUvxArgs } from "../lib/launcher.mjs";

test("the MCP launcher pins the Python distribution to the npm package version", () => {
  assert.deepEqual(buildUvxArgs("onedrive-mcp", [], "0.1.0"), [
    "--from",
    "joescars-onedrive-mcp==0.1.0",
    "onedrive-mcp",
  ]);
});

test("the setup launcher forwards arguments after the pinned Python entry point", () => {
  assert.deepEqual(buildUvxArgs("onedrive-mcp-setup", ["--help"], "0.1.0"), [
    "--from",
    "joescars-onedrive-mcp==0.1.0",
    "onedrive-mcp-setup",
    "--help",
  ]);
});

test("unknown console scripts cannot be selected", () => {
  assert.throws(() => buildUvxArgs("python", [], "0.1.0"), /Unsupported/);
});
