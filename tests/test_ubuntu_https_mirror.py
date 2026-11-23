import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from subprocess import CompletedProcess

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/select-ubuntu-https-mirror.py"
spec = importlib.util.spec_from_file_location("selector", SCRIPT)
selector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selector)


class UbuntuMirrorTest(unittest.TestCase):
    def test_selects_reachable_official_https_and_preserves_other_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "apt-mirrors.txt"
            original = ("http://us-east-2.ec2.archive.ubuntu.com/ubuntu/ priority:1\n"
                        " http://security.ubuntu.com/ubuntu priority:2\n"
                        "http://packages.example/ubuntu/\n"
                        "http://archive.ubuntu.com.example/ubuntu/\n"
                        "https://archive.ubuntu.com/ubuntu/\n")
            path.write_text(original)
            for codes in ([0, 0], [22, 0, 0], [22, 28]):
                with self.subTest(codes=codes), patch.object(
                    selector.subprocess, "run",
                    side_effect=[CompletedProcess([], status) for status in codes],
                ) as run:
                    selector.configure(path)
                    if codes[-1]:
                        self.assertTrue(all(c.args[0][0] == "curl" for c in run.call_args_list))
                        continue
                    host = selector.ARCHIVES[len(codes)-2]
                    final = run.call_args_list[-1]
                    self.assertEqual(final.args[0], ["sudo", "tee", str(path)])
                    self.assertEqual(final.kwargs["input"].decode(), original.replace(
                        "http://us-east-2.ec2.archive.ubuntu.com", "https://" + host
                    ).replace("http://security.ubuntu.com", "https://" + host))
                    self.assertTrue(final.kwargs["check"])

    def test_preserves_missing_file_symlink_and_non_ubuntu_lists(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(selector.subprocess, "run") as run:
            path = Path(directory) / "test"
            selector.configure(path)
            path.write_text("https://archive.ubuntu.com/ubuntu/\n")
            selector.configure(path)
            link = path.with_suffix(".link")
            link.symlink_to(path)
            path.write_text("http://archive.ubuntu.com/ubuntu/\n")
            selector.configure(link)
            run.assert_not_called()
