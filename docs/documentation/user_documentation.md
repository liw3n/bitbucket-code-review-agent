## Repository Requirements
- Code base must be written in Python.
- Test files must be named as test_<file_name>.py.
- Test functions in the test files must be in this format:
- test_<function_name>_<optional_test_case>(optional_fixture, ...)
- Function names must be unique within each file.
- Naming must follow PEP 8 style.

## Configuration
1. Add [sentinel-config.yaml](https://github.com/liw3n/bitbucket-code-review-agent/blob/main/sentinel-config-template.yaml) file at the root of your project.
    - Set `language: python`. Sentinel only supports Python repository.
    - Set `test_folder`:
        - If all test files are in a single folder (e.g. `tests/` ), enter the path to that folder (e.g. `tests/`).
        - If tests are spread across multiple folders/subfolders, enter `subfolder`.
    - (Optional) Set `indexing`:
        - `indexing: False` -> Faster, less context-aware reviews (quicker, fewer tokens).
        - `indexing: True` -> More thorough, code-aware reviews (slower, uses more tokens). 
        - Defaults to False if no `indexing` value set.
    - (Optional) Set `deadcode`:
        - `deadcode: False` -> Only code review (no dead code review)
        - `deadcode: True` -> Both code review and dead code review 
        - Defaults to False if no deadcode value set.
    - (Optional) Set `doc_folder`:
        - Path to folder that includes a .instructions file or .agents file. These files provide additional instructions specific to your project for LLM to give better code reviews.
            - Example of .agent file: [sentinel.agents.md](https://github.com/liw3n/bitbucket-code-review-agent/blob/main/docs/sentinel.agents.md)
        - Defaults to empty folder if `doc_folder` not set.

## Webhook Usage
1. Follow configuration steps above.
2. Add webhook url to your Bitbucket repository. 
    - Under **Status**, choose **Active**. Under **SSL/TLS**, choose **Skip certification verification**.
    - Under **Pull request**, choose **Opened** and **Source branch updated**. Do not choose any other settings.
3. Open or update a pull request and wait for a few minutes for the review to appear.

## Feedback on review comments
- Each Bitbucket comment has a 'Rate this comment' section at the end.
- Pressing onto the stars rating will direct you to a page that saves the feedback in the backend and automatically redirects back to the Bitbucket pull request after a few seconds.