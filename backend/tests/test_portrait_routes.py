"""Portrait API shape and safe errors; direct handlers avoid sandbox sockets."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import xml.etree.ElementTree as ET

from fastapi import HTTPException

from backend.tests.test_candidates import fixture
from backend.tests.test_edl import source_fixture
from clipforge.decision.candidates import generate_candidates
from clipforge.editing import edl, mlt
from clipforge.routes import editing
from clipforge.services.editing import ProjectError


class PortraitRouteTests(unittest.TestCase):
    def test_returns_new_revision_location_and_preserves_parent_identity(self):
        task, parent, new_id = uuid4(), uuid4(), str(uuid4())
        value = {'revision_id': new_id, 'derivation': {'parent_revision_id': str(parent)}}
        with patch.object(editing.editing_service, 'create_task_portrait', return_value=value) as create:
            response = editing.create_portrait(task, parent)
        create.assert_called_once_with(str(task), str(parent))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers['location'], f'/tasks/{task}/edl/{new_id}')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(json.loads(response.body), value)

    def test_service_error_safe_and_unexpected_private_output_hidden(self):
        for error, expected in ((ProjectError(409, '需要原横屏版本。'), 409),
                                (RuntimeError('private local paths'), 500)):
            with patch.object(editing.editing_service, 'create_task_portrait', side_effect=error), \
                    patch.object(editing.logger, 'exception') as logged:
                with self.assertRaises(HTTPException) as caught:
                    editing.create_portrait(uuid4(), uuid4())
            self.assertEqual(logged.call_count, int(expected == 500))
            self.assertEqual(caught.exception.status_code, expected)
            self.assertNotIn('private', caught.exception.detail)

    def test_openapi_exposes_explicit_fixed_portrait_endpoint(self):
        from clipforge.api import app

        operation = app.openapi()['paths']['/tasks/{task_id}/edl/{revision_id}/portrait']['post']
        self.assertIn('201', operation['responses'])
        self.assertNotIn('requestBody', operation)
        for parameter in operation['parameters']:
            self.assertEqual(parameter['schema']['format'], 'uuid')

    def test_mlt_preserves_saved_crop_and_inclusive_cut_ranges(self):
        source = source_fixture(total_frames=930)
        analysis = fixture([(0, 10), (10, 20), (20, 30)])
        analysis['media']['normalized_sha256'] = source['sha256']
        candidates = generate_candidates(analysis)
        original = edl.candidates_to_edl(candidates, [candidates['candidates'][0]['id']], source)
        portrait = edl.create_portrait_edl(original)
        with tempfile.TemporaryDirectory() as folder:
            root = ET.fromstring(mlt.export_mlt(portrait, Path(folder), require_media=False))
        self.assertEqual((root.find('profile').get('width'), root.find('profile').get('height')), ('1080', '1920'))
        effect = root.find('chain/filter')
        properties = {value.get('name'): value.text for value in effect.findall('property')}
        crop = portrait['framing']['sources'][0]
        self.assertEqual(properties['mlt_service'], 'crop')
        self.assertEqual((properties['left'], properties['top']), (str(crop['x']), str(crop['y'])))
        self.assertEqual(properties['right'], str(source['width'] - crop['x'] - crop['width']))
        self.assertEqual(properties['bottom'], str(source['height'] - crop['y'] - crop['height']))
        entry = root.find("playlist[@id='playlist0']/entry")
        clip = portrait['clips'][0]
        self.assertEqual((int(entry.get('in')), int(entry.get('out'))),
                         (clip['source_in_frame'], clip['source_out_frame'] - 1))


if __name__ == '__main__':
    unittest.main()
