# Claude Code adapter

`SKILL.md` here is the canonical copy. Point the skill directory at it so the
instructions cannot drift from the engine:

```
mkdir -p ~/.claude/skills/video-edit
ln -sf "$PWD/SKILL.md" ~/.claude/skills/video-edit/SKILL.md
```

Then give the skill the engine — installed from this clone, so it works offline:

```
uv venv --python 3.12 ~/.claude/skills/video-edit/.venv
VIRTUAL_ENV=~/.claude/skills/video-edit/.venv uv pip install -e "$PWD[mlx]"
```

Finally drop in the launcher `scripts/ve`, which execs the package and, if it is
missing, prints the exact commands above instead of a traceback.

The skill holds no engine code: fixes go into the package, and both this adapter
and any other agent pick them up. For a non-Claude agent use
[AGENT.md](../../AGENT.md) instead.
