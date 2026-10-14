---
name: taxmap-uninstall
description: Disconnect, disable, re-enable or uninstall the taxmap plugin, its skills and MCP connection, or clean up a legacy taxmap installation. Use when the user asks to disable taxmap, remove taxmap tools, uninstall skills or stop taxmap MCP. Keep projects and data unless separately approved.
---

# Manage or disconnect taxmap

Read the bundled `../../UNINSTALL.md` before making changes. If this is a
source skill, the same path points to the repository's fallback document.
Read `TAXMAP-TOOLS.md` beside this skill when present and the recorded setup
handoff. Inspect the actual CLI's plugin/server lists and settings; never
guess ownership or scope from a name alone.

Ask which operation the user wants: temporary disable, re-enable, plugin
uninstall, or optional tools cleanup. Explain the exact targets, get
confirmation, and follow UNINSTALL.md. Never remove projects or data as part
of disconnecting the agent.

Use native plugin management. Do not manually delete plugin caches, globally
remove similarly named skills, or stop processes by name. Finish independent
legacy cleanup and save the handoff before disabling/uninstalling this plugin:
that action removes this skill from future sessions. Tell the user how to
restart and verify the result. If already disabled, use the external
UNINSTALL.md or the plugin manager instead of claiming this skill is loaded.
