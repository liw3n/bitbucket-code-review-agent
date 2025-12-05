from dotenv import load_dotenv
import os
from openai import AsyncAzureOpenAI
from openai.types.chat.chat_completion import ChatCompletion
from pull_request_processor import PullRequestProcessor
from typing_extensions import Optional, List, Union
import time
from logger_config import console_logger, file_logger
import asyncio
from qdrant_client import QdrantClient, models
import requests
from requests.exceptions import HTTPError

class Reviewer:
    def __init__(self, processor: PullRequestProcessor, agent_files: List[str]) -> None:
        load_dotenv()
        self.llm_client = AsyncAzureOpenAI(
            api_version="2024-06-01",
            api_key=os.environ["OPENAI_KEY"],
            azure_endpoint=os.environ["OPENAI_ENDPOINT"]
        )
        self.prev_tokens = 0
        self.total_tokens = 0
        self.qdrant_client = QdrantClient(url=os.environ["QDRANT_ENDPOINT"])
        self.embed_url = os.environ["OLLAMA_ENDPOINT"] 
        self.processor = processor
        self.agent_files = agent_files
        self.agent_content = ""
        self.console_logger = console_logger
        self.file_logger = file_logger

    async def process_prompt(self, prompt: str, system_message: str) -> str:
        try:
            response = await asyncio.wait_for(
                self.llm_client.chat.completions.create(
                    model="gpt-4o-mini",
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": prompt}
                    ],
                ),
                timeout=90
            )
            self.check_token_limit(response)
            response_content = self.get_response_content(response)
            return response_content
        except asyncio.TimeoutError:
            self.console_logger.exception(
                "Timeout occurred while processing prompt",
                pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
                file="src/reviewer.py",
                function="process_prompt"
            )
            self.file_logger.exception(
                "Timeout occurred while processing prompt",
                pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
                file="src/reviewer.py",
                function="process_prompt"
            )
            raise asyncio.TimeoutError("Timeout occurred after 90s while processing prompt") from None
        except Exception as e:
            self.console_logger.exception(
                f"Error occurred while processing prompt: {e}",
                pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
                file="src/reviewer.py",
                function="process_prompt"
            )
            self.file_logger.exception(
                f"Error occurred while processing prompt: {e}",
                pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
                file="src/reviewer.py",
                function="process_prompt"
            )
            raise
    
    async def enhance_prompt_with_config(self, original_prompt: str) -> str:
        if not self.agent_files:
            return original_prompt
        
        try:
            if not self.agent_content:
                local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
                config_context = ""
                for file in self.agent_files:
                    local_config_file = os.path.join(local_folder, file)
                    with open(local_config_file, "r", encoding="utf-8") as f:
                        new_config_context = f.read()
                        config_context += new_config_context + "\n"
                self.agent_content = config_context
            
            prompt = f"""
            These are configuration instructions and context for a Bitbucket repository:
            {self.agent_content}

            Reviewer prompt to expand:
            {original_prompt}

            Task:
            - Choose the most relevant context above and rewrite the reviewer prompt into a single, paste-ready prompt.

            Requirements:
            - Provide as much information in the enhanced prompt deemed suitable.
            - Do not provide the answer to the prompt here.
            - Output only the complete, paste-ready enhanced prompt text that a reviewer would use.
            - Ensure that the enhanced prompt is as detailed as possible.
            - Do not include explanations, commentary, or any extra content beyond the enhanced prompt.
            """
            system_message = (
                "You are an expert prompt engineer. Select the most relevant repo context and produce "
                "a concise, paste-ready reviewer prompt. Output only the enhanced prompt text; no "
                "answers or commentary."
            )
            enhanced_prompt = await self.process_prompt(prompt, system_message)
            return enhanced_prompt
        except Exception as e:
            error_message = f"Error occurred while enhancing prompt with agent file: {e}. Defaulting to original prompt."
            self.console_logger.exception(
                error_message,
                pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
                file="src/reviewer.py",
                function="enhance_prompt_with_config"
            )
            self.file_logger.exception(
                error_message,
                pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
                file="src/reviewer.py",
                function="enhance_prompt_with_config"
            )
            return original_prompt
    
    def query_points(self, query: str) -> List:
        try:
            collection_name = f'embeddings_for_{self.processor.project}_{self.processor.repo}'
            embedded_query = self.embed_text(query)

            try:
                code_hits = self.qdrant_client.query_points(
                    collection_name,
                    query=embedded_query,
                    using="code",
                    limit=5,
                ).points
            except Exception:
                code_hits = []
                
            try:
                desc_hits = self.qdrant_client.query_points(
                    collection_name,
                    query=embedded_query,
                    using="description",
                    limit=5,
                ).points
            except Exception:
                desc_hits = []

            top_hits = self.filter_hits(code_hits=code_hits, desc_hits=desc_hits)
            return top_hits
        except Exception as e:
            error_message = f"Error occurred while querying Qdrant points: {e}"
            self.log_errors(error_message, "query_points")
            raise
    
    def filter_hits(self, code_hits: List, desc_hits: List) -> List:
        try:
            code_hits_id = [hit.id for hit in code_hits]
            desc_hits_id = [hit.id for hit in desc_hits]
            overlaps = [hit_id for hit_id in desc_hits_id if hit_id in code_hits_id]

            if len(overlaps) < 5:
                remaining_desc = [hit.id for hit in desc_hits if hit.id not in overlaps]
                overlaps = overlaps + remaining_desc
            
            payload = []
            for hit in desc_hits:
                if hit.id in overlaps:
                    hit_payload = hit.payload
                    payload.append(hit_payload)
            return payload
        except Exception:
            return desc_hits
    
    def embed_text(self, text_to_embed: Union[str, List[str]], model: str = "nomic-embed-text:latest") -> Optional[List[float]]:
        try:
            # Use ollama to embed
            # cert_path = './.venv/Lib/site-packages/certifi/cacert.pem'
            response = requests.post(
                self.embed_url,
                json={
                    "model": model,
                    "input": text_to_embed
                },
                verify=False
            )
            response.raise_for_status()
            result = response.json()
            embeddings = result.get('embeddings', [])
            if len(embeddings) == 1:
                return embeddings[0]
            else:
                return embeddings
        except HTTPError:
            error_message = f"An HTTPError occurred while embedding text: {response.text}"
            self.log_errors(error_message, "embed_text")
            raise
    
    def check_token_limit(self, response: Optional[ChatCompletion] = None) -> None:
        if response:
            response_tokens = response.usage.total_tokens
            self.total_tokens = self.total_tokens + response_tokens

        if self.total_tokens > 5000000:
            raise Exception("Tokens exceeded 0.5 million. Stopping execution")
        
    def get_response_content(self, response: ChatCompletion) -> str:
        return response.choices[0].message.content
    
    def join_reviews(self, *review_list: List[str]) -> List[str]:
        try:
            word_limit = 30000
            current_review_list = []
            
            review_content = ""
            for content_list in review_list:
                header = content_list[0]
                if content_list[1:]: # check if there is other reviews besides header
                    review_content += header
                    for content in content_list[1:]:
                        if len(review_content) + len(content) > word_limit:
                            review_content = review_content + "\n" + ("-" * 40)
                            current_review_list.append(review_content)
                            review_content = header

                        review_content += content + "\n\n"
                        
                    # Add divider at the end of reviews
                    review_content += "\n" + ("-" * 40) + "\n"
            
            # Check if there is review content that is below word limit
            if review_content and len(review_content) <= word_limit:
                current_review_list.append(review_content)

            return current_review_list
        except Exception as e:
            error_message = f"Error occurred while joining reviews: {e}"
            self.log_errors(error_message)
            raise
    
    def generate_directory_structure(self, file: str) -> str:
        # Initialize the structure string with dir name
        dir_name = str(os.path.dirname(file))
        if dir_name:
            structure = f"{dir_name}/\n"
            files = self.processor.get_files(dir_name)
        else:
            structure = "root/\n"
            files = [file]

        # Track the previous path for indentation
        prev_levels = []

        for filename in files:
            parts = filename.split('/')
            # Determine how many levels are shared with previous
            common_length = 0
            for prev, curr in zip(prev_levels, parts):
                if prev == curr:
                    common_length += 1
                else:
                    break

            # Build indentation based on the depth
            indent = '    ' * common_length

            # Add remaining parts as folders or files
            for i in range(common_length, len(parts)):
                if i == len(parts) - 1:
                    # Last part: file
                    structure += f"{indent}| ---- {parts[i]}\n"
                else:
                    # Folder
                    structure += f"{indent}| ---- {parts[i]}/\n"
                    indent += '    '
            prev_levels = parts

        return structure
    
    def log_review_metrics(self, task: str, start_time: float = None):
        pr_id = self.processor.pr_id
        repo = self.processor.repo
        project = self.processor.project
        tokens_used = self.total_tokens - self.prev_tokens
        self.prev_tokens = self.total_tokens

        if start_time:
            duration = time.time() - start_time

            if tokens_used:
                self.console_logger.info(
                    "%s", task,
                    duration = f"{duration:.2f} seconds", 
                    tokens_used = tokens_used,
                    pull_request=(project, repo, pr_id)
                )
                self.file_logger.info(
                    "%s", task,
                    duration = f"{duration:.2f} seconds", 
                    tokens_used = tokens_used,
                    pull_request=(project, repo, pr_id)
                )
            else:
                self.console_logger.info(
                    "%s", task,
                    duration = f"{duration:.2f} seconds",
                    pull_request=(project, repo, pr_id)
                )
                self.file_logger.info(
                    "%s", task,
                    duration = f"{duration:.2f} seconds",
                    pull_request=(project, repo, pr_id)
                )
        else:
            self.console_logger.info(
                "%s", task,
                pull_request=(project, repo, pr_id)
            )
            self.file_logger.info(
                "%s", task,
                pull_request=(project, repo, pr_id)
            )
    
    def get_context(self, document: str) -> str:
        try:
            code_context = self.query_points(document)
            description = []
            for context in code_context:
                file = context.get('file', '')
                file_desc = context.get('file_description', '')
                function = context.get('function', '')
                func_desc = context.get('function_description', '')
                combined_desc = f"{file}: {file_desc}\n" + f"{function}: {func_desc}"
                description.append(combined_desc)
            full_desc = ("\n").join(description)
            return full_desc
        except Exception as e:
            error_message = f"Error occurred while fetching context: {e}"
            self.log_errors(error_message, "get_context")
            raise
    
    def log_errors(self, error_message: str, function: str) -> None:
        self.console_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/reviewer.py",
            function=function
        )
        self.file_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/reviewer.py",
            function=function
        )