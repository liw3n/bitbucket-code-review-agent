## Project Overview
Bitbucket webhook to automatically run code reviews when pull requests are opened or modified. Code reviews include documentation, unit tests and logic review against Jira ticket and Confluence Pages.

## Directory
- 'src/': Main folder that handles code review webhook logic.
- 'tests/': Test folder that includes unit tests.

## Development Rules
- Use uv for package management: uv add package
- Develop in SSH remote environment

## Coding Style
- Type hints required for all code
- PEP 8 naming (snake_case for functions/variables)
- Google-style docstrings for functions
- One-line docstrings for files

## Pull Requests
- Create a detailed message of what changed. Focus on the high level description of the problem it tries to solve, and how it is solved. Don't go into the specifics of the code unless it adds clarity.