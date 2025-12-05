from typing_extensions import TypedDict, List, Dict
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime
import asyncio
import shutil
from structlog._config import BoundLoggerLazyProxy
import time
from logger_config import console_logger, file_logger
from pull_request_processor import PullRequestProcessor
from unit_test_reviewer import UnitTestReviewer
from documentation_reviewer import DocumentationReviewer
from logic_reviewer import LogicReviewer
from function import Function
from code_index_builder import CodeIndexBuilder
from deadcode_finder import DeadcodeFinder
from dotenv import load_dotenv
import psycopg2
import os

class AgentState(TypedDict):
    modified_func_dict: Dict[str, List[Function]]
    existing_test_dict: Dict[Function, str]
    missing_test_dict: Dict[str, str]
    file_doc_dict: Dict[str, str]
    func_doc_dict: Dict[str, str]
    logic_review: List[str]
    deadcode_review: List[str]
    processor: PullRequestProcessor
    duration: List[float]
    total_tokens: int
    test_folder: str
    language: str
    indexing: bool
    deadcode: bool
    agent_files: List[str]

class Context(TypedDict):
    pr_id: int
    project: str
    repo: str
    console_logger: BoundLoggerLazyProxy
    file_logger: BoundLoggerLazyProxy

###### Helper Functions 
async def download_docs(processor: PullRequestProcessor, doc_folder: str, runtime: Runtime[Context]) -> None:
    try:
        doc_files = processor.get_files(doc_folder)
        agent_files = [file for file in doc_files if '.instructions' in file or '.agents' in file]
        download_tasks = []
        for file in agent_files:
            task = processor.download_file_content(file)
            download_tasks.append(task)
        await asyncio.gather(*download_tasks)
    except Exception as e:
        error_message = f"Error downloading agent files in doc folder: {e}. Skipping instructions from agent files."
        log_errors(error_message, runtime, "download_docs")
        return

def log_metrics(task: str, runtime: Runtime[Context], state: AgentState, start_time: float = None, total_tokens: int = None) -> None:
    pr_id = runtime.context.get("pr_id", "")
    project = runtime.context.get("project", "")
    repo = runtime.context.get("repo", "")
    console_logger = runtime.context.get("console_logger")
    file_logger = runtime.context.get("file_logger")

    try:
        duration = state["duration"]
    except KeyError:
        state["duration"] = []

    if start_time and total_tokens:
        duration = time.time() - start_time
        state["duration"].append(duration)
        state["total_tokens"] = total_tokens

        console_logger.info(
            task,
            pull_request=(project, repo, pr_id),
            duration=f"{duration:.2f} seconds",
            tokens_used=total_tokens
        )
        file_logger.info(
            task,
            pull_request=(project, repo, pr_id),
            duration=f"{duration:.2f} seconds",
            tokens_used=total_tokens
        )
    elif start_time:
        duration = time.time() - start_time
        state["duration"].append(duration)

        console_logger.info(
            task,
            duration=f"{duration:.2f} seconds",
            pull_request=(project, repo, pr_id)
        )
        file_logger.info(
            task,
            duration=f"{duration:.2f} seconds",
            pull_request=(project, repo, pr_id)
        )
    else:
        console_logger.info(
            task,
            pull_request=(project, repo, pr_id)
        )
        file_logger.info(
            task,
            pull_request=(project, repo, pr_id)
        )

def log_errors(error_message: str, runtime: Runtime[Context], function: str) -> None:
    pr_id = runtime.context.get("pr_id", "")
    project = runtime.context.get("project", "")
    repo = runtime.context.get("repo", "")
    file = "src/review_code.py"
    console_logger = runtime.context.get("console_logger")
    file_logger = runtime.context.get("file_logger")

    console_logger.exception(
        error_message, 
        pull_request=(project, repo, pr_id),
        file=file,
        function=function
    )
    file_logger.exception(
        error_message, 
        pull_request=(project, repo, pr_id),
        file=file,
        function=function
    )

