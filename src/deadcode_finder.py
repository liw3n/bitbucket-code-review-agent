import subprocess
import re
import os
from typing_extensions import List, Tuple, Set, Dict, override
from code_context_provider import CodeContextProvider
from pathlib import Path
from reviewer import Reviewer
from pull_request_processor import PullRequestProcessor
import time

class DeadcodeFinder(Reviewer):
    def __init__(self, processor: PullRequestProcessor) -> None:
        super().__init__(processor)
        self.codebase = f'code_for_review_{processor.repo}_{processor.pr_id}/'
        
        self.unused_pipelines_review = ['### Unused pipelines found\n']
        self.undefined_pipelines_review = ['### Undefined pipelines found\n']
        self.unused_nodes_review = ['### Unused nodes found\n']
        self.undefined_nodes_review = ['### Undefined nodes found\n']
        self.deadcode_review = ['### Deadcode found\n']
    
    def find_unused_code(self) -> List[str]:
        start_time = time.time()
        super().log_review_metrics('Generating deadcode reviews...')

        # Unused pipelines/nodes
        try:
            conf_dir = os.path.join(self.codebase, 'conf')
            # List all entries in the conf directory
            for country_name in os.listdir(conf_dir):
                folder_path = os.path.join(conf_dir, country_name)

                # Check if it is a directory
                if os.path.isdir(folder_path):
                    pipelines_file = os.path.join(folder_path, 'pipelines.yml')
                    pipelines_file = str(os.path.normpath(pipelines_file)).replace('\\', '/')
                    called_pipelines, all_pipelines = self.find_unused_pipelines(pipelines_file)
                    called_nodes, all_nodes = self.find_unused_nodes(called_pipelines, all_pipelines, folder_path)
                    self.simulate_call(called_nodes, all_nodes)
        except Exception as e:
            error_message = f"Error occurred while finding unused pipelines and nodes: {e}. Skipping unused pipelines/nodes review."
            self.log_errors(error_message, "find_unused_code")
        
        # Deadcode
        try:
            self.find_deadcode()
            review_content_list = super().join_reviews(
                self.unused_pipelines_review, self.undefined_pipelines_review,
                self.unused_nodes_review, self.undefined_nodes_review, self.deadcode_review
            )
            super().log_review_metrics("Finished generating deadcode review", start_time)
            return review_content_list[::-1] # reviews posted in correct sequence
        except Exception:
            error_message = f"Error occurred while finding deadcode: {e}. Skipping deadcode review."
            self.log_errors(error_message, "find_unused_code")
            return []
        
    def find_unused_pipelines(self, pipelines_file: str) -> Tuple[List[str], Dict[str, Set[str]]]:
        try:
            provider = CodeContextProvider(pipelines_file)
            pipelines_called = provider.get_called_pipelines() 
            all_pipelines = provider.get_defined_pipelines()
            all_pipelines_names = all_pipelines.keys()
            unused_pipelines = all_pipelines_names - pipelines_called
            unused_pipelines_list = list(unused_pipelines)
            undefined_pipelines = pipelines_called - all_pipelines_names
            undefined_pipelines_list = list(undefined_pipelines)

            local_file_path = Path(pipelines_file)
            repo_file_path = Path(*local_file_path.parts[1:])
            if unused_pipelines_list:
                summarised_unused_pipelines = (", ").join(unused_pipelines_list)
                subheader = f"#### Unused pipelines in `{repo_file_path}`: \n"
                review_content = subheader + summarised_unused_pipelines
                self.unused_pipelines_review.append(review_content)
            
            if undefined_pipelines_list:
                summarised_undefined_pipelines = (", ").join(undefined_pipelines_list)
                subheader = f"#### Undefined pipelines in `{repo_file_path}`: \n"
                review_content = subheader + summarised_undefined_pipelines
                self.undefined_pipelines_review.append(review_content)
            
            return (pipelines_called, all_pipelines)
        except Exception as e:
            error_message = f"Error occurred while finding unused piplines; {e}"
            self.log_errors(error_message, "find_unused_pipelines")
            raise

    def find_unused_nodes(self, called_pipelines: List[str], all_pipelines: Dict[str, Set[str]], folder: str) -> Tuple[List[str], Dict[str, str]]:
        try:
            nodes_called = set()
            for pipeline_name in called_pipelines:
                nodes = all_pipelines.get(pipeline_name)
                if nodes:
                    nodes_called = nodes_called.union(nodes)
            
            all_nodes = {}
            nodes_folder = os.path.join(folder, 'nodes')
            if os.path.isdir(nodes_folder):
                for subfolder in os.listdir(nodes_folder):
                    subfolder_path = os.path.join(nodes_folder, subfolder)
                    if os.path.isdir(subfolder_path): 
                        for node_file in os.listdir(subfolder_path):
                            node_file_path = os.path.join(subfolder_path, node_file)  
                            node_file_path = str(os.path.normpath(node_file_path)).replace('\\', '/')
                            provider = CodeContextProvider(node_file_path)
                            nodes = provider.get_defined_nodes()
                            all_nodes.update(nodes)
            
            all_nodes_names = set(all_nodes.keys())
            unused_nodes_name = all_nodes_names - nodes_called
            undefined_nodes_name = nodes_called - all_nodes_names
            unused_nodes_dict = {}
            undefined_nodes_dict = {}

            for undefined_node in undefined_nodes_name:
                undefined_node_file = os.path.join(folder, 'pipelines.yml')
                undefined_node_file = str(os.path.normpath(undefined_node_file)).replace('\\', '/')
                if undefined_node_file not in undefined_nodes_dict:
                    undefined_nodes_dict[undefined_node_file] = []
                undefined_nodes_dict[undefined_node_file].append(undefined_node)
            
            for file, undefined_nodes in undefined_nodes_dict.items():
                subheader = f"#### Undefined nodes in `{file}`:\n"
                joined_undefined_nodes = (", ").join(undefined_nodes)
                review = subheader + joined_undefined_nodes
                self.undefined_nodes_review.append(review)

            for unused_node in unused_nodes_name:
                unused_node_file = all_nodes.get(unused_node, {}).get('file')
                if unused_node_file not in unused_nodes_dict:
                    unused_nodes_dict[unused_node_file] = []
                unused_nodes_dict[unused_node_file].append(unused_node)
            
            for file, unused_nodes in unused_nodes_dict.items():
                subheader = f"#### Unused nodes in `{file}`:\n"
                joined_unused_nodes = (", ").join(unused_nodes)
                review = subheader + joined_unused_nodes
                self.unused_nodes_review.append(review)
            
            return (nodes_called, all_nodes)
        except Exception as e:
            error_message = f"Error occurred while finding unused nodes: {e}"
            self.log_errors(error_message, "find_unused_nodes")
            raise

    def find_deadcode(self) -> None:
        try:
            deadcode_dict = {}
            # Use modified deadcode submodule
            result = subprocess.run(['deadcode', self.codebase], capture_output=True, text=True)
            lines = result.stdout.splitlines()
            for line in lines:
                split_content = line.split(':')
                if len(split_content) == 4:
                    filename, line_num, col_num, content = split_content
                    content = content + f" (line {line_num}, col {col_num}\\)"

                    # if 'Function' in content: # only extract unused functions
                    folder = os.path.dirname(filename)
                    file = os.path.basename(filename)

                    # Add review by folder
                    if folder not in deadcode_dict:
                        deadcode_dict[folder] = {}
                    if file not in deadcode_dict[folder]:
                        deadcode_dict[folder][file] = []
                    
                    content = f"`{file}`:" + content
                    deadcode_dict[folder][file].append(self.clean_text(content))

            sorted_review_list = {}
            for folder, file_reviews in deadcode_dict.items():
                sorted_file_reviews = dict(sorted(file_reviews.items(), key=lambda x: len(x[1]), reverse=True))
                sorted_review_list[folder] = sorted_file_reviews
            
            # Sort folder by decreasing total count
            sorted_review_list = dict(sorted(
                sorted_review_list.items(),
                key=lambda x: sum(len(file_list) for file_list in x[1].values()),
                reverse=True
            ))
            reviews_by_folder = self.join_deadcode_reviews(sorted_review_list)
            self.deadcode_review.extend(reviews_by_folder)
        except Exception as e:
            error_message = f"Error occurred while finding dead code: {e}"
            self.log_errors(error_message, "find_deadcode")
            raise
    
    def simulate_call(self, called_nodes: List[str], all_nodes: Dict) -> None:
        try:
            local_folder = f'code_for_review_{self.processor.repo}_{self.processor.pr_id}'
            call_path = os.path.join(local_folder, 'simulate_call.py')

            # Write file to call node functions
            for node in called_nodes:
                node_info = all_nodes.get(node, {})
                func = node_info.get('func')
                if func:
                    _, func_name = func.rsplit('.', 1)
                    inputs = node_info.get('inputs')
                    params = (", ").join(inputs)
                    function_call = f"{func_name}({params})\n"

                    with open(call_path, 'a', encoding='utf-8') as f:
                        f.write(function_call)
        except Exception as e:
            error_message = f"Error occurred while simulating function call: {e}"
            self.log_errors(error_message, "simulate_call")
            raise
    
    def clean_text(self, string: str) -> str:
        try:
            # Remove ANSI escape sequences
            ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
            cleaned_string = ansi_escape.sub('', string)
            # Remove patterns like DC, DC01, DC02, ..., DC13
            pattern = re.compile(r'DC(0[1-9]|1[0-3])?')
            return pattern.sub('', cleaned_string) 
        except Exception:
            return string
    
    def join_deadcode_reviews(self, sorted_review_list: dict) -> List[str]:
        try:
            reviews_by_folder = []
            # Join reviews of each folder
            for folder, file_reviews in sorted_review_list.items():
                total_count = sum(len(file_list) for file_list in file_reviews.values())
                review_content = f"#### `{folder}`: {total_count} deadcode instance(s) found\n"
                for content_list in file_reviews.values():
                    for content in content_list:
                            review_content += content + "\n"

                reviews_by_folder.append(review_content)
            
            return reviews_by_folder
        except Exception as e:
            error_message = f"Error occurred while joining dead code reviews: {e}"
            self.log_errors(error_message, "join_deadcode_reviews")
            raise
    
    @override
    def log_errors(self, error_message: str, function: str) -> None:
        self.console_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/deadcode_finder.py",
            function=function
        )
        self.file_logger.exception(
            error_message,
            pull_request=(self.processor.project, self.processor.repo, self.processor.pr_id),
            file="src/deadcode_finder.py",
            function=function
        )