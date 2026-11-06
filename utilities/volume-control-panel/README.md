# Volume control panel

Open the classic Windows Sound control panel (`mmsys.cpl`) from a batch file, so a mouse button, keyboard shortcut, or launcher action can reach it in one step.

## Contents

- [Use case](#use-case)
- [Prerequisites](#prerequisites)
- [Guided setup](#guided-setup)
- [Manual setup](#manual-setup)
- [Inputs](#inputs)
- [Permissions](#permissions)
- [Dry run](#dry-run)
- [Changes made](#changes-made)
- [Safeguard reasoning](#safeguard-reasoning)
- [Rollback](#rollback)
- [Troubleshooting](#troubleshooting)
- [Exit behavior](#exit-behavior)

## Use case

Windows 10 and 11 hide the classic Sound panel behind the Settings app. Bind `volume-control-panel.bat` to a mouse button, a keyboard shortcut, or a launcher so the playback and recording device list opens directly.

Tested status: Static content test only; live matrix pending.

## Prerequisites

- Windows 10 or 11.
- Python 3.11+ and pytest to run the local test, which only reads the file.

## Guided setup

| Field | Value |
|---|---|
| Platform | Windows 10 and 11 |
| Runtime | Windows command processor (`cmd.exe`) |
| Privilege | Ordinary user |
| State change | None; it opens a window |
| Preview | Not applicable |
| Rollback | Not applicable |

The batch file has no settings, so there is no configurator. Copy `volume-control-panel.bat` anywhere and point your shortcut or launcher at it.

## Manual setup

1. Copy `volume-control-panel.bat` to a folder you keep scripts in.
2. Create a shortcut to it, or select it as the program for a mouse or keyboard action.

The file lists other targets in comments: Sound settings (`ms-settings:sound`), App volume (`ms-settings:app-volume`), and the Volume mixer (`sndvol`). To open one of those instead, replace the `start "" mmsys.cpl` line with that commented line, without the leading `::`.

## Inputs

None.

## Permissions

An ordinary user can open the Sound control panel. No elevation is needed.

## Dry run

Not applicable. The only action is opening a window.

## Changes made

None. The batch file opens the control panel and exits. Changes you make in the panel are your own.

## Safeguard reasoning

The script runs one fixed command with no arguments, so it can't act on anything outside the Sound panel.

## Rollback

Not applicable. Close the window.

## Troubleshooting

- **Nothing opens:** run the file from a Command Prompt to see the error text. On a stripped-down Windows image, `mmsys.cpl` may be missing; use one of the commented alternatives.
- **The command window stays open:** a shortcut launched with "Run: Minimized" hides it.

## Exit behavior

| Status | Meaning |
|---|---|
| 0 | The Sound control panel was launched |
| 1 | Windows couldn't start `mmsys.cpl` |
