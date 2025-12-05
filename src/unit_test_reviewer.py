import os
import asyncio
from typing_extensions import List, Dict, Tuple, override
from reviewer import Reviewer
from function import Function
from code_context_provider import CodeContextProvider
from pull_request_processor import PullRequestProcessor
import time
import re

class UnitTestReviewer(Reviewer):
    def __init__(
        self, modified_func: Dict[str, List[Function]], processor: PullRequestProcessor, 
        agent_files: List[str], indexing: bool = False
    ) -> None:
        super().__init__(processor, agent_files)
        self.modified_func = modified_func
        self.indexing = indexing
        self.existing_test_review = {}
        self.missing_test_review = {}

    async def review_test(self) -> Tuple[Dict[Function, str], Dict[str, str]]:
        try:
            start_time = time.time()
            super().log_review_metrics('Generating unit test review...')
            all_test_files = self.processor.test_files
            modified_and_tested = {}

            ##### Find test files that test modified functions and evaluate them
            for modified_file, modified_func_list in self.modified_func.items():
                # Skip unit test review for test and python base files
                file_name = os.path.basename(modified_file)
                if file_name.startswith('test_') or re.match(r'^__\w+__\.py$', file_name):
                    continue
       
                test_name = f'test_{os.path.basename(modified_file)}'
                relevant_test_file = [file for file in all_test_files if test_name in file]
                modified_and_tested = await self.review_tested_functions(relevant_test_file, modified_func_list, modified_file, modified_and_tested)

            #### Generate missing unit test review
            self.generate_review_for_untested(modified_and_tested, all_test_files)
        except Exception as e:
            error_message = f"Error occurred while generating unit test review: {e}. Skipping all unit test reviews."
            self.log_errors(error_message, "review_test")
            return ({}, {})
        else:
            super().log_review_metrics("Finished generating unit test review", start_time)
            return (self.existing_test_review, self.missing_test_review)
    
    async def review_tested_functions(self, test_file_list: List[str], modified_func_list: List[Function], modified_file: str, modified_and_tested: Dict) -> Dict:
        tasks = []
        local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
        try:
            for test_file in test_file_list:
                dir_name = os.path.dirname(modified_file)
                local_test_file = os.path.join(local_folder, test_file)
                
                test_cases_dict, fixtures = await asyncio.gather(
                    self.get_test_cases(local_test_file), 
                    self.get_fixtures(local_test_file)
                )

                for modified_func in modified_func_list:
                    test_cases_list = test_cases_dict.get(modified_func.func_name, [])
                    test_cases = []
                    if test_cases_list:
                        merged_func = test_cases_list[0]
                        test_cases.append(merged_func.func_name)
                        for tested_func in test_cases_list[1:]:
                            test_cases.append(tested_func.func_name)
                            merged_func.mergeFunctions(tested_func)

                        task = self.generate_review_for_tested(merged_func, modified_func, 
                                                        fixtures, dir_name, 
                                                        modified_file, test_cases)
                        tasks.append(task)

                        if modified_file not in modified_and_tested:
                            modified_and_tested[modified_file] = []
                        modified_and_tested[modified_file].append(modified_func)
                
            ### Generate all reviews for tested functions at once
            await asyncio.gather(*tasks)
            return modified_and_tested
        except Exception as e:
            error_message = f"Error occurred while reviewing unit test for {modified_file}: {e}. Skipping unit test review for {modified_file}."
            self.log_errors(error_message, "review_tested_functions")
            modified_and_tested[modified_file] = modified_func_list
            return modified_and_tested
    
    def generate_review_for_untested(self, modified_and_tested: Dict[str, List[Function]], all_test_files: List[str]) -> None:
        try:
            for file, modified_func_list in self.modified_func.items():
                # Skip missing test review for non python files
                if '.py' not in file:
                    continue
                
                # Skip missing test review for test and python base files
                file_name = os.path.basename(file)
                if file_name.startswith('test_') or re.match(r'^__\w+__\.py$', file_name):
                    continue

                test_name = f'test_{os.path.basename(file)}'
                relevant_test_file = [file for file in all_test_files if test_name in file]
                if relevant_test_file:
                    tested_func_list = modified_and_tested.get(file, [])
                    untested_func_list = list(set(modified_func_list) - set(tested_func_list))
                    self._review_untested_func(file, untested_func_list, relevant_test_file)
                else:
                    content = "### Missing unit test: \n" + f"No unit test file found. Add in a unit test file `{test_name}` to improve test coverage."
                    self.missing_test_review[file] = content
        except Exception as e:
            error_message = f"Error occurred while generating unit test reviews for untested functions: {e}. Skipping reviews for missing unit test file."
            self.log_errors(error_message, "generate_review_for_untested")
            return
        
    async def generate_review_for_tested(
        self, tested_func: Function, modified_func: Function, 
        fixtures: List[Function], dir_name: str, 
        modified_file: str, test_cases: List[str]
    ) -> None:
        try:
            relevant_fixtures = self.get_relevant_fixture(tested_func, fixtures)
            relevant_dep = await self.get_relevant_dep(tested_func, dir_name)
            comments_prompt = await self.generate_comments_prompt(tested_func, modified_func, modified_file, 
                                                                  relevant_fixtures, relevant_dep)
            comments = await self.generate_comments_with_generated_prompt(comments_prompt)
            score = await self.generate_score(comments)
            
            if comments and score:
                header = f"#### Review of unit test: {score}/5 \n"
                subheader = f"**{len(test_cases)} test case(s) found: ```{", ".join(test_cases)}```** \n"

                review_content = header + subheader + "\n" + comments
                self.existing_test_review[modified_func] = review_content
        except Exception as e:
            error_message = f"Error occurred while generating unit test review for {modified_func.func_name} in {modified_file}: {e}. Skipping unit test review for {modified_func.func_name}."
            self.log_errors(error_message, "generate_review_for_tested")
            return
    
    async def generate_comments_prompt(
        self, tested_func: Function, modified_func: Function, modified_file: str,
        relevant_fixtures: List[Function], relevant_dep: List[Function],
    ) -> str:
        test_code = '```python' + "\n\n" + tested_func.func_code + '```'
        original_prompt = f"""
        The unit test code below is written in Python, testing the function {modified_func.func_name} that has been recently modified in a pull request.

        Test code:
        {test_code}

        Please review the unit test code based on these criteria:

        1. Clarity and Readability: Is the test clear, concise, and well-structured? Do test names clearly describe their purpose?  
        2. Coverage: Does the test thoroughly cover key cases, including critical paths and edge cases? Are both expected and unexpected inputs tested?  
        3. Accuracy: Are the tests correct and do they accurately verify the intended behavior? Are assertions specific and meaningful?  
        4. Maintainability: Does each test focus on a single case or behavior? Are the tests simple, avoiding complex logic or dependencies?

        - Provide a brief, direct, and specific review as comments suitable for a pull request on Bitbucket.  
        - Focus solely on the test code's quality; avoid markdown, code blocks, or backticks.  
        - Use short, specific comments highlighting key issues or strengths.  
        - For positive feedback, add a tick emoji ✅ on the left; for negative feedback and suggestions, add a cross ❌ on the left.  
        - Ensure no line spacing for each point.
        """

        original_prompt = await super().enhance_prompt_with_config(original_prompt)

        if not self.indexing:
            return original_prompt
        
        try:
            modified_func_context = super().get_context(modified_file + " " + modified_func.func_name)
            test_file = f"test_{modified_file}"
            test_func_context = super().get_context(test_file + " " + tested_func.func_name)

            fixture_code = '```python'
            for fixture in relevant_fixtures:
                fixture_code += "\n\n" + fixture.func_code
            fixture_code += '```'

            dependency_code = '```python'
            for dep in relevant_dep:
                dependency_code += "\n\n" + dep.func_code
            dependency_code = '```'

            prompt = f"""
            The prompt below is used to generate review for a unit test:
            {original_prompt}

            Information:
            Function: {modified_func.func_name}
            Function code: {modified_func.func_code}
            Context surrounding function: {modified_func_context}

            Unit test code: {tested_func.func_code}
            Fixture code for unit test: {fixture_code}
            Dependency code for unit test: {dependency_code}
            Context surrounding unit test: {test_func_context}

            Task:
            - Enhance the original prompt with information above to be given as context for the review.
            - Provide as much information in the enhanced prompt deemed suitable for the review.

            Output:
            - Do not provide a review or analysis of the unit test yourself here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            message = "You are an expert in crafting clear, context-rich prompts that enable effective unit-test reviews. " \
            "Your task here is to generate a polished, paste-ready reviewer-prompt text. Do not produce any actual review content in this step."
            generated_prompt = await super().process_prompt(prompt, message)
            return generated_prompt
        except Exception as e:
            error_message = f"Error occurred while generating unit test comments prompt: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_comments_prompt")
            return original_prompt
    
    async def generate_comments_with_generated_prompt(self, prompt: str) -> str:
        try:
            prompt += "Respond with only the points for improvements without markdown, code blocks or backticks. Do not add any additional commentary." \
            "Add a tick emoji ✅ on the left for positive feedback; add a cross emoji ❌ on the left for negative feedback and suggestions. Ensure no extra spaces between each point."
            message = "You are an expert software engineer skilled in unit testing and code quality. " \
            "Your task is to review unit test code and give brief, clear, and to-the-point comments. Be direct, specific, and concise in your feedback." \
            "Feedback should be as short and concise as possible."

            comments = await super().process_prompt(prompt, message)
            return comments
        except Exception as e:
            error_message = f"Error occurred while generating unit test reviews: {e}"
            self.log_errors(error_message, "generate_comments_with_generated_prompt")
            raise

    async def generate_score(self, comments: str) -> str:
        try:
            score_prompt = f"""
            The evaluation below reviews unit test code for a specific function.

            Evaluation:
            {comments}

            Based on the evaluation and criteria below, review the unit test code and give a score on each category: 
            1. Clarity and Readability: Is the test code clear, concise, and well-structured? Does the test names clearly describe what is being tested? (1 point)
            2. Coverage: Are the tests thorough and cover key cases? Are critical paths and edge cases covered? Does the test cover both expected and unexpected inputs? (2 points)
            3. Accuracy: Are the tests correct and do they accurately verify the intended behaviour? Are there use of specific and meaningful assertions? (1 points)
            4. Maintainability: Does each test function focus on a single test case or behavior? Do the tests avoid complex logic or dependencies? (1 point)

            Sum up the scores and rate the unit test code out of 5. Just give the score as a plain number.
            """
            score_message = "You are an expert software engineer skilled in unit testing and code quality. " \
            "Your task is to review unit test code and give a score for unit test code based on evaluation comments."

            score = await super().process_prompt(score_prompt, score_message)
            return score
        except Exception as e:
            error_message = f"Error occurred while generating unit test score: {e}"
            self.log_errors(error_message, "generate_score")
            raise

    def _review_untested_func(self, file: str, untested_func_list: List[Function], relevant_test_file: List[str]) -> None:
        try:
            untested_func_names = [func.func_name for func in untested_func_list]
            content = ""
            if untested_func_names:
                untested_func_str = (', ').join([name for name in untested_func_names])
                header = "### Missing unit test: \n"
                test_file_name = relevant_test_file[0]
                content = f"These functions have been modified in the pull request but no unit tests were found: ```{untested_func_str}```. Add in the corresponding unit tests in `{test_file_name}` to improve test coverage."
                content = header + content
                self.missing_test_review[file] = content
        except Exception as e:
            error_message = f"Error occurred while generating missing unit test review for {file}: {e}. Skipping missing unit test review for {file}."
            self.log_errors(error_message, "_review_untested_func")
            return
    
    async def get_test_cases(self, test_file: str) -> Dict[str, List[Function]]:
        try:
            tested_func_dict = {}

            test_context_provider = CodeContextProvider(test_file)
            test_context = await test_context_provider.build_context()
            tested_func_list = test_context.get(test_file, [])

            # Get all test cases
            for func in tested_func_list:
                dependencies = func.dependencies
                for dependency in dependencies:
                    if dependency in func.func_name:
                        if dependency not in tested_func_dict:
                            tested_func_dict[dependency] = [func]
                        else:
                            tested_func_dict[dependency].append(func)

            return tested_func_dict
        except Exception as e:
            error_message = f"Error occurred while fetching test cases: {e}"
            self.log_errors(error_message, "get_tested_func")
            raise

    async def get_fixtures(self, test_file: str) -> List[Function]:
        try:
            test_context_provider = CodeContextProvider(test_file)
            fixtures = await test_context_provider.get_fixtures()
            return fixtures
        except Exception as e:
            norm_test_file = test_file.replace("\\", "/")
            parts = norm_test_file.split('/', 1)
            repo_test_file = parts[1]
            error_message = f"Error occurred while extracting fixtures for {repo_test_file}: {e}"
            self.log_errors(error_message, "get_fixtures")
            raise
    
    def get_relevant_fixture(self, test_func: Function, all_fixtures: List[Function]) -> List[Function]:
        try:
            relevant_fixtures = []
            fixtures_params = test_func.params
            for fixture_param in fixtures_params:
                for fixture in all_fixtures:
                    if fixture == fixture_param:
                        relevant_fixtures.append(fixture)
                        break
            return relevant_fixtures
        except Exception as e:
            error_message = f"Error occurred while extracting fixtures for {test_func.func_name}: {e}"
            self.log_errors(error_message, "get_relevant_fixtures")
            return []
    
    async def get_relevant_dep(self, tested_func: Function, dir_name: str) -> List[Function]:
        try:
            dependencies_names = tested_func.dependencies
            imports = tested_func.imports

            ##### Extract dependency functions
            dep_func_set = set()

            for dep in dependencies_names:
                for imp in imports:
                    if dep in imp:
                        local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
                        local_dir_name = os.path.join(local_folder, dir_name)
                        dep_file = self._get_dep_filepath(imp, local_dir_name)

                        if dep_file:
                            # Use Bitbucket API to get content of dep file
                            await self.processor.download_file_content(dep_file)

                            # Parse through to find dependency function
                            local_path = os.path.join(local_folder, dep_file)
                            
                            provider = CodeContextProvider(local_path)
                            dep_func = await provider.get_dep_func(local_path, dep)
                            
                            if dep_func:
                                dep_func_set.add(dep_func)

            return list(dep_func_set)
        except Exception as e:
            error_message = f"Error occurred while extracting dependencies of {tested_func.func_name}: {e}"
            self.log_errors(error_message, "get_relevant_dep")
            return []
    
    def _get_dep_filepath(self, imp: str, local_dir_name: str) -> str:
        try:
            if imp.startswith('from '):
                # Extract the part after 'from ' and before ' import'
                start = len('from ')
                end = imp.find(' import')
                if end != -1:
                    dep_module = imp[start:end].strip()
                
            module_parts = [part for part in dep_module.split('.') if part]
            relative_local_path = os.path.relpath(local_dir_name, os.getcwd())
            local_parts = relative_local_path.split(os.sep)
            if len(module_parts) == 1:
                repo_dir_name = os.sep.join(local_parts[1:])
                dep_filepath = os.path.join(repo_dir_name, module_parts[0])
            else:
                for i, part in enumerate(local_parts):
                        if part == module_parts[0]:
                            repo_dir_name = os.sep.join(local_parts[1:i])
                            module_name = os.sep.join(module_parts)
                            dep_filepath = os.path.join(repo_dir_name, module_name)
            
            if '.py' not in dep_filepath:
                dep_filepath += '.py'
            
            return dep_filepath
        except Exception:
            return ""

    @override
    def log_errors(self, error_message: str, function: str) -> None:
        self.console_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/unit_test_reviewer.py",
            function=function
        )
        self.file_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/unit_test_reviewer.py",
            function=function
        )
    