def save_metrics(
        project: str, repo: str, pr_id: int, 
        total_duration: float, total_tokens: int, num_files: int, 
        indexing: bool, deadcode: bool
) -> None:
    try:
        load_dotenv()
        conn = psycopg2.connect(
            dbname=os.environ.get('POSTGRES_DB'),
            user=os.environ.get('POSTGRES_USER'),
            password=os.environ.get('POSTGRES_PASSWORD'),
            host='sentinel_db',
            port='5432'
        )
        cursor = conn.cursor()

        # Create the metrics table if it doesn't exist
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS review_metrics (
                project TEXT,
                repo TEXT,
                pr_id INTEGER,
                run_id INTEGER DEFAULT 0,
                duration INTEGER DEFAULT 0,
                tokens INTEGER DEFAULT 0,
                num_files INTEGER DEFAULT 0,
                indexing BOOLEAN DEFAULT False,
                deadcode BOOLEAN DEFAULT False
            )
            """
        )

        cursor.execute(
            """
            SELECT 1 FROM review_metrics 
            WHERE project = %s AND repo = %s AND pr_id = %s
            """,
            (project, repo, pr_id)
        )
        exists = cursor.fetchone()
        last_row_id = 0
        if exists:
            # Get the latest run
            cursor.execute(
                """
                SELECT run_id FROM review_metrics
                WHERE project = %s AND repo = %s AND pr_id = %s
                """,
                (project, repo, pr_id)
            )
            rows = cursor.fetchall()
            last_row_id = max((row[0] for row in rows), default=0)

        row_id = last_row_id + 1
        # Insert row
        cursor.execute(
            """
            INSERT INTO review_metrics (project, repo, pr_id, run_id, duration, tokens, num_files, indexing, deadcode)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, 
            (project, repo, pr_id, row_id, total_duration, total_tokens, num_files, indexing, deadcode)
        )

        conn.commit()
        conn.close()
    except Exception as e:
        console_logger.exception(
            f"Error occurred while saving review metrics to postgres db: {e}",
            pull_request=(project, repo, pr_id),
            file="src/review_code.py",
            function="save_metrics"
        )
        file_logger.exception(
            f"Error occurred while saving review metrics to postgres db: {e}",
            pull_request=(project, repo, pr_id),
            file="src/review_code.py",
            function="save_metrics"
        )
        raise

def consolidate_reviews(
    modified_func_dict: Dict[str, List[Function]], file_doc_dict: Dict[str, str],
    func_doc_dict: Dict[Function, str], existing_test_dict: Dict[Function, str],
    missing_test_dict: Dict[str, str]
) -> List[str]:
    full_review_list = []
    word_limit = 30000
    for file, modified_func_list in modified_func_dict.items():
        # File level reviews
        file_header = f"## Review of `{file}`\n" + ("-" * 40) + "\n"

        full_review = ""
        file_doc_review = file_doc_dict.get(file, "")
        if file_doc_review:
            full_review += file_doc_review + "\n"
        missing_test_review = missing_test_dict.get(file, "")
        if missing_test_review:
            full_review += missing_test_review + "\n"
        
        if full_review:
            full_review = file_header + full_review + "\n" + ("-" * 40) + "\n"

        # Function level reviews
        for func in modified_func_list:
            func_header = f"### Review of `{func.func_name}` function \n"
            
            func_review = ""
            func_doc_review = func_doc_dict.get(func, "")
            if func_doc_review:
                func_review += func_doc_review + "\n"
            existing_test_review = existing_test_dict.get(func, "")
            if existing_test_review:
                func_review += existing_test_review + "\n"
            
            if func_review:
                func_review = func_header + func_review + "\n" + ("-" * 40) + "\n"

            if len(full_review) + len(func_review) > word_limit:
                full_review_list.append(full_review)
                full_review = file_header + func_review + "\n"
            else:
                full_review += func_review + "\n"
        
        if full_review and len(full_review) <= word_limit:
            full_review_list.append(full_review)

    return full_review_list

