import unittest
from unittest.mock import patch, MagicMock
from evaluator.model_discovery import ModelDiscovery
import subprocess

class TestModelDiscovery(unittest.TestCase):
    @patch('evaluator.model_discovery.subprocess.run')
    @patch('evaluator.model_discovery.Path.home')
    def test_detect_opencode_subprocess_error(self, mock_home, mock_subprocess_run):
        # Prevent GUI config from being read
        mock_gui_config = MagicMock()
        mock_gui_config.exists.return_value = False
        mock_home.return_value = MagicMock()
        mock_home.return_value.__truediv__.return_value = mock_gui_config

        # First call is for opencode --version, second call is for opencode models
        def subprocess_run_side_effect(*args, **kwargs):
            if args[0] == ["opencode", "--version"]:
                return MagicMock(returncode=0, stdout="1.0.0\n")
            elif args[0] == ["opencode", "models"]:
                raise Exception("subprocess error")
            return MagicMock(returncode=1)

        mock_subprocess_run.side_effect = subprocess_run_side_effect

        discovery = ModelDiscovery()
        result = discovery._detect_opencode()

        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["models"], [])

    @patch('evaluator.model_discovery.subprocess.run')
    @patch('evaluator.model_discovery.Path.home')
    def test_detect_opencode_subprocess_nonzero(self, mock_home, mock_subprocess_run):
        # Prevent GUI config from being read
        mock_gui_config = MagicMock()
        mock_gui_config.exists.return_value = False
        mock_home.return_value = MagicMock()
        mock_home.return_value.__truediv__.return_value = mock_gui_config

        # First call is for opencode --version, second call is for opencode models
        def subprocess_run_side_effect(*args, **kwargs):
            if args[0] == ["opencode", "--version"]:
                return MagicMock(returncode=0, stdout="1.0.0\n")
            elif args[0] == ["opencode", "models"]:
                return MagicMock(returncode=1, stdout="Error")
            return MagicMock(returncode=1)

        mock_subprocess_run.side_effect = subprocess_run_side_effect

        discovery = ModelDiscovery()
        result = discovery._detect_opencode()

        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["models"], [])
