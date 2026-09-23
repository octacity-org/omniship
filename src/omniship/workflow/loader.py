import hashlib
import importlib.util
import sys
from pathlib import Path

from .errors import WorkflowError
from .model import Pipeline


def load_workflow(path: str | Path) -> Pipeline:
    source = Path(path).resolve()
    if not source.is_file():
        raise WorkflowError(f"Workflow file not found: {source}")
    module_name = f"_omniship_workflow_{hashlib.sha256(str(source).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise WorkflowError(f"Could not load workflow: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        # Workflow definitions are edited and regenerated frequently. Execute
        # the current source, not a timestamp-based cached bytecode file.
        exec(compile(source.read_bytes(), str(source), "exec"), module.__dict__)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise WorkflowError(f"Could not import workflow '{source}': {exc}") from exc
    pipeline = getattr(module, "pipeline", None)
    if not isinstance(pipeline, Pipeline):
        raise WorkflowError("Workflow must export a Pipeline named 'pipeline'")
    return pipeline
