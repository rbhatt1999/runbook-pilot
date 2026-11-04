import io

import pytest
from rich.console import Console


@pytest.fixture
def quiet() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, no_color=True, highlight=False)
