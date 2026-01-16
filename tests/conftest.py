"""
Pytest configuration and shared fixtures.
"""

import os
import pytest
from pathlib import Path

# Set test environment
os.environ["PAPER_TRADING"] = "true"
os.environ["DEBUG_MODE"] = "true"
os.environ["POLYMARKET_PRIVATE_KEY"] = "0x" + "0" * 64
os.environ["PANDASCORE_API_KEY"] = "test_key"
os.environ["DATABASE_PATH"] = "./test_data/trades.db"


@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Set up test environment."""
    # Create test data directory
    test_dir = Path("./test_data")
    test_dir.mkdir(exist_ok=True)
    
    yield
    
    # Cleanup
    import shutil
    if test_dir.exists():
        shutil.rmtree(test_dir)


@pytest.fixture
def mock_config():
    """Provide a mock configuration."""
    from src.config import get_config
    return get_config()




