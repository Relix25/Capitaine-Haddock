import io
import json
import unittest

from courswatch.conversation.metrics import (MeasuredResult, footer, local_metrics,
    read_model_response, reply_with_footer, response_metrics)


def stream(*events):
    return io.BytesIO(b''.join(b'data: '+json.dumps(event).encode()+b'\n\n' for event in events))


class MetricsTests(unittest.TestCase):
    def test_first_token_ignores_placeholder_events_and_assembles_terminal_output(self):
        times=iter((10.2,10.7))
        source=stream({'type':'response.created'},
            {'type':'response.output_text.delta','delta':''},
            {'type':'response.output_text.delta','delta':'{"action":"text",'},
            {'type':'response.output_text.delta','delta':'"reply":"Bonjour.","sample_id":"none"}'},
            {'type':'response.completed','response':{'status':'completed',
                'usage':{'input_tokens':100,'output_tokens':21}}})
        result=read_model_response(source,10,clock=lambda:next(times))
        self.assertAlmostEqual(result.metrics['ttft_ms'],200)
        self.assertAlmostEqual(result.metrics['api_ms'],700)
        self.assertEqual(json.loads(result['output'][0]['content'][0]['text'])['reply'],'Bonjour.')
        metrics=response_metrics(result,'deepseek/deepseek-v4.1-flash',900)
        self.assertAlmostEqual(metrics['output_tps'],40)
        self.assertEqual(metrics['total_tokens'],121)

    def test_json_gateway_has_unknown_ttft_instead_of_total_disguised_as_ttft(self):
        result=read_model_response(io.BytesIO(b'{"status":"completed","output":[]}'),2,clock=lambda:3)
        self.assertIsNone(result.metrics['ttft_ms'])
        self.assertEqual(result.metrics['api_ms'],1000)
        self.assertIsNone(response_metrics(result,'test',1000)['output_tps'])

    def test_incomplete_and_refusal_results_preserve_usage_and_status(self):
        for status in ('incomplete','failed'):
            result=read_model_response(stream({'type':'response.'+status,'response':{
                'status':status,'usage':{'input_tokens':100,'output_tokens':5}}}),0,clock=lambda:1)
            self.assertEqual(result['status'],status)
            self.assertEqual(result['usage']['output_tokens'],5)
            self.assertIsNone(result.metrics['ttft_ms'])

    def test_truncated_stream_does_not_produce_a_successful_response(self):
        source=stream({'type':'response.output_text.delta','delta':'partial'})
        with self.assertRaisesRegex(ValueError,'without terminal'):
            read_model_response(source,0,clock=lambda:1)

    def test_usage_total_is_authoritative_and_cache_is_a_subset_of_input(self):
        response=MeasuredResult({'usage':{'input_tokens':100,'output_tokens':21,'total_tokens':121,
            'input_tokens_details':{'cached_tokens':80}}},{'ttft_ms':100,'api_ms':600})
        metrics=response_metrics(response,'deepseek/deepseek-v4.1-flash',999)
        self.assertEqual(metrics['total_tokens'],121)
        self.assertEqual(metrics['cached_input_tokens'],80)
        self.assertEqual(metrics['output_tps'],40)
        rendered=footer(metrics)
        self.assertIn('121 tokens',rendered)
        self.assertIn('100 entrée + 21 sortie ; cache 80',rendered)
        self.assertIn('≈ 40,0 tok/s',rendered)

    def test_tps_excludes_reported_reasoning_and_does_not_divide_near_zero(self):
        response=MeasuredResult({'usage':{'input_tokens':100,'output_tokens':41,
            'output_tokens_details':{'reasoning_tokens':20}}},{'ttft_ms':100,'api_ms':600})
        self.assertEqual(response_metrics(response,'test',600)['output_tps'],40)
        response.metrics['api_ms']=101
        self.assertIsNone(response_metrics(response,'test',101)['output_tps'])

    def test_unknown_usage_is_not_displayed_as_zero(self):
        metrics=response_metrics({'usage':{}},'test',10)
        rendered=footer(metrics)
        self.assertIn('tokens non confirmés',rendered)
        self.assertIn('TTFT n/d',rendered)
        self.assertNotIn('0 tokens',rendered)

    def test_local_reply_reports_zero_model_usage_and_unknown_ttft(self):
        rendered=footer(local_metrics(),local_response=True)
        self.assertIn('Local',rendered)
        self.assertIn('0 tokens',rendered)
        self.assertIn('TTFT n/d',rendered)

    def test_discord_subtext_remains_outside_a_truncated_code_block(self):
        rendered=reply_with_footer('```python\n'+('x'*3000),{
            'model':'deepseek/deepseek-v4.1-flash','ttft_ms':200,'api_ms':700,'total_tokens':100})
        self.assertLessEqual(len(rendered),2000)
        body,subtext=rendered.rsplit('\n\n',1)
        self.assertEqual(body.count('```')%2,0)
        self.assertTrue(subtext.startswith('-# '))

    def test_footer_uses_only_known_metrics_never_identifiers_or_error_text(self):
        rendered=footer({'model':'sk-private-synthetic-secret','error':'PRIVATE_ERROR',
            'user_id':'123456789012345678','total_tokens':None,'ttft_ms':float('nan'),'api_ms':-1})
        self.assertNotIn('PRIVATE_ERROR',rendered)
        self.assertNotIn('sk-private',rendered)
        self.assertNotIn('123456789012345678',rendered)
        self.assertNotIn('nan',rendered)


if __name__=='__main__':unittest.main()
