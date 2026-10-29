---
name: publish-skill
description: Share a skill with every agent on every machine through agent-hub. Use when the user says "publish this skill", "share this skill with the team", "把这个 skill 同步到所有机器", or after writing a skill that other agents should have.
---

# Publishing a skill to the team

Skills are distributed by the hub, not by copying directories between machines.

1. Make sure the skill is a directory with a `SKILL.md` (YAML frontmatter `name` + `description`)
   plus any scripts/references it needs. Keep paths inside it relative; do not hard-code
   machine-specific paths unless the skill only makes sense on one node.
2. Publish it:
   - with hub tools: `hub_skill_publish(path="<dir>")`
   - from a shell: `agenthub skill publish <dir>`
3. The hub commits it into its skill store (the `team-skills` plugin of its Claude Code
   marketplace) and tells every node's hubd to update: Claude Code sees it as
   `team-skills:<name>`; Codex and pi get it as a folder in their skills directory.

Publishing the same name again replaces it. `hub_skills` / `agenthub skill list` shows what is published.
