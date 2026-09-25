#!/usr/bin/env node
// Serve the non-dotfile paths that git tracks in one working tree, for local preview.

import { execFile } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs, promisify } from "node:util";

const execFileAsync = promisify(execFile);

export const DEFAULT_HOST = "127.0.0.1";
export const DEFAULT_PORT = 8123;
const MINIMUM_NODE_MAJOR = 20;
const GIT_OUTPUT_LIMIT = 64 * 1024 * 1024;
const CONFIG_KEYS = new Set(["root", "host", "port"]);

const LOOPBACK = new net.BlockList();
LOOPBACK.addSubnet("127.0.0.0", 8, "ipv4");
LOOPBACK.addAddress("::1", "ipv6");

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".htm": "text/html; charset=utf-8",
  ".svg": "image/svg+xml; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".md": "text/plain; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".excalidraw": "application/json; charset=utf-8",
};

const OPTIONS = {
  config: { type: "string" },
  root: { type: "string" },
  host: { type: "string" },
  port: { type: "string" },
  "allow-non-loopback": { type: "boolean" },
  "dry-run": { type: "boolean" },
  help: { type: "boolean", short: "h" },
};

const USAGE = `Usage: node serve.mjs [--config PATH] [--root PATH] [--host ADDRESS]
                      [--port NUMBER] [--allow-non-loopback] [--dry-run]

Serve the non-dotfile paths that git tracks in one working tree over HTTP so
HTML and SVG files can be previewed in a browser. Ignored and untracked files
return 404. git is asked for the tracked list on every request, and a failed
git query returns HTTP 500 without opening a file.

Configuration precedence: command line, config file, documented default.

Options:
  --config PATH          local JSON configuration copied from config.example.json
  --root PATH            top level of the git working tree to serve
                         (default: git top level of the current directory)
  --host ADDRESS         IP address to listen on (default: ${DEFAULT_HOST})
  --port NUMBER          TCP port, or 0 for any free port (default: ${DEFAULT_PORT})
  --allow-non-loopback   accept a --host that is not a loopback address; every
                         tracked file becomes readable from that network
  --dry-run              resolve settings and query git once, then exit
                         without opening a port
  -h, --help             show this help
`;

export function checkNodeVersion() {
  const major = Number(process.versions.node.split(".")[0]);
  if (major < MINIMUM_NODE_MAJOR) {
    throw new Error(
      `Node.js ${MINIMUM_NODE_MAJOR} or newer is required; found ${process.version}`,
    );
  }
}

export function isLoopbackAddress(address) {
  const family = net.isIP(address);
  if (family === 0) {
    return false;
  }
  return LOOPBACK.check(address, family === 4 ? "ipv4" : "ipv6");
}

// Only IP literals are accepted. A host name can resolve to a different
// interface than the one it appears to name, which defeats the loopback check.
export function validateHost(host) {
  if (typeof host !== "string" || net.isIP(host) === 0) {
    throw new Error(
      `host must be an IP address such as 127.0.0.1 or ::1, not ${JSON.stringify(host)}`,
    );
  }
  return host;
}

export function parsePort(value, source) {
  if (!/^\d{1,5}$/.test(value) || Number(value) > 65535) {
    throw new Error(`${source} must be an integer from 0 to 65535`);
  }
  return Number(value);
}

function expandHome(value) {
  if (value === "~") {
    return os.homedir();
  }
  if (value.startsWith("~/") || value.startsWith("~\\")) {
    return path.join(os.homedir(), value.slice(2));
  }
  return value;
}

function firstLine(text) {
  return String(text ?? "")
    .trim()
    .split(/\r?\n/)[0];
}

function gitFailure(error) {
  if (error.code === "ENOENT") {
    return "git was not found on PATH";
  }
  return firstLine(error.stderr) || error.message;
}

export async function resolveRoot(requested, cwd) {
  const start = requested ? path.resolve(cwd, expandHome(requested)) : cwd;
  let topLevel;
  try {
    const { stdout } = await execFileAsync(
      "git",
      ["-C", start, "rev-parse", "--show-toplevel"],
      { encoding: "utf8", windowsHide: true },
    );
    topLevel = stdout.replace(/\r?\n$/, "");
  } catch (error) {
    if (error.code === "ENOENT") {
      throw new Error("git was not found on PATH");
    }
    throw new Error(
      `${start} is not inside a git working tree: ${gitFailure(error)}`,
    );
  }
  if (!topLevel) {
    throw new Error(`${start} is not inside a git working tree`);
  }
  const realTopLevel = await fsp.realpath(topLevel);
  // An explicit root that is a subdirectory is refused rather than widened to
  // the whole repository, so the served tree is never larger than requested.
  if (requested && (await fsp.realpath(start)) !== realTopLevel) {
    throw new Error(
      `${start} is not the top level of its git working tree; git reports ${topLevel}`,
    );
  }
  return realTopLevel;
}

export function parseConfig(payload, source) {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error(`${source}: configuration root must be a JSON object`);
  }
  for (const key of Object.keys(payload)) {
    if (key.startsWith("_comment")) {
      continue;
    }
    // A stale config file must never expose the tree by itself.
    if (key === "allow_non_loopback") {
      throw new Error(
        `${source}: allow_non_loopback is accepted only on the command line ` +
          "as --allow-non-loopback",
      );
    }
    if (!CONFIG_KEYS.has(key)) {
      throw new Error(`${source}: unknown key: ${key}`);
    }
  }
  const settings = {};
  if ("root" in payload) {
    if (typeof payload.root !== "string") {
      throw new Error(`${source}: root must be a string`);
    }
    if (payload.root && !path.isAbsolute(expandHome(payload.root))) {
      throw new Error(`${source}: root must be an absolute path or empty`);
    }
    settings.root = payload.root;
  }
  if ("host" in payload) {
    settings.host = validateHost(payload.host);
  }
  if ("port" in payload) {
    const port = payload.port;
    if (!Number.isInteger(port) || port < 0 || port > 65535) {
      throw new Error(`${source}: port must be an integer from 0 to 65535`);
    }
    settings.port = port;
  }
  return settings;
}

async function loadConfig(configPath) {
  let text;
  try {
    text = await fsp.readFile(configPath, "utf8");
  } catch (error) {
    throw new Error(`cannot read config ${configPath}: ${error.message}`);
  }
  let payload;
  try {
    payload = JSON.parse(text);
  } catch (error) {
    throw new Error(`${configPath}: invalid JSON: ${error.message}`);
  }
  return parseConfig(payload, configPath);
}

export async function resolveSettings(values, cwd) {
  const config = values.config ? await loadConfig(values.config) : {};
  const host = validateHost(values.host ?? config.host ?? DEFAULT_HOST);
  const port =
    values.port !== undefined
      ? parsePort(values.port, "--port")
      : (config.port ?? DEFAULT_PORT);
  const exposed = !isLoopbackAddress(host);
  if (exposed && !values["allow-non-loopback"]) {
    throw new Error(
      `host ${host} is not a loopback address; add --allow-non-loopback to serve ` +
        "every tracked file to that network",
    );
  }
  const root = await resolveRoot(values.root ?? config.root ?? "", cwd);
  return { root, host, port, exposed };
}

export async function trackedFiles(root) {
  const { stdout } = await execFileAsync("git", ["-C", root, "ls-files", "-z"], {
    encoding: "utf8",
    maxBuffer: GIT_OUTPUT_LIMIT,
    windowsHide: true,
  });
  return new Set(stdout.split("\0").filter(Boolean));
}

// rel is repository-relative with forward slashes and no leading slash.
export function isServablePath(rel, tracked) {
  if (!rel || rel.split("/").some((segment) => !segment || segment.startsWith("."))) {
    return false;
  }
  return tracked.has(rel);
}

