"""Development stage markers for code components.

This module provides decorators to mark functions, methods, and classes with
their current development stage according to the LabsNFoundries process.

The stage markers serve multiple purposes:
1. Document the maturity level and expected code quality
2. Guide code review and refactoring priorities
3. Enable selective test coverage tracking
4. Provide searchable metadata for development process auditing

Usage:
    from palaver.stage_markers import Stage, stage

    @stage(Stage.MVP, track_coverage=True)
    class ProductionReadyComponent:
        '''This component should have high test coverage.'''
        pass

    @stage(Stage.POC, track_coverage=False)
    def experimental_feature():
        '''Proof of concept - minimal quality investment.'''
        pass
"""

from enum import Enum
from typing import Callable, TypeVar, Union

F = TypeVar('F', bound=Callable)


class Stage(Enum):
    """Development stages from the LabsNFoundries process.

    Learning Stages (human understanding focused):
    - RESEARCH: Exploring the problem space (100% understanding, 0% sustainable code)
    - STUDY: Learning solution components (100% understanding, 0% sustainable code)
    - POC: Proving a solution slice works (90% understanding, 10% sustainable code)

    Building Stages (sustainable code focused):
    - PROTOTYPE: First realistic use implementation (50% understanding, 50% code)
    - MVP: Complete enough for real use/refinement cycles (20% understanding, 80% code)
    - PRODUCTION: Long-term sustainable solution (0% understanding, 100% code)
    """

    RESEARCH = "research"
    STUDY = "study"
    POC = "poc"
    PROTOTYPE = "prototype"
    MVP = "mvp"
    PRODUCTION = "production"

    def __str__(self) -> str:
        return self.value

    @property
    def is_learning_stage(self) -> bool:
        """True if this is a learning stage (Research/Study/POC)."""
        return self in (Stage.RESEARCH, Stage.STUDY, Stage.POC)

    @property
    def is_building_stage(self) -> bool:
        """True if this is a building stage (Prototype/MVP/Production)."""
        return self in (Stage.PROTOTYPE, Stage.MVP, Stage.PRODUCTION)

    @property
    def expected_quality(self) -> str:
        """Human-readable description of expected code quality."""
        quality_map = {
            Stage.RESEARCH: "Minimal - throwaway code for learning",
            Stage.STUDY: "Minimal - throwaway code for learning",
            Stage.POC: "Minimal - focus on component contracts only",
            Stage.PROTOTYPE: "Moderate - clean interfaces, basic tests",
            Stage.MVP: "High - good architecture, solid test coverage",
            Stage.PRODUCTION: "Very high - comprehensive tests, full documentation",
        }
        return quality_map[self]


def stage(
    dev_stage: Stage,
    track_coverage: bool = True,
) -> Callable[[F], F]:
    """Mark a function, method, or class with its development stage.

    This decorator adds metadata attributes to the decorated object for
    programmatic access and process tooling integration.

    Args:
        dev_stage: The current stage of this code component
        track_coverage: Whether this code should be included in coverage goals.
            Typically False for RESEARCH/STUDY/POC stages, True for building stages.

    Returns:
        Decorator function that adds __stage__ and __track_coverage__ attributes

    Example:
        @stage(Stage.MVP, track_coverage=True)
        class WhisperThread:
            '''Production-ready transcription worker.'''
            pass

        assert WhisperThread.__stage__ == Stage.MVP
        assert WhisperThread.__track_coverage__ is True

        @stage(Stage.POC, track_coverage=False)
        def experiment():
            '''Quick proof of concept.'''
            pass
    """
    def decorator(func_or_class: F) -> F:
        func_or_class.__stage__ = dev_stage
        func_or_class.__track_coverage__ = track_coverage
        return func_or_class
    return decorator


def get_stage(obj: Union[type, Callable]) -> Stage:
    """Get the development stage of a decorated object.

    Args:
        obj: A class or function that may have been decorated with @stage

    Returns:
        The Stage enum value, or None if not decorated

    Raises:
        AttributeError: If the object has no __stage__ attribute
    """
    return getattr(obj, '__stage__')


def should_track_coverage(obj: Union[type, Callable]) -> bool:
    """Check if a decorated object should be included in coverage tracking.

    Args:
        obj: A class or function that may have been decorated with @stage

    Returns:
        True if coverage should be tracked, False otherwise, or None if not decorated

    Raises:
        AttributeError: If the object has no __track_coverage__ attribute
    """
    return getattr(obj, '__track_coverage__')
