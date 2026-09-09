"""Exercise the actual CLI request with the environment left by run_as_user."""
import io
import json
import unittest
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from contextlib import redirect_stdout
from unittest.mock import patch

from team_brain import tool


class ToolSessionTests(unittest.TestCase):
    def invoke(self, environment):
        output = io.StringIO()
        with patch.dict(tool.os.environ, {'HTTP_PROXY': 'http://127.0.0.1:18080', **environment}, clear=True), \
                patch.object(tool.sys, 'argv', ['team-brain-tool', 'knowledge_search']), \
                patch.object(tool.sys, 'stdin', io.StringIO('{"query":"fictional","subject":"team"}')), \
                patch.object(tool.urllib.request, 'build_opener') as build, redirect_stdout(output):
            build.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"status":"ok","sources":[]}'
            tool.main()
        return json.loads(output.getvalue()), build

    def test_isolated_session_uses_existing_gateway_action_token(self):
        # The deployed paired gateway injects one token under both names, but
        # its run_as_user filter/sudo env_keep only retain the existing name.
        result, build = self.invoke({'MYANC_CRM_ACTION_TOKEN': 'fictional-bound-session'})
        self.assertEqual(result['status'], 'ok')
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:18743/tool')
        self.assertEqual(request.get_header('Authorization'), 'Bearer fictional-bound-session')
        self.assertEqual(json.loads(request.data), {
            'command': 'knowledge_search', 'input': {'query': 'fictional', 'subject': 'team'}})

    def test_dedicated_and_matching_session_names(self):
        for environment in ({'TEAM_BRAIN_SESSION_TOKEN': 'fictional-session'},
                            {'TEAM_BRAIN_SESSION_TOKEN': 'fictional-session',
                             'MYANC_CRM_ACTION_TOKEN': 'fictional-session'}):
            with self.subTest(keys=list(environment)):
                result, build = self.invoke(environment)
                self.assertEqual(result['status'], 'ok')
                request = build.return_value.open.call_args.args[0]
                self.assertEqual(request.get_header('Authorization'), 'Bearer fictional-session')

    def test_missing_session_does_not_contact_host(self):
        result, build = self.invoke({'MYANC_CRM_HOST_SECRET': 'not-a-session'})
        self.assertEqual(result['code'], 'no_host_session')
        build.assert_not_called()

    def test_conflicting_session_names_fail_before_network(self):
        result, build = self.invoke({'TEAM_BRAIN_SESSION_TOKEN': 'fictional-one',
                                     'MYANC_CRM_ACTION_TOKEN': 'fictional-two'})
        self.assertEqual(result['code'], 'conflicting_host_session')
        build.assert_not_called()


class NativeProxyTests(unittest.TestCase):
    def test_native_loopback_proxy_is_used_despite_no_proxy(self):
        requests = []

        class Proxy(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append((self.path, self.headers['Authorization'],
                                 json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok","sources":[]}')

            def log_message(self, *_):
                pass

        with HTTPServer(('127.0.0.1', 0), Proxy) as server:
            server.timeout = 2
            worker = threading.Thread(target=server.handle_request)
            worker.start()
            output = io.StringIO()
            environment = {'MYANC_CRM_ACTION_TOKEN': 'fictional-native-session',
                           'http_proxy': 'http://127.0.0.1:' + str(server.server_port),
                           'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '*'}
            try:
                with patch.dict(tool.os.environ, environment, clear=True), \
                        patch.object(tool.sys, 'argv', ['team-brain-tool', 'knowledge_search']), \
                        patch.object(tool.sys, 'stdin', io.StringIO('{"query":"fictional","subject":"team"}')), \
                        redirect_stdout(output):
                    tool.main()
            finally:
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(json.loads(output.getvalue())['status'], 'ok')
            self.assertEqual(requests, [('http://127.0.0.1:18743/tool', 'Bearer fictional-native-session',
                                        {'command': 'knowledge_search', 'input': {'query': 'fictional', 'subject': 'team'}})])

    def test_proxy_validation_rejects_missing_remote_and_credentialed_endpoints(self):
        for proxy in ('', 'http://example.test:3128', 'https://127.0.0.1:3128',
                      'http://user:password@127.0.0.1:3128', 'http://127.0.0.1:3128/path',
                      'http://127.0.0.1:3128?query=1', 'http://127.0.0.1:99999',
                      'http://127.0.0.1:3128\n'):
            with self.subTest(proxy=proxy):
                result, build = ToolSessionTests().invoke({'MYANC_CRM_ACTION_TOKEN': 'fictional-session',
                                                         'HTTP_PROXY': proxy})
                self.assertEqual(result['code'], 'sandbox_proxy_required')
                build.assert_not_called()

if __name__ == '__main__':
    unittest.main()