###### Code Review Nodes 
async def set_up_config(state: AgentState, runtime: Runtime[Context]) -> AgentState:
    log_metrics("Setting up configuration...", runtime, state)
    start_time = time.time()

    pr_id = runtime.context.get("pr_id", "")
    project = runtime.context.get("project", "")
    repo = runtime.context.get("repo", "")
    processor = PullRequestProcessor(project, repo, pr_id)
    state['processor'] = processor
    try:
        language, test_folder, indexing, deadcode, doc_folder = await processor.process_config_file()
        state['language'] = language
        state['test_folder'] = test_folder
        state['indexing'] = indexing
        state['deadcode'] = deadcode

        if language and language.lower() != 'python':
            raise ValueError("Code review process cannot run on non-python codebases.")

        if doc_folder:
            try:
                doc_files = processor.get_files(doc_folder)
                agent_files = [file for file in doc_files if '.instructions' in file or '.agents' in file]
                download_tasks = []
                full_agent_files = []
                for file in agent_files:
                    full_path = os.path.join(doc_folder, file)
                    full_agent_files.append(full_path)
                    task = processor.download_file_content(full_path)
                    download_tasks.append(task)
                await asyncio.gather(*download_tasks)
                state['agent_files'] = full_agent_files
            except Exception as e:
                error_message = f"Error occurred while downloading agent files: {e}. Skipping instructions from agent files."
                log_errors(error_message, runtime, "set_up_config")
                raise
    except Exception as e:
        error_message = f"Error occurred while setting up configuration: {e}"
        log_errors(error_message, runtime, "set_up_config")
        await processor.post_reviews(f"Sentinel code review process failed. {error_message}", feedback = False)
        raise
    else:
        log_metrics("Finished setting up configuration.", runtime, state, start_time)
        return state

async def process_diff(state: AgentState, runtime: Runtime[Context]) -> AgentState:
    log_metrics("Processing diff in pull request for code review...", runtime, state)
    start_time = time.time()
    processor = state['processor']

    try:
        test_folder = state.get('test_folder', '')
        language = state.get('language', '')
                
        diff_dict = processor.get_diff()
        if test_folder:
            modified_func_dict = await processor.get_modified_functions(diff_dict, test_folder)
        else:
            try: # default to downloading from 'tests/' folder
                modified_func_dict = await processor.get_modified_functions(diff_dict, 'tests/')
            except Exception:
                if language:
                    raise ValueError("'test_folder' path is empty or not specified in the sentinel-config YAML file.") from None
                else:
                    raise ValueError("sentinel-config YAML file is missing or empty. The required 'language' and 'test_folder' parameters are not set.") from None
        
        state['modified_func_dict'] = modified_func_dict
    except ValueError as e:
        log_errors(str(e), runtime, "process_diff")
        documentation_link = "https://github.com/liw3n/bitbucket-code-review-agent/blob/main/docs/documentation/user_documentation.md"
        comment = f"Sentinel code review process failed. Ensure that sentinel-config.yaml file is located in the repository root and contains all required configuration values. Refer to documentation [here]({documentation_link})."
        await processor.post_reviews(comment, feedback = False)
        raise
    except Exception as e:
        error_message = f"Error occurred while processing diff: {e}"
        log_errors(error_message, runtime, "process_diff")
        await processor.post_reviews(f"Sentinel code review process failed. {error_message}", feedback = False)
        raise
    else:
        log_metrics("Finished processing diff in pull request for code review", runtime, state, start_time)
        return state

async def download_repo(state: AgentState, runtime: Runtime[Context]) -> AgentState:
    log_metrics('Downloading repository...', runtime, state)
    start_time = time.time()
    processor = state['processor']

    try:
        await processor.download_all_files()
        log_metrics('Finished downloading repository.', runtime, state, start_time)
        return state
    except Exception as e:
        error_message = f"Error occurred while downloading repository: {e}"
        log_errors(error_message, runtime, "download_repo")
        await processor.post_reviews(f"Sentinel code review process failed. {error_message}", feedback = False)
        raise
        
