"""Typed tool registration, execution, and persistent tracing."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any, TypeVar

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.contracts.common import RunStatus
from app.contracts.tool import ToolError, ToolExecution, ToolInput, ToolOutput
from app.db import models

InputT = TypeVar("InputT", bound=ToolInput)
OutputT = TypeVar("OutputT", bound=ToolOutput)
ToolFunction = Callable[[Any, Session], ToolOutput]


class UnknownToolError(LookupError):
    """Raised when an agent requests a tool outside the frozen registry."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolFunction] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def register(self, name: str, function: ToolFunction) -> None:
        if not name or name in self._tools:
            raise ValueError(f"Tool name is empty or already registered: {name!r}")
        self._tools[name] = function

    def execute(
        self,
        name: str,
        tool_input: InputT,
        session: Session,
    ) -> ToolExecution[InputT, ToolOutput]:
        started = perf_counter()
        output: ToolOutput | None = None
        error: ToolError | None = None
        status = RunStatus.SUCCEEDED
        try:
            function = self._tools[name]
            with session.begin_nested():
                output = function(tool_input, session)
        except KeyError:
            status = RunStatus.FAILED
            error = ToolError(code="unknown_tool", message=f"Unknown tool: {name}")
        except (ValueError, ValidationError) as exc:
            status = RunStatus.FAILED
            error = ToolError(code="invalid_input", message=str(exc))
        except Exception as exc:  # noqa: BLE001 - boundary converts failures to trace records
            status = RunStatus.FAILED
            error = ToolError(
                code="tool_execution_failed",
                message="Tool execution failed safely.",
                details={"exception_type": type(exc).__name__},
            )
        latency_ms = max(0, round((perf_counter() - started) * 1000))
        execution = ToolExecution[InputT, ToolOutput](
            run_id=tool_input.run_id,
            tool_name=name,
            status=status,
            input=tool_input,
            output=output,
            latency_ms=latency_ms,
            error=error,
        )
        session.add(
            models.ToolExecution(
                run_id=tool_input.run_id,
                tool_name=name,
                input_json=tool_input.model_dump(mode="json"),
                output_json=output.model_dump(mode="json") if output is not None else None,
                status=status.value,
                latency_ms=latency_ms,
                error=error.model_dump(mode="json") if error is not None else None,
            )
        )
        session.flush()
        return execution

