import os
from typing_extensions import List, Optional, Tuple, Dict, override
from code_context_provider import CodeContextProvider
from function import Function
from reviewer import Reviewer
from pull_request_processor import PullRequestProcessor
from qdrant_client import models
import uuid
import aiofiles
import time
import json

class CodeIndexBuilder(Reviewer):
    def __init__(self, processor: PullRequestProcessor, modified_func_dict: Dict[str, List[Function]]) -> None:
        super().__init__(processor)
        self.modified_func_dict = modified_func_dict
        self.folder_path = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}/'
        self.func_description = {}
        self.file_description = {}
        self.project_description = ""
        self.project_structure = self.get_project_structure()
        self.collection_name = f'embeddings_for_{self.processor.project}_{self.processor.repo}'

    def get_project_structure(self) -> str:
        try:
            def recurse(current_folder, prefix=''):
                structure = ''
                items = sorted(os.listdir(current_folder))
                for item in items:
                    path = os.path.join(current_folder, item)
                    if os.path.isdir(path):
                        structure += f"{prefix}|--- {item}/\n"
                        structure += recurse(path, prefix + '    ')
                    else:
                        structure += f"{prefix}|--- {item}\n"
                return structure

            root_name = os.path.basename(os.path.normpath(self.folder_path))
            result = f"{root_name}/\n"
            result += recurse(self.folder_path)
            return result
        except Exception as e:
            error_message = f"Error occurred while generating project structure: {e}"
            self.log_errors(error_message, "get_project_structure")
            raise
            
    async def embed_and_store_codebase(self) -> None:
        try:
            super().log_review_metrics("Generating embeddings and storing into vector database...")
            start_time = time.time()

            # Set up
            collection_exist = self.qdrant_client.collection_exists(collection_name=self.collection_name)
            if collection_exist:
                await self.update_index()
            else:
                await self.create_index()
        except Exception as e:
            error_message = f"Error occurred while indexing repository: {e}"
            self.log_errors(error_message, "embed_and_store_codebase")
            raise
        else:
            super().log_review_metrics("Finished generating embeddings and storing into vector database.", start_time)

    async def update_index(self) -> None:
        try:
            update_tasks = []
            num_to_update = sum(len(funcs) for funcs in self.modified_func_dict.values())
            if num_to_update > 10:
                comment = "Sentinel is currently indexing a large number of files. Processing may take additional time. Comments will be published upon completion."
                await self.processor.post_reviews(comment, feedback=False)

            for file, modified_func_list in self.modified_func_dict.items():
                for func in modified_func_list:
                    task = self.update_index_indiv(func, file)
                    update_tasks.append(task)
            await asyncio.gather(*update_tasks)

            deleted_files = self.processor.get_deleted_files()
            for file in deleted_files:
                self.delete_points(file)
        except Exception as e:
            error_message = f"Error occurred while updating index: {e}"
            self.log_errors(error_message, "update_index")
            raise

    async def update_index_indiv(self, func: Function, file: str) -> None:
        try:
            key = f"{file}_{func.func_name}"
            json_file = f"qdrant_id/qdrant_id_{self.processor.project}_{self.processor.repo}"
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            point_id = data.get(key, '')
            if point_id: # existing function
                payload = self.retrieve_payload(point_id)
                func_desc = await self.update_func_description(func, payload)
                file_desc = await self.update_file_description(func, payload)
                payload = {
                    'file': file,
                    'function': func.func_name,
                    'code': func.func_code,
                    'file_description': file_desc,
                    'function_description': func_desc
                }
                self.update_point(func, payload, point_id)
            else: # added function
                sample_id = ""
                for key, id in data.items():
                    if key.startswith(file):
                        sample_id = id
                        break
                if sample_id: # function added in existing file
                    sample_payload = self.retrieve_payload(sample_id)
                    func_desc = await self.generate_func_desc(func)
                    file_desc = await self.update_file_description(func, sample_payload)
                    func_code = func.func_code
                    description = f"""
                    Function Description: {func_desc}
                    File Description: {file_desc}
                    """
                    embedded_code = super().embed_text(func_code)
                    embedded_description = super().embed_text(description)

                    payload = {
                        "file": file,
                        "function": func.func_name,
                        "code": func_code,
                        "file_description": file_desc,
                        "function_description": func_desc,
                    }
                    if embedded_code and embedded_description:
                        self.store_embedding(payload=payload, code=embedded_code, description=embedded_description)
                    elif embedded_code:
                        self.store_embedding(payload=payload, code=embedded_code)
                    elif embedded_description: 
                        self.store_embedding(payload=payload, description=embedded_description)
                else: # function added in new file
                    local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
                    local_file = os.path.join(local_folder, file)
                    await self.run_embed_process(local_file)

        except Exception as e:
            error_message = f"Error occurred while updating index for {func.func_name} in {file}: {e}"
            self.log_errors(error_message, "update_index_indiv")
            raise
    
    def delete_points(self, file: str) -> None:
        try:
            # Find points related to file
            json_file = f"qdrant_id/qdrant_id_{self.processor.project}_{self.processor.repo}"
            point_id = []
            keys_to_delete = []
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for key, id in data.items():
                if key.startswith(file):
                    point_id.append(id)
                    keys_to_delete.append(key)

            # Delete all points related to file
            self.qdrant_client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(
                    points=point_id,
                ),
            )

            # Remove key in json file
            for key in keys_to_delete:
                data.pop(key, None)
            with open(json_file, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception:
            return 
    
    def retrieve_payload(self, point_id: str) -> Dict:
        try:
            point_list = self.qdrant_client.retrieve(
                collection_name=self.collection_name,
                ids=[point_id],
            )
            point = point_list[0]
            payload = point.payload
            return payload
        except Exception as e:
            error_message = f"Error occurred while retrieving Qdrant payload from point {point_id}: {e}"
            self.log_errors(error_message, "retrieve_payload")
            raise
    
    async def update_func_description(self, func: Function, payload: Dict) -> str:
        try:
            old_func_desc = payload.get('function_description', '')
            function_prompt = f"""
            The function {func.func_name} has been modified in a pull request. 
            The previous function description is:
            {old_func_desc}

            The new function code is:
            {func.func_code}

            Generate a new function description based on the old function description and the new function code.
            Respond with only the function description with no extra commentary and markdown marks.
            """
            function_message = """
            Your task is to produce a single, clear description of the function's behavior based on the updated code and the prior description.
            Return only the plain-text description.
            """
            new_func_desc = await super().process_prompt(function_prompt, function_message)
            return new_func_desc
        except Exception as e:
            error_message = f"Error occurred while updating function description for {func.func_name} in Qdrant: {e}"
            self.log_errors(error_message, "update_func_description")
            raise

    async def update_file_description(self, func: Function, payload: Dict) -> str:
        file = payload.get('file', '')
        try:
            old_file_desc = payload.get('file_description', '')
            code_context = super().get_context(file)

            file_prompt = f"""
            The function {func.func_name} has been modified in a pull request. The function is in the file {file}.
            The previous file description is:
            {old_file_desc}
            Some context for the file includes:
            {code_context}

            The new function code is:
            {func.func_code}

            Based on the previous description and the provided context, assess how the new code changes the file's description.
            Generate a new file description.Respond with only the file description with no extra commentary and markdown marks.
            """
            file_message = """
            Your task is to produce a single, clear description of the file's behavior based on the updated code and the prior description.
            Return only the plain-text description.
            """
            new_file_desc = await super().process_prompt(file_prompt, file_message)
            return new_file_desc
        except Exception as e:
            error_message = f"Error occurred while updating file description for {file}: {e}"
            self.log_errors(error_message, "update_file_description")
            raise
    
    def update_point(self, func: Function, payload: Dict, point_id: str) -> None:
        try:
            # Delete old point
            self.qdrant_client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(
                    points=[point_id],
                ),
            )

            # Add new point
            func_desc = payload.get('function_description', '')
            file_desc = payload.get('file_description', '')
            description = f"""
            Function Description: {func_desc}
            File Description: {file_desc}
            """
            embedded_description = super().embed_text(description)
            embedded_code = super().embed_text(func.func_code)
            if embedded_description and embedded_code:
                self.store_embedding(payload=payload, code=embedded_code, description=embedded_description)
            elif embedded_code:
                self.store_embedding(payload=payload, code=embedded_code)
            elif embedded_description: 
                self.store_embedding(payload=payload, description=embedded_description)
        except Exception as e:
            error_message = f"Error occurred while updating point for {func.func_name}: {e}"
            self.log_errors(error_message, "update_point")
            raise
        
    async def create_index(self) -> None:
        try:
            comment = "Sentinel is currently indexing a large number of files. Processing may take additional time. Comments will be published upon completion."
            await self.processor.post_reviews(comment, feedback=False)
            
            await self.processor.download_all_files()
            self.create_collection()
            self.project_description = await self.get_project_description()
            all_files = self.get_all_files()

            # Run embedding process for 50 files at a time -- generate description, embed, store
            file_index = 0
            chunk_size = 50
            while file_index < len(all_files):
                files_for_processing = all_files[file_index:file_index+chunk_size]
                all_embedding_tasks = [
                    self.run_embed_process(file)
                    for file in files_for_processing
                ]
                await asyncio.gather(*all_embedding_tasks)
                file_index += chunk_size
        except Exception as e:
            try:
                self.qdrant_client.delete_collection(collection_name=self.collection_name)
            except Exception:
                pass
            try:
                json_file = f"qdrant_id/qdrant_id_{self.processor.project}_{self.processor.repo}"
                if os.path.isfile(json_file):
                    os.remove(json_file)
            except Exception:
                pass
            
            error_message = f"Error occurred while creating index: {e}"
            self.log_errors(error_message, "create_index")
            raise 
    
    def create_collection(self) -> None:
        try:
            self.qdrant_client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "description": models.VectorParams(
                        size=768,
                        distance=models.Distance.COSINE,
                    ),
                    "code": models.VectorParams(
                        size=768,
                        distance=models.Distance.COSINE,
                    ),
                },
            )
        except Exception as e:
            error_message = f"Error occurred while creating Qdrant collection: {e}"
            self.log_errors(error_message, "create_collection")
            raise

    async def get_project_description(self) -> str:
        readme_file = os.path.join(self.folder_path, 'README.md')
        try:
            with open(readme_file, 'r', encoding='utf-8') as f:
                readme_content = f.read()
            proj_prompt = f"""
            Please read and summarise the content of the README file for the project {self.processor.repo}:
            {readme_content}

            Provide a concise summary that captures the main purpose and key features of the project.
            Return only the summary, without any additional commentary or details.
            """
            proj_msg = "Your task is to generate a clear and concise summary of the project's README file to describe the project effectively."
            project_description = await super().process_prompt(proj_prompt, proj_msg)
            return project_description
        except FileNotFoundError:
            return ""
    
    def get_all_files(self) -> List[str]:
        try:
            all_files = []
            for root, _, files in os.walk(self.folder_path):
                for file in files:
                    all_files.append(os.path.join(root, file))
            return all_files
        except Exception as e:
            error_message = f"Error occurred while retrieving all files local downloaded folder: {e}"
            self.log_errors(error_message, "get_all_files")
            raise
    
    async def run_embed_process(self, file: str) -> None:
        try:
            file_desc, func_desc_tuple = await self.generate_file_and_func_desc(file)
            repo_file = self.get_repo_file(file)
            if file_desc and func_desc_tuple: # files with functions
                for func, func_desc in func_desc_tuple:
                    func_code = func.func_code
                    description = f"""
                    Function Description: {func_desc}
                    File Description: {file_desc}
                    """
                    embedded_code = super().embed_text(func_code)
                    embedded_description = super().embed_text(description)
                    payload = {
                        "file": repo_file,
                        "function": func.func_name,
                        "code": func_code,
                        "file_description": file_desc,
                        "function_description": func_desc,
                    }
                    if embedded_code and embedded_description:
                        self.store_embedding(payload=payload, code=embedded_code, description=embedded_description)
                    elif embedded_code:
                        self.store_embedding(payload=payload, code=embedded_code)
                    elif embedded_description: 
                        self.store_embedding(payload=payload, description=embedded_description)

            elif file_desc: # files without functions
                async with aiofiles.open(file, 'r', encoding='utf-8') as f:
                    file_code = await f.read()
                
                embedded_code = super().embed_text(file_code)
                embedded_description = super().embed_text(file_desc)
                payload = {
                    "file": repo_file,
                    "code": file_code,
                    "file_description": file_desc
                }
                if embedded_code and embedded_description:
                    self.store_embedding(payload=payload, code=embedded_code, description=embedded_description)
                elif embedded_code:
                        self.store_embedding(payload=payload, code=embedded_code)
                elif embedded_description: 
                    self.store_embedding(payload=payload, description=embedded_description)
                    
        except Exception as e:
            error_message = f"Error occurred while running embed process for {file}: {e}"
            self.log_errors(error_message, "run_embed_process")
            raise
    
    async def generate_file_and_func_desc(self, file: str) -> Tuple[str, List[Tuple]]:
        try:
            if file.endswith('.yaml') or file.endswith('.yml'):
                file_desc = await self.generate_desc_for_yml_file(file)
                return (file_desc, [])
                
            else:
                # Generate function description within a python file
                provider = CodeContextProvider(file)
                func_dict = await provider.build_context()
                func_list = func_dict.get(file, [])
                if func_list: 
                    file_desc, func_desc = await self.process_files_with_functions(file, func_list)
                else:
                    file_desc, func_desc = await self.process_files_without_functions(file)
                
                return (file_desc, func_desc)
        except Exception as e:
            error_message = f"Error occurred while generating file and function descriptions for {file}: {e}"
            self.log_errors(error_message, "generate_file_and_func_desc")
            raise
    
    async def generate_desc_for_yml_file(self, file: str) -> str:
        try:            
            provider = CodeContextProvider(file) 
            yml_dict = provider.parse_yaml_file()
            file_desc = await self.generate_file_desc(file, yml_dict=yml_dict)
            return file_desc
        except Exception as e:
            error_message = f"Error occurred while generating description for {file}: {e}"
            self.log_errors(error_message, "generate_desc_for_yml_file")
            raise
    
    async def process_files_with_functions(self, file: str, func_list: List[Function]) -> Tuple[str, List[Tuple]]:
        try:
            all_func_desc = []
            for func in func_list:
                func_desc = await self.generate_func_desc(func)
                func_desc_tuple = (func, func_desc)
                all_func_desc.append(func_desc_tuple)

            # Use functions description to generate file description
            file_desc = await self.generate_file_desc(file, func_desc_tuple=all_func_desc)
            return (file_desc, all_func_desc)
        except Exception as e:
            error_message = f"Error occurred while processing {file} for indexing: {e}"
            self.log_errors(error_message, "process_files_with_functions")
            raise
    
    async def process_files_without_functions(self, file: str) -> Tuple[str, List[Tuple]]:
        try:
            file_desc = await self.generate_file_desc(file)
            return (file_desc, [])
        except Exception as e:
            error_message = f"Error occurred while processing {file} for indexing: {e}"
            self.log_errors(error_message, "process_files_without_functions")
            raise
    
    async def generate_func_desc(self, func: Function) -> str:
        try:
            desc_prompt = f"""
            The function {func.func_name} has the code below:
            {func.func_code}

            Project information:
            - Description: {self.project_description}
            - Structure: {self.project_structure}

            Based on the code and project information, provide a concise and clear description of what this function does and its purpose.
            Keep the description as concise and factual with no filler words.
            """
            desc_message = "You are an expert in analyzing Python functions. Your task is to accurately describe the functionality and purpose of the given function."
            desc_content = await super().process_prompt(desc_prompt, desc_message)
            return desc_content
        except Exception as e:
            error_message = f"Error occurred while generating description for {func.func_name}: {e}"
            self.log_errors(error_message, "generate_func_desc")
            raise   
    
    async def generate_file_desc(self, file: str, func_desc_tuple: List[Tuple] = None, yml_dict: Dict = None) -> str:
        try:
            if isinstance(yml_dict, dict): # yml file
                if yml_dict:
                    desc_prompt = f"""
                    The summary of the yml file {file} is:
                    {yml_dict}

                    Project information:
                    - Description: {self.project_description}
                    - Structure: 
                    {self.project_structure}

                    Based on the summary and project information, please provide a concise and clear description of what this file does and its purpose.
                    Keep the description as concise and factual with no filler words.
                    """
                    desc_message = "You are an expert in analyzing yml file. Your task is to accurately describe the functionality and purpose of the given file."
                else:
                    return ""
        
            else: # non yml file
                if func_desc_tuple:
                    full_func_description = ""
                    for _, func_description in func_desc_tuple:
                        full_func_description += func_description + "\n\n"

                    desc_prompt = f"""
                    The compiled description of functions in the file {file} is:
                    {full_func_description}

                    Project information:
                    - Description: {self.project_description}
                    - Structure:
                    {self.project_structure}

                    Based on the compiled description and project information, please provide a concise and clear description of what this file does and its purpose.
                    Keep the description as concise and factual with no filler words.
                    """
                else: # no functions in file
                    file_summary = await self.describe_large_file(file)

                    desc_prompt = f"""
                    The file {file} has the following summary:
                    {file_summary}

                    Project information:
                    - Description: {self.project_description}
                    - Structure: {self.project_structure}

                    Based on the file summary and project information, please provide a concise and clear description of what this file does and its purpose.
                    Keep the description as concise and factual with no filler words.
                    """
            
                desc_message = "You are an expert in analyzing Python file. Your task is to accurately describe the functionality and purpose of the given file."

            desc_content = await super().process_prompt(desc_prompt, desc_message)
            return desc_content
        except Exception as e:
            error_message = f"Error occurred while generating description for {file}: {e}"
            self.log_errors(error_message, "generate_file_desc")
            raise
    
    def get_repo_file(self, path: str) -> str:
        try:
            local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
            if path.startswith(local_folder):
                # Normalize to forward slashes for consistent splitting
                p = path.replace("\\", "/")
                parts = [part for part in p.split("/") if part]
                if len(parts) <= 1:
                    return ""
                return "/".join(parts[1:])
            else:
                return path
        except Exception:
            return path
    
    def store_embedding(self, payload: Dict[str, str], code: List[float] = None, description: List[float] = None) -> None:
        try:
            point_id = str(uuid.uuid4())
            self.save_id(point_id, payload)
            
            if code and description:
                self.qdrant_client.upsert(
                    collection_name=self.collection_name,
                    points=[
                        models.PointStruct(
                            id=point_id,
                            payload=payload,
                            vector={
                                "description": description,
                                "code": code,
                            },
                        ),
                    ]
                )
            elif code:
                self.qdrant_client.upsert(
                    collection_name=self.collection_name,
                    points=[
                        models.PointStruct(
                            id=point_id,
                            payload=payload,
                            vector={
                                "code": code,
                            },
                        ),
                    ]
                )
            elif description:
                self.qdrant_client.upsert(
                    collection_name=self.collection_name,
                    points=[
                        models.PointStruct(
                            id=point_id,
                            payload=payload,
                            vector={
                                "description": description,
                            },
                        ),
                    ]
                )
        except Exception as e:
            error_message = f"Error occurred while storing embeddings into Qdrant: {e}"
            self.log_errors(error_message, "store_embedding")
            raise
    
    def save_id(self, point_id: str, payload: Dict[str, str]) -> None:
        func_name = payload.get('function', '')
        file = payload.get('file', '')

        # Keep a record of point id of function/file 
        key = f"{file}_{func_name}" if func_name else file
        try:
            json_file = f"qdrant_id/qdrant_id_{self.processor.project}_{self.processor.repo}"
            with open(json_file, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        # Update the data with the new point_id
        data[key] = point_id
        with open(json_file, 'w') as f:
            json.dump(data, f, indent=4)
    
    def get_point_id(self, key: str) -> str:
        try:
            json_file = f"qdrant_id/qdrant_id_{self.processor.project}_{self.processor.repo}"
            with open(json_file, 'r') as f:
                data = json.load(f)
            return data.get(key, "")
        except Exception:
            return ""
    
    def check_point_exist(self, key: str) -> bool:
        try:
            point_id = self.get_point_id(key)
            if point_id:
                result = self.qdrant_client.retrieve(
                    collection_name=self.collection_name,
                    ids=[point_id],
                )
                # If the result contains the point, it exists
                return bool(result)
            else:
                return False
        except Exception:
            return False
    
    def chunk_code_by_lines(self, code: str, chunk_size: int = 500) -> List[str]:
        try:
            lines = code.splitlines()
            chunks = []
            for i in range(0, len(lines), chunk_size):
                chunk = "\n".join(lines[i:i + chunk_size])
                chunks.append(chunk)
            return chunks
        except Exception as e:
            error_message = f"Error occurred while chunking code: {e}"
            self.log_errors(error_message, "chunk_code_by_lines")
            raise

    async def describe_large_file(self, file: str) -> str:
        try:
            async with aiofiles.open(file, 'r', encoding='utf-8') as f:
                file_code = await f.read()
            chunks = self.chunk_code_by_lines(file_code)
            summaries = []

            for chunk in chunks:
                prompt = f"""
                Please provide a brief summary of the following code snippet in the file {file}:
                {chunk}

                Project information:
                - Description: {self.project_description}
                - Structure:
                {self.project_structure}
                """
                msg = "You are an expert in describing Python code snippets. Your task is to accurately describe the functionality and purpose of the given code."
                description = await super().process_prompt(prompt, msg)
                summaries.append(description)

            # Combine summaries
            combined_summary = "\n".join(summaries)

            return combined_summary
        except Exception as e:
            error_message = f"Error occurred while chunking and generating description for {file}: {e}"
            self.log_errors(error_message, "describe_large_files")
            raise
    
    @override
    def log_errors(self, error_message: str, function: str) -> None:
        self.console_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/code_index_builder.py",
            function=function
        )
        self.file_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/code_index_builder.py",
            function=function
        )