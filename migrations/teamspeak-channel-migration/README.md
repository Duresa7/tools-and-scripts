# TeamSpeak channel migration

`teamspeak_channels.py` exports a TeamSpeak 3 channel hierarchy through the desktop client's ClientQuery plugin and recreates it through the target server's ServerQuery endpoint. This path works when the source ServerQuery port is unavailable but an administrator can connect with the desktop client.

The export includes channel names, hierarchy, topics, descriptions, codecs, limits, flags, phonetic names, and banner settings. TeamSpeak does not expose channel passwords in a reversible form, so the importer cannot copy them. Custom icon files also need a separate migration.

## What you must customize

For export, the default `127.0.0.1:25639` works when ClientQuery runs in the desktop client on the same machine. Change `--host`, `--port`, or `--handler-id` only when your client layout differs. For import, supply your target ServerQuery host, port, virtual server ID, and username. Put credentials in the documented environment variables or let the hidden prompt ask for them. Do not paste credentials into this source file or a command argument. Run each subcommand with `--help` to see every input.

## Export through ClientQuery

Enable the ClientQuery plugin in the TeamSpeak 3 desktop client, connect to the source server, and read the API key from the plugin settings. Put the key in an environment variable so it does not enter shell history or the process list:

```bash
read -rsp 'ClientQuery API key: ' TS3_CLIENTQUERY_API_KEY
export TS3_CLIENTQUERY_API_KEY
python migrations/teamspeak-channel-migration/teamspeak_channels.py export --output channels.json
unset TS3_CLIENTQUERY_API_KEY
```

Use `--handler-id` when several server tabs are open. The default ClientQuery endpoint is `127.0.0.1:25639`. The exporter writes through a temporary file, syncs it before atomic installation, sets mode `0600`, and refuses to replace an existing JSON file unless `--force` is present.

## Preview the import

The dry run reads and validates the JSON without opening a network connection:

```bash
python migrations/teamspeak-channel-migration/teamspeak_channels.py import \
  --input channels.json \
  --dry-run
```

## Import through ServerQuery

Set the target credential, then run the import against the target's LAN or local ServerQuery address:

```bash
read -rsp 'ServerQuery password: ' TS3_SERVERQUERY_PASSWORD
export TS3_SERVERQUERY_PASSWORD
python migrations/teamspeak-channel-migration/teamspeak_channels.py import \
  --input channels.json \
  --host 192.0.2.40 \
  --port 10011 \
  --server-id 1 \
  --username serveradmin \
  --skip-existing
unset TS3_SERVERQUERY_PASSWORD
```

Without the environment variable, the script asks for the credential through a hidden terminal prompt. It does not accept a password or API key argument. Query exceptions identify the failed operation but omit the command text.

Parents are created before children. A child is not moved to the root if its parent fails; the child fails and the process returns exit code `2`. `--skip-existing` maps a same-name channel under the same parent to its existing target ID so descendants retain their hierarchy.

Review `channels.json` before sharing or committing it. Channel names, topics, and descriptions can contain private information.
