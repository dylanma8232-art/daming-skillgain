import sys
import os
sys.path.insert(0, os.path.abspath("."))
import pytest
from pathlib import Path
from unittest.mock import patch
from evaluator.content_validator import ContentValidator
from evaluator.promise_parser import FilePromise

def test_csv_parsing_failure():
    validator = ContentValidator()
    promise = FilePromise(filename="test.csv", file_type="csv")
    content = "col1,col2\n1,2"
    path = Path("test.csv")

    with patch("csv.DictReader") as mock_reader:
        mock_reader.side_effect = Exception("Mocked CSV Error")
        result = validator._validate_csv(content, path, promise)

        assert result.score == 30
        assert any("CSV解析失败" in detail for detail in result.details)
        assert any("Mocked CSV Error" in detail for detail in result.details)
