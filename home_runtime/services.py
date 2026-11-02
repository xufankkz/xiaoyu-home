"""Explicitly configured speech and image services, metered before dispatch."""
from pathlib import Path
import json
import urllib.request
import uuid
from .model import ModelClient, endpoint
from .files import read_json

class Services:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.client = ModelClient(workspace)

    def dispatch(self, feature, url, raw, content_type, key, price, limit=32*1024*1024):
        identifier = self.client.reserve_fixed(feature, price)
        try:
            headers = {'Content-Type': content_type, 'User-Agent': 'XiaoyuHome/0.2.0-beta.1'}
            if key:
                headers['Authorization'] = 'Bearer ' + key
            request = urllib.request.Request(url, data=raw, headers=headers, method='POST')
            with urllib.request.urlopen(request, timeout=240) as response:
                data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError('服务响应超过大小限制。')
            self.client.finish_fixed(identifier, True)
            return data
        except Exception:
            self.client.finish_fixed(identifier, False)
            raise ValueError('本次服务请求未能确认，请核对后端配置；不会自动重发，预留费用暂保留。') from None

    def speech(self, text, tone='gentle'):
        config = self.client.settings().get('integrations', {}).get('tts', {})
        if not config.get('enabled') or not config.get('http_url'):
            raise ValueError('请在接入与组件页面配置并启用自己的语音后端。')
        text = str(text or '').strip()
        if not 0 < len(text) <= 300:
            raise ValueError('语音文本需要 1–300 字。')
        if config.get('backend') == 'openai':
            payload = {'model': config.get('model', ''), 'voice': config.get('voice', ''), 'input': text, 'response_format': 'wav'}
            route = '/audio/speech'
        else:
            tones = read_json(self.workspace / 'astrbot/data/plugins/astrbot_plugin_xiaoyu_voice/voice_config.json').get('tones', {})
            selected = tones.get(tone) or tones.get('gentle') or {}
            reference = selected.get('ref') or config.get('reference_path', '')
            if not reference:
                raise ValueError('GPT-SoVITS 需要你自己的参考音频路径。')
            payload = {'text': text, 'text_lang': 'zh', 'ref_audio_path': reference,
                'prompt_text': selected.get('prompt') or config.get('prompt_text', ''), 'prompt_lang': 'zh',
                'text_split_method': 'cut0', 'batch_size': 1, 'media_type': 'wav', 'streaming_mode': False,
                'temperature': selected.get('temperature', 0.8), 'speed_factor': selected.get('speed', 1.0)}
            route = '/tts'
        audio = self.dispatch('speech', endpoint(config['http_url']) + route, json.dumps(payload).encode(),
            'application/json', self.client.secrets.get('tts:api_key'), config.get('price_per_request', 0))
        if len(audio) < 44 or not audio.startswith(b'RIFF'):
            raise ValueError('语音后端没有返回 WAV 音频，请检查协议和模型。')
        return tone, audio, text

    def transcribe(self, raw):
        config = self.client.settings().get('integrations', {}).get('asr', {})
        if not config.get('enabled') or not config.get('http_url') or not config.get('model'):
            raise ValueError('尚未配置语音转写，请在接入与组件页面设置本地或 API 转写服务。')
        boundary = 'xiaoyu-' + uuid.uuid4().hex
        form = ('--' + boundary + '\r\nContent-Disposition: form-data; name="model"\r\n\r\n' + config['model'] +
                '\r\n--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
        payload = form + raw + ('\r\n--' + boundary + '--\r\n').encode()
        data = json.loads(self.dispatch('transcription', endpoint(config['http_url']) + '/audio/transcriptions', payload,
            'multipart/form-data; boundary=' + boundary, self.client.secrets.get('asr:api_key'), config.get('price_per_request', 0), 1024*1024))
        return {'text': str(data.get('text', '')), 'segments': data.get('segments', []), 'silence': False}

    def image(self, body):
        settings = self.client.settings()
        config = settings.get('models', {}).get('drawing') or read_json(self.workspace / 'qq-bridge/data/drawing/config.json')
        if not config.get('endpoint') or not config.get('model'):
            raise ValueError('请配置自己的绘画 API 和模型。')
        key = self.client.secrets.get('model:drawing')
        if not key and config.get('key_mode') == 'chat':
            key = self.client.secrets.get('model:chat')
        payload = {'model': config['model'], 'prompt': str(body.get('prompt', ''))[:14000], 'n': 1,
                   'size': str(body.get('size', config.get('size', '1024x1024')))}
        raw = self.dispatch('drawing', endpoint(config['endpoint']) + '/images/generations', json.dumps(payload).encode(),
            'application/json', key, config.get('price_per_request', 0))
        return json.loads(raw)
