"""Normal-path integration checks against isolated synthetic backends only."""
import io,json,threading,time,unittest,urllib.request,wave
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import test_release as fixture
from home_runtime.chat import LocalChat
from home_runtime.files import atomic_json,database
from home_runtime.gateway_handler import gateway_handler
from home_runtime.model import ModelClient
from home_runtime.services import Services

@contextmanager
def serve(handler):
 server=ThreadingHTTPServer(('127.0.0.1',0),handler)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 try: yield server
 finally: server.shutdown();server.server_close();thread.join(timeout=3)

class IntegrationTests(unittest.TestCase):
 setUp = fixture.ReleaseTests.setUp
 tearDown = fixture.ReleaseTests.tearDown
 def test_tool_calls_preserved_through_sse_gateway(self):
  sent=[]
  def transport(url,payload,key):
   sent.append(payload)
   return {'id':'synthetic','choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'synthetic_weather','arguments':'{}'}}]},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':20,'completion_tokens':8}}
  client=ModelClient(self.root,transport)
  payload={'messages':[{'role':'user','content':'synthetic'}],'stream':True,'tools':[{'type':'function','function':{'name':'synthetic_weather','parameters':{'type':'object','properties':{}}}}]}
  with serve(gateway_handler(client,'synthetic-local-token')) as server:
   request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer synthetic-local-token','Content-Type':'application/json'})
   with urllib.request.urlopen(request) as response: raw=response.read().decode()
  chunks=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: {')]
  self.assertEqual(chunks[0]['choices'][0]['delta']['tool_calls'][0]['function']['name'],'synthetic_weather')
  self.assertIn('data: [DONE]',raw);self.assertEqual(sent[0]['tools'],payload['tools']);self.assertFalse(sent[0]['stream'])
 def test_reply_survives_memory_outage_and_duplicate_submit(self):
  persona=self.root/'dafeiyu/data/config/personality.md';persona.write_text('Synthetic test persona',encoding='utf-8')
  sent=[]
  def transport(*args):
   sent.append(1);return {'choices':[{'message':{'content':'Durable synthetic reply'}}],'usage':{'prompt_tokens':10,'completion_tokens':8}}
  chat=LocalChat(self.root,ModelClient(self.root,transport))
  def core(route,body):
   if route=='/api/context/prepare':return {'ok':True,'block':''}
   raise OSError('Synthetic core temporarily unavailable')
  chat.core=core
  body={'text':'Synthetic request','request_id':'synthetic-request-1'}
  reply=chat.send(body);self.assertEqual(reply['status'],'completed');self.assertEqual(reply['reply'],'Durable synthetic reply')
  self.assertEqual(chat.send(body)['reply'],'Durable synthetic reply');self.assertEqual(len(sent),1)
  deadline=time.monotonic()+2
  while chat.memory_lock.locked() and time.monotonic()<deadline:time.sleep(.01)
  restored=LocalChat(self.root,chat.model);restored.core=lambda *args:{'ok':True};restored.flush_memories()
  with database(restored.db) as conn:self.assertEqual(conn.execute('SELECT saved FROM memory_queue').fetchone()[0],2)
 def test_pending_chat_is_not_automatically_resent(self):
  chat=LocalChat(self.root)
  with database(chat.db) as conn:conn.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',('pending-test','synthetic','','pending','2026-01-01',''))
  restored=LocalChat(self.root,ModelClient(self.root,lambda *args:self.fail('must not call model')))
  self.assertEqual(restored.history()[0]['status'],'unconfirmed')
 def test_speech_and_transcription_use_configured_backend(self):
  seen=[];audio=io.BytesIO()
  with wave.open(audio,'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0'*320)
  wav_data=audio.getvalue()
  class Backend(BaseHTTPRequestHandler):
   def do_POST(self):
    body=self.rfile.read(int(self.headers['Content-Length']));seen.append((self.path,body))
    raw=wav_data if self.path.endswith('/speech') else json.dumps({'text':'Synthetic transcript'}).encode()
    self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
   def log_message(self,*args):pass
  with serve(Backend) as server:
   config=ModelClient(self.root).settings();config['integrations']={'tts':{'enabled':True,'backend':'openai','http_url':f'http://127.0.0.1:{server.server_port}/v1','model':'synthetic-tts','voice':'synthetic-voice','price_per_request':.1},'asr':{'enabled':True,'http_url':f'http://127.0.0.1:{server.server_port}/v1','model':'synthetic-asr','price_per_request':.1}}
   atomic_json(self.root/'release/settings.json',config)
   service=Services(self.root);self.assertEqual(service.speech('Synthetic speech')[1],wav_data);self.assertEqual(service.transcribe(wav_data)['text'],'Synthetic transcript')
  self.assertEqual([path for path,_ in seen],['/v1/audio/speech','/v1/audio/transcriptions'])
  self.assertIn(b'Synthetic speech',seen[0][1]);self.assertAlmostEqual(service.client.usage()['estimated_cost'],.2)

 def test_image_import_preserves_original_and_deduplicates(self):
  import base64,hashlib
  from PIL import Image
  from home_runtime.memes import import_image
  output=io.BytesIO();Image.new('RGB',(12,12),(120,180,230)).save(output,'PNG');raw=output.getvalue()
  body={'file':base64.b64encode(raw).decode(),'name':'synthetic-picture.png'}
  first=import_image(self.root,body);second=import_image(self.root,body)
  self.assertTrue(first['added']);self.assertFalse(second['added'])
  self.assertEqual((self.root/'meme-library'/(hashlib.sha256(raw).hexdigest()+'.png')).read_bytes(),raw)
