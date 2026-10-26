"""An MCP server (stdio) that lets another agent plan bike routes with this service.

``bike-router mcp serve --api URL`` speaks the Model Context Protocol over stdin/stdout and answers
its tool calls by asking a running pyBikeRouter API. See ``docs/mcp.md``.
"""
