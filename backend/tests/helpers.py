import shutil

import pytest

needs_cc = pytest.mark.skipif(shutil.which("gcc") is None and shutil.which("clang") is None,
                              reason="needs a C compiler")
