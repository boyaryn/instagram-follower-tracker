import os

import pytest


@pytest.mark.db
def test_database_url_is_available():
    assert os.environ["IGFT_TEST_DATABASE_URL"]
