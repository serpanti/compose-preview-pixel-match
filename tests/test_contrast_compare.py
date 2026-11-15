import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from contrast_compare import PngImage, read_png, write_png


class ContrastCompareTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.geometry = self.folder / "geometry.png"
        self.color = self.folder / "color.png"

    def image(self, name, color, *, left=4, background=(255, 255, 255, 255)):
        pixels = bytearray(bytes(background) * 400)
        for y in range(4, 14):
            for x in range(left, left + 10):
                offset = (y * 20 + x) * 4
                pixels[offset : offset + 4] = bytes(color)
        path = self.folder / name
        write_png(path, PngImage(20, 20, bytes(pixels)))
        return path

    def compare(self, reference, candidate, *options, color_output=True):
        command = [
            sys.executable, str(SCRIPTS / "contrast_compare.py"),
            "--reference", str(reference), "--candidate", str(candidate),
            "--output", str(self.geometry),
        ]
        if color_output:
            command.extend(("--color-output", str(self.color)))
        command.extend(options)
        return json.loads(subprocess.check_output(command))

    def test_same_geometry_with_changed_fill(self):
        reference = self.image("reference.png", (255, 0, 0, 255))
        candidate = self.image("candidate.png", (0, 0, 255, 255))
        report = self.compare(reference, candidate)
        self.assertEqual(report["weighted_iou"], 1)
        self.assertIsNone(report["diff_bbox"])
        self.assertEqual(report["color_diff"]["max_channel_delta"], 255)
        self.assertEqual(report["color_diff"]["changed_fraction"], 0.25)
        self.assertEqual(report["color_diff"]["diff_bbox"], [4, 4, 13, 13])
        heatmap = read_png(self.color)
        self.assertEqual(heatmap.rgba[:4], bytes((0, 0, 0, 255)))
        self.assertGreater(heatmap.rgba[(4 * 20 + 4) * 4], 0)

    def test_detects_shift_and_applies_the_same_explicit_offset(self):
        reference = self.image("reference.png", (255, 0, 0, 255))
        candidate = self.image("candidate.png", (255, 0, 0, 255), left=6)
        report = self.compare(reference, candidate)
        self.assertAlmostEqual(report["binary_iou"], 2 / 3)
        self.assertEqual(report["color_diff"]["changed_pixels"], 40)
        # Explicit alignment shifts two columns beyond the candidate canvas.
        aligned = self.compare(reference, candidate, "--candidate-offset-x", "-2")
        self.assertEqual(aligned["binary_iou"], 1)
        self.assertEqual(aligned["color_diff"]["diff_bbox"], [18, 0, 19, 19])

    def test_alpha_only_change_is_visible_and_hidden_rgb_is_ignored(self):
        reference = self.image("reference.png", (0, 0, 0, 128), background=(255, 0, 0, 0))
        candidate = self.image("candidate.png", (0, 0, 0, 64), background=(0, 255, 0, 0))
        report = self.compare(reference, candidate)
        self.assertEqual(report["color_diff"]["max_channel_delta"], 64)
        self.assertEqual(report["color_diff"]["changed_pixels"], 100)

    def test_threshold_and_legacy_report(self):
        reference = self.image("reference.png", (255, 255, 255, 255))
        candidate = self.image("candidate.png", (248, 255, 255, 255))
        self.assertEqual(self.compare(reference, candidate)["color_diff"]["changed_pixels"], 0)
        strict = self.compare(reference, candidate, "--color-threshold", "0")
        self.assertEqual(strict["color_diff"]["changed_pixels"], 100)
        legacy = self.compare(reference, candidate, color_output=False)
        self.assertNotIn("color_diff", legacy)
        self.assertEqual(strict["binary_iou"], legacy["binary_iou"])


if __name__ == "__main__":
    unittest.main()
