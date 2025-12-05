import os
import asyncio
import aiofiles
from reviewer import Reviewer
from jira_processor import JIRAProcessor
from pull_request_processor import PullRequestProcessor
from confluence_processor import ConfluenceProcessor
from typing_extensions import List, Dict, Tuple, override
from commit import Commit
import time
from requests.exceptions import HTTPError

class LogicReviewer(Reviewer):
    def __init__(self, processor: PullRequestProcessor, agent_files: List[str], indexing: bool = False) -> None:
        super().__init__(processor, agent_files)
        self.indexing = indexing
        self.confluence_links = []
        self.confluence_content = ""
        self.pr_description = ""
        self.issue_keys = []
        self.branch_ticket = ""
        self.commit_diff_dict = {}
        self.added_files = {}
        self.removed_files = {}
        self.reviews = ["## PR Evaluation: Alignment with Jira Issue and Confluence\n" + ("-" * 40) + "\n"]
        self.purpose = [] # LLM-generated commit purpose

    async def set_up(self) -> None:
        try:
            start_time = time.time()
            super().log_review_metrics('Setting up logic review...')

            description = self.processor.get_pr_description()
            if description:
                self.pr_description = description

            jira_processor = JIRAProcessor()
            issue_keys, branch_ticket = self.processor.get_issue_key()
            if issue_keys:
                self.issue_keys = issue_keys
            if branch_ticket:
                self.branch_ticket = branch_ticket

            confluence_links = jira_processor.get_confluence_links(branch_ticket)
            if confluence_links:
                self.confluence_links = confluence_links
            confluence_processor = ConfluenceProcessor()
            confluence_content = confluence_processor.get_confluence_content(confluence_links)
            if confluence_content:
                self.confluence_content = confluence_content

            commit_list = self.processor.get_pr_commits()
            diff_tasks = []
            for commit in commit_list:
                diff_tasks.append(self.processor.get_diff_in_commit(commit))

            results = await asyncio.gather(*diff_tasks)

            download_tasks = []
            for commit, (diff_dict, removed_files, added_files) in zip(commit_list, results):
                # Download modified, added, removed files
                tasks = [
                    self.processor.download_file_content(path)
                    for path in diff_dict.keys()
                ]
                download_tasks.extend(tasks)

                # Initialise dictionary
                self.commit_diff_dict[commit] = diff_dict
                self.removed_files[commit] = removed_files
                self.added_files[commit] = added_files
            
            await asyncio.gather(*download_tasks)
            super().log_review_metrics("Finished setting up logic review", start_time)
        except Exception as e:
            error_message = f"Error occurred while setting up for logic review: {e}"
            self.log_errors(error_message, "set_up")
            raise

    async def review_logic(self) -> List[str]:
        try:
            await self.set_up()
            start_time = time.time()
            super().log_review_metrics('Generating logic review...')

            # Generate commit purpose + Review individual commits
            for commit, diff_dict in self.commit_diff_dict.items():
                file_modification_purpose, commit_purpose = await self.generate_purpose(commit, diff_dict)
                if commit_purpose:
                    self.purpose.append(commit_purpose)
                if file_modification_purpose and self.issue_keys: # Only review individual commits if there are linked Jira tickets
                    # Review if commit message has JIRA issue ID 
                    found_key = next((key for key in self.issue_keys if key in commit.message), None)
                    if found_key:
                        review = await self.review_commit_messages(found_key, file_modification_purpose, commit)
                        if review:
                            self.reviews.append(review)

            # Review Jira ticket
            full_commit_purpose = ("\n").join(self.purpose[::-1]) # Sort commits in chronological order
            if self.branch_ticket:
                # Check if Jira ticket exist in Jira
                try:
                    jira_processor = JIRAProcessor()
                    issue_summary, issue_description = jira_processor.get_issue(self.branch_ticket)
                except Exception:
                    unfound_ticket_header = "### Invalid Jira ticket found\n"
                    unfound_ticket_review = f"❌ No Jira ticket {self.branch_ticket} found in Jira. Please ensure that {self.branch_ticket} is a valid Jira ticket."
                    unfound_ticket = unfound_ticket_header + unfound_ticket_review
                    self.reviews.append(unfound_ticket)
                    issue_summary, issue_description = "", ""
                
                if full_commit_purpose and issue_summary: # Only review Jira ticket if it exists
                    await self.review_overall_issue(full_commit_purpose, issue_summary, issue_description)
            else: # Missing Jira review
                branch = self.processor.get_pr_source_branch()
                branch_type = branch.split('/')[0]
                branch_description = branch.split('/')[-1]
                missing_ticket_header = "### Missing Jira ticket in branch\n"
                missing_ticket_review = f"❌ No Jira ticket found in the branch name. Please add in a Jira ticket by renaming the branch to match the pattern: `{branch_type}/<JIRA_ticket>-{branch_description}`"
                missing_ticket = missing_ticket_header + missing_ticket_review
                self.reviews.append(missing_ticket)

            # Review confluence
            if full_commit_purpose:
                if self.confluence_content:
                    await self.review_overall_confluence(full_commit_purpose)
                else:
                    await self.suggest_overall_confluence(full_commit_purpose)

            final_reviews = super().join_reviews(self.reviews)
            super().log_review_metrics("Finished generating logic review", start_time)
            return final_reviews
            
        except Exception as e:
            error_message = f"Error occurred while generating logic review: {e}. Skipping all logic reviews."
            self.log_errors(error_message, "review_logic")
            return []
    
    async def generate_purpose(self, commit: Commit, diff_dict: Dict[str, str]) -> Tuple[str, str]:
        tasks = []
        for file, diff in diff_dict.items():
            task = self.generate_file_modification_purpose(diff, file, commit)
            tasks.append(task)
        
        # Generate a list of purpose of FILE modifications
        results = await asyncio.gather(*tasks)
        full_modification_purpose = "\n".join([r for r in results if r])

        commit_purpose = ""
        if full_modification_purpose:
            # Generate purpose of commits
            commit_purpose = await self.generate_commit_purpose(full_modification_purpose, commit)
        
        return (full_modification_purpose, commit_purpose)
            
    async def generate_commit_purpose(self, full_modification_purpose: str, commit: Commit) -> str:
        try:
            prompt = f"""
            The following is a summary of modifications made to files in a git commit, including the purpose of each change:
            {full_modification_purpose}

            The related git commit message is:
            "{commit.message}"

            Based on this information, provide a brief, clear, and focused explanation of the primary goal of the commit.
            """

            prompt = await super().enhance_prompt_with_config(prompt)
            prompt_message = (
                "You are an AI assistant tasked with identifying the purpose of a git commit. "
                "Review the modification summary and commit message, then succinctly describe the commit's main objective."
            )

            purpose = await super().process_prompt(prompt, prompt_message)
            return f"{commit.message}: {purpose}"
        except Exception as e:
            error_message = f"Error occurred while generating commit purpose: {e}. Skipping the processing of commit {commit.message}."
            self.log_errors(error_message, "generate_commit_purpose")
            return ""

    async def review_commit_messages(self, issue_key: str, full_modification_purpose: str, commit: Commit) -> str:
        try:
            jira_processor = JIRAProcessor()
            try:
                issue_summary, issue_description = jira_processor.get_issue(issue_key)
            except HTTPError:
                review = f"❌ Jira ticket {issue_key} not found in Jira. Please ensure that {issue_key} is a valid Jira ticket."
                return f"### Evaluation of commit '{commit.message}' against {issue_key}\n {review}"

            prompt = f"""
            Analyze the following summary of modifications made to files in a git commit:
            {full_modification_purpose}

            The commit message is:
            {commit.message}

            This commit is linked to a JIRA issue:
            Issue Summary: {issue_summary}
            Issue Description: {issue_description}

            Your task:
            - Evaluate whether the modifications effectively address the linked JIRA issue and reflects the commit message. 
            - Consider the relevance and completeness of the changes in relation to the issue's goals and description.
            - Provide a brief, point-by-point evaluation:
                - For each point, indicate positive aspects with a ✅ on the left.
                - For any negative aspects or suggestions, mark with a ❌ on the left.
            - Ensure no line spacing for each point.
            - Provide the feedback in plain text without markdown, code blocks, or backticks.
            """

            prompt = await super().enhance_prompt_with_config(prompt)
            prompt_message = (
                "You are an AI assistant specialized in assessing whether code modifications address linked JIRA issues. "
                "Review the provided modification summary and JIRA details, then determine if the changes fulfill the issue's requirements."
            )

            review = await super().process_prompt(prompt, prompt_message)
            if review:
                return f"### Evaluation of commit '{commit.message}' against {issue_key}\n {review}"
            else:
                return ""
        except Exception as e:
            error_message = f"Error occurred while reviewing commit messages: {e}. Skipping review of commit `{commit.message}`"
            self.log_errors(error_message, "review_commit_messages")
            return ""
            
    async def generate_file_modification_purpose(self, diff: str, file: str, commit: Commit) -> str:
        try:
            removed_files_in_commit = self.removed_files.get(commit, [])
            if file in removed_files_in_commit:
                return f"{file}: File has been removed in the git commit."
            
            generated_prompt = await self.generate_prompt_for_file_modification(file, commit, diff)
            prompt_message = "You are an AI assistant specialized in summarizing code changes based on diffs. " \
            "Review the provided diff and file purpose, then generate a clear, concise summary of the modifications and their relation to the file's purpose." \
            "Keep the summary as short as possible."

            file_modification_purpose = await super().process_prompt(generated_prompt, prompt_message)
            if file_modification_purpose:
                added_files_in_commit = self.added_files.get(commit, [])
                if file in added_files_in_commit:
                    file_modification_purpose = f"{file}: File has been added in git commit. " + file_modification_purpose
                    return file_modification_purpose
                else:
                    return f"{file}: {file_modification_purpose}"
            else:
                return ""
        except Exception as e:
            error_message = f"Error occurred while generating file modification purpose for {file}: {e}. Skipping {file} in logic review."
            self.log_errors(error_message, "generate_file_modification_purpose")
            return ""
    
    async def generate_prompt_for_file_modification(self, file: str, commit: Commit, diff: str) -> str:
        original_prompt = f"""
        The file '{file}' has been modified in a git commit. The commit has message {commit.message}

        Diff of the file in this commit:
        {diff}

        Task:
        - Determine the underlying purpose or intent behind modifying this file in the commit.
        - Infer why the changes were made and how they support the commit.

        Guidelines:
        - Keep your summary brief and to the point.
        - Highlight the key modifications and the purpose of the file's modification in this commit.
        - Ensure the summary reflects the overall nature of the changes and their significance.

        Your analysis should help understand the essence of what was altered and why.
        """

        original_prompt = await super().enhance_prompt_with_config(original_prompt)

        if not self.indexing:
            return original_prompt
        
        try:
            code_context = super().get_context(file)
            prompt = f"""
            The prompt below is used to determine the purpose of a file modification in a git commit:
            {original_prompt}

            Information:
            Context surrounding modified_file: {code_context}

            Task:
            - Enhance the original prompt with information above to be given as context to correctly determine file modification purpose.
            - Provide as much information in the enhanced prompt deemed suitable to properly determine the purpose of file modification. 

            Output:
            - Do not provide the file modification purpose here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            message = "You are an expert in crafting clear, context-rich prompts to effectively determine file modification purpose. " \
            "Your task here is to generate a polished, paste-ready reviewer-prompt text. Do not generate purpose here in this step."
            generated_prompt = await super().process_prompt(prompt, message)
            return generated_prompt
        except Exception as e:
            error_message = f"Error occurred while generating prompt to identify file modification purpose: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_prompt_for_file_modification")
            return original_prompt
    
    async def review_overall_issue(self, full_commit_purpose: str, issue_summary: str, issue_description: str) -> None:
        try:
            individual_reviews = ""
            if len(self.reviews) > 1:
                individual_reviews = ("\n").join(self.reviews[1:])

            prompt = f"""
            The pull request includes the following commit messages and their purposes:
            {full_commit_purpose}

            Issue Summary: {issue_summary}
            Issue Description: {issue_description}

            These are comments for some of the commits on whether it addresses the issue:
            {individual_reviews}

            Task:
            - Review all commits in chronological order and evaluate the patch set as a single, coherent change.
            - Determine if the combined changes effectively resolve the issue.
            - Provide a brief, point-by-point evaluation (no line spacing between points):
            - Start each line with a checkbox symbol: ✅ for positive, ❌ for negative or suggestions.
            - Keep each line concise; ideally one sentence per point.
            - Output must be plain text with no markdown, code blocks, or backticks.
            """

            prompt = await super().enhance_prompt_with_config(prompt)
            prompt_message = (
                "You are an AI assistant specialized in assessing whether pull requests appropriately address linked issues. "
                "Review the entire set of commits in chronological order as a whole, then provide a concise evaluation with clear indicators."
            )
            review = await super().process_prompt(prompt, prompt_message)

            issue_header = f"### Overall evaluation of PR against {self.branch_ticket}\n"
            issue_review = issue_header + review
            self.reviews.append(issue_review)
        except Exception as e:
            error_message = f"Error occurred while reviewing PR against Jira ticket: {e}. Skipping Jira review."
            self.log_errors(error_message, "review_overall_issue")
            return
    
    async def review_overall_confluence(self, full_commit_purpose: str) -> None:
        try:
            prompt = f"""
            You are an AI assistant specialized in assessing whether a pull request (PR) and its commits address content in a linked Confluence page. Use the full commit messages and the Confluence content below.

            Context to use:
            The pull request includes these commits and purposes:
            {full_commit_purpose}

            The pull request is linked to this Confluence page content:
            {self.confluence_content}

            Task (high-level):
            1. Review all commits in chronological order and evaluate the patch set as a single, coherent change (treat the PR as one combined change).
            2. Determine whether the combined changes address any specific parts, sections, or requests described in the Confluence page.
            3. If a Confluence item is only partially or not addressed, provide a concise suggestion for what is missing or what to add to fully address it.

            Output requirements (strict):
            - Provide a brief, point-by-point evaluation where each line is a single concise sentence with no blank lines between points.
            - Start each line with a checkbox symbol: ✅ for positive (fully or acceptably addressed), ❌ for negative or suggestions (partially/not addressed or needs change).
            - Lines should be plain text only (no Markdown formatting, no code blocks, no backticks), and each line should ideally be one sentence.
            """

            prompt = await super().enhance_prompt_with_config(prompt)
            prompt_message = (
                "You are an AI assistant specialized in assessing whether pull requests appropriately address content in a linked Confluence page. "
                "Review the entire set of commits in chronological order as a whole, then provide a concise evaluation with clear indicators."
            )
            review = await super().process_prompt(prompt, prompt_message)

            confluence_header = "### Overall evaluation of PR against Confluence content\n"
            embedded_links = ""
            for idx, link in enumerate(self.confluence_links, start=1):
                link_content = f"[Confluence Page #{idx}]({link})"
                if embedded_links:
                    embedded_links += f", {link_content}"
                else:
                    embedded_links = link_content
            confluence_description = f"Confluence content found from: {embedded_links}. \n\n"
            confluence_review = confluence_header + confluence_description + review
            self.reviews.append(confluence_review)
        except Exception as e:
            error_message = f"Error occurred while reviewing PR against Confluence: {e}. Skipping Confluence review."
            self.log_errors(error_message, "review_overall_confluence")
            return
    
    async def suggest_overall_confluence(self, full_commit_purpose: str) -> None:
        try:
            prompt = f"""
            A pull request (PR) has the following commits and description; there is currently NO linked Confluence page and one must be created:

            Context:
            Commits: {full_commit_purpose}
            PR description: {self.pr_description}

            Goal:
            Based on the commits and PR description, produce a set of concise questions that a reviewer or author should answer in a new Confluence page to fully document the PR.

            Task details:
            - Review commits in chronological order and the PR description as a single cohesive change.
            - Identify any vague or underspecified commits or description points that need clarification in Confluence.
            - For each unclear area, generate a specific, actionable question that, when answered, will make the Confluence page complete and useful for reviewers, maintainers, and future readers.

            Output format (strict):
            - Respond with ONLY plain-text bullet points (one question per bullet), each line beginning with a single dash and a space ("- ").
            - Each bullet must be a single, direct question ending with a question mark.
            - Do NOT include headings, explanatory text, numbered lists, markdown, code blocks, or any extra commentary — only the bullet points.
            - Keep each question concise and specific.
            """

            prompt = await super().enhance_prompt_with_config(prompt)
            prompt_message = (
                "You are an AI assistant specialized in producing reviewer-facing questions for a Confluence page that documents a pull request. "
                "Analyze commits (chronological) and the PR description, identify vagueness or missing details, and produce only plain-text bullet-question lines as specified."
            )
            review = await super().process_prompt(prompt, prompt_message)
            
            confluence_header = "### Overall evaluation of PR against Confluence content\n"
            confluence_description = "❌ No Confluence page linked to the PR. Consider adding a Confluence page that includes these details: \n\n"
            confluence_review = confluence_header + confluence_description + review
            self.reviews.append(confluence_review)
        except Exception as e:
            error_message = f"Error occurred while generating questions for Confluence page: {e}. Skipping Confluence review."
            self.log_errors(error_message, "suggest_overall_confluence")
            return

    @override
    def log_errors(self, error_message: str, function: str) -> None:
        self.console_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/logic_reviewer.py",
            function=function
        )
        self.file_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/logic_reviewer.py",
            function=function
        )
