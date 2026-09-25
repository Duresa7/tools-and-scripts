#!/usr/bin/env node
// Write a local preview-server configuration without starting the server.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

import {
  checkNodeVersion,
  DEFAULT_HOST,
  DEFAULT_PORT,
  isLoopbackAddress,
  parsePort,
  resolveRoot,
  validateHost,
} from "./serve.mjs";

const TOOL_DIR = path.dirname(fileURLToPath(import.meta.url));

const OPTIONS = {
  output: { type: "string" },
  root: { type: "string" },
  host: { type: "string" },
  port: { type: "string" },
  help: { type: "boolean", short: "h" },
};

const USAGE = `Usage: node configure.mjs [--output PATH] [--root PATH] [--host ADDRESS]
                          [--port NUMBER]

Write a local JSON configuration for serve.mjs. When --root is given, git must
confirm it is the top level of a working tree. The configurator starts no
server and refuses to replace an existing output file.

Options:
  --output PATH    file to create (default: config.local.json beside this script)
  --root PATH      top level of the git working tree to serve; leave it out to
                   serve the git top level of the directory serve.mjs starts in
  --host ADDRESS   IP address to listen on (default: ${DEFAULT_HOST})
  --port NUMBER    TCP port, or 0 for any free port (default: ${DEFAULT_PORT})
  -h, --help       show this help
`;

function configurationPayload({ root, host, port }) {
  return {
    _comment: "CUSTOMIZE: Review every generated value before using it.",
    _comment_root:
      "CUSTOMIZE: Confirm the absolute top level of the git working tree, or " +
      "leave it empty to serve the git top level of the current directory.",
    root,
    _comment_host:
      "CUSTOMIZE: Confirm the listen address. serve.mjs refuses a non-loopback " +
      "address unless --allow-non-loopback is given on the command line.",
    host,
    _comment_port: "CUSTOMIZE: Confirm the TCP port, or use 0 for any free port.",
    port,
  };
}

function refuse(output) {
  console.error(`error: refusing to replace existing file: ${output}`);
  return 1;
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
    console.error("Run node configure.mjs --help for usage.");
    return 1;
  }
  if (values.help) {
    process.stdout.write(USAGE);
    return 0;
  }

  const output = path.resolve(values.output ?? path.join(TOOL_DIR, "config.local.json"));
  if (fs.existsSync(output)) {
    return refuse(output);
  }

  let settings;
  try {
    const host = validateHost(values.host ?? DEFAULT_HOST);
    const port =
      values.port !== undefined ? parsePort(values.port, "--port") : DEFAULT_PORT;
    const root = values.root ? await resolveRoot(values.root, process.cwd()) : "";
    settings = { root, host, port };
  } catch (error) {
    console.error(`error: ${error.message}`);
    return 1;
  }

  try {
    fs.mkdirSync(path.dirname(output), { recursive: true });
    // Exclusive creation closes the race between the existence check above and
    // this write. A concurrently created local config is never replaced.
    const text = `${JSON.stringify(configurationPayload(settings), null, 2)}\n`;
    fs.writeFileSync(output, text, { encoding: "utf8", flag: "wx" });
  } catch (error) {
    if (error.code === "EEXIST") {
      return refuse(output);
    }
    console.error(`error: cannot write ${output}: ${error.message}`);
    return 1;
  }

  console.log(`configuration-written: ${output}`);
  if (!isLoopbackAddress(settings.host)) {
    console.log(
      `note: ${settings.host} is not a loopback address; serve.mjs refuses it ` +
        "unless you add --allow-non-loopback",
    );
  }
  const serve = JSON.stringify(path.join(TOOL_DIR, "serve.mjs"));
  console.log(`next-step: node ${serve} --config ${JSON.stringify(output)} --dry-run`);
  return 0;
}

main(process.argv.slice(2)).then((code) => {
  process.exitCode = code;
});
