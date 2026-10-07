"""RefCheck orchestration entry points."""

__all__ = ["run_orchestrator"]


def run_orchestrator(*args, **kwargs):
    """Import lazily so lightweight orchestration utilities can be used without
    pulling in the heavier execution stack during tests or package imports."""
    from .runner import run_orchestrator as _run_orchestrator

    return _run_orchestrator(*args, **kwargs)
