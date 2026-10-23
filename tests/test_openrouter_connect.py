import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch,MagicMock
import urllib.request
import urllib.error
from urllib.parse import urlparse,parse_qs,urlencode
from openrouter_connect import Connection,save_key,load_key


class ConnectionTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_saved_key_is_encrypted_and_round_trips(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            p=Path(folder)/'key.dpapi';save_key(p,'test-secret-123')
            self.assertNotIn(b'test-secret-123',p.read_bytes())
            self.assertEqual(load_key(p),'test-secret-123')

    def test_loopback_callback_rejects_wrong_state_and_only_exchanges_once(self):
        keys=[];flow=Connection(keys.append,timeout=5)
        query=parse_qs(urlparse(flow.url).query)
        self.assertEqual(query['code_challenge_method'],['S256'])
        self.assertNotIn(flow.verifier,flow.url)
        callback=query['callback_url'][0]
        with patch.object(flow,'exchange',return_value='test-key') as exchange:
            thread=threading.Thread(target=flow.run);thread.start()
            try:
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(callback+'?'+urlencode(dict(code='fake',state='wrong')),timeout=2)
                exchange.assert_not_called()
                with urllib.request.urlopen(callback+'?'+urlencode(dict(code='test-code',state=flow.state)),timeout=2) as response:
                    self.assertEqual(response.status,200)
                thread.join(3);self.assertFalse(thread.is_alive())
                self.assertEqual(keys,['test-key']);exchange.assert_called_once_with('test-code')
            finally:flow.done.set();thread.join(3)

    def test_exchange_uses_fixed_endpoint_without_key_in_url(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"key":"test-key"}'
        flow=Connection(lambda _:None)
        try:
            with patch('urllib.request.urlopen',return_value=response) as request:
                self.assertEqual(flow.exchange('one-time-code'),'test-key')
                sent=request.call_args.args[0]
                self.assertEqual(sent.full_url,'https://openrouter.ai/api/v1/auth/keys')
                self.assertEqual(json.loads(sent.data)['code_verifier'],flow.verifier)
        finally:flow.server.server_close()
