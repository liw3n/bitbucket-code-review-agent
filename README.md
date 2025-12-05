## Project Overview -- Sentinel
This is a code review bot for Bitbucket repositories to be used as a webhook. Code reviews will be triggered once a pull request is opened or modified. 

The reviews include comments for unit test, documentation and logic (a check of PR changes against Jira and Confluence requirements). It uses indexing and semantic search techniques for deep contextual understanding of the repository.

Performance metrics of Sentinel review bot are tracked and stored in your Postgres database. Reviewers can also give feedback on the review comments which are also stored in the same Postgres database. Refer to [Developer Documentation](https://github.com/liw3n/bitbucket-code-review-agent/blob/main/docs/documentation/developer_documentation.md#accessing-metrics-database) on how to access these metrics.

## Project Requirements
- Cloud server to deploy the Sentinel code review.
- Ollamma endpoint for vector embeddings.
- Qdrant vector database.
- Postgres database.
- uv package manager.

## Steps
1. Sentinel first needs to be deployed as a webhook. Follow steps in the [Developer Documentation](https://github.com/liw3n/bitbucket-code-review-agent/blob/main/docs/documentation/developer_documentation.md) for more details.
2. Once the webhook is deployed, follow the steps in [User Documentation](https://github.com/liw3n/bitbucket-code-review-agent/blob/main/docs/documentation/user_documentation.md) to start using the code review bot.