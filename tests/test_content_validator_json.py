import unittest
from pathlib import Path
from evaluator.content_validator import ContentValidator
from evaluator.promise_parser import FilePromise, SectionConstraint

class TestContentValidator(unittest.TestCase):
    def setUp(self):
        self.validator = ContentValidator()
        self.dummy_path = Path("dummy.json")

    def test_validate_json_decode_error(self):
        """Test that invalid JSON triggers JSONDecodeError and returns score 20"""
        promise = FilePromise(filename="dummy.json", file_type="json")
        invalid_json_content = "{ invalid json"

        result = self.validator._validate_json(invalid_json_content, self.dummy_path, promise)

        self.assertEqual(result.score, 20)
        self.assertFalse(result.valid)
        self.assertTrue(any("JSON解析失败" in detail for detail in result.details))

    def test_validate_json_valid_object(self):
        """Test valid JSON object parsing and scoring"""
        promise = FilePromise(filename="dummy.json", file_type="json")
        valid_json_content = '{"key1": "value1", "key2": "value2"}'

        result = self.validator._validate_json(valid_json_content, self.dummy_path, promise)

        self.assertEqual(result.score, 50) # base 30 + 20 for >0 keys
        self.assertTrue(result.valid) # 50 >= 50

    def test_validate_json_valid_object_with_sections(self):
        """Test valid JSON object parsing with section constraints"""
        sections = [SectionConstraint(name="items", min_items=2)]
        promise = FilePromise(filename="dummy.json", file_type="json", sections=sections)
        valid_json_content = '{"items": [1, 2, 3]}'

        result = self.validator._validate_json(valid_json_content, self.dummy_path, promise)

        self.assertEqual(result.score, 60) # base 30 + 20 (keys>0) + 10 (matching section)
        self.assertTrue(result.valid)

    def test_validate_json_valid_array(self):
        """Test valid JSON array parsing and scoring"""
        promise = FilePromise(filename="dummy.json", file_type="json")
        valid_json_content = '[1, 2, 3]'

        result = self.validator._validate_json(valid_json_content, self.dummy_path, promise)

        self.assertEqual(result.score, 50) # base 30 + 20 for array
        self.assertTrue(result.valid)

    def test_validate_json_valid_array_with_sections(self):
        """Test valid JSON array parsing with section constraints"""
        sections = [SectionConstraint(name="any", min_items=2)]
        promise = FilePromise(filename="dummy.json", file_type="json", sections=sections)
        valid_json_content = '[1, 2, 3]'

        result = self.validator._validate_json(valid_json_content, self.dummy_path, promise)

        self.assertEqual(result.score, 65) # base 30 + 20 for array + 15 for min_items match
        self.assertTrue(result.valid)

if __name__ == '__main__':
    unittest.main()
