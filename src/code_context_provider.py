import tree_sitter_python as tspython
from tree_sitter import Language, Parser, Query, QueryCursor, Tree, Node
import yaml
from function import Function
from line import Line
from typing_extensions import List, Tuple, Optional, Dict, Set, Union
import textwrap
import asyncio
from docstring import Docstring
from pathlib import Path
import os

class CodeContextProvider:
    def __init__(self, codebase_path: str) -> None:
        self.language = Language(tspython.language())
        self.parser = Parser(self.language)
        self.codebase_path = codebase_path
        self.codebase_dict = {}

    async def build_context(self) -> Dict[str, List[Function]]:
        try:
            with open(self.codebase_path, 'r', encoding='utf-8') as f:
                code = f.read()

            tree = self.parser.parse(bytes(code, 'utf-8'))
            await self._extract_info(tree, code)
            
            return self.codebase_dict
        except FileNotFoundError:
            return self.codebase_dict
    
    async def _extract_info(self, tree: Tree, code: str) -> None:
        if self.codebase_path not in self.codebase_dict:
            self.codebase_dict[self.codebase_path] = []

        # Recursive function to traverse AST
        async def visit(node, current_class=None):
            if node.type == 'function_definition':
                func_name_bytes = node.child_by_field_name('name').text
                func_name = func_name_bytes.decode('utf-8')

                func_code, dependencies, imports, (start_line, end_line), params, docstring = await asyncio.gather(
                    self._extract_code(code, node),
                    self._extract_dependencies(node),
                    self._extract_imports(code, tree.root_node),
                    self._extract_lines(node),
                    self._extract_fixtures(node),
                    self._extract_docstring(code, node)
                )

                current_func = Function(func_name, func_code, current_class, dependencies, imports, docstring, params, start_line, end_line)
                self.codebase_dict[self.codebase_path].append(current_func)

            elif node.type == 'class_definition':
                class_name_bytes = node.child_by_field_name('name').text
                class_name = class_name_bytes.decode('utf-8')
                # Recurse with current_class
                for child in node.children:
                    await visit(child, current_class=class_name)

            else:
                for child in node.children:
                    await visit(child, current_class=current_class)

        # Start traversal
        for child in tree.root_node.children:
            await visit(child)

    async def _extract_code(self, code: str, node: Node) -> str:
        lines = code.splitlines()
        start_line, _ = node.start_point
        end_line, _ = node.end_point

        extracted_lines = []
        for line_num in range(start_line, end_line + 1):
            extracted_lines.append(lines[line_num])

        # Join extracted lines into a single string
        snippet = "\n".join(extracted_lines)

        # Dedent the snippet to remove any existing indentation
        dedented_snippet = textwrap.dedent(snippet)

        return dedented_snippet
    
    async def _extract_dependencies(self, node: Node) -> List[str]:
        ###### Extract names of dependency
        dependencies_names = set()
        # Recursive traversal to find call nodes
        def visit(node):
            if node.type == 'call':
                func_node = node.child_by_field_name('function')
                if func_node:
                    if func_node.type == 'identifier':
                        dependencies_names.add(func_node.text.decode('utf-8'))
                    elif func_node.type == 'attribute':
                        # Extract the attribute name
                        attribute_node = func_node.child_by_field_name('attribute')
                        if attribute_node:
                            dependencies_names.add(attribute_node.text.decode('utf-8'))
            for child in node.children:
                visit(child)

        visit(node)
        return list(dependencies_names)
    
    async def _extract_imports(self, code: str, root_node: Node) -> List[str]:
        imports = []

        query_str = """
        (import_statement) @import
        (import_from_statement) @import
        """

        query = Query(self.language, query_str)
        cursor = QueryCursor(query)

        for capture_name, node_list in cursor.captures(root_node).items():
            if capture_name == 'import':
                # extract code snippet or process node
                for node in node_list:
                    import_code = await self._extract_code(code, node)
                    imports.append(import_code)
        
        return imports
    
    async def _extract_lines(self, node: Node) -> Tuple[int, int]:
        start_point = node.start_point
        end_point = node.end_point
        start_line = start_point[0] + 1
        end_line = end_point[0] + 1
        return (start_line, end_line)
    
    async def _extract_docstring(self, code, node) -> Optional[str]:
        for child in node.children:
            if child.type == "block": # The function body
                for body_child in child.children:
                    if body_child.type == "expression_statement" and \
                       body_child.children[0].type == "string":
                        docstring_node = body_child.children[0]
                        docstring_content = await self._extract_code(code, docstring_node)
                        start_line, end_line = await self._extract_lines(docstring_node)
                        return Docstring(start_line, end_line, docstring_content)
    
    async def _extract_fixtures(self, node: Node) -> List[str]:
        params = []

        # Find the 'parameters' child node of the function node
        parameters_node = node.child_by_field_name('parameters')
        if parameters_node:
            # Loop through the parameters
            for param in parameters_node.named_children:
                if param.type == 'identifier':
                    # Extract parameter name
                    param_name = param.text.decode('utf-8')
                    params.append(param_name)

        return params

    # Used by UnitTestReviewer
    async def get_fixtures(self) -> List[Function]:
        try:
            with open(self.codebase_path, 'r', encoding='utf-8') as f:
                test_code = f.read()
            tree = self.parser.parse(bytes(test_code, 'utf-8'))

            query_str = """
            (
            (decorator) @decorator_node
            )
            """
            query = Query(self.language, query_str)
            cursor = QueryCursor(query)
            fixtures = []

            for capture_name, node_list in cursor.captures(tree.root_node).items():
                if capture_name == 'decorator_node':
                    for node in node_list:
                        decorator_text = await self._extract_code(test_code, node)
                        if 'fixture' in decorator_text:
                            parent_node = node.parent
                            if parent_node and parent_node.type == 'decorated_definition':
                                fixture_node = None
                                for child in parent_node.children:
                                    if child.type == 'function_definition':
                                        fixture_node = child
                                        break
                                
                                if fixture_node:
                                    fixture_name_bytes = fixture_node.child_by_field_name('name').text
                                    fixture_name = fixture_name_bytes.decode('utf-8')
                                    fixture_code = await self._extract_code(test_code, fixture_node)
                                    fixture = Function(fixture_name, fixture_code)
                                    fixtures.append(fixture)

            return fixtures
        except Exception:
            return []

    # Used by UnitTestReviewer   
    async def get_dep_func(self, path: str, dep: str) -> Optional[Function]:
        if '.py' not in path:
            path = path + '.py'
        with open(path, 'r', encoding='utf-8') as f:
            code = f.read()
        
        tree = self.parser.parse(bytes(code, 'utf-8'))

        async def visit(node):
            if node.type == 'function_definition':
                func_name_bytes = node.child_by_field_name('name').text
                func_name = func_name_bytes.decode('utf-8')
                if func_name == dep:
                    dep_code = await self._extract_code(code, node)
                    dep_func = Function(func_name, dep_code)
                    return dep_func

            # Recursively search children
            for child in node.children:
                result = await visit(child)
                if result:
                    return result
        
        dep_func = await visit(tree.root_node)
        return dep_func
    
    # Used by DocumentationReviewer
    async def get_file_docstring(self) -> Optional[Docstring]:
        with open(self.codebase_path, 'r', encoding='utf-8') as f:
            code = f.read()

        tree = self.parser.parse(bytes(code, 'utf8'))
        root_node = tree.root_node

        # Find the module node (top-level)
        # The first statement often is the module docstring
        for child in root_node.children:
            # Check if this is a string statement at the top level
            if child.type == 'expression_statement':
                for grandchild in child.children:
                    if grandchild.type == 'string':
                        for string_part in grandchild.children:
                            if string_part.type == 'string_content':
                                file_docstring = await self._extract_code(code, string_part)
                                start_line, end_line = await self._extract_lines(string_part)
                                return Docstring(start_line, end_line, file_docstring)
    
    # Used by DeadcodeFinder
    def get_called_pipelines(self) -> Set[str]:
        try:
            with open(self.codebase_path, 'r', encoding='utf-8') as f:
                code = f.read()

            data = yaml.safe_load(code)

            pipelines_called = set()
            def visit(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        if key == 'pipelines' and isinstance(value, list):
                            for item in value:
                                if isinstance(item, str):
                                    pipelines_called.add(item)
                        else:
                            visit(value)
                elif isinstance(node, list):
                    for item in node:
                        visit(item)

            visit(data)
            return pipelines_called
        except Exception:
            return set()

    # Used by DeadcodeFinder
    def get_defined_pipelines(self) -> Dict[str, Set[str]]:
        try:
            with open(self.codebase_path, 'r', encoding='utf-8') as f:
                code = f.read()

            data = yaml.safe_load(code)

            all_pipelines = dict()
            def visit(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        if isinstance(value, dict):
                            # Check if 'nodes' key exists
                            if 'nodes' in value:
                                pipeline_name = key
                                nodes = value['nodes']
                                nodes_set = set(nodes)
                                all_pipelines[pipeline_name] = nodes_set

                                # Recursively traverse nested dicts
                                visit(value)
                        else:
                            visit(value)
                elif isinstance(node, list):
                    for item in node:
                        visit(item)

            visit(data)
            return all_pipelines
        except Exception:
            return {}
    
    # Used by DeadcodeFinder
    def get_defined_nodes(self) -> Dict[str, Dict[str, Union[str, List[str]]]]:
        try:
            with open(self.codebase_path, 'r', encoding='utf-8') as f:
                code = f.read()

            data = yaml.safe_load(code)
            if not data:
                return {}

            all_nodes = {}
            for node_name, values in data.items():
                func = values['func']
                inputs = values['inputs']
                local_file_path = Path(self.codebase_path)
                repo_file_path = Path(*local_file_path.parts[1:])
                repo_file_path = str(os.path.normpath(repo_file_path)).replace('\\', '/')

                if node_name not in all_nodes:
                    all_nodes[node_name] = {}
                all_nodes[node_name]['file'] = repo_file_path
                all_nodes[node_name]['func'] = func
                all_nodes[node_name]['inputs'] = inputs
            
            return all_nodes
        except Exception:
            return {}

    # Used by CodeIndexBuilder
    def parse_yaml_file(self) -> Dict:
        try:
            with open(self.codebase_path, 'r', encoding='utf-8') as f:
                code = f.read()
        except FileNotFoundError:
            return {}

        try:
            data = yaml.safe_load(code)
            if data:
                return data
            else:
                return {}
        except yaml.YAMLError:
            return {}