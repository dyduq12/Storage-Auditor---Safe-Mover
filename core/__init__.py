"""Núcleo de processamento, auditoria e cópia segura de arquivos."""

from core.action_plan import ActionOrder, ActionPlan, ActionPlanGenerator, ActionPlanValidationError
from core.safe_mover import (
    CopyCancelledError,
    HashMismatchError,
    InsufficientSpaceError,
    SafeCopyExecutor,
    SafeMoverError,
    SourceMutatedError,
    TargetConflictError,
)

__all__ = [
    "ActionOrder",
    "ActionPlan",
    "ActionPlanGenerator",
    "ActionPlanValidationError",
    "SafeCopyExecutor",
    "SafeMoverError",
    "CopyCancelledError",
    "SourceMutatedError",
    "TargetConflictError",
    "HashMismatchError",
    "InsufficientSpaceError",
]
