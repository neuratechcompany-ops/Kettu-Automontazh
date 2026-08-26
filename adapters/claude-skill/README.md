# Claude Code adapter

Symlink or copy `SKILL.md` into `~/.claude/skills/video-edit/`:

```
mkdir -p ~/.claude/skills/video-edit
ln -sf "$PWD/SKILL.md" ~/.claude/skills/video-edit/SKILL.md
```

The skill drives the installed `automontazh` command; it carries no code of its own.
For any other agent, use [AGENT.md](../../AGENT.md) instead.