// A browser always sends Host. Requiring an IP literal or localhost stops a
// page on another site from reaching this server through a DNS name that it
// later points at 127.0.0.1.
export function isAllowedHostHeader(value) {
  if (typeof value !== "string") {
    return false;
  }
  const header = value.trim().toLowerCase();
  let name;
  let rest;
  if (header.startsWith("[")) {
    const end = header.indexOf("]");
    if (end < 0) {
      return false;
    }
    name = header.slice(1, end);
    rest = header.slice(end + 1);
  } else {
    const colon = header.indexOf(":");
    name = colon < 0 ? header : header.slice(0, colon);
    rest = colon < 0 ? "" : header.slice(colon);
  }
  if (rest && !/^:\d{1,5}$/.test(rest)) {
    return false;
  }
  return name === "localhost" || net.isIP(name) !== 0;
}

function send(response, status, body, headers = {}) {
  response.writeHead(status, {
    "content-type": "text/plain; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    ...headers,
  });
  response.end(body);
}

function notFound(response) {
  send(response, 404, "not found\n");
}

async function serveFile(root, rel, tracked, request, response) {
  const file = path.resolve(root, rel);
  // Re-check after resolution so ".." cannot climb out of the working tree.
  const resolvedRel = path.relative(root, file).split(path.sep).join("/");
  if (
    file !== path.join(root, ...rel.split("/")) ||
    !isServablePath(resolvedRel, tracked)
  ) {
    return notFound(response);
  }

  // Follow a symbolic link only when its final target is itself a tracked,
  // non-dotfile path inside the root. This covers a tracked link and a tracked
  // directory later replaced by a link.
  let real;
  try {
    real = await fsp.realpath(file);
  } catch {
    return notFound(response);
  }
  const realRel = path.relative(root, real);
  if (
    path.isAbsolute(realRel) ||
    !isServablePath(realRel.split(path.sep).join("/"), tracked)
  ) {
    return notFound(response);
  }

  // Checking before open keeps a FIFO or device node from blocking the open.
  try {
    if (!(await fsp.stat(real)).isFile()) {
      return notFound(response);
    }
  } catch {
    return notFound(response);
  }
  let handle;
  try {
    handle = await fsp.open(real, "r");
  } catch {
    return notFound(response);
  }
  let stats;
  try {
    stats = await handle.stat();
  } catch {
    await handle.close();
    return notFound(response);
  }
  if (!stats.isFile()) {
    await handle.close();
    return notFound(response);
  }

  response.writeHead(200, {
    "content-type": TYPES[path.extname(real).toLowerCase()] || "application/octet-stream",
    "content-length": stats.size,
    // A reload always shows the file as it is on disk.
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  if (request.method === "HEAD") {
    await handle.close();
    response.end();
    return;
  }
  const stream = handle.createReadStream();
  stream.on("error", () => response.destroy());
  stream.pipe(response);
}

async function handleRequest(root, request, response) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return send(response, 405, "method not allowed\n", { allow: "GET, HEAD" });
  }
  if (!isAllowedHostHeader(request.headers.host)) {
    return send(response, 403, "host not allowed\n");
  }

  let urlPath;
  try {
    urlPath = decodeURIComponent(request.url.split("?")[0].split("#")[0]);
  } catch {
    return send(response, 400, "bad request\n");
  }

  // Query git on every request instead of caching at startup. One local git
  // process per request is slower, but tracking changes take effect at once
  // and there is no stale allowlist that keeps serving an untracked file.
  let tracked;
  try {
    tracked = await trackedFiles(root);
  } catch (error) {
    console.error(`git-error: ${gitFailure(error)}`);
    return send(response, 500, "unable to read tracked files\n");
  }

  // There is no default page. "/" reports the serving rule and a count so a
  // bare URL is useful without guessing an index file.
  if (urlPath === "/") {
    let visible = 0;
    for (const rel of tracked) {
      if (isServablePath(rel, tracked)) {
        visible += 1;
      }
    }
    return send(
      response,
      200,
      "git preview server\n\n" +
        "serving non-dotfile paths reported by git ls-files\n" +
        `currently visible: ${visible} files\n\n` +
        "request a repository-relative path, for example /docs/index.html\n",
    );
  }

  const rel = urlPath.replace(/^\/+/, "").replace(/\\/g, "/");
  if (!isServablePath(rel, tracked)) {
    return notFound(response);
  }
  return serveFile(root, rel, tracked, request, response);
}

