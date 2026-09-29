import json
import unittest
import zipfile
from io import BytesIO
from unittest.mock import patch

import document_generator


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode('utf-8')


class DocumentGeneratorTests(unittest.TestCase):
    def test_docx_and_pdf_are_valid_containers(self):
        docx = document_generator._docx_bytes('Jane Example\nIntegration Engineer')
        with zipfile.ZipFile(BytesIO(docx)) as archive:
            self.assertIn('word/document.xml', archive.namelist())
            self.assertIn(b'Jane Example', archive.read('word/document.xml'))
        pdf = document_generator._pdf_bytes('Jane Example\nIntegration Engineer')
        self.assertTrue(pdf.startswith(b'%PDF-1.4'))
        self.assertTrue(pdf.rstrip().endswith(b'%%EOF'))

    def test_openai_request_uses_store_false_and_structured_output(self):
        captured = {}
        generated = {
            'tailored_resume_text': 'Built HL7 interfaces.',
            'cover_letter_text': 'I built HL7 interfaces.',
            'changes': [{'summary': 'Emphasized HL7 work',
                         'resume_evidence': 'Built HL7 interfaces'}],
            'claim_trace': [{'generated_claim': 'built HL7 interfaces',
                             'resume_evidence': 'Built HL7 interfaces'}],
        }

        def fake_urlopen(request, timeout):
            captured['payload'] = json.loads(request.data)
            return FakeResponse({
                'output_text': json.dumps(generated),
                'usage': {'input_tokens': 1000, 'output_tokens': 500},
            })

        with patch('document_generator.urllib.request.urlopen', fake_urlopen):
            result, usage = document_generator._call_openai(
                'sk-test', 'Built HL7 interfaces',
                {'title': 'Integration Engineer'}, {'full_name': 'Jane Example'})
        self.assertEqual(result, generated)
        self.assertIs(captured['payload']['store'], False)
        self.assertEqual(captured['payload']['text']['format']['type'], 'json_schema')
        self.assertEqual(usage['input_tokens'], 1000)
        self.assertGreater(usage['actual_cost_usd'], 0)

    def test_change_evidence_must_come_from_master_resume(self):
        with self.assertRaises(document_generator.GenerationError):
            document_generator._validate({
                'tailored_resume_text': 'Invented skill',
                'cover_letter_text': 'Invented skill',
                'changes': [{'summary': 'Added a credential',
                             'resume_evidence': 'Certified in imaginary systems'}],
                'claim_trace': [{'generated_claim': 'Invented skill',
                                 'resume_evidence': 'Certified in imaginary systems'}],
            }, 'Built HL7 interfaces')

    def test_anthropic_uses_messages_structured_output(self):
        captured = {}
        generated = {'tailored_resume_text': 'Built HL7 interfaces.',
                     'cover_letter_text': 'I built HL7 interfaces.',
                     'changes': [], 'claim_trace': []}

        def fake_urlopen(request, timeout):
            captured['payload'] = json.loads(request.data)
            captured['headers'] = dict(request.headers)
            return FakeResponse({'content': [{'type': 'text',
                                              'text': json.dumps(generated)}],
                                 'usage': {'input_tokens': 120, 'output_tokens': 80}})

        with patch('document_generator.urllib.request.urlopen', fake_urlopen):
            result, usage = document_generator._call_anthropic(
                'test-key', 'Built HL7 interfaces', {'title': 'Engineer'}, {})
        self.assertEqual(result, generated)
        self.assertEqual(captured['payload']['model'], 'claude-sonnet-5')
        self.assertEqual(captured['payload']['output_config']['format']['type'],
                         'json_schema')
        self.assertEqual(usage['provider'], 'anthropic')

    def test_xai_uses_chat_completions_structured_output(self):
        captured = {}
        generated = {'tailored_resume_text': 'Built HL7 interfaces.',
                     'cover_letter_text': 'I built HL7 interfaces.',
                     'changes': [], 'claim_trace': []}

        def fake_urlopen(request, timeout):
            captured['url'] = request.full_url
            captured['payload'] = json.loads(request.data)
            return FakeResponse({'choices': [{'message': {
                'content': json.dumps(generated)}}],
                'usage': {'prompt_tokens': 120, 'completion_tokens': 80}})

        with patch('document_generator.urllib.request.urlopen', fake_urlopen):
            result, usage = document_generator._call_xai(
                'test-key', 'Built HL7 interfaces', {'title': 'Engineer'}, {})
        self.assertEqual(result, generated)
        self.assertEqual(captured['url'], 'https://api.x.ai/v1/chat/completions')
        self.assertTrue(captured['payload']['response_format']['json_schema']['strict'])
        self.assertEqual(usage['provider'], 'xai')

    def test_resume_only_schema_and_estimate_exclude_cover_letter(self):
        estimate = document_generator.estimate_cost(
            'Built HL7 interfaces', {'title': 'Engineer'}, {}, 'openai', True, False)
        schema = document_generator._output_schema(['resume'])
        self.assertEqual(estimate['generated_documents'], ['resume'])
        self.assertNotIn('cover_letter_text', schema['properties'])
        self.assertLess(estimate['estimated_output_tokens'],
                        document_generator.MAX_OUTPUT_TOKENS)

    def test_generation_requires_at_least_one_document_kind(self):
        with self.assertRaises(document_generator.GenerationError):
            document_generator.estimate_cost(
                'Resume', {'title': 'Engineer'}, {}, 'openai', False, False)


if __name__ == '__main__':
    unittest.main()