async def process_index(state: AgentState, runtime: Runtime[Context]) -> AgentState:
    log_metrics("Creating or updating code index...", runtime, state)
    start_time = time.time()
    processor = state['processor']
    try:
        modified_func_dict = state['modified_func_dict']
        builder = CodeIndexBuilder(processor, modified_func_dict)
        await builder.embed_and_store_codebase()
    except Exception as e:
        error_message = f"Error occurred while processing code index: {e}"
        log_errors(error_message, runtime, "process_index")
        await processor.post_reviews(f"Sentinel code review process failed. {error_message}. Consider setting `indexing: false` in `sentinel-config.yaml` file to skip indexing process.", feedback = False)
        raise
    else:
        log_metrics("Finished creating or updating code index", runtime, state, start_time)
        return state

async def generate_code_reviews(state: AgentState, runtime: Runtime[Context]) -> AgentState:
    log_metrics("Generating code reviews...", runtime, state)
    start_time = time.time()
    processor = state['processor']
    try:
        modified_func_dict = state['modified_func_dict']
        processor = state['processor']
        indexing = state.get('indexing', False)
        agent_files = state.get('agent_files', [])

        # Documentation Review
        doc_reviewer = DocumentationReviewer(modified_func_dict, processor, agent_files, indexing)
        documentation_task = doc_reviewer.review_documentation()

        # Unit Test Review
        test_reviewer = UnitTestReviewer(modified_func_dict, processor, agent_files, indexing)
        test_task = test_reviewer.review_test()

        # Logic Review
        logic_reviewer = LogicReviewer(processor, agent_files, indexing)
        logic_task = logic_reviewer.review_logic()

        # Run all reviews async
        (file_doc_dict, func_doc_dict), (existing_test_dict, missing_test_dict), logic_review = await asyncio.gather(
            documentation_task,
            test_task,
            logic_task
        )

        state['file_doc_dict'] = file_doc_dict
        state['func_doc_dict'] = func_doc_dict
        state['existing_test_dict'] = existing_test_dict
        state['missing_test_dict'] = missing_test_dict
        state['logic_review'] = logic_review

        total_tokens = test_reviewer.total_tokens + doc_reviewer.total_tokens + logic_reviewer.total_tokens
    except Exception as e:
        error_message = f"Error occurred while generating code reviews: {e}"
        log_errors(error_message, runtime, "generate_code_reviews")
        await processor.post_reviews(f"Sentinel code review process failed. {error_message}", feedback = False)
        raise
    else:
        log_metrics("Finished generating all code reviews", runtime, state, start_time, total_tokens)
        return state

async def generate_deadcode_reviews(state: AgentState, runtime: Runtime[Context]) -> AgentState:
    log_metrics('Generating deadcode reviews...', runtime, state)
    start_time = time.time()
    processor = state['processor']
    try:
        finder = DeadcodeFinder(processor)
        deadcode_review = finder.find_unused_code()
        state['deadcode_review'] = deadcode_review
    except Exception as e:
        error_message = f"Error occurred while generating dead code reviews: {e}"
        log_errors(error_message, runtime, "generate_deadcode_reviews")
        await processor.post_reviews(f"Sentinel code review process failed. {error_message}", feedback = False)
        raise
    else:
        log_metrics('Finished generating deadcode reviews.', runtime, state, start_time) 
        return state