export function createPreviewServer(root) {
  return http.createServer((request, response) => {
    handleRequest(root, request, response).catch((error) => {
      console.error(`request-error: ${error.message}`);
      if (response.headersSent) {
        response.destroy();
      } else {
        send(response, 500, "internal error\n");
      }
    });
  });
}

export function formatUrl(host, port) {
  return `http://${net.isIP(host) === 6 ? `[${host}]` : host}:${port}/`;
}

function isWildcard(host) {
  return host === "0.0.0.0" || /^[0:]+$/.test(host);
}

function exposureWarning(settings) {
  const scope = isWildcard(settings.host)
    ? `${settings.host} accepts connections on every interface`
    : `${settings.host} is not a loopback address`;
  return (
    `warning: --allow-non-loopback is set and ${scope}. Every tracked, ` +
    `non-dotfile path in ${settings.root} is readable without authentication or ` +
    "encryption by anything that can reach it. Stop the server when you finish."
  );
}

function listenFailure(error, host, port) {
  switch (error.code) {
    case "EADDRINUSE":
      return `port ${port} is already in use on ${host}; choose another --port`;
    case "EACCES":
      return `permission denied listening on ${host}:${port}; choose a port above 1023`;
    case "EADDRNOTAVAIL":
      return `address ${host} is not assigned to this machine`;
    default:
      return `cannot listen on ${host}:${port}: ${error.message}`;
  }
}

async function main(argv) {
  let values;
  try {
    checkNodeVersion();
    ({ values } = parseArgs({
      args: argv,
      options: OPTIONS,
      strict: true,
      allowPositionals: false,
    }));
  } catch (error) {
    console.error(`error: ${error.message}`);
    console.error("Run node serve.mjs --help for usage.");
    return 1;
  }
  if (values.help) {
    process.stdout.write(USAGE);
    return 0;
  }

  let settings;
  try {
    settings = await resolveSettings(values, process.cwd());
  } catch (error) {
    console.error(`error: ${error.message}`);
    return 1;
  }
  if (settings.exposed) {
    console.error(exposureWarning(settings));
  }

  if (values["dry-run"]) {
    let tracked;
    try {
      tracked = await trackedFiles(settings.root);
    } catch (error) {
      console.error(`error: unable to read tracked files: ${gitFailure(error)}`);
      return 1;
    }
    const visible = [...tracked].filter((rel) => isServablePath(rel, tracked)).length;
    console.log(`root: ${settings.root}`);
    console.log(`would-listen: ${formatUrl(settings.host, settings.port)}`);
    console.log(`servable-files: ${visible}`);
    console.log("dry-run: no port was opened");
    return 0;
  }

  const server = createPreviewServer(settings.root);
  try {
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen({ host: settings.host, port: settings.port }, () => {
        server.off("error", reject);
        resolve();
      });
    });
  } catch (error) {
    console.error(`error: ${listenFailure(error, settings.host, settings.port)}`);
    return 1;
  }

  let failed = false;
  const closed = new Promise((resolve) => server.once("close", resolve));
  const stop = () => {
    server.close();
    server.closeAllConnections();
  };
  server.on("error", (error) => {
    console.error(`server-error: ${error.message}`);
    failed = true;
    stop();
  });
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);

  console.log(`root: ${settings.root}`);
  console.log(`listening: ${formatUrl(settings.host, server.address().port)}`);
  console.log("stop: press Ctrl+C");
  await closed;
  return failed ? 1 : 0;
}

function invokedDirectly() {
  if (!process.argv[1]) {
    return false;
  }
  try {
    return (
      fs.realpathSync.native(process.argv[1]) ===
      fs.realpathSync.native(fileURLToPath(import.meta.url))
    );
  } catch {
    return false;
  }
}

if (invokedDirectly()) {
  main(process.argv.slice(2)).then((code) => {
    process.exitCode = code;
  });
}
