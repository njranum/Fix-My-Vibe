Sure. Here's the full vision for what Fix My Vibe could become with the improvements layered in.

What it produces today (baseline)
Config files: CLAUDE.md, .cursorrules, .cursorignore, copilot-instructions.md, .gitignore. Useful but narrow.

The expanded output set
PROMPTS.md — a starter library of project-specific prompt patterns. Instead of the developer starting every Claude Code or Cursor session from scratch, they get prompts pre-written for their actual stack. A FastAPI project gets prompts for "add a new endpoint with validation", "write a pytest fixture for this service", "review this for security issues". A Next.js project gets different ones. Nobody else ships this. It's a genuinely novel output for a vibe coding tool.
SECURITY.md — an audit of the codebase for patterns that AI tools commonly introduce. Not just the .env exposure fix that's already there, but active scanning for hardcoded strings that look like API keys, eval() calls, missing input validation on route handlers, SQL strings built with f-strings. Framed as "here's what your AI assistant may have introduced without you noticing." Given that around 45% of AI-generated code contains vulnerabilities such as hardcoded secrets or improper input validation, this is a concrete, demonstrable value-add. daily.dev
MCP server recommendations — baked into CLAUDE.md rather than a separate file. Based on the detected stack, the Planner recommends specific MCP servers with one-line install commands. A Postgres-backed FastAPI project gets the Postgres MCP server suggested. A project with heavy filesystem work gets the filesystem MCP server. This makes the CLAUDE.md genuinely actionable rather than just descriptive.
AI decision log template — a AI_DECISIONS.md starter file. Vibe-coded projects need documentation of the conversation with AI, not just the resulting code — which prompts generated which features, what edge cases were discovered, why certain approaches were taken. Fix My Vibe scaffolds this file with the right structure so the developer has somewhere to put it going forward. Codingwithvibe

The Foundry IQ knowledge base layer
This is where it gets architecturally interesting and directly targets the "Best Use of IQ Tools" prize.
Right now the Researcher agent does live Tavily web searches, which are unpredictable and hard to demo reliably. The upgrade is to replace or supplement this with three curated Foundry IQ knowledge bases:
Knowledge Base 1: Vibe coding security patterns — indexed from OWASP Top 10, known AI-generated vulnerability patterns, and stack-specific pitfalls. When the Scanner detects a FastAPI project, the Researcher queries this KB for FastAPI-specific AI security anti-patterns, gets back grounded, cited answers rather than raw web results. The reasoning trace shows the agent deciding which knowledge base to query and why — that's a much stronger demo for Best Reasoning Agent than "agent called web search."
Knowledge Base 2: MCP server catalogue — a curated index of the most useful MCP servers by stack and use case, with setup instructions. The Planner queries this to populate its MCP recommendations rather than reasoning from scratch. Structured, reliable, demonstrable.
Knowledge Base 3: Config file exemplars — real, high-quality CLAUDE.md and .cursorrules files from well-maintained open source projects, indexed by stack. Instead of the Planner generating config content from first principles, it retrieves the best real-world examples for the detected stack and adapts them. The output quality goes up, and you can show the retrieval happening in the reasoning trace.
Foundry IQ is positioned as the knowledge layer for agents — agents running in Azure AI Foundry call it as a tool when they need factual, grounded answers. Wiring three domain-specific knowledge bases into the Researcher and Planner agents is exactly the intended use pattern and would make Fix My Vibe a strong demonstration of the platform's capabilities. ITNEXT

The sharpened pitch
With all of this in place, the one-liner becomes:
"Run fix-my-vibe . and get a complete AI coding environment audit — security vulnerabilities, missing configs, MCP server recommendations, and a prompt library — all generated from your actual codebase, grounded in curated knowledge."
That's materially more interesting than "generates config files." It's a setup tool that also catches real problems, teaches good patterns, and leaves the developer better equipped to use their AI tools effectively going forward.
