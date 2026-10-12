---
name: setup-aws-agents-devsecops
description: Set up the AWS DevOps Agent and AWS Security Agent connections. Use ONLY when the user mentions the DevOps Agent or Security Agent (e.g. "set up the DevOps Agent", "configure Security Agent"), or when the aws-devops-agent MCP tools are missing. Do not use for generic "set up"/"configure"/"connect" requests unrelated to these agents.
---

# Setup

Run these skills in order:

1. Invoke the `setup-devops-agent` skill to configure the DevOps Agent MCP connection.
2. Invoke the `setup-security-agent` skill to configure the Security Agent workspace (agent space, IAM role, S3 bucket).

If the user only needs one agent, run only the relevant skill.
