# MCP tools

The EasyCrypt MCP server should already be configured in Claude Code.

Expected local MCP server path:

```
/home/lr/easycrypt-mcp/easycrypt_mcp.py
```

Use the configured EasyCrypt MCP tools for compile, goal inspection, and
interactive proof work. Do not use web search or external downloads.

Shell search rules:
- Prefer rg for source search.
- Do not use find -exec, xargs, shell loops, or semicolon-chained command groups.
- Example: rg -l "proc \*" /path/to/easycrypt/theories -g "*.ec" | head -10
