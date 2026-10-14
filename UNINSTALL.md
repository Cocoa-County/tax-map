# Disconnect taxmap from an AI CLI

This is the agent-facing fallback for the `taxmap-uninstall` skill. It remains
usable from the tools checkout when the plugin is disabled or removed.
**Keep city projects, resources, source documents, previews, and published maps
by default.** Disabling or uninstalling agent tools is not deleting a project.

## 1. Identify the installation

Read the setup handoff `TAXMAP-SETUP.md` in the tools checkout. For a bundled
skill, read its adjacent `TAXMAP-TOOLS.md` and the plugin's
`taxmap-plugin.json`. Inspect the selected CLI's installed plugins, scopes,
marketplaces, MCP servers, and any legacy personal skill copies.

If records are missing, compare executable paths and skill contents to the
approved taxmap checkout, show what you found, and ask the user to confirm.
A plugin/server/skill name alone does not prove ownership. Do not dump
credentials or unrelated private configuration into chat.

Ask whether the user wants:

- **Disable:** retain the installed plugin but stop loading its skills/MCP.
- **Re-enable:** load the existing plugin again; no project reinitialization.
- **Uninstall plugin:** remove its client installation, keeping projects and
  the separate tools environment.
- **Optional tools cleanup:** a separate, explicitly approved removal of
  identified taxmap-only files after unregistering all dependencies.

Show the exact plugin id, client, scope, and proposed targets, then obtain
confirmation. Managed installations may require administrator action; respect
policy rather than overriding it.

## 2. Use the client plugin manager

Check the installed version's help and the official documentation. Substitute
the verified installed id, which may be `taxmap@taxmap-local`, a bare name,
or another recorded id. These forms are agent references, not commands the
user needs to assemble:

| Client | Disable / enable | Uninstall | Reference |
|---|---|---|---|
| Copilot CLI | `copilot plugin disable ID` / `copilot plugin enable ID` | `copilot plugin uninstall ID` | [Copilot plugins](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/plugins-finding-installing) |
| Claude Code | `claude plugin disable ID` / `claude plugin enable ID` in the recorded scope | `claude plugin uninstall ID` in the recorded scope | [Claude plugin management](https://code.claude.com/docs/en/plugins/install) |
| Codex CLI | Use `/plugins` on/off controls, or the documented scoped `plugins."ID".enabled` setting with approval | `codex plugin remove` with the installed version's required id arguments | [Codex plugins](https://developers.openai.com/codex/plugins) |

Preserve every unrelated entry and existing approval policy. Use native
commands or documented, scoped configuration edits. Do not delete plugin
caches yourself. Do not invent enable/disable subcommands for versions
without them. Disabling the bundle affects its skills and plugin-managed
MCP, **not independently registered servers or loose skill copies**.

Inspect and resolve those independent entries as below before claiming a
complete disconnect. Finish this inspection, any approved legacy cleanup, and
save the handoff before uninstalling/disabling the skill's own plugin.
Current-session tools may remain until the CLI exits.

Some clients load local-marketplace plugins directly from disk. In that mode,
native uninstall may only disable the entry while leaving it discoverable
from the catalog. Record the actual result rather than claiming files were
deleted. Removing the dedicated marketplace (with approval and no other
dependents) can remove that remaining catalog entry; the generated bundle
stays on disk until separately approved cleanup.

## 3. Handle legacy or duplicate registrations

Older setup copied skills to personal directories and registered MCP
separately. Removing a plugin alone does not remove those installations.

- Verify the recorded server's command, arguments, environment and scope.
  With approval, remove only that server through the client's MCP manager
  (`codex mcp remove`, `claude mcp remove` with the correct scope, or
  Copilot's supported MCP removal UI/configuration). Consult current help.
- For each identified loose skill, compare all files with the recorded
  source/revision. Preserve modifications, offer a user-approved backup,
  and ask before removal. Remove only exact, confirmed skill directories,
  not the personal skills directory. Do not remove similarly named third-party
  skills or a shared `.agents/skills` copy still used by another client.
- For temporary disable, prefer reversible relocation of approved legacy
  copies outside discovery locations with a recorded restore path, not deletion.

If ownership or cross-client use is uncertain, stop with the remaining targets
and ask. Report partial removal explicitly; do not give a success-shaped
fallback.

## 4. Optional tools cleanup

Plugin uninstall keeps the separate Python environment, generated marketplace,
tools checkout, and setup record. Offer cleanup separately, never as a default.
Check all known CLI registrations and running previews for references to these
paths first. Explain the inspection limits; do not assume another client is
unused just because this CLI no longer lists taxmap.

Inspect the exact resolved paths for project manifests, user documents, Git
changes, and other data. Do not remove a tools directory containing projects
or uncertain files. In particular, the optional direct installer stores the
environment inside a city project: **never delete that project root**.

Stop only identified taxmap-owned processes with approval and specific process
ids, or use the live `site_serve(stop=true)` tool before disconnecting it.
Keep a removal record outside any approved cleanup targets before deletion.
Never recursively delete a repository/workspace root, user home, shared
configuration root, or a directory chosen through a wildcard. Only remove
specific inspected tool-only subdirectories/files. Leave the checkout root
for the user to remove manually if they want it gone.

Leave shared Python, uv, Git, the AI CLI, authentication, shell profiles, and
published sites alone. Do not revoke accounts, uninstall shared runtimes,
commit, push, or change deployments.

## 5. Verify and restart

Record what changed, what was preserved, blockers, and exact re-enable or
reinstall instructions. Inspect the client's plugin/server lists and settings.
Remove the dedicated marketplace only after the user approves and you verify
no other installed plugin uses it; never use a force option to remove others.

Tell the user to exit and restart the selected CLI. In the new session, verify
that the disabled/uninstalled bundle's skills and MCP tools are absent (or
present again after re-enable). Verify identified city project paths remain.
If you cannot inspect a fresh session, say that the final live check remains
for the user; do not claim it happened.

For reinstallation, follow [SETUP.md](SETUP.md), reuse the intended tools
environment where healthy, and open existing projects rather than creating
duplicates. Plugin updates never migrate or delete city project data.
