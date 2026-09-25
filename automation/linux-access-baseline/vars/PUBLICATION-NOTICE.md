# Public-key example

The tracked example holds only a placeholder. Put the deployed public key in
ignored `vars/automation-key.yml`. Never put a private key in this project.
Public keys aren't passwords, but their data and comments identify devices.
The offline validator requires exactly one placeholder key in the example.

Passwords come from the configured environment-variable names or encrypted
Vault variables. Never put plaintext passwords in inventory or configuration.
An optional encrypted file belongs at ignored `vars/secrets.local.yml`.
