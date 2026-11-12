"""Read-only tools over a repository snapshot.

The tools work on the in-memory snapshot, a mapping of relative path to
bytes. They never touch the host filesystem, so a path argument can only
select a key of that mapping or select nothing.

Every tool takes a validated request model and is reached through one fixed
registry. When a language model is connected in a later milestone, it will
supply the request arguments and nothing else: it cannot add a tool, skip
validation or exceed the budget, because none of that is decided by text.

File contents and search hits come back wrapped as untrusted text.
"""

import ast
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, StringConstraints, ValidationError

from patchpilot.intake.text import UntrustedText
from patchpilot.sandbox.models import FrozenModel
from patchpilot.sandbox.tree import validate_relative_path

MAX_LINE_CHARS = 400
MAX_OUTLINE_BYTES = 524_288
_BINARY_PROBE_BYTES = 8192

SnapshotPath = Annotated[str, AfterValidator(validate_relative_path)]
PathPrefix = Annotated[
    str, AfterValidator(lambda value: value if value == "" else validate_relative_path(value))
]
Query = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class BudgetExceededError(Exception):
    """The run's tool budget is used up. The run must stop, not retry."""


class ToolBudget:
    """How much the tools may do in one run. Shared by all tools of that run."""

    def __init__(
        self,
        *,
        max_calls: int = 200,
        max_output_bytes: int = 2_000_000,
        max_call_output_bytes: int = 64_000,
    ) -> None:
        self.max_calls = max_calls
        self.max_output_bytes = max_output_bytes
        self.max_call_output_bytes = max_call_output_bytes
        self.calls = 0
        self.output_bytes = 0

    def start_call(self) -> int:
        """Count one call and return how many output bytes it may produce."""
        if self.calls >= self.max_calls:
            raise BudgetExceededError(f"the limit of {self.max_calls} tool calls is used up")
        remaining = self.max_output_bytes - self.output_bytes
        if remaining <= 0:
            raise BudgetExceededError("the tool output budget is used up")
        self.calls += 1
        return min(remaining, self.max_call_output_bytes)

    def spend(self, output_bytes: int) -> None:
        self.output_bytes += output_bytes


# -- requests ----------------------------------------------------------------


class ListFilesRequest(FrozenModel):
    prefix: PathPrefix = ""
    limit: int = Field(default=200, ge=1, le=1000)


class ReadFileRequest(FrozenModel):
    path: SnapshotPath
    start_line: int = Field(default=1, ge=1, le=10_000_000)
    max_lines: int = Field(default=200, ge=1, le=2000)


class SearchCodeRequest(FrozenModel):
    query: Query
    prefix: PathPrefix = ""
    case_sensitive: bool = True
    max_matches: int = Field(default=50, ge=1, le=500)


class OutlineRequest(FrozenModel):
    path: SnapshotPath


# -- results -------------------------------------------------------------------


class FileEntry(FrozenModel):
    path: str
    size: int


class ListFilesResult(FrozenModel):
    tool: Literal["list_files"] = "list_files"
    entries: tuple[FileEntry, ...]
    total: int
    truncated: bool


class ReadFileResult(FrozenModel):
    tool: Literal["read_file"] = "read_file"
    path: str
    start_line: int
    end_line: int
    total_lines: int
    content: UntrustedText
    truncated: bool


class SearchMatch(FrozenModel):
    path: str
    line: int
    text: UntrustedText


class SearchCodeResult(FrozenModel):
    tool: Literal["search_code"] = "search_code"
    matches: tuple[SearchMatch, ...]
    truncated: bool


class Symbol(FrozenModel):
    kind: Literal["class", "function", "method"]
    name: str
    line: int
    end_line: int


class OutlineResult(FrozenModel):
    tool: Literal["outline"] = "outline"
    path: str
    symbols: tuple[Symbol, ...]
    truncated: bool


class ToolError(FrozenModel):
    """A tool could not do what was asked. Never contains file content."""

    tool: str
    code: Literal[
        "unknown_tool", "invalid_arguments", "not_found", "binary_file", "not_python", "unparseable"
    ]
    message: str


ToolResult = ListFilesResult | ReadFileResult | SearchCodeResult | OutlineResult | ToolError


def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:_BINARY_PROBE_BYTES]


def _decode(data: bytes) -> str | None:
    if _is_binary(data):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + " [line cut]"


def _under(path: str, prefix: str) -> bool:
    return prefix == "" or path == prefix or path.startswith(prefix.rstrip("/") + "/")


