/**
 * odl-guard — repo-law enforcement at the tool-call layer.
 *
 * Enforces AGENTS.md laws mechanically, regardless of what the model decides:
 *  - blocks destructive git (force push, reset --hard, clean -f, checkout .,
 *    --no-verify)
 *  - blocks writes to env/secret files
 *  - blocks ad-hoc bash requests to *.wnba.com — every live-source request is
 *    budgeted (docs/compliance.md); the Live Smoke workflow is the only
 *    sanctioned live path
 *  - warns on fixtures/ writes (provenance law), http_client/extractor edits
 *    (compliance commitments), and schema.sql writes (live Postgres layer)
 *
 * Runs in every pi mode; in headless (-p/json/rpc) blocks are unconditional
 * since no one can confirm.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const DESTRUCTIVE_GIT = [
  // --force-with-lease is blocked too: intentionally strict — no agent
  // force-pushes of any variant in this repo (AGENTS.md).
  /git\s+push\b[^\n]*(--force\b|-f\b)/,
  /git\s+push\b[^\n]*\s\+\S+/, // refspec force-push: git push origin +main
  /git\s+reset\s+--hard\b/,
  /git\s+clean\s+-[a-z]*f/,
  /git\s+checkout\s+(--\s+)?\.(\s|$)/,
  /git\s+restore\b(?![^\n]*--staged)[^\n]*\s\.(\s|$)/, // git restore [--source=...] .
  /git\s+commit\b[^\n]*--no-verify\b/,
];

// HTTP clients aimed at the live source. Deliberately requires BOTH a client
// invocation AND the wnba.com host in the same command, so `grep wnba.com src/`
// and other read-only mentions stay allowed.
const LIVE_SOURCE = [
  /\b(curl|wget|httpie|xh|aria2c)\b[^\n]*\bwnba\.com/i,
  /\bhttp\b\s[^\n]*\bwnba\.com/i, // httpie's `http` binary
  /\bpython3?\b[^\n]*\b(requests|urllib|httpx|aiohttp)\b[^\n]*\bwnba\.com/i,
];

const PROTECTED_WRITE_PATTERNS = [
  {
    re: /(^|\/)\.env(rc)?(\.|$)|\.env$/,
    why: "environment/secret files are managed by hand, never by agents",
  },
];

const FIXTURE_RE = /(^|\/)fixtures\//;
const COMPLIANCE_RE = /(^|\/)src\/wnba_pipeline\/(http_client|extractor)\.py$/;
const SCHEMA_RE = /(^|\/)src\/wnba_pipeline\/schema\.sql$/;

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName === "bash") {
      const command = String((event.input as { command?: unknown }).command ?? "");
      for (const re of DESTRUCTIVE_GIT) {
        if (re.test(command)) {
          return {
            block: true,
            reason: `odl-guard: "${command.slice(0, 80)}" is a destructive git operation banned by repo law (AGENTS.md). Not allowed in any mode.`,
          };
        }
      }
      for (const re of LIVE_SOURCE) {
        if (re.test(command)) {
          return {
            block: true,
            reason:
              "odl-guard: ad-hoc requests to *.wnba.com are banned — every live-source request is budgeted (docs/compliance.md). Work offline against fixtures/; the Live Smoke GitHub workflow is the only sanctioned live path.",
          };
        }
      }
      return;
    }

    if (event.toolName === "write" || event.toolName === "edit") {
      const input = event.input as { path?: unknown; file_path?: unknown };
      const path = String(input.path ?? input.file_path ?? "");
      for (const { re, why } of PROTECTED_WRITE_PATTERNS) {
        if (re.test(path)) {
          return { block: true, reason: `odl-guard: writes to "${path}" are blocked — ${why}.` };
        }
      }
      const warn = (msg: string) => {
        if (ctx.hasUI) ctx.ui.notify(msg, "warning");
      };
      if (FIXTURE_RE.test(path)) {
        warn(
          "odl-guard: fixtures law — every JSON fixture needs a _provenance object, the synthetic flag must be honest, and no secrets ever (fixtures/README.md).",
        );
      }
      if (COMPLIANCE_RE.test(path)) {
        warn(
          "odl-guard: http_client/extractor edits touch compliance commitments — retry counts, backoff, spacing, and headers are hard limits (docs/compliance.md §2). Never loosen them.",
        );
      }
      if (SCHEMA_RE.test(path)) {
        warn(
          "odl-guard: schema.sql is the live Postgres serving layer — plan the rollout (docs/deployment.md) before changing it.",
        );
      }
    }
  });
}
