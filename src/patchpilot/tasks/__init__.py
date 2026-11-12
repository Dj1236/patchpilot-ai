"""Evaluation task format, loading and independent grading."""

from patchpilot.tasks.evaluate import FixtureReport, Grade, grade, grade_candidate, verify_fixture
from patchpilot.tasks.loader import LoadedTask, TaskError, discover_tasks, load_task
from patchpilot.tasks.schema import AgentTask, Evaluation, Issue, Task

__all__ = [
    "AgentTask",
    "Evaluation",
    "FixtureReport",
    "Grade",
    "Issue",
    "LoadedTask",
    "Task",
    "TaskError",
    "discover_tasks",
    "grade",
    "grade_candidate",
    "load_task",
    "verify_fixture",
]