class RepositoryTools:
    """The fixed set of read-only tools for one snapshot and one budget."""

    def __init__(self, files: Mapping[str, bytes], budget: ToolBudget | None = None) -> None:
        self._files = files
        self._budget = budget or ToolBudget()
        self._registry: dict[str, tuple[type[FrozenModel], Callable[[Any, int], ToolResult]]] = {
            "list_files": (ListFilesRequest, self._list_files),
            "read_file": (ReadFileRequest, self._read_file),
            "search_code": (SearchCodeRequest, self._search_code),
            "outline": (OutlineRequest, self._outline),
        }

    @property
    def budget(self) -> ToolBudget:
        return self._budget

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(self._registry)

    def call(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        """Run the tool ``name`` with ``arguments`` after validating both.

        Problems with the request come back as a ``ToolError``. Only an
        exhausted budget raises, because that must end the run.
        """
        allowance = self._budget.start_call()
        entry = self._registry.get(name) if isinstance(name, str) else None
        if entry is None:
            return ToolError(tool="unknown", code="unknown_tool", message="no such tool")
        request_model, handler = entry
        try:
            request = request_model.model_validate(dict(arguments))
        except (ValidationError, TypeError, ValueError):
            return ToolError(
                tool=name, code="invalid_arguments", message="the arguments were not accepted"
            )
        result = handler(request, allowance)
        self._budget.spend(len(result.model_dump_json()))
        return result

    # -- tools ---------------------------------------------------------------

    def _list_files(self, request: ListFilesRequest, allowance: int) -> ToolResult:
        matching = sorted(path for path in self._files if _under(path, request.prefix))
        entries: list[FileEntry] = []
        used = 0
        for path in matching[: request.limit]:
            used += len(path) + 24
            if used > allowance:
                break
            entries.append(FileEntry(path=path, size=len(self._files[path])))
        return ListFilesResult(
            entries=tuple(entries), total=len(matching), truncated=len(entries) < len(matching)
        )

    def _read_file(self, request: ReadFileRequest, allowance: int) -> ToolResult:
        data = self._files.get(request.path)
        if data is None:
            return ToolError(tool="read_file", code="not_found", message="no such file")
        text = _decode(data)
        if text is None:
            return ToolError(tool="read_file", code="binary_file", message="not a text file")
        lines = text.splitlines()
        first = request.start_line - 1
        selected: list[str] = []
        used = 0
        for line in lines[first : first + request.max_lines]:
            clipped = _clip(line)
            used += len(clipped.encode("utf-8")) + 1
            if used > allowance:
                break
            selected.append(clipped)
        end_line = first + len(selected)
        return ReadFileResult(
            path=request.path,
            start_line=request.start_line,
            end_line=end_line,
            total_lines=len(lines),
            content=UntrustedText(untrusted_value="\n".join(selected)),
            truncated=end_line < len(lines),
        )

    def _search_code(self, request: SearchCodeRequest, allowance: int) -> ToolResult:
        needle = request.query if request.case_sensitive else request.query.lower()
        matches: list[SearchMatch] = []
        used = 0
        truncated = False
        for path in sorted(self._files):
            if not _under(path, request.prefix):
                continue
            text = _decode(self._files[path])
            if text is None:
                continue
            haystack = text if request.case_sensitive else text.lower()
            if needle not in haystack:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle not in (line if request.case_sensitive else line.lower()):
                    continue
                clipped = _clip(line)
                used += len(path) + len(clipped.encode("utf-8")) + 16
                if len(matches) >= request.max_matches or used > allowance:
                    truncated = True
                    break
                matches.append(
                    SearchMatch(path=path, line=number, text=UntrustedText(untrusted_value=clipped))
                )
            if truncated:
                break
        return SearchCodeResult(matches=tuple(matches), truncated=truncated)

    def _outline(self, request: OutlineRequest, allowance: int) -> ToolResult:
        data = self._files.get(request.path)
        if data is None:
            return ToolError(tool="outline", code="not_found", message="no such file")
        if not request.path.endswith(".py"):
            return ToolError(tool="outline", code="not_python", message="not a Python file")
        if len(data) > MAX_OUTLINE_BYTES or _is_binary(data):
            return ToolError(tool="outline", code="unparseable", message="the file is too large")
        try:
            # Parsed only. The file is never imported or executed.
            module = ast.parse(data)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return ToolError(tool="outline", code="unparseable", message="not valid Python")

        symbols: list[Symbol] = []

        def add(node: ast.AST, kind: Literal["class", "function", "method"]) -> None:
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                symbols.append(
                    Symbol(
                        kind=kind,
                        name=node.name[:200],
                        line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                    )
                )

        for node in module.body:
            if isinstance(node, ast.ClassDef):
                add(node, "class")
                for member in node.body:
                    if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                        add(member, "method")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                add(node, "function")

        kept: list[Symbol] = []
        used = 0
        for symbol in symbols:
            used += len(symbol.name) + 48
            if used > allowance:
                break
            kept.append(symbol)
        return OutlineResult(
            path=request.path, symbols=tuple(kept), truncated=len(kept) < len(symbols)
        )
