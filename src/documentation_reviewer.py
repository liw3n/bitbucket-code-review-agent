from reviewer import Reviewer
import os
from code_context_provider import CodeContextProvider
from pull_request_processor import PullRequestProcessor
from function import Function
from line import Line
from docstring import Docstring
from typing_extensions import List, Dict, Optional, Tuple, override
import asyncio
import time

class DocumentationReviewer(Reviewer):
    def __init__(
        self, modified_func_dict: Dict[str, List[Function]], 
        processor: PullRequestProcessor, agent_files: List[str],
        indexing: bool = False,
    ) -> None:
        super().__init__(processor, agent_files)
        self.modified_func_dict = modified_func_dict
        self.indexing = indexing
        self.file_docstring_dict = {}
        self.format = {}

        # Comments
        self.file_review_dict = {}
        self.func_review_dict = {}

    async def review_documentation(self) -> Tuple[Dict[str, str], Dict[Function, str]]:
        try:
            start_time = time.time()
            super().log_review_metrics('Generating documentation review...')
            
            all_tasks = []
            for modified_file, modified_func_list in self.modified_func_dict.items():
                file_review_task = self.review_documentation_by_file(modified_file)
                all_tasks.append(file_review_task)

                docstring_format = await self.identify_docstring_format(modified_func_list, modified_file)
                for func in modified_func_list:
                    func_review_task = self.review_documentation_by_function(modified_file, func, docstring_format)
                    all_tasks.append(func_review_task)
            
            await asyncio.gather(*all_tasks)
        except Exception as e:
            error_message = f"Error occurred while running documentation review: {e}. Skipping all documentation reviews."
            self.log_errors(error_message, "review_documentation")
            return ({}, {})
        else:
            super().log_review_metrics("Finished generating documentation review", start_time)
            return (self.file_review_dict, self.func_review_dict)
    
    async def review_documentation_by_file(self, file: str) -> None:
        try:
            file_level_review = ""

            # File name review
            file_name_review = await self.review_file_name(file)
            if file_name_review:
                file_level_review += file_name_review + "\n"

            # File docstring review
            file_docstring_review = await self.review_file_docstrings(file)
            if file_docstring_review:
                file_level_review += file_docstring_review + "\n"

            self.file_review_dict[file] = file_level_review
        except Exception as e:
            error_message = f"Error occurred while generating documentation review for {file}: {e}. Skipping documentation review for {file}."
            self.log_errors(error_message, "review_documentation")
            return 
    
    async def review_documentation_by_function(self, file: str, func: Function, docstring_format: str) -> None:
        try:
            function_level_review = ""

            # Naming review
            func_name_review = await self.review_func_name(func, file)
            if func_name_review:
                function_level_review += func_name_review + "\n"
            var_name_review = await self.review_var_name(func, file)
            if var_name_review:
                function_level_review += var_name_review + "\n"

            # Docstring review
            func_docstring_review = await self.review_func_docstrings(func, file, docstring_format)
            if func_docstring_review:
                function_level_review += func_docstring_review + "\n"

            self.func_review_dict[func] = function_level_review
        except Exception as e:
            error_message = f"Error occurred while reviewing documentation of {func.func_name} in {file}: {e}. Skipping documentation review for {func.func_name}."
            self.log_errors(error_message, "review_documentation_by_function")
            return 
    
    async def review_func_docstrings(self, func: Function, file: str, docstring_format: str) -> str:
        try:
            docstring = func.docstring
            if docstring: # review function docstring
                func_docstring_review_prompt = await self.generate_prompt_for_func_docstring_review(func, file)
                func_docstring_review = await self.review_func_docstring_with_generated_prompt(func_docstring_review_prompt, func, file)
                return func_docstring_review
            else: # suggest function docstring
                func_docstring_generation_prompt = await self.generate_prompt_for_func_docstring_generation(func, docstring_format, file)
                func_docstring_review = await self.generate_func_docstring_with_generated_prompt(func_docstring_generation_prompt, func, file)
                return func_docstring_review
        except Exception as e:
            error_message = f"Error occurred while reviewing function docstring for {func.func_name} in {file}: {e}. Skipping review of function docstring for {func.func_name}."
            self.log_errors(error_message, "review_func_docstrings")
            return ""
    
    async def generate_prompt_for_func_docstring_review(self, func: Function, file: str) -> str:
        docstring_format = self.format.get(func, "")
        original_prompt = f"""
        The function {func.func_name} has a docstring in {docstring_format}:
        {func.docstring.code}

        Function code:
        {func.func_code}

        Evaluate the quality of this docstring based on these criteria:
        1. Clarity and Conciseness: Does the docstring clearly describe the function's purpose? Is it succinct yet comprehensive?
        2. Accuracy and Completeness: Does it include all essential details? Is it specific about what the function does?
        3. Correct Formatting: Does the docstring adhere to the {docstring_format} conventions?

        Instructions:
        - Provide brief, concise and specific evaluation of the docstring.
        """

        original_prompt = await super().enhance_prompt_with_config(original_prompt)

        if not self.indexing:
            return original_prompt

        try:
            query = file + " " + func.func_name
            code_context = super().get_context(query)
            
            prompt = f"""
            The prompt below is used to review the function docstring:
            {original_prompt}

            Information:
            Docstring: {func.docstring.code}
            Docstring style: {docstring_format}
            Docstring is for function: {func.func_name}
            Function code: {func.func_code}
            Context: {code_context}

            Task:
            - Enhance the original prompt with information above to be given as context for the review.
            - Provide as much information in the enhanced prompt deemed suitable for the review.

            Additional Requirements:
            - Criteria: Clarity and conciseness, Accuracy and completeness, Adherence to docstring style. Add other criteria if deemed fit.
            - Improvements: Ask to provide specific and detailed suggestions for improvements in bullet points. If no improvements needed, respond with only 'no improvements needed'.
            - Context: Provide a summarised function purpose to better assess the docstring. Give as much information deemed useful to review the docstring properly. 

            Output:
            - Do not provide a review and suggestions for improvement of the docstring yourself here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            message = "You are an expert in crafting clear, context-rich prompts that enable effective file name reviews. " \
            "Your task here is to generate a polished, paste-ready reviewer-prompt text. Do not produce any actual review content in this step."

            generated_prompt = await super().process_prompt(prompt, message)
            return generated_prompt
        except Exception as e:
            error_message = f"Error occurred while generating prompt for function docstring review of {func.func_name} in {file}: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_prompt_for_func_docstring_review")
            return original_prompt
    
    async def review_func_docstring_with_generated_prompt(self, prompt: str, func: Function, file: str) -> str:
        try:
            prompt += "Provide only suggestions in bullet points with no headers and markdown marks. Do not add extra commentary and any revised docstring."
            message = "You are an expert in Python docstrings. Your feedback should be concise, direct, and to the point."
            docstring_content = await super().process_prompt(prompt, message)
            if 'no improvements needed' not in docstring_content.lower():
                general_subheader = "#### Review of function docstring: \n" 
                existing_docstring = "**Original docstring:** \n" + "```python\n" + func.docstring.code + "\n```\n"
                docstring_content = "**Suggestions for improvements:** \n" + docstring_content
                review_content = general_subheader + existing_docstring + docstring_content
                return review_content
        except Exception as e:
            error_message = f"Error occurred while reviewing function docstring of {func.func_name} in {file}: {e}. Skipping function docstring review for {func.func_name}."
            self.log_errors(error_message, "review_func_docstring_with_generated_prompt")
            return ""
    
    async def generate_prompt_for_func_docstring_generation(self, func: Function, docstring_format: str, file: str) -> str:
        original_prompt = f"""
        Write a docstring in {docstring_format} for the Python function {func.func_name} below:
        {func.func_code}

        Respond with only the docstring with triple quotation marks and without markdown, code blocks, or backticks.
        """

        original_prompt = await super().enhance_prompt_with_config(original_prompt)

        if not self.indexing:
            return original_prompt
        
        try:
            query = file + " " + func.func_name
            code_context = super().get_context(query)

            prompt = f"""
            The prompt below is used to generate function docstring:
            {original_prompt}
            
            Information:
            Docstring style: {docstring_format}
            Docstring is for function: {func.func_name}
            Function code: {func.func_code}
            Context: {code_context}

            Task:
            - Enhance the original prompt with information above to be given as context to generate appropriate docstrings.
            - Provide as much information in the enhanced prompt deemed suitable for the generation of docstrings.

            Additional Requirements:
            - Criteria: Clarity and conciseness, Accuracy and completeness, Adherence to docstring style. Add other criteria if deemed fit.
            - Suggestions: Ask to generate a docstring that fulfils all the criteria.
            - Context: Provide a summarised function purpose to generate the docstring. Give as much information as possible to generate the docstring properly, including function code, exceptions raised and any other information deemed useful.

            Output:
            - Do not generate the docstring yourself here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            message = "You are an expert in crafting clear, context-rich prompts that enable effective docstring generation. " \
            "Your task here is to generate a polished, paste-ready reviewer-prompt text. Do not generate any docstring in this step."
            generated_prompt = await super().process_prompt(prompt, message)
            return generated_prompt
        except Exception as e:
            error_message = f"Error occurred while generating prompt to suggest docstrings for {func.func_name} in {file}: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_prompt_for_func_docstring_generation")
            return original_prompt
    
    async def generate_func_docstring_with_generated_prompt(self, prompt: str, func: Function, file: str) -> str:
        try:
            prompt += "Respond with only the generated docstring with no markdown marks. Do not include any other commentary."
            message = "You are an expert in writing Python docstrings. " \
            "Your task is to produce a clear, concise, and well-formatted docstring for the given function."
            docstring_content = await super().process_prompt(prompt, message)
            if docstring_content:
                header = "#### Review of function docstring: \n"
                content = "Missing function docstrings. Add a function-level docstring describing the function's purpose to improve readability and maintainability.\n\n"
                generated_content = "**Suggested function docstring:**\n```python\n" + docstring_content + "\n```"
                review_content = header + content + generated_content
                return review_content
        except Exception as e:
            error_message = f"Error occurred while suggesting docstrings for {func.func_name} in {file}: {e}. Skipping suggestion of function docstring for {func.func_name}."
            self.log_errors(error_message, "generate_func_docstring_with_generated_prompt")
            return ""

    async def review_file_docstrings(self, file: str) -> str:
        try:
            if not file.endswith('.py'):
                return "" # Skip file docstring review for non python files
            
            file_docstring = await self.extract_file_docstring(file)
            if file_docstring: # review existing file docstring
                file_docstring_review_prompt = await self.generate_file_docstring_review_prompt(file_docstring, file)
                file_docstring_review = await self.review_file_docstring_with_generated_prompt(file_docstring_review_prompt, file_docstring, file)
                return file_docstring_review
            else: # suggest file docstring
                if '.py' in file: # only suggest docstring for python file
                    file_docstring_generation_prompt = await self.generate_file_docstring_generation_prompt(file)
                    file_docstring_review = await self.generate_file_docstring_with_generated_prompt(file_docstring_generation_prompt, file)
                    return file_docstring_review
                else:
                    return ""
        except Exception as e:
            error_message = f"Error occurred while reviewing file docstring for {file}: {e}. Skipping file docstring review for {file}."
            self.log_errors(error_message, "review_file_docstrings")
            return ""
    
    async def extract_file_docstring(self, file: str) -> Optional[Docstring]:
        try:
            local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
            local_file = str(os.path.join(local_folder, file))
            provider = CodeContextProvider(local_file)
            file_docstring = await provider.get_file_docstring()
            return file_docstring
        except Exception as e:
            error_message = f"Error occurred while extracting file docstrings for {file}: {e}"
            self.log_errors(error_message, "extract_file_docstrings")
            raise
    
    async def generate_file_docstring_review_prompt(self, file_docstring: Docstring, file: str) -> str:
        original_prompt = f"""
        The module {file} has a docstring:
        {file_docstring.code}

        Instructions:
        - If the docstring is appropriate and needs no changes, reply with: 'no improvements needed'.
        - Otherwise, list brief, clear, and specific suggestions for improvement. Provide only suggestions for improvements.
        """

        original_prompt = await super().enhance_prompt_with_config(original_prompt)

        if not self.indexing:
            return original_prompt
        
        try:
            code_context = super().get_context(file)

            prompt = f"""
            The prompt below is used to review file docstring:
            {original_prompt}

            Information:
            Docstring: {file_docstring.code}
            Docstring is for file: {file}
            Context: {code_context}

            Task:
            - Enhance the original prompt with information above to be given as context for the review.
            - Provide as much information in the enhanced prompt deemed suitable for the review.

            Additional Requirements:
            - Criteria: Clarity and conciseness, Accuracy and completeness, Adherence to docstring style. Add other criteria if deemed fit.
            - Improvements: Ask to provide specific and detailed suggestions for improvements in bullet points. If no improvements needed, respond with only 'no improvements needed'.
            - Context: Provide a summarised file purpose to better assess the docstring. Give as much information deemed useful to review the docstring properly. 

            Output:
            - Do not provide a review and suggestions for improvement of the docstring yourself here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            message = "You are an expert in crafting clear, context-rich prompts that enable effective file docstring reviews. " \
            "Your task here is to generate a polished, paste-ready reviewer-prompt text. Do not produce any actual review content in this step."
            generated_prompt = await super().process_prompt(prompt, message)
            return generated_prompt
        except Exception as e:
            error_message = f"Error occurred while prompt for file docstring review of {file}: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_file_docstring_review_prompt")
            return original_prompt
    
    async def review_file_docstring_with_generated_prompt(self, prompt: str, file_docstring: Docstring, file: str) -> str:
        try:
            prompt += "Provide only suggestions in bullet points with no headers and markdown marks. Do not add extra commentary and any revised docstring."
            message = "You are an expert in Python docstrings. Your feedback should be concise, direct, and to the point."
            docstring_content = await super().process_prompt(prompt, message)
            if 'no improvements needed' not in docstring_content.lower():
                general_subheader = "### Review of file docstring: \n" 
                existing_docstring = "**Original file docstring:**\n" + "```python\n" + file_docstring.code + "\n```\n"
                docstring_content = "**Suggestions for improvements:**\n" + docstring_content
                review_content = general_subheader + existing_docstring + docstring_content
                return review_content
        except Exception as e:
            error_message = f"Error occurred while reviewing file docstring of {file}: {e}. Skipping file docstring review for {file}."
            self.log_errors(error_message, "review_file_docstring_with_generated_prompt")
            raise
    
    async def generate_file_docstring_generation_prompt(self, file: str) -> str:
        original_prompt = f"""
        Generate a single-line file docstring for {file}. 
        Respond with only the docstring with triple quotation marks and without markdown, code blocks, or backticks.
        """

        original_prompt = await super().enhance_prompt_with_config(original_prompt)

        if not self.indexing:
            local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
            local_file = os.path.join(local_folder, file)
            with open(local_file, 'r') as f:
                file_code = f.read()
            return original_prompt + "\n" + f"File code: \n {file_code}"

        # Get context -- Use file code if fails
        try:
            local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
            local_file = os.path.join(local_folder, file)
            provider = CodeContextProvider(local_file)
            func_dict = await provider.build_context()
            func_list = func_dict.get(local_file, [])
            func_names = [func.func_name for func in func_list]
            if len(func_names) > 5:
                func_names = func_names[:5] # truncate function name to search faster
            func_names = (" ").join(func_names)
            code_context = super().get_context(file + " " + func_names)
        except Exception as e:
            error_message = f"Error occurred while fetching context to generate file docstring suggestion prompt for {file}: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_file_docstring_generation_prompt")
            local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
            local_file = os.path.join(local_folder, file)
            with open(local_file, 'r') as f:
                file_code = f.read()
            return original_prompt + "\n" + f"File code: \n {file_code}"
        
        # Enhance prompt with context
        try:
            prompt = f"""
            The prompt below is used to generate file docstring:
            {original_prompt}

            Information:
            Docstring is for file: {file}
            Context: {code_context}

            Task:
            - Enhance the original prompt with information above to be given as context to generate appropriate docstrings.
            - Provide as much information in the enhanced prompt deemed suitable for the generation of docstrings.

            Additional Requirements:
            - Criteria: Clarity and conciseness, Accuracy and completeness, Adherence to docstring style. Add other criteria if deemed fit.
            - Suggestions: Ask to generate a docstring that fulfils all the criteria.
            - Context: Provide a summarised file purpose to generate the docstring. Give as much information deemed useful to generate the docstring properly.

            Output:
            - Do not generate the docstring yourself here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            message = "You are an expert in crafting clear, context-rich prompts that enable effective docstring generation. " \
            "Your task here is to generate a polished, paste-ready reviewer-prompt text. Do not generate any docstring in this step."
            generated_prompt = await super().process_prompt(prompt, message)
            return generated_prompt
        except Exception as e:
            error_message = f"Error occurred while generating prompt to suggest docstring for {file}: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_file_docstring_generation_prompt")
            return original_prompt + "\n" + f"File context:\n {code_context}"
            
    
    async def generate_file_docstring_with_generated_prompt(self, prompt: str, file: str) -> str:
        try:
            prompt += "Respond with only the docstring with triple quotation marks and without markdown, code blocks, or backticks."
            message = "You are an expert in writing Python file docstrings. " \
            "Your task is to produce a clear, concise, and well-formatted docstring for the given file."

            docstring_content = await super().process_prompt(prompt, message)
            if docstring_content:
                general_subheader = "### Review of file docstring: \n"
                comments = "Missing file docstring. Add a file-level docstring describing the file's purpose to improve readability and maintainability.\n\n"
                generated_content = "**Suggested file docstring:**\n" + "```python\n" + docstring_content + "\n```"
                review_content = general_subheader + comments + generated_content
                return review_content
        except Exception as e:
            error_message = f"Error occurred while generating docstring for {file}: {e}. Skipping generation of file docstring for {file}."
            self.log_errors(error_message, "generate_file_docstring_with_generated_prompt")
            raise

    async def review_file_name(self, file: str) -> str:
        try:
            if file.startswith('test_') or not file.endswith('.py'):
                return "" # skip file name review for test files and non-python files
            else:
                file_name_prompt = await self.generate_file_name_review_prompt(file)
                file_name_review = await self.review_file_name_with_generated_prompt(file_name_prompt, file)
                return file_name_review
        except Exception as e:
            error_message = f"Error occurred while reviewing file name for {file}: {e}. Skipping file name review for {file}."
            self.log_errors(error_message, "review_file_name")
            return ""
    
    async def generate_file_name_review_prompt(self, file: str) -> str:
        file_name = os.path.basename(file)
        original_prompt = f"""
        File: {file_name}

        Please evaluate the filename based on these categories:
        1. Adherence to Python naming conventions: Does the filename follow PEP 8 naming standards?
        2. Readability and clarity: Is the filename clear about the file's purpose? Is it easy to understand?

        - If the filename is appropriate and needs no change, respond with: 'no improvements needed'.
        - If improvements are needed, review the file name and suggest a new filename with reasoning.
        - Keeping the suggestions and review brief, concise and to the point.
        """

        original_prompt = await super().enhance_prompt_with_config(original_prompt)

        if not self.indexing:
            return original_prompt
        
        try:
            code_context = super().get_context(file)
            prompt = f"""
            The prompt below is used to review file name for {file_name}:
            {original_prompt}

            Information:
            File: {file_name}
            Context: {code_context}
            
            Task:
            - Enhance the original prompt with information above to be given as context for the review.
            - Provide as much information in the enhanced prompt deemed suitable for the review.

            Additional Requirements:
            - Criteria: naming conventions, readability, clarity.
            - Rename: provide concise rationale if changes are suggested.
            - Suggestions: 1 snake_case candidate with 1 sentence justification.
            - Constraints: snake_case, ASCII, avoid vague names.
            - Edge cases: note when current name is acceptable by returning 'no improvements needed'. 

            Output:
            - Do not provide a review or analysis of the file name yourself here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            message = "You are an expert in crafting clear, context-rich prompts that enable effective file name reviews. " \
            "Your task here is to generate a polished, paste-ready reviewer-prompt text. Do not produce any actual review content in this step."
            generated_prompt = await super().process_prompt(prompt, message)
            return generated_prompt
        except Exception as e:
            error_message = f"Error occurred while generating prompt for file name review of {file}: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_file_name_review_prompt")
            return original_prompt

    async def review_file_name_with_generated_prompt(self, prompt: str, file: str) -> str:
        try:
            prompt += "Respond with only one concise sentence for improvements. Be direct and do not respond in first-person perspective."
            name_message = (
                "You are an expert in Python naming conventions and best practices for organizing codebases. "
                "Your task is to analyze the provided filename and recommend improvements if necessary. "
                "Keep suggestions concise and direct."
            )
            name_content = await super().process_prompt(prompt, name_message)
            if 'no improvements needed' not in name_content.lower():
                name_content = "### Review of file name: \n" + name_content 
                return name_content
        except Exception as e:
            error_message = f"Error occurred while reviewing file name for {file}: {e}. Skipping file name review for {file}."
            self.log_errors(error_message, "review_file_name_with_generated_prompt")
            raise
    
    async def review_func_name(self, func: Function, file: str) -> str:
        try:
            func_name_prompt = await self.generate_func_name_review_prompt(func, file)
            func_name_review = await self.review_func_name_with_generated_prompt(func_name_prompt, func)
            return func_name_review
        except Exception as e:
            error_message = f"Error occurred while reviewing function name {func.func_name} in {file}: {e}. Skipping function name review for {func.func_name}."
            self.log_errors(error_message, "review_func_name")
            return ""
    
    async def generate_func_name_review_prompt(self, func: Function, file) -> str:
        original_prompt = f"""
        Please evaluate the function name {func.func_name} based on these categories:
        1. Adherence to Python naming conventions: Does the function name follow PEP 8 naming standards?
        2. Readability and clarity: Is the function name clear about the function's purpose? Is it easy to understand?

        Function code:
        {func.func_code}

        - If the function is appropriate and needs no change, respond with: 'no improvements needed'.
        - If improvements are needed, review the function name and suggest a new function name. 
        - Respond only with the review and suggestion. Keep it brief, concise and to the point.
        """

        original_prompt = await super().enhance_prompt_with_config(original_prompt)

        if not self.indexing:
            return original_prompt

        try:
            query = file + " " + func.func_name
            code_context = super().get_context(query)

            prompt = f"""
            The prompt below is used to review function name for {func.func_name}:
            {original_prompt}

            Information:
            Function: {func.func_name}
            Function code: {func.func_code}
            Context: {code_context}

            Task:
            - Enhance the original prompt with information above to be given as context for the review.
            - Provide as much information in the enhanced prompt deemed suitable for the review.

            Additional Requirements:
            - Criteria: naming conventions, readability, clarity.
            - Rename: provide concise rationale if changes are suggested.
            - Suggestions: 1 snake_case candidate with 1 sentence justification.
            - Constraints: snake_case, ASCII, avoid vague names.
            - Edge cases: note when current name is acceptable by returning 'no improvements needed'. 
            
            Output:
            - Do not provide a review or analysis of the function name yourself here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            message = "You are an expert in crafting clear, context-rich prompts that enable effective function name reviews. " \
            "Your task here is to generate a polished, paste-ready reviewer-prompt text. Do not produce any actual review content in this step."
            generated_prompt = await super().process_prompt(prompt, message)
            return generated_prompt
        except Exception as e:
            error_message = f"Error occurred while generating prompt for function name review of {func.func_name} in {file}: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_func_name_review_prompt")
            return original_prompt
    
    async def review_func_name_with_generated_prompt(self, prompt: str, func: Function) -> str:
        try:
            name_message = (
                "You are an expert in Python naming conventions for functions. "
                "Your task is to analyze the provided function name and recommend improvements if necessary. "
                "Keep suggestions concise and direct."
            )

            name_content = await super().process_prompt(prompt, name_message)
            if 'no improvements needed' not in name_content.lower():
                review_content = "#### Review of function name: \n " + name_content
                return review_content
        except Exception as e:
            error_message = f"Error occurred while reviewing function name for {func.func_name}: {e}. Skipping function name review for {func.func_name}."
            self.log_errors(error_message, "review_func_name_with_generated_prompt")
            raise
    
    async def review_var_name(self, func: Function, file: str) -> str:
        try:
            var_name_prompt = await self.generate_var_name_review_prompt(func, file)
            var_name_review = await self.review_var_name_with_generated_prompt(var_name_prompt, file)
            return var_name_review
        except Exception as e:
            error_message = f"Error occurred while reviewing variable names for {func.func_name} in {file}: {e}. Skipping variable name reviews for {func.func_name}."
            self.log_errors(error_message, "review_var_name")
            return ""
    
    async def generate_var_name_review_prompt(self, func: Function, file: str) -> str:
        original_prompt = f"""
        The function '{func.func_name}' has the following code:
        {func.func_code}

        Task: Review only the variable names used inside this function (including parameters and local variables). Do NOT review logic, formatting, comments, function name, or suggest code changes. 
        You only need to assess variables names.
        - Determine whether each variable name is meaningful and follows PEP 8 (use lowercase_with_underscores for variables and parameters).
        - Only recommend renames that are substantive — avoid trivial or cosmetic changes. If the best change would be an insignificant or very simple renaming, reply exactly: no improvements needed. Do not nitpick.
        - Otherwise, list each recommended rename as a bullet point using this format: old_name -> new_name for <short justification and rationale>
        - Keep each suggestion concise (one line each). Do not include explanations, examples, or extra text.
        - The reply must contain no markdown, no code fences, and no additional commentary.
        """

        original_prompt = await super().enhance_prompt_with_config(original_prompt)

        if not self.indexing:
            return original_prompt
        
        try:
            query = file + " " + func.func_name
            code_context = super().get_context(query)

            prompt = f"""
            The prompt below is to review variable and parameter names in {func.func_name}:
            {original_prompt}

            Information: 
            Function code: {func.func_code}
            Context: {code_context}

            Task:
            - Enhance the original prompt with information above to be given as context for the review.
            - Provide as much information in the enhanced prompt deemed suitable for the review.

            Additional Requirements:
            - Criteria: naming conventions, readability, clarity.
            - Rename: provide concise rationale if changes are suggested.
            - Suggestions: 1 snake_case candidate with 1 sentence justification.
            - Constraints: snake_case, ASCII, avoid vague names.
            - Edge cases: note when current name is acceptable by returning 'no improvements needed'. 
            
            Output:
            - Do not provide a review or analysis of the variable name yourself here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            message = "You are an expert in crafting clear, context-rich prompts that enable effective variable name reviews. " \
            "Your task here is to generate a polished, paste-ready reviewer-prompt text. Do not produce any actual review content in this step."
            generated_prompt = await super().process_prompt(prompt, message)
            return generated_prompt
        except Exception as e:
            error_message = f"Error occurred while generating prompt for variable name review for {func.func_name} in {file}: {e}. Defaulting to original prompt."
            self.log_errors(error_message, "generate_var_name_review_prompt")
            return original_prompt
    
    async def review_var_name_with_generated_prompt(self, prompt: str, file: str) -> str:
        try:
            message = "You are an expert in Python variable naming conventions. " \
            "Your task is to assess the variable names within a function and suggest concise improvements."
            var_content = await super().process_prompt(prompt, message)
            if 'no improvements needed' not in var_content.lower(): 
                validation_content = await self.validate_var_name_review(var_content)
                if 'no improvements needed' not in validation_content.lower():
                    content = "Consider renaming the following variables in the function: \n" + validation_content
                    review_content = "#### Review of variable names in function: \n " + content
                    return review_content
        except Exception as e:
            error_message = f"Error occurred while reviewing variable names in {file}: {e}. Skipping variable name review in {file}."
            self.log_errors(error_message, "review_var_name_with_generated_prompt")
            return ""
    
    async def validate_var_name_review(self, var_content: str) -> str:
        prompt = f"""
        These are the recommended renames for variables in a function.

        Review: {var_content}

        Task: Validate whether each recommended rename is significant and non-trivial. Be strict and conservative: accept only renames that clearly and materially improve meaning, clarity, or correctness beyond cosmetic or purely stylistic changes.

        Treat as trivial/insignificant (call "no improvements needed") when the change is only:
        - Minor punctuation/underscore changes, reordering of synonymous words, or tiny length adjustments.
        - Simple abbreviation expansions or contractions that do not increase semantic clarity.
        - Changes that do not alter the understood meaning in a way that improves readability or correctness.

        Output rules:
        - If ONE OR MORE renames in the Review are significant, return only those significant rename lines (and nothing else). Preserve each significant line verbatim as it appears in the Review. Do not add, remove, or modify text, punctuation, formatting, or order of those returned lines. No markdown, no code fences, no extra commentary.
        - If NO renames are significant, return exactly: no improvements needed
        - Do not modify the Review text when returning it. Be decisive and conservative; prefer to label a rename trivial.
        """
        prompt_message = (
            "You are an AI assistant that strictly validates variable rename suggestions. "
            "Be conservative and do not nitpick purely stylistic changes."
        )
        review = await super().process_prompt(prompt, prompt_message)
        return review
    
    async def identify_docstring_format(self, func_list: List[Function], file: str) -> str:
        try:
            default_format = 'Google Style'
            format_set = set()
            for func in func_list:
                try:
                    format_prompt = f"""
                    Identify the format that the docstring is written in:
                    {func.docstring.code}

                    Respond with only the format name and no additional information.
                    """
                    format_message = "You are an expert in Python software engineering. Your task is to identify the format that a docstring is written in."

                    format_content = await super().process_prompt(format_prompt, format_message)
                    self.format[func] = format_content
                    format_set.add(format_content)
                except Exception as e:
                    if func.docstring:
                        error_message = f"Error occurred while identify docstring format for {func.func_name} in {file}: {e}. Defaulting to 'Google Style' format for {func.func_name}."
                        self.log_errors(error_message, "identify_docstring_format")
                    self.format[func] = default_format
                    continue
            
            if len(format_set) == 1: # unique docstring format
                format_list = list(format_set)
                return format_list[0]
            else: # default to Google Style if no unique docstring format
                return default_format
        except Exception as e: # default to Google Style if errors
            error_message = f"Error occurred while identify docstring format in {file}: {e}. Defaulting to 'Google Style' format for {file}."
            self.log_errors(error_message, "identify_docstring_format")
            return default_format
    
    @override
    def log_errors(self, error_message: str, function: str) -> None:
        self.console_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/documentation_reviewer.py",
            function=function
        )
        self.file_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/documentation_reviewer.py",
            function=function
        )