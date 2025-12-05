from __future__ import annotations
import textwrap
from typing_extensions import List, Optional, Union

class Function:
    def __init__(
        self, 
        func_name: str, 
        func_code: str, 
        class_name: Optional[str] = None, 
        dependencies: Optional[List[str]] = None, 
        imports: Optional[List[str]] = None, 
        docstring: Optional[str] = None, 
        params: Optional[List[str]] = None, 
        start_line: Optional[int] = None, 
        end_line: Optional[int] = None
    ) -> None:
        self.func_name = func_name
        self.func_code = func_code
        self.class_name = class_name
        self.is_method = self.class_name is not None
        self.dependencies = dependencies
        self.imports = imports
        self.params = params
        self.docstring = docstring
        self.start_line = start_line
        self.end_line = end_line
        self.added_lines = []
        self.removed_lines = []
    
    def __hash__(self) -> int:
        return hash(self.func_name)
    
    def __eq__(self, other: Union[Function, str]) -> bool:
        if isinstance(other, Function):
            return self.func_name == other.func_name and self.func_code == other.func_code
        elif isinstance(other, str):
            return other == self.func_name

    def addInformation(self, added_line: Optional[int] = None, removed_line: Optional[int] = None):
        if added_line:
            self.added_lines.append(added_line)
        if removed_line:
            self.removed_lines.append(removed_line)
    
    def mergeFunctions(self, func: Function) -> None:
        added_code = func.func_code
        new_code = self.func_code + '\n\n' + added_code
        self.func_code = textwrap.dedent(new_code)

        dependencies_set = set(self.dependencies)
        dependencies_set.update(func.dependencies)
        self.dependencies = list(dependencies_set)

