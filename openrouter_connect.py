"""Loopback PKCE connection. Secrets never enter URLs, console or plain files."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.parse
import urllib.request


def protect(value,decrypt=False):
    if os.name!='nt':raise ValueError('Защищённое хранение доступно только в Windows.')
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(value);source=Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL;kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):
        raise ValueError('Windows не смогла защитить или прочитать ключ.')
    try:return ctypes.string_at(out.data,out.size)
    finally:kernel.LocalFree(out.data)


def save_key(path,key):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp');temporary.write_bytes(protect(key.encode()))
    temporary.replace(path)


def load_key(path):
    try:return protect(Path(path).read_bytes(),decrypt=True).decode()
    except FileNotFoundError:return ''


class Connection:
    def __init__(self,on_key,timeout=600):
        self.on_key=on_key;self.timeout=timeout;self.done=threading.Event();self.error=None
        self.verifier=secrets.token_urlsafe(48);self.state=secrets.token_urlsafe(32)
        self.path='/connect/'+secrets.token_urlsafe(24)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup();self.connection.settimeout(5)
            def log_message(self,*args):pass
            def do_GET(self):
                query=urllib.parse.urlsplit(self.path);values=urllib.parse.parse_qs(query.query)
                if query.path!=owner.path:
                    self.send_error(404);return
                if values.get('state')!=[owner.state] or len(values.get('code',[]))!=1 or owner.done.is_set():
                    self.send_error(400,'Invalid callback');return
                try:
                    key=owner.exchange(values['code'][0]);owner.on_key(key)
                    body='Shorts Studio AI connected. You can close this tab. No video analysis was started.'.encode()
                    self.send_response(200)
                except Exception:
                    owner.error='Не удалось завершить подключение. Повторите вход из приложения.'
                    body=owner.error.encode();self.send_response(502)
                self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Cache-Control','no-store')
                self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(body)
                owner.done.set()
        self.server=HTTPServer(('127.0.0.1',0),Handler);self.server.timeout=.3
        callback=f'http://127.0.0.1:{self.server.server_port}{self.path}'
        challenge=base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip('=')
        self.url='https://openrouter.ai/auth?'+urllib.parse.urlencode(dict(callback_url=callback,code_challenge=challenge,
            code_challenge_method='S256',state=self.state,key_label='Shorts Studio AI'))

    def exchange(self,code):
        request=urllib.request.Request('https://openrouter.ai/api/v1/auth/keys',
            data=json.dumps(dict(code=code,code_verifier=self.verifier,code_challenge_method='S256')).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=30) as response:raw=response.read(65537)
        if len(raw)>65536:raise ValueError('Invalid response')
        key=json.loads(raw).get('key')
        if not isinstance(key,str) or not key or len(key)>4096:raise ValueError('Invalid key')
        return key

    def run(self):
        deadline=time.monotonic()+self.timeout
        try:
            while not self.done.is_set() and time.monotonic()<deadline:self.server.handle_request()
            if not self.done.is_set():self.error='Время подключения истекло. Нажмите «Подключить» ещё раз.'
        finally:self.server.server_close();self.verifier='';self.done.set()


if __name__=='__main__':
    # Agent-assisted setup: key is stored only after the user approves browser consent.
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--save-to',type=Path,required=True);args=parser.parse_args()
    flow=Connection(lambda key:save_key(args.save_to,key))
    print(flow.url,flush=True);flow.run()
    print('CONNECTED' if not flow.error else flow.error,flush=True)
