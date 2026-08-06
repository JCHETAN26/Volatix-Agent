"""AST symbol extraction -- Phase 3.

The Planner needs to know a module's shape, not its every line. This parses source with
``ast`` and emits class/function signatures with bodies stripped, which is the single
biggest context-window saving in the pipeline: a 900-line module becomes a ~30-line
outline.

Syntax errors are returned as data rather than raised. A bug report is frequently *about*
a syntax error, so "this file does not parse, here is where" is the useful answer, not a
tool failure.
"""

import ast
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Symbol:
    """One class, function, or method, with its body discarded."""

    kind: str  # "class" | "function" | "method"
    name: str
    signature: str
    lineno: int
    end_lineno: int
    docstring: Optional[str] = None
    decorators: List[str] = field(default_factory=list)

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        lines = [f"{pad}@{d}" for d in self.decorators]
        lines.append(f"{pad}{self.signature}  # L{self.lineno}-{self.end_lineno}")
        if self.docstring:
            lines.append(f'{pad}  """{self.docstring}"""')
        return "\n".join(lines)


@dataclass
class SyntaxProblem:
    """A file that does not parse -- usually the bug itself."""

    message: str
    lineno: Optional[int]
    offset: Optional[int]
    text: Optional[str]

    def render(self) -> str:
        where = f" at line {self.lineno}" if self.lineno else ""
        rendered = [f"SyntaxError{where}: {self.message}"]
        if self.text:
            rendered.append(f"  {self.text.rstrip()}")
            if self.offset:
                rendered.append("  " + " " * max(self.offset - 1, 0) + "^")
        return "\n".join(rendered)


@dataclass
class ModuleOutline:
    """Everything the Planner needs about one module."""

    path: str
    imports: List[str] = field(default_factory=list)
    constants: List[str] = field(default_factory=list)
    symbols: List[Symbol] = field(default_factory=list)
    children: dict = field(default_factory=dict)  # class name -> list[Symbol]
    docstring: Optional[str] = None
    syntax_error: Optional[SyntaxProblem] = None

    @property
    def parsed(self) -> bool:
        return self.syntax_error is None

    def render(self) -> str:
        if self.syntax_error is not None:
            return f"# {self.path}\n{self.syntax_error.render()}"

        out = [f"# {self.path}"]
        if self.docstring:
            out.append(f'"""{self.docstring}"""')
        if self.imports:
            out.append("")
            out.extend(self.imports)
        if self.constants:
            out.append("")
            out.extend(f"{name} = ..." for name in self.constants)
        for symbol in self.symbols:
            out.append("")
            out.append(symbol.render())
            for method in self.children.get(symbol.name, []):
                out.append(method.render(indent=1))
        return "\n".join(out)


def _docstring_summary(node) -> Optional[str]:
    """First line of the docstring only -- enough to convey intent, cheap in tokens."""
    doc = ast.get_docstring(node)
    if not doc:
        return None
    return doc.strip().splitlines()[0]


def _signature(node) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({args}){returns}:"


def _class_signature(node: ast.ClassDef) -> str:
    bases = [ast.unparse(b) for b in node.bases]
    bases += [f"{kw.arg}={ast.unparse(kw.value)}" for kw in node.keywords]
    joined = f"({', '.join(bases)})" if bases else ""
    return f"class {node.name}{joined}:"


def _decorators(node) -> List[str]:
    return [ast.unparse(d) for d in node.decorator_list]


def _function_symbol(node, kind: str) -> Symbol:
    return Symbol(
        kind=kind,
        name=node.name,
        signature=_signature(node),
        lineno=node.lineno,
        end_lineno=node.end_lineno or node.lineno,
        docstring=_docstring_summary(node),
        decorators=_decorators(node),
    )


def extract_outline(source: str, path: str = "<source>") -> ModuleOutline:
    """Parse ``source`` and return its symbol outline, or the syntax error that blocked it."""
    outline = ModuleOutline(path=path)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        outline.syntax_error = SyntaxProblem(
            message=exc.msg,
            lineno=exc.lineno,
            offset=exc.offset,
            text=exc.text,
        )
        return outline

    outline.docstring = _docstring_summary(tree)

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            outline.imports.append(ast.unparse(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            outline.symbols.append(_function_symbol(node, "function"))
        elif isinstance(node, ast.ClassDef):
            symbol = Symbol(
                kind="class",
                name=node.name,
                signature=_class_signature(node),
                lineno=node.lineno,
                end_lineno=node.end_lineno or node.lineno,
                docstring=_docstring_summary(node),
                decorators=_decorators(node),
            )
            outline.symbols.append(symbol)
            outline.children[node.name] = [
                _function_symbol(child, "method")
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
        elif isinstance(node, ast.Assign):
            outline.constants.extend(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            outline.constants.append(node.target.id)

    return outline
