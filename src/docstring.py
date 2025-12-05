class Docstring:
    def __init__(self, start_line: int, end_line: int, code: str) -> None:
        self.start_line = start_line
        self.end_line = end_line
        self.code = code