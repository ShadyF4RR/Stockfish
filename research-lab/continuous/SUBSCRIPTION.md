# Running Stockfish research with a ChatGPT subscription

Start research from the signed-in Codex Cloud interface with the saved **Stockfish** environment. Use the strongest online model available in the account's model picker for the task. Keep automatic credit purchases and paid overage disabled if the account offers them. The queue has a hard $0 external API cap. The normal Codex usage dashboard is authoritative for included allowance. The queue cannot read account billing or identify the model selected in a future run.

## What the connectivity tests establish

| Request | Codex workspace shell | Standard GitHub-hosted Ubuntu runner |
| --- | --- | --- |
| Anonymous GET to the Codex model catalogue on chatgpt.com | Proxy refused CONNECT, HTTP 403 | HTTP 401, an HTTP response from the reachable service |
| Anonymous GET to the Codex Responses path | CONNECT 403, no origin HTTP status | HTTP 405 (GET is disallowed) |
| Anonymous GET to the official Fishtest active-run list | Proxy refused CONNECT, HTTP 403 | HTTP 200 |
| Nested subscription-authenticated CLI inference | WebSocket CONNECT 403, then HTTPS connection failure. No final model message. | Untested. The runner was sent no subscription credentials. |

The workspace network status showed an enforced restricted host list, without chatgpt.com. The CLI recognized a ChatGPT sign-in, which reports local session state. The nested CLI did not establish a tunnel to complete the test. Account quota, expiry and model entitlement could not produce the measured CONNECT reply. The HTTPS fallback log does not record an origin status. A normal hosted Codex task can still call its own platform tools, read and push the fork, and run an engine build. It uses a separate execution path.

The official Codex GitHub Action documents provider API-key authentication. OpenAI separately documents an advanced account-authenticated CLI workflow on trusted private infrastructure: serialize access to the protected login cache and preserve refreshed credentials. The guide explicitly excludes public and open-source repositories. The public Stockfish fork never receives a ChatGPT session, browser cookie, password or API key. The experimental CLI command `codex cloud exec --env ...` submits a cloud task from a signed-in reachable client. Installing that binary in Actions gives the job no account access.

[Codex authentication](https://developers.openai.com/codex/auth/) · [Advanced private CI restrictions](https://learn.chatgpt.com/docs/auth/ci-cd-auth) · [Official GitHub Action](https://github.com/openai/codex-action) · [GitHub runner probe](https://github.com/ShadyF4RR/Stockfish/actions/runs/44824424088) · [Fishtest production read](https://github.com/ShadyF4RR/Stockfish/actions/runs/44821143223)

## Scheduling and verification

Use a first-party scheduled task with the managed cloud environment and the signed-in account. The durable job prompt should identify the fork, reviewed control commit, normal subscription route, permitted budget, [claim protocol](QUEUE.md) and [worker evidence](WORKER.md). On each four-hour tick, read the queue and claim at most one experiment under a unique stable task/run ID. If an active lease, exhausted allowance or budget prevents a claim, record the reason and exit. Continue through design, implementation, separate review, isolated build, tests, benchmark and justified matches. Settle the claim and append a distinct, justified next hypothesis. A cloud task can also be started manually using the same procedure.

Check the scheduled task's environment and model in the product. Each of two consecutive unattended runs needs its own run record, lease, branch and build receipt. A generic reminder, a read-only inventory, manual runs and offline rehearsals cannot establish continuous research. A desktop schedule bound to a local directory depends on that computer. Keep all durable state on the fork.

The optional GitHub inventory runs at minute 17 every fourth UTC hour once installed on the fork default branch and enabled via `RESEARCH_QUEUE_INVENTORY=enabled`. It reads only the queue. Promotion and public Fishtest spending require their documented approvals. Public test submission remains human-operated.

[Scheduled tasks](https://developers.openai.com/codex/app/automations/) · [Codex Cloud](https://help.openai.com/en/articles/20001545-using-codex-cloud) · [Included usage and billing](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan)
