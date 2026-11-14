import json
import unittest
from types import SimpleNamespace

from server.bk_interpreter import InterpretationError, interpret


class Response:
    status_code = 200
    def __init__(self, content):
        self.content = content
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def iter_content(self, chunk_size):
        yield json.dumps({'choices': [{'message': {'content': self.content}}]}).encode()


class InterpreterTests(unittest.TestCase):
    def model(self, **changes):
        values = {'provider': 'openai_compatible', 'api_base': 'https://model.example/v1',
                  'model_name': 'example', 'api_key': 'test-only'}
        return SimpleNamespace(**dict(values, **changes))

    def test_untrusted_model_output_is_returned_only_as_candidate(self):
        candidate = {'goal': '读取状态', 'target': {'kind': 'group', 'name': '一组'}, 'schedule': {'kind': 'now'}}
        def post(url, **kwargs):
            self.assertEqual(url, 'https://model.example/v1/chat/completions')
            self.assertFalse(kwargs['allow_redirects'])
            return Response(json.dumps(candidate, ensure_ascii=False))
        result = interpret(self.model(), '让一组读取状态', 'Asia/Shanghai', {}, '2026-11-14T12:00:00+08:00', post)
        self.assertEqual(result, candidate)

    def test_clarification_is_kept_as_question(self):
        result = interpret(self.model(), '看一下', 'Asia/Shanghai', {}, 'now',
                           lambda *args, **kwargs: Response('{"clarification":"请指定手机。"}'))
        self.assertEqual(result, {'clarification': '请指定手机。'})

    def test_malformed_output_and_unsupported_provider_fail_closed(self):
        with self.assertRaisesRegex(InterpretationError, 'model_response_invalid'):
            interpret(self.model(), '任务', 'Asia/Shanghai', {}, 'now',
                      lambda *args, **kwargs: Response('{"goal":"任务","code":"run"}'))
        with self.assertRaisesRegex(InterpretationError, 'model_provider_unsupported'):
            interpret(self.model(provider='unknown'), '任务', 'Asia/Shanghai', {}, 'now')

    def test_existing_anthropic_and_ollama_models_use_their_protocols(self):
        candidate = json.dumps({'clarification':'请选择设备'}, ensure_ascii=False)
        for provider, expected_url, envelope in [
            ('anthropic', 'https://model.example/v1/messages', {'content':[{'type':'text','text':candidate}]}),
            ('ollama', 'https://model.example/v1/api/chat', {'message':{'content':candidate}}),
        ]:
            class ProviderResponse(Response):
                def iter_content(self, chunk_size): yield json.dumps(envelope).encode()
            def post(url, **kwargs):
                self.assertEqual(url, expected_url)
                return ProviderResponse('')
            with self.subTest(provider=provider):
                result = interpret(self.model(provider=provider), '任务', 'Asia/Shanghai', {}, 'now', post)
                self.assertEqual(result['clarification'], '请选择设备')

    def test_redirects_are_not_followed_and_credentials_not_in_error(self):
        class Redirect(Response):
            status_code = 302
        with self.assertRaisesRegex(InterpretationError, '^model_request_failed$'):
            interpret(self.model(), '任务', 'Asia/Shanghai', {}, 'now',
                      lambda *args, **kwargs: Redirect(''))

    def test_unsupported_long_task_does_not_call_model_or_dispatch(self):
        for message in ('让一组浏览两小时', '每隔2小时执行', '每天持续运行', '浏览 for 2 hours'):
            def no_call(*args, **kwargs):
                self.fail('unsupported task must not call the model')
            with self.subTest(message=message):
                result = interpret(self.model(), message, 'Asia/Shanghai', {}, 'now', no_call)
                self.assertIn('clarification', result)


if __name__ == '__main__':
    unittest.main()
