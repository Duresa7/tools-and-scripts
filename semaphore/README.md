# Semaphore SQLite guard

`semaphore_sqlite.py` contains two operations for Semaphore installations that use SQLite:

- `backup` uses SQLite's online backup API, refuses to overwrite a file, checks source and destination integrity, sets the destination to mode `0600`, and removes an incomplete destination.
- `compare` checks database integrity, compares non-secret project structure, and compares digests of encrypted access-key and environment payload rows. It prints counts and booleans, not credential values or hashes.

Stop Semaphore before restoring a backup. A verified online backup is safe to create while Semaphore is running, but replacing its live database is a separate maintenance action.

## Create a backup

Create the destination directory first, then choose a destination file that does not exist:

```bash
install -d -m 0700 /root/semaphore-backups/pre-upgrade
python semaphore/semaphore_sqlite.py backup \
  /var/lib/semaphore/database.sqlite \
  /root/semaphore-backups/pre-upgrade/database.sqlite
```

## Compare state after a change

```bash
python semaphore/semaphore_sqlite.py compare \
  /var/lib/semaphore/database.sqlite \
  /root/semaphore-backups/pre-upgrade/database.sqlite \
  --require-secret-records
```

The comparison returns `0` when integrity, structure, and secret digests match. It returns `1` for an input, schema, or SQLite error and `2` for a completed comparison that found a change.

The selected columns match Semaphore's project, environment, inventory, repository, template, view, and access-key tables. Test the script against a backup after a Semaphore schema upgrade because upstream table names can change between releases.
