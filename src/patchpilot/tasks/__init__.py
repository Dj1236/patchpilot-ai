"""Evaluation task format, loading and independent grading."""

from patchpilot.tasks.evaluate import FixtureReport, Grade, grade, grade_candidate, verify_fixture
from patchpilot.tasks.integrity import EvaluationTree, build_evaluation_tree, is_protected
from patchpilot.tasks.loader import LoadedTask, TaskError, discover_tasks, load_task
from patchpilot.tasks.schema import AgentTask, Evaluation, Issue, Task

__all__ = [
    "AgentTask",
    "Evaluation",
    "EvaluationTree",
    "FixtureReport",
    "Grade",
    "Issue",
    "LoadedTask",
    "Task",
    "TaskError",
    "build_evaluation_tree",
    "discover_tasks",
    "grade",
    "grade_candidate",
    "is_protected",
    "load_task",
    "verify_fixture",
]