async def post_reviews(state: AgentState, runtime: Runtime[Context]) -> AgentState:
    try:
        log_metrics("Consolidating and posting reviews to pull request...", runtime, state)
        start_time = time.time()

        processor = state['processor']    
        existing_test_dict = state['existing_test_dict']
        missing_test_dict = state['missing_test_dict']
        file_doc_dict = state['file_doc_dict']
        func_doc_dict = state['func_doc_dict']
        logic_review = state['logic_review']
        modified_func_dict = state['modified_func_dict']
        deadcode_review = state.get('deadcode_review', [])
        
        # Consolidate doicumentation and test comments
        full_review_list = consolidate_reviews(modified_func_dict, file_doc_dict, func_doc_dict, existing_test_dict, missing_test_dict)
        
        # Post comments
        post_tasks = []
        for review in deadcode_review:
            task = processor.post_reviews(review)
            post_tasks.append(task)
        for review in full_review_list:
            task = processor.post_reviews(review)
            post_tasks.append(task)
        for review in logic_review:
            task = processor.post_reviews(review)
            post_tasks.append(task)
        await asyncio.gather(*post_tasks)

    except Exception as e:
        error_message = f"Error occurred while posting reviews: {e}"
        log_errors(error_message, runtime, "post_reviews")
        await processor.post_reviews(f"Sentinel code review process failed. {error_message}", feedback = False)
        raise
    else:
        log_metrics("Reviews successfully posted to pull request.", runtime, state, start_time)
        processor.update_pr_status()

        # Summary logs
        pr_id = runtime.context.get("pr_id", "")
        project = runtime.context.get("project", "")
        repo = runtime.context.get("repo", "")
        duration = state["duration"]
        total_duration = sum(duration)
        total_tokens = state["total_tokens"]
        modified_func_dict = state["modified_func_dict"]
        files = modified_func_dict.keys()
        num_files = len(files)
        indexing = state.get('indexing', False)
        deadcode = state.get('deadcode', False)
        save_metrics(project, repo, pr_id, total_duration, total_tokens, num_files, indexing, deadcode)

        console_logger = runtime.context.get("console_logger")
        file_logger = runtime.context.get("file_logger")
        console_logger.info(
            "Code review processed successfully", 
            pull_request=(project, repo, pr_id),
            total_duration = f"{total_duration:.2f} seconds",
            total_tokens_used = total_tokens
        )
        file_logger.info(
            "Code review processed successfully", 
            pull_request=(project, repo, pr_id),
            total_duration = f"{total_duration:.2f} seconds",
            total_tokens_used = total_tokens
        )
        return state

##### Conditional edge
def check_download_type(state: AgentState) -> str:
    deadcode = state.get('deadcode', False)
    if deadcode:
        return "download_repo"
    else:
        return "process_diff"
    
def check_indexing(state: AgentState) -> str:
    indexing = state.get('indexing', False)
    if indexing:
        return "process_index"
    else:
        return "generate_code_reviews"

def check_deadcode(state: AgentState) -> str:
    deadcode = state.get('deadcode', False)
    if deadcode:
        return "generate_deadcode_reviews"
    else:
        return "post_reviews"

##### Main functions
async def build_code_review_graph(pr_id, repo, project, console_logger, file_logger):
    try:
        builder = StateGraph(state_schema=AgentState, context_schema=Context)

        # Add nodes and edges
        builder.add_node(set_up_config)
        builder.add_node(process_diff)
        builder.add_node(download_repo)
        builder.add_node(process_index)
        builder.add_node(generate_code_reviews)
        builder.add_node(generate_deadcode_reviews)
        builder.add_node(post_reviews)

        builder.add_edge(START, 'set_up_config')
        builder.add_conditional_edges('set_up_config', check_download_type)
        builder.add_edge('download_repo', 'process_diff')
        builder.add_conditional_edges('process_diff', check_indexing)
        builder.add_edge('process_index', 'generate_code_reviews')
        builder.add_conditional_edges('generate_code_reviews', check_deadcode)
        builder.add_edge('generate_deadcode_reviews', 'post_reviews')
        builder.add_edge('post_reviews', END)

        graph = builder.compile()
        state = {}
        context = {
            "pr_id": pr_id,
            "project": project,
            "repo": repo,
            "console_logger": console_logger,
            "file_logger": file_logger
        }
        await graph.ainvoke(state, context=context)
    except Exception as e:
        console_logger.exception(
            f"Error occurred while building review graph : {e}", 
            pull_request=(project, repo, pr_id),
            file="src/review_code.py",
            function="build_code_review_graph"
        )
        file_logger.exception(
            f"Error occurred while building review graph : {e}", 
            pull_request=(project, repo, pr_id),
            file="src/review_code.py",
            function="build_code_review_graph"
        )
        raise
    finally:
        def clean_up(path):
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass

        local_path = f'code_for_review_{repo}_{pr_id}'
        clean_up(local_path)
