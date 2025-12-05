import os
import requests
import aiofiles
import aiohttp
from aiohttp.client import ClientSession
from requests.exceptions import HTTPError
import base64
import json
from dotenv import load_dotenv
from code_context_provider import CodeContextProvider
from line import Line
import psycopg2
from commit import Commit
import asyncio
import re
from typing_extensions import Dict, Tuple, List, override
from function import Function
from logger_config import console_logger, file_logger
import uuid

class PullRequestProcessor:
    def __init__(self, project: str, repo: str, pr_id: int) -> None:
        load_dotenv()
        self.project = project
        self.repo = repo
        self.access_token = os.environ["BITBUCKET_ACCESS_TOKEN"]
        self.username = os.environ["BITBUCKET_USERNAME"]
        self.bitbucket_link = os.environ["BITBUCKET_LINK"]
        self.deployment_endpoint = os.environ["DEPLOYMENT_ENDPOINT"]
        self.forms_url = os.environ["FEEDBACK_FORM"]
        self.encoded_token = self._encode_token()
        self.modified_func_dict = {}
        self.pr_id = pr_id
        self.console_logger = console_logger
        self.file_logger = file_logger
        self.test_files = []

    async def post_reviews(self, review_content: str, feedback: bool = True) -> None:
        try:
            if feedback:
                updated_review_content = self.update_comment_with_feedback_url(review_content)
            else:
                updated_review_content = review_content
                
            url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}/comments"
            headers = {
                "Accept": "application/json;charset=UTF-8",
                "Content-Type": "application/json; charset=UTF-8",
                "Authorization": f"Basic {self.encoded_token}",
            }

            payload = json.dumps({
                "text": updated_review_content
            }, ensure_ascii=False)
            response = requests.request("POST", url, data=payload, headers=headers)
            response.raise_for_status()
        except HTTPError as e:
            error_message = f'Error occurred while posting comments: {e.response.text}'
            self.log_errors(error_message, "post_reviews")
        except Exception as e:
            error_message = f'Error occurred while posting comments: {e}'
            self.log_errors(error_message, "post_reviews")
    
    def update_comment_with_feedback_url(self, review_content: str) -> str:
        comment_id = self.save_comment(review_content)
        # Generate feedback URL with embedded info
        feedback_url = (
            f"http://{self.deployment_endpoint}/feedback?"
            f"project={self.project}&repo={self.repo}&pr_id={self.pr_id}&comment_id={comment_id}"
        )
        
        review_content += "\n" + "#### Rate this comment: \n"
        review_content += f"[⭐]({feedback_url+"&score=1"})\n"
        review_content += f"[⭐⭐]({feedback_url+"&score=2"})\n"
        review_content += f"[⭐⭐⭐]({feedback_url+"&score=3"})\n"
        review_content += "#### Provide detailed feedback: \n"
        review_content += f"Copy and paste this information into the first question: **{self.project}, {self.repo}, {self.pr_id}, {comment_id}** \n"
        review_content += f"Submit your feedback to this [form]({self.feedback_form}). Make sure to copy the information given above.\n"
        review_content += "\n" + ("-" * 40) + "\n"
        return review_content
    
    def save_comment(self, review_content: str) -> str:
        load_dotenv()
        conn = psycopg2.connect(
            dbname=os.environ.get('POSTGRES_DB'),
            user=os.environ.get('POSTGRES_USER'),
            password=os.environ.get('POSTGRES_PASSWORD'),
            host='sentinel_db',
            port='5432'
        )
        cursor = conn.cursor()

        # Create the comments table if it doesn't exist
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
                comment_id TEXT,
                project TEXT,
                repo TEXT,
                pr_id TEXT,
                content TEXT
            )
            """
        )

        comment_id = str(uuid.uuid4())
        exists = True
        while exists:
            # Ensure comment_id is unique
            cursor.execute(
                """
                SELECT 1 FROM comments
                WHERE comment_id = %s
                """,
                (comment_id,)
            )
            exists = cursor.fetchone()
            if exists:
                comment_id = str(uuid.uuid4())

        # Insert row
        cursor.execute(
            """
            INSERT INTO comments (comment_id, project, repo, pr_id, content)
            VALUES (%s, %s, %s, %s, %s)
            """, 
            (comment_id, self.project, self.repo, self.pr_id, review_content)
        )

        conn.commit()
        conn.close()
        return comment_id

    async def get_modified_functions(self, diff_dict: Dict[str, Tuple[List[int], List[int]]], test_folder: str) -> Dict[str, List[Function]]:
        try:
            if test_folder == 'subfolder':
                download_tasks = [
                    task
                    for path in diff_dict.keys()
                    for task in [self.download_file_content(path), self.download_test_files_in_subfolder(path)]
                ]
                await asyncio.gather(*download_tasks)
            else:
                download_tasks = [
                    task
                    for path in diff_dict.keys()
                    for task in [self.download_file_content(path), self.download_test_files_in_test_folder(path, test_folder)]
                ]
                await asyncio.gather(*download_tasks)

            diff_tasks = []
            for path, (added_lines, removed_lines) in diff_dict.items():
                local_folder = f'code_for_review_{self.repo}_{self.pr_id}'
                temp_path = os.path.join(local_folder, path)
                functions_in_file = await self.get_functions_in_file(temp_path)

                diff_task = self.analyse_diff(added_lines, removed_lines, functions_in_file, path)
                diff_tasks.append(diff_task)
            
            await asyncio.gather(*diff_tasks)
            return self.modified_func_dict
        except Exception as e:
            error_message = f"Error occurred while extracting modified functions: {e}"
            self.log_errors(error_message, "get_modified_functions")
            raise

    def get_diff(self) -> Dict[str, Tuple[List[int], List[int]]]:
        try:
            change_url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}/changes"
            headers = {
            "Accept": "application/json;charset=UTF-8",
            "Authorization": f"Basic {self.encoded_token}",
            }

            params = {
                "changeScope": "UNREVIEWED"
            }
            changes = requests.request("GET", change_url, headers=headers, params=params)
            changes_json = json.loads(changes.text)
            changed_files = []
            for change in changes_json.get('values', []):
                if change.get('type') != 'DELETE':
                    path = change.get('path', {}).get('toString')
                    if path is not None:
                        changed_files.append(path)

            diff_dict = {}
            # Stream diff in each file
            for path in changed_files:
                diff_url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}/diff/{path}"
                diff_response = requests.request("GET", diff_url, headers=headers)
                diff_json = json.loads(diff_response.text)

                added_lines = set()
                removed_lines = set()
                for diff_info in diff_json.get('diffs', []):
                    for hunk_info in diff_info.get('hunks', []):
                        for segment_info in hunk_info.get('segments', []):
                            for line_info in segment_info.get('lines', []):
                                if segment_info.get('type', '') == 'ADDED':
                                    added_line = Line(line_info.get('destination', 0), line_info.get('line', ''))
                                    added_lines.add(added_line)
                                elif segment_info.get('type', '') == 'REMOVED':
                                    removed_line = Line(line_info.get('destination', 0), line_info.get('line', ''))
                                    removed_lines.add(removed_line)

                diff_dict[path] = (list(added_lines), list(removed_lines))
            
            return diff_dict
        except HTTPError as e:
            error_message = f"Error occurred while extracting diff: {e.response.text}"
            self.log_errors(error_message, "get_diff")
            raise
        except Exception as e:
            error_message = f"Error occurred while extracting diff: {e}"
            self.log_errors(error_message, "get_diff")
            raise
    
    def get_deleted_files(self) -> List[str]:
        try:
            change_url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}/changes"
            headers = {
            "Accept": "application/json;charset=UTF-8",
            "Authorization": f"Basic {self.encoded_token}",
            }

            params = {
                "changeScope": "UNREVIEWED"
            }
            changes = requests.request("GET", change_url, headers=headers, params=params)
            changes_json = json.loads(changes.text)
            deleted_files = [
                change['path']['toString']
                for change in changes_json['values']
                if change['type'] == 'DELETE'
            ]
            return deleted_files
        except Exception:
            return []
    
    def update_pr_status(self) -> None:
        try:
            latest_commit = self.get_latest_commit()
            url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}/participants/{self.username}"

            headers = {
                "Accept": "application/json;charset=UTF-8",
                "Content-Type": "application/json",
                "Authorization": f"Basic {self.encoded_token}"
            }

            payload = json.dumps({
                "lastReviewedCommit": latest_commit,
                "status": "NEEDS_WORK"
            })

            response = requests.request("PUT", url, data=payload, headers=headers)
            response.raise_for_status()
        except HTTPError as e:
            pr_id = self.pr_id
            repo = self.repo
            project = self.project
            self.console_logger.warning(
                f"Pull request status not correctly updated: {e.response.text}. " \
                "This may affect the review process. Code review bot will retrieve all changes, instead of the latest changes when pull request is updated.",
                pull_request=(project, repo, pr_id),
                file="src/pull_request_processor.py",
                function="update_pr_status"
            )
            self.file_logger.warning(
                f"Pull request status not correctly updated: {e.response.text}. " \
                "This may affect the review process. Code review bot will retrieve all changes, instead of the latest changes when pull request is updated.",
                pull_request=(project, repo, pr_id),
                file="src/pull_request_processor.py",
                function="update_pr_status"
            )
    
    def get_pr_source_branch(self) -> str:
        try:
            url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}"

            headers = {
                "Accept": "application/json;charset=UTF-8",
                "Authorization": f"Basic {self.encoded_token}",
            }

            response = requests.request("GET", url, headers=headers)
            response_json = json.loads(response.text)
            source_branch = response_json['fromRef']['displayId']
            return source_branch
        except HTTPError as e:
            error_message = f"Error occurred while getting source branch of pull request: {e.response.text}"
            self.log_errors(error_message, "get_pr_source_branch")
            raise 
        except Exception as e:
            error_message = f"Error occurred while getting source branch of pull request: {e}"
            self.log_errors(error_message, "get_pr_source_branch")
            raise 

    def get_latest_commit(self) -> str:
        url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}"

        headers = {
            "Accept": "application/json;charset=UTF-8",
            "Authorization": f"Basic {self.encoded_token}",
        }

        response = requests.request("GET", url, headers=headers)
        response_json = json.loads(response.text)
        latest_commit = response_json['fromRef']['latestCommit']
        return latest_commit
    
    async def fetch(self, session: ClientSession, url: str, headers: Dict, params: Dict) -> str:
        async with session.get(url, headers=headers, params=params) as response:
            response_text = await response.text()
            return response_text
    
    async def download_file_content(self, path: str) -> None:
        norm_path = str(os.path.normpath(path)).replace('\\', '/')
        try:
            branch = self.get_pr_source_branch()

            url = f"http://{self.bitbucket_link}rest/api/latest/projects/{self.project}/repos/{self.repo}/browse/{norm_path}"
            headers = {
                "Accept": "application/json;charset=UTF-8",
                "Authorization": f"Basic {self.encoded_token}",
            }

            async with aiohttp.ClientSession() as session:
                start = 0
                limit = 500
                lines = []

                while True:
                    params = {
                        "start": start,
                        "at": branch,
                        "limit": limit,
                    }
                        
                    response_text = await self.fetch(session, url, headers, params)
                    if response_text:
                        response_json = json.loads(response_text)

                    lines.extend(response_json.get('lines', []))
                    if response_json.get('isLastPage', True):
                        break
                    else:
                        start = response_json.get('nextPageStart', 0)
                        if start >= limit:
                            limit += 500

                local_folder = f'code_for_review_{self.repo}_{self.pr_id}'
                temp_path = os.path.join(local_folder, path) 
                os.makedirs(os.path.dirname(temp_path), exist_ok=True)

                async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                    await f.write('\n'.join(line['text'] for line in lines))
        except HTTPError as e:
            error_message = f"Error occurred while downloading file {norm_path}: {e.response.text}"
            self.log_errors(error_message, "download_file_content")
            raise
        except Exception as e:
            error_message = f"Error occurred while downloading file {norm_path}: {e}"
            self.log_errors(error_message, "download_file_content")
            raise
    
    async def get_functions_in_file(self, temp_path: str) -> List[Function]:
        try:
            context_provider = CodeContextProvider(temp_path)
            context = await context_provider.build_context()
            if temp_path in context:
                return context[temp_path]
            else:
                return []
        except Exception as e:
            file = os.path.basename(temp_path)
            error_message = f"Error occurred while extracting functions in {file}: {e}"
            self.log_errors(error_message, "get_functions_in_file")
            raise
    
    async def analyse_diff(self, added_lines: List[Line], removed_lines: List[Line], 
                           functions_in_file: List[Function], path: str) -> None:
        try:
            added_line_num = [line.line_num for line in added_lines]
            removed_line_num = [line.line_num for line in removed_lines]
            modified_lines = list(set(added_line_num + removed_line_num))
            
            modified_functions = set()
            for line in modified_lines:
                for function in functions_in_file:
                    if function.start_line <= line <= function.end_line:
                        if line in added_line_num:
                            for added_line in added_lines:
                                if added_line.line_num == line:
                                    function.addInformation(added_line=added_line)
                        elif line in removed_line_num:
                            for removed_line in removed_lines:
                                if removed_line.line_num == line:
                                    function.addInformation(removed_line=removed_line)
                        modified_functions.add(function)
                        break
            
            self.modified_func_dict[path] = modified_functions
        except Exception as e:
            error_message = f"Error occurred while analysing diff: {e}"
            self.log_errors(error_message, "analyse_diff")
            raise
    
    async def download_test_files_in_subfolder(self, path: str) -> None:
        try:
            branch = self.get_pr_source_branch()
            test_files = self.get_test_files_in_subfolder(path, branch)
            self.test_files.extend(test_files)
            await self.download_test_files(test_files)
        except Exception as e:
            error_message = f"Error occurred while downloading test files for {path}: {e}"
            self.log_errors(error_message, "download_test_files_in_subfolder")
            raise
        
    async def download_test_files_in_test_folder(self, path: str, test_folder: str) -> None:
        try:
            branch = self.get_pr_source_branch()
            test_files = self.get_test_files_in_test_folder(path, test_folder, branch)
            self.test_files.extend(test_files)
            await self.download_test_files(test_files)
        except Exception as e:
            error_message = f"Error occurred while downloading test file for {path} in test folder {test_folder}: {e}"
            self.log_errors(error_message, "download_test_files_in_test_folder")
            raise

    async def download_test_files(self, test_files: List[str]) -> None:
        try:
            download_tasks = []
            for file in test_files:
                task = self.download_file_content(file)
                download_tasks.append(task)
            await asyncio.gather(*download_tasks)
        except Exception as e:
            error_message = f"Error occurred while downloading test files: {e}"
            self.log_errors(error_message, "download_test_files")
            raise 
    
    def get_test_files_in_test_folder(self, path: str, test_folder: str, branch: str) -> List[str]:
        try:
            file_name = os.path.basename(path)
            test_files = []
            url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/files/{test_folder}"
            headers = {
                "Accept": "application/json;charset=UTF-8",
                "Authorization": f"Basic {self.encoded_token}"
            }
            start = 0

            while True:
                params = {
                    "start": start,
                    "at": branch
                }

                response = requests.request("GET", url, headers=headers, params=params)
                response.raise_for_status()
                response_json = json.loads(response.text)

                # Filter out relevant test file
                test_files.extend([
                    os.path.join(test_folder, file) for file in response_json['values']
                    if f"test_{file_name}" in file
                ])

                if response_json['isLastPage']:
                    break
                else:
                    start = response_json.get('nextPageStart', 0)

            return test_files
        except HTTPError as e:
            error_message= f"Error occurred while fetching test files from folder {test_folder}: {e.response.text}"
            self.log_errors(error_message, "get_test_files_in_test_folder")
            raise
        except Exception as e:
            error_message= f"Error occurred while fetching test files from folder {test_folder}: {e}"
            self.log_errors(error_message, "get_test_files_in_test_folder")
            raise
    
    def get_test_files_in_subfolder(self, path: str, branch: str) -> List[str]:
        file_name = os.path.basename(path)
        folder_path = os.path.dirname(path)
        try:
            test_files = []
            url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/files/{folder_path}"
            headers = {
                "Accept": "application/json;charset=UTF-8",
                "Authorization": f"Basic {self.encoded_token}"
            }
            start = 0

            while True:
                params = {
                "start": start,
                "at": branch
                }

                response = requests.request("GET", url, headers=headers, params=params)
                response.raise_for_status()
                response_json = json.loads(response.text)

                # Filter out the relvant test file
                test_files.extend([
                    os.path.join(folder_path, file) for file in response_json['values']
                    if f"test_{file_name}" in file
                ])

                if response_json['isLastPage']:
                    break
                else:
                    start = response_json.get('nextPageStart', 0)

            return test_files
        except HTTPError as e:
            error_message = f"Error occurred while fetching test files in {folder_path}: {e.response.text}"
            self.log_errors(error_message, "get_test_files_in_subfolder")
            raise
        except Exception as e:
            error_message = f"Error occurred while fetching test files in {folder_path}: {e}"
            self.log_errors(error_message, "get_test_files_in_subfolder")
            raise
        
    def get_files(self, dir_name: str) -> List[str]:
        branch = self.get_pr_source_branch()
        url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/files/{dir_name}"

        headers = {
            "Accept": "application/json;charset=UTF-8",
            "Authorization": f"Basic {self.encoded_token}"
        }
        start = 0
        files = []

        while True:
            params = {
                "start": start,
                "at": branch
            }

            response = requests.request("GET", url, headers=headers, params=params)
            response_json = json.loads(response.text)
            response_files = response_json.get('values', [])
            files.extend(response_files)

            if response_json.get('isLastPage', True):
                break
            else:
                start = response_json.get('nextPageStart', 0)
        
        return files
    
    async def download_all_files(self) -> None:
        try:
            files = self.get_all_files()
            index = 0
            chunk_size = 50
            while index < len(files):
                chunk = files[index:index + chunk_size]
                tasks = [self.download_file_content(f) for f in chunk]
                await asyncio.gather(*tasks)
                index += chunk_size
        except Exception as e:
            error_message = f"Error occurred while downloading all files: {e}"
            self.log_errors(error_message, "download_all_files")
            raise
    
    def get_all_files(self) -> List[str]:
        try:
            branch = self.get_pr_source_branch()
            url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/files"

            files = []
            headers = {
                "Accept": "application/json;charset=UTF-8",
                "Authorization": f"Basic {self.encoded_token}"
            }
            start = 0

            while True:
                params = {
                    "start": start,
                    "at": branch
                }

                response = requests.request("GET", url, headers=headers, params=params)
                response_json = json.loads(response.text)
                response_files = response_json.get('values', [])
                files.extend(response_files)

                if response_json.get('isLastPage', True):
                    break
                else:
                    start = response_json.get('nextPageStart', 0)
            
            return files
        except HTTPError as e:
            error_message = f"An HTTPError occurred while fetching all files: {e.response.text}"
            self.log_errors(error_message, "get_all_files")
            raise
    
    def get_pr_commits(self) -> List[Commit]:
        try:
            url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}/commits"

            headers = {
                "Accept": "application/json;charset=UTF-8",
                "Authorization": f"Basic {self.encoded_token}"
            }

            response = requests.request("GET", url, headers=headers)
            response_json = json.loads(response.text)
            commits = response_json.get('values', [])
            commit_list = []
            for commit_info in commits:
                commit_id = commit_info.get('id', "")
                commit_message = commit_info.get('message', "")
                commit = Commit(commit_id, commit_message)
                commit_list.append(commit)
            return commit_list
        except HTTPError as e:
            error_message = f"Error occurred while fetching commits in pull request: {e.response.text}"
            self.log_errors(error_message, "get_pr_commits")
            raise
    
    async def get_diff_in_commit(self, commit: Commit) -> Tuple[Dict[str, str], List[str], List[str]]:
        try:
            # Get modified, removed and added files
            async with aiohttp.ClientSession() as session:
                change_url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/changes"
                change_headers = {
                    "Accept": "application/json;charset=UTF-8",
                    "Authorization": f"Basic {self.encoded_token}"
                }

                start = 0
                limit = 500
                modified_files = []
                removed_files = []
                added_files = []

                while True:
                    change_params = {
                        "start": start,
                        "limit": limit, 
                        "until": commit.id
                    }

                    changes_text = await self.fetch(session, change_url, change_headers, change_params)
                    changes_json = json.loads(changes_text)

                    modified = [
                        change['path']['toString']
                        for change in changes_json['values']
                        if change['type'] == 'MODIFY'
                    ]
                    modified_files.extend(modified)
                    removed = [
                        change['path']['toString']
                        for change in changes_json['values']
                        if change['type'] == 'DELETE'
                    ]
                    removed_files.extend(removed)
                    added = [
                        change['path']['toString']
                        for change in changes_json['values']
                        if change['type'] == 'ADD'
                    ]
                    added_files.extend(added)

                    if changes_json.get('isLastPage', True):
                        break
                    else:
                        start = changes_json.get('nextPageStart', 0)
                        if start >= limit:
                            limit += 500

            # Get diff within modified, added, removed files
            async with aiohttp.ClientSession() as session:
                diff_dict = {}
                changed_files = modified_files + added_files + removed_files
                diff_headers = {
                    "Accept": "text/plain",
                    "Authorization": f"Basic {self.encoded_token}"
                }

                # Stream raw diff in each file
                for path in changed_files:
                    diff_url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/diff/{path}"
                    diff_params = {
                        "until": commit.id
                    }

                    raw_diff = await self.fetch(session, diff_url, diff_headers, diff_params)
                    diff_dict[path] = raw_diff # provide to LLM
            
            return diff_dict, removed_files, added_files
        except HTTPError as e:
            error_message = f"Error occurred while fetching diff from commit: {e.response.text}"
            self.log_errors(error_message, "get_diff_in_commit")
            raise
    
    def get_issue_key(self) -> Tuple[List[str], str]:
        try:
            url = f"http://{self.bitbucket_link}/rest/jira/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}/issues"

            headers = {
                "Accept": "application/json;charset=UTF-8", 
                "Authorization": f"Basic {self.encoded_token}"
            }

            response = requests.request("GET", url,headers=headers)
            response_json = json.loads(response.text)
            keys_list = [item["key"] for item in response_json]

            branch = self.get_pr_source_branch()
            ticket_part = branch.split('/')[-1]
            matches = re.findall(r'([A-Z]+-\d+)', ticket_part)
            branch_ticket = matches[0] if matches else ""

            return (keys_list, branch_ticket)
        except Exception:
            return ([], "")
    
    def get_pr_description(self) -> str:
        try:
            url = f"http://{self.bitbucket_link}/rest/api/latest/projects/{self.project}/repos/{self.repo}/pull-requests/{self.pr_id}"

            headers = {
                "Accept": "application/json;charset=UTF-8", 
                "Authorization": f"Basic {self.encoded_token}"
            }

            response = requests.request("GET", url,headers=headers)
            response_json = json.loads(response.text)
            description = response_json.get("description", "")
            return description
        except Exception:
            return ""
    
    async def process_config_file(self) -> Tuple[str, str, bool, bool, str]:
        try:
            config_file = 'sentinel-config.yaml'
            await self.download_file_content(config_file)

            local_folder = f'code_for_review_{self.repo}_{self.pr_id}'
            local_config_file = os.path.join(local_folder, config_file)
            provider = CodeContextProvider(local_config_file)
            yml_dict = provider.parse_yaml_file()
            language = yml_dict.get('language', '')
            test_folder = yml_dict.get('test_folder', '')
            indexing = yml_dict.get('indexing', False)
            deadcode = yml_dict.get('deadcode', False)
            doc_folder = yml_dict.get('doc_folder', '')

            # Handle invalid values
            if not isinstance(indexing, bool): 
                indexing = False
            if not isinstance(deadcode, bool): 
                deadcode = False
            return (language, test_folder, indexing, deadcode, doc_folder)
        except Exception as e:
            error_message = f"Error occurred while processing config file: {e}"
            self.log_errors(error_message, "process_config_file")
            raise

    @override 
    def log_errors(self, error_message: str, function: str) -> None:
        self.console_logger.exception(
            error_message,
            pull_request=(self.project, self.repo, self.pr_id),
            file="src/pull_request_processor.py",
            function=function
        )
        self.file_logger.exception(
            error_message,
            pull_request=(self.project, self.repo, self.pr_id),
            file="src/pull_request_processor.py",
            function=function
        )

    def _encode_token(self) -> str:
        full_token = f"{self.username}:{self.access_token}".encode('utf-8')
        encoded_full_token = base64.b64encode(full_token)
        return encoded_full_token.decode('utf-8')