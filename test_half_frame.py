"""Regression checks for half-frame detection and its project lifecycle."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'app'))
if importlib.util.find_spec('cv2') is None:
    sys.path.insert(0, str(ROOT.parents[1]/'work/python_packages'))
import cv2
import numpy as np

from half_frame import detect_half_frames
from server import Store


def scan(right_visible=True):
    left = np.array([[48, 22], [606, 32], [597, 820], [37, 814]], np.int32)
    right = np.array([[672, 25], [1238, 33], [1226, 827], [660, 819]], np.int32)
    image = np.full((853, 1280), 2200, np.uint16)
    cv2.fillConvexPoly(image, left, 48000)
    if right_visible:
        cv2.fillConvexPoly(image, right, 35000)
    else:
        cv2.circle(image, (1060, 560), 65, 21000, -1)
    return image, [left, right]


class HalfFrameTests(unittest.TestCase):
    def test_two_frames_in_both_layouts_at_original_coordinates(self):
        image, expected = scan()
        for layout, source, target in [
            ('half-lr', image, expected),
            ('half-tb', image.T.copy(), [q[[0, 3, 2, 1], ::-1] for q in expected]),
        ]:
            source = np.repeat(np.repeat(source, 2, axis=0), 2, axis=1)
            for (result, error), quad in zip(detect_half_frames(source, layout), target):
                self.assertIsNone(error)
                np.testing.assert_allclose(result['corners'], quad * 2, atol=10)
                self.assertFalse(result['automatic_crop_allowed'])

    def test_dark_frame_does_not_invalidate_neighbor_or_guess_boundary(self):
        image, _ = scan(right_visible=False)
        first, second = detect_half_frames(image, 'half-lr')
        self.assertIsNotNone(first[0])
        self.assertIsNone(second[0])
        self.assertIn('手動框選', second[1])

    def test_project_can_confirm_export_and_remove_each_half_independently(self):
        image, _ = scan()
        raw = cv2.imencode('.png', image)[1].tobytes()
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            store = Store(Path(temp))
            try:
                def finish():
                    limit = time.monotonic() + 30
                    while any(j['status'] in ('queued', 'running') for j in store.snapshot()['jobs']):
                        self.assertLess(time.monotonic(), limit)
                        time.sleep(.02)
                imported = store.import_file('strip.png', 'strip.png', raw, layout='half-lr')
                a, b = imported['items']
                self.assertEqual([a['frame'], b['frame']], ['A', 'B'])
                self.assertEqual(a['source'], b['source'])
                self.assertTrue(store.import_file('again.png', 'again.png', raw, layout='half-lr')['duplicate'])
                finish()
                self.assertIsNotNone(a['corners'])
                self.assertIsNotNone(b['corners'])
                store.patch(a['id'], {'confirmed': True})
                job = store.create_job('export', [a['id'], b['id']], {'format': 'png'})
                finish()
                report = store.jobs[job['id']]
                self.assertEqual([x['id'] for x in report['outputs']], [a['id']])
                self.assertEqual([x['id'] for x in report['errors']], [b['id']])
                output = store.root/'Cropped'/job['id']/report['outputs'][0]['name']
                cropped = cv2.imdecode(np.fromfile(str(output), np.uint8), cv2.IMREAD_UNCHANGED)
                self.assertEqual(cropped.dtype, np.uint16)
                self.assertLess(cropped.shape[1], 640)
                self.assertEqual(json.loads(output.with_suffix('.png.json').read_bytes())['frame'], 'A')
                saved_source = store.source(b)
                store.remove_items([a['id']])
                self.assertEqual(hashlib.sha256(saved_source.read_bytes()).hexdigest(), b['sha256'])
                self.assertEqual(store.item(b['id'])['frame'], 'B')
                restored = store.import_file('strip.png', 'strip.png', raw, layout='half-lr')['items']
                self.assertEqual([x['frame'] for x in restored], ['A'])
                self.assertEqual(restored[0]['source'], b['source'])
                finish()
                removal = store.remove_items([b['id'], restored[0]['id']])
                self.assertFalse(saved_source.exists())
                self.assertTrue((Path(removal['recovery_folder'])/'Originals'/saved_source.name).is_file())
                self.assertTrue(output.is_file())
            finally:
                store.queue.put(None)
                store.worker.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
