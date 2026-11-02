"""Ordinary visible Windows interaction with the user's existing Douyin window.

No process injection, hidden browser endpoints, login export or arbitrary model clicks.
Only this allowlisted set of UI operations is exposed to the social worker.
"""
from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes as w
import hashlib
import io
import os
from pathlib import Path
import json
import re
import time


class DesktopUnavailable(RuntimeError):
    pass


def compact(text):
    return re.sub(r'\s+', '', str(text))


def recognize(image):
    """Local Windows OCR; the screenshot never leaves this function for OCR."""
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
    from winrt.windows.storage.streams import DataWriter

    async def run():
        writer = DataWriter()
        writer.write_bytes(image.convert('RGBA').tobytes('raw', 'BGRA'))
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                         image.width, image.height, BitmapAlphaMode.PREMULTIPLIED)
        try:
            engine = OcrEngine.try_create_from_language(Language('zh-Hans-CN'))
            if engine is None:
                raise DesktopUnavailable('Windows 中文文字识别未安装，请先安装中文语言识别组件。')
            result = await engine.recognize_async(bitmap)
            lines = []
            for line in result.lines:
                words = list(line.words)
                if not words:
                    continue
                x = min(v.bounding_rect.x for v in words)
                y = min(v.bounding_rect.y for v in words)
                right = max(v.bounding_rect.x + v.bounding_rect.width for v in words)
                bottom = max(v.bounding_rect.y + v.bounding_rect.height for v in words)
                lines.append({'text': compact(line.text), 'x': x, 'y': y, 'w': right-x, 'h': bottom-y})
            return lines
        finally:
            bitmap.close()
            writer.close()
    return asyncio.run(run())


class DouyinDesktop:
    def __init__(self):
        self.window = None
        self.last_frame = None
        self._initial = None
        self._full_image = None
        self._staged = None
        self._editor_focused = False
        self._owns_draft = False
        self._may_focus = True
        self._source_binding = None
        self.binding_path = Path(__file__).resolve().parents[1] / 'qq-bridge/data/douyin/mention-binding.json'

    def _api(self):
        if os.name != 'nt':
            raise DesktopUnavailable('桌面抖音连接仅支持 Windows。')
        u, g = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('gdi32', use_last_error=True)
        try:
            u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        for name in ('GetForegroundWindow', 'GetThreadDesktop'):
            getattr(u, name).restype = w.HWND
        u.GetWindowDC.argtypes = [w.HWND]
        u.GetWindowDC.restype = w.HDC
        u.ReleaseDC.argtypes = [w.HWND, w.HDC]
        u.GetAncestor.argtypes = [w.HWND, w.UINT]
        u.GetAncestor.restype = w.HWND
        u.WindowFromPoint.argtypes = [w.POINT]
        u.WindowFromPoint.restype = w.HWND
        u.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
        u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        u.SetForegroundWindow.argtypes = [w.HWND]
        u.IsWindow.argtypes = [w.HWND]
        u.IsIconic.argtypes = [w.HWND]
        u.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        g.CreateCompatibleDC.argtypes = [w.HDC]
        g.CreateCompatibleDC.restype = w.HDC
        g.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
        g.CreateCompatibleBitmap.restype = w.HBITMAP
        g.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
        g.SelectObject.restype = w.HGDIOBJ
        g.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT]
        g.DeleteObject.argtypes = [w.HGDIOBJ]
        g.DeleteDC.argtypes = [w.HDC]
        return u, g

    def connect(self, *, foreground_wait=0, cancelled=None):
        if cancelled and cancelled():
            raise DesktopUnavailable('本轮已暂停。')
        u, _ = self._api()
        found = []
        callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        def visit(hwnd, _):
            title = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, title, 256)
            if title.value == '抖音' and u.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True
        callback = callback_type(visit)
        u.EnumWindows(callback, 0)
        if len(found) != 1:
            raise DesktopUnavailable('请在当前 Windows 桌面打开唯一的抖音主窗口并保持登录。')
        self.window = found[0]
        pid = w.DWORD()
        u.GetWindowThreadProcessId(self.window, ctypes.byref(pid))
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        kernel.OpenProcess.restype = w.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        name, size = ctypes.create_unicode_buffer(2048), w.DWORD(2048)
        try:
            if not handle or not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                raise DesktopUnavailable('无法核对抖音进程身份。')
            if os.path.basename(name.value).lower() != 'douyin.exe':
                raise DesktopUnavailable('当前窗口不是抖音客户端。')
        finally:
            if handle:
                kernel.CloseHandle(handle)
        self._initial = {'minimized': bool(u.IsIconic(self.window)), 'pid': pid.value}
        self._ready(focus=True, foreground_wait=foreground_wait, cancelled=cancelled)
        self._may_focus = False
        return self._initial

    def _wait_for_foreground(self, u, seconds=0, cancelled=None):
        deadline = time.monotonic() + max(0, min(seconds, 15))
        while True:
            if cancelled and cancelled():
                raise DesktopUnavailable('本轮已暂停。')
            if u.GetForegroundWindow() == self.window:
                return
            if time.monotonic() >= deadline:
                raise DesktopUnavailable('抖音没有取得输入焦点，请切到抖音后再点“开始刷抖音”。')
            time.sleep(.1)

    def _ready(self, *, focus=False, foreground_wait=0, cancelled=None):
        u, _ = self._api()
        if not self.window or not u.IsWindow(self.window):
            raise DesktopUnavailable('抖音窗口已关闭，请重新连接。')
        # OpenInputDesktop fails on locked/disconnected sessions; never type elsewhere.
        u.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        u.OpenInputDesktop.restype = w.HANDLE
        u.CloseDesktop.argtypes = [w.HANDLE]
        desktop = u.OpenInputDesktop(0, False, 0x0100)
        if not desktop:
            raise DesktopUnavailable('Windows 当前已锁屏或无法交互，已暂停桌面操作。')
        u.CloseDesktop(desktop)
        if not self._may_focus and u.GetForegroundWindow() != self.window:
            raise DesktopUnavailable('你已切换到其他窗口，本轮抖音操作已暂停。')
        if focus:
            if self._may_focus:
                if u.IsIconic(self.window):
                    u.ShowWindow(self.window, 9)
                u.SetForegroundWindow(self.window)
                time.sleep(.15)
            self._wait_for_foreground(u, foreground_wait if self._may_focus else 0, cancelled)
        rect = w.RECT()
        if not u.GetWindowRect(self.window, ctypes.byref(rect)):
            raise DesktopUnavailable('无法读取抖音窗口。')
        width, height = rect.right-rect.left, rect.bottom-rect.top
        if width < 900 or height < 600:
            raise DesktopUnavailable('请将抖音窗口放大到至少 900 × 600。')
        return u, rect, width, height

    def capture(self):
        from PIL import Image
        u, rect, width, height = self._ready()
        _, g = self._api()
        class Header(ctypes.Structure):
            _fields_ = [('size', w.DWORD), ('width', w.LONG), ('height', w.LONG), ('planes', w.WORD),
                        ('bits', w.WORD), ('compression', w.DWORD), ('size_image', w.DWORD),
                        ('xppm', w.LONG), ('yppm', w.LONG), ('used', w.DWORD), ('important', w.DWORD)]
        dc = u.GetWindowDC(self.window)
        mem = g.CreateCompatibleDC(dc)
        bitmap = g.CreateCompatibleBitmap(dc, width, height)
        old = g.SelectObject(mem, bitmap)
        try:
            header = Header()
            header.size, header.width, header.height, header.planes, header.bits = ctypes.sizeof(header), width, -height, 1, 32
            buf = ctypes.create_string_buffer(width*height*4)
            if not u.PrintWindow(self.window, mem, 2) or not g.GetDIBits(mem, bitmap, 0, height, buf, ctypes.byref(header), 0):
                raise DesktopUnavailable('抖音窗口暂时无法截图，已暂停。')
            image = Image.frombuffer('RGB', (width, height), buf, 'raw', 'BGRX', 0, 1).copy()
        finally:
            g.SelectObject(mem, old)
            g.DeleteObject(bitmap)
            g.DeleteDC(mem)
            u.ReleaseDC(self.window, dc)
        lines = recognize(image)
        self._full_image = image.copy()
        # Friend names and the comment editor are smaller than the main captions.
        # Enlarge this bounded local region before OCR, then map coordinates back.
        left, top = int(width*.71), int(height*.59)
        crop = image.crop((left, top, width, height)).resize(((width-left)*2, (height-top)*2))
        for line in recognize(crop):
            lines.append({**line, 'x': left+line['x']/2, 'y': top+line['y']/2,
                          'w': line['w']/2, 'h': line['h']/2, 'detail': True})
        text = '\n'.join(line['text'] for line in lines)
        if re.search('安全验证|人机验证|访问过于频繁|扫码登录|请先登录|登录后免费', text):
            raise DesktopUnavailable('抖音需要本人登录或验证，请在客户端手动处理后继续。')
        output = io.BytesIO()
        image.thumbnail((1400, 900))
        image.save(output, format='JPEG', quality=80)
        self.last_frame = {'image': output.getvalue(), 'lines': lines, 'text': text,
                           'width': width, 'height': height, 'at': time.time()}
        return self.last_frame

    def _click(self, x, y):
        u, rect, width, height = self._ready(focus=True)
        if not 8 < x < width-8 or not 8 < y < height-8:
            raise DesktopUnavailable('控件位置超出抖音窗口。')
        old = w.POINT()
        u.GetCursorPos(ctypes.byref(old))
        point = w.POINT(rect.left+int(x), rect.top+int(y))
        u.SetCursorPos(point.x, point.y)
        try:
            time.sleep(.12)
            if u.GetAncestor(u.WindowFromPoint(point), 2) != self.window:
                raise DesktopUnavailable('目标被其他窗口遮挡，已暂停。')
            u.mouse_event(2, 0, 0, 0, 0)
            u.mouse_event(4, 0, 0, 0, 0)
            time.sleep(.2)
        finally:
            u.SetCursorPos(old.x, old.y)

    def _key(self, key, ctrl=False):
        u, _, _, _ = self._ready(focus=True)
        if ctrl:
            u.keybd_event(0x11, 0, 0, 0)
        try:
            u.keybd_event(key, 0, 0, 0)
            u.keybd_event(key, 0, 2, 0)
        finally:
            if ctrl:
                u.keybd_event(0x11, 0, 2, 0)

    def _type(self, text):
        u, _, _, _ = self._ready(focus=True)
        class Keyboard(ctypes.Structure):
            _fields_ = [('vk', w.WORD), ('scan', w.WORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', ctypes.c_size_t)]
        class Mouse(ctypes.Structure):
            _fields_ = [('x', w.LONG), ('y', w.LONG), ('data', w.DWORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', ctypes.c_size_t)]
        class Union(ctypes.Union):
            _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]
        class Input(ctypes.Structure):
            _fields_ = [('type', w.DWORD), ('value', Union)]
        u.SendInput.argtypes = [w.UINT, ctypes.POINTER(Input), ctypes.c_int]
        for offset in range(0, len(text.encode('utf-16-le')), 2):
            unit = int.from_bytes(text.encode('utf-16-le')[offset:offset+2], 'little')
            batch = (Input*2)()
            for i, flags in enumerate((4, 6)):
                batch[i].type = 1
                batch[i].value.keyboard = Keyboard(0, unit, flags, 0, 0)
            if u.SendInput(2, batch, ctypes.sizeof(Input)) != 2:
                raise DesktopUnavailable('抖音输入失败，已停止。')

    def _find(self, pattern, frame=None, *, lower=False, right=False):
        frame = frame or self.capture()
        candidates = [l for l in frame['lines'] if re.search(pattern, l['text'])
                      and (not lower or l['y'] > frame['height']*.65)
                      and (not right or l['x'] > frame['width']*.6)]
        if not candidates:
            raise DesktopUnavailable('当前页面未找到所需控件，请检查抖音界面。')
        return candidates[0]

    def click_text(self, pattern, **kwargs):
        line = self._find(pattern, **kwargs)
        self._click(line['x']+line['w']/2, line['y']+line['h']/2)
        time.sleep(.5)

    def account(self, expected_id):
        self._source_binding = None
        frame = self.capture()
        if expected_id in frame['text']:
            return frame
        viewer = bool(re.search('全部评论|连播|听抖音',frame['text']))
        if not viewer:
            try:
                frame = self._playback_frame()
                self.playback_state(frame);viewer=True
            except DesktopUnavailable:
                pass
        if viewer:
            if '全部评论' in frame['text'] and not self._empty_editor(frame):
                raise DesktopUnavailable('抖音评论框已有内容，请先处理草稿再连接账号。')
            self._click(frame['width']*.036, frame['height']*.065)
            self._editor_focused = False
            time.sleep(.6)
            frame = self.capture()
        self._click(frame['width']*.89, 36*frame['height']/1280)
        for _ in range(4):
            time.sleep(.65)
            frame = self.capture()
            if expected_id in frame['text']:
                return frame
            box=(round(frame['width']*.13),round(frame['height']*.07),
                 round(frame['width']*.44),round(frame['height']*.23))
            region=self._full_image.crop(box)
            detail=recognize(region.resize((region.width*2,region.height*2)))
            if expected_id in ''.join(l['text'] for l in detail):
                return frame
        raise DesktopUnavailable('当前登录账号未匹配小鱼账号，已停止；请在客户端核对。')

    def search(self, query):
        self._source_binding = None
        query = str(query).strip()
        if not query or len(query) > 60 or any(ord(c) < 32 for c in query):
            raise ValueError('搜索词为 1–60 字。')
        frame = self.capture()
        self._find('搜索', frame)
        self._click(frame['width']*.45, 36*frame['height']/1280)
        self._key(0x41, ctrl=True)
        self._type(query)
        self._key(0x0D)
        time.sleep(1.5)
        return self.capture()

    @staticmethod
    def _search_result_page(frame):
        return '搜索' in frame['text'] and any(
            line['text'] == '用户' and frame['width']*.18 < line['x'] < frame['width']*.27
            and frame['height']*.05 < line['y'] < frame['height']*.12 for line in frame['lines'])

    def open_result(self, index=0):
        self._source_binding = None
        frame = self.capture()
        if not self._search_result_page(frame):
            raise DesktopUnavailable('当前不是已确认的搜索结果页。')
        # The combined tab can insert a large AI answer ahead of all cards.
        # The ordinary video tab has a verified four-column result layout.
        self._click(frame['width']*.15, frame['height']*.078)
        for _ in range(10):
            time.sleep(.6)
            frame = self.capture()
            if not self._search_result_page(frame):
                raise DesktopUnavailable('搜索结果布局已变化，未打开作品。')
            creators = [line for line in frame['lines'] if line['text'].startswith('@')
                        and frame['width']*.08 < line['x'] < frame['width']*.74
                        and frame['height']*.35 < line['y'] < frame['height']*.55]
            if len(creators) >= 2:
                break
        else:
            raise DesktopUnavailable('视频搜索卡片未加载完成，未打开作品。')
        index = max(0, min(3, int(index)))
        self._click(frame['width']*(.159+.167*index), frame['height']*.29)
        time.sleep(1.2)
        current = self.capture()
        # Search cards can finish loading just after the first click. A second
        # navigation is allowed only while the original search tabs remain visible.
        if self._search_result_page(current):
            time.sleep(.7)
            self._click(frame['width']*(.159+.167*index), frame['height']*.29)
            time.sleep(1.2)
            current = self.capture()
        return current

    def ensure_viewer(self, frame=None):
        frame = frame or self.capture()
        if not re.search('发一条友善|善语结善缘|留下你的精彩|评论|听抖音|连播', frame['text']):
            frame = self._playback_frame()
            self.playback_state(frame)
        if '全部评论' not in frame['text']:
            # Desktop 8.7 standard viewer sidebar, only comments icon (never like/follow).
            self._click(frame['width']*.974, frame['height']*.661)
            time.sleep(.6)
            frame = self.capture()
        if not re.search('全部评论|暂无评论', frame['text']):
            raise DesktopUnavailable('评论面板未确认，已暂停，请手动打开评论区。')
        return frame

    def identity(self, frame):
        return frame.get('source_id') or self.current_source()

    @staticmethod
    def _work_signature(frame):
        """Only stationary creator/caption text, excluding comments and video subtitles."""
        width,height=frame['width'],frame['height']
        authors=[line for line in frame['lines'] if not line.get('detail')
                 and line['text'].startswith('@') and line['x']<width*.12
                 and height*.68<line['y']<height*.90]
        if len(authors)!=1:
            raise DesktopUnavailable('作品作者未确认，已暂停，避免混淆作品。')
        author=authors[0]
        captions=sorted((line for line in frame['lines'] if not line.get('detail')
                         and line['x']<width*.64 and author['y']+author['h']<line['y']<height*.913),
                        key=lambda line:(line['y'],line['x']))
        value=re.sub(r'[^\w\u4e00-\u9fff]','',author['text']+'|'+''.join(line['text'] for line in captions))
        if not captions or len(value)<12:
            raise DesktopUnavailable('作品说明未确认，已暂停，避免混淆作品。')
        return value

    def samples(self, count=3, wait=None):
        self._source_binding = None
        initial = self.set_playing(False)
        original = self._work_signature(initial)
        try:
            self.set_playing(True)
            frames = []
            for i in range(count):
                if wait and wait(2 if i else 0):
                    raise DesktopUnavailable('本轮已暂停。')
                frame=self.capture()
                if self._work_signature(frame)!=original:
                    raise DesktopUnavailable('抽样期间作者或作品说明变化，已暂停。')
                frames.append(frame)
            frozen = self.set_playing(False)
            if self._work_signature(frozen)!=original:
                raise DesktopUnavailable('抽样期间作者或作品说明变化，已暂停。')
            frames.append(frozen)
            current = self.current_source()
            if not self._source_binding or self._source_binding['signature'] != original:
                raise DesktopUnavailable('读取作品链接时作者或作品说明变化，已暂停。')
            for frame in frames:
                frame['source_id'] = current
            return frames
        except Exception:
            self._source_binding = None
            try:
                self.set_playing(False)  # This checks foreground ownership and never steals it back.
            except Exception:
                pass
            raise

    def _playback_frame(self):
        """Reveal the ordinary player controls without changing playback or focus."""
        u, rect, width, height = self._ready(focus=True)
        old = w.POINT()
        u.GetCursorPos(ctypes.byref(old))
        point = w.POINT(rect.left+round(80*width/2305), rect.top+round(1253*height/1280))
        try:
            u.SetCursorPos(point.x, point.y)
            time.sleep(.3)
            if u.GetAncestor(u.WindowFromPoint(point), 2) != self.window:
                raise DesktopUnavailable('播放控件被其他窗口遮挡，已暂停。')
            frame = self.capture()
            try:
                self.playback_state(frame)
            except DesktopUnavailable:
                # One more read allows the ordinary fade-in animation to finish.
                # No click or state change is permitted without a recognized glyph.
                time.sleep(.35)
                frame = self.capture()
                self.playback_state(frame)
            return frame
        finally:
            u.SetCursorPos(old.x, old.y)

    def playback_state(self, frame):
        # Recognize only the standard bottom-left play/pause glyph, never video content.
        box = (round(14*frame['width']/2305), round(1238*frame['height']/1280),
               round(44*frame['width']/2305), round(1268*frame['height']/1280))
        icon = self._full_image.crop(box).resize((30,30)).convert('L')
        pixels = {(x,y) for y in range(30) for x in range(30) if icon.getpixel((x,y)) > 70}
        components = []
        while pixels:
            seed = pixels.pop(); stack = [seed]; found = {seed}
            while stack:
                x,y = stack.pop()
                for point in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                    if point in pixels:
                        pixels.remove(point); found.add(point); stack.append(point)
            if len(found) >= 12:
                components.append((max(x for x,y in found)-min(x for x,y in found)+1,
                                   max(y for x,y in found)-min(y for x,y in found)+1))
        if len(components) == 2 and all(3 <= w <= 9 and 12 <= h <= 26 for w,h in components):
            return True
        if len(components) == 1 and 9 <= components[0][0] <= 20 and 12 <= components[0][1] <= 26:
            return False
        raise DesktopUnavailable('播放按钮样式无法确认，已暂停桌面操作。')

    def set_playing(self, playing):
        frame = self._playback_frame()
        if self.playback_state(frame) != playing:
            self._click(29*frame['width']/2305,1253*frame['height']/1280)
            time.sleep(.15)
            frame = self._playback_frame()
            if self.playback_state(frame) != playing:
                raise DesktopUnavailable('播放状态未确认，已暂停。')
        return frame

    def _select_father(self, father):
        """Match the provisioned, verified sole-mutual tile, then select the real UI item."""
        from PIL import Image, ImageChops, ImageStat
        try:
            binding = json.loads(self.binding_path.read_text(encoding='utf-8'))
            template_path = self.binding_path.with_name('father-picker.png')
            config = json.loads(self.binding_path.with_name('config.json').read_text(encoding='utf-8'))
            if (not config.get('self_id') or not config.get('father_id')
                    or binding['self_id'] != config['self_id'] or binding['father_id'] != config['father_id']
                    or binding['father_name'] != father
                    or hashlib.sha256(template_path.read_bytes()).hexdigest() != binding['sha256']):
                raise ValueError()
            frame = self.capture()
            box = tuple(round(v*(frame['width'] if i%2 == 0 else frame['height']))
                        for i,v in enumerate(binding['box']))
            tile = self._full_image.crop(box).convert('RGB')
            with Image.open(template_path) as reference:
                reference = reference.convert('RGB')
                if tile.size != reference.size:
                    raise ValueError()
                error = max(ImageStat.Stat(ImageChops.difference(tile, reference)).mean)
            if error > 5:
                raise ValueError()
            self._click((box[0]+box[2])/2, (box[1]+box[3])/2)
            time.sleep(.3)
        except Exception:
            raise DesktopUnavailable('@ 好友列表与已核对的爸爸不一致，未选择或发布；请本人重新核对。') from None

    def _empty_editor(self, frame):
        from PIL import Image, ImageChops, ImageStat
        path = self.binding_path.with_name('empty-editor.png')
        box = tuple(round(v*(frame['width'] if i%2 == 0 else frame['height']))
                    for i,v in enumerate((.729,.953,.859,.979)))
        current = self._full_image.crop(box).convert('RGB')
        if any(l.get('detail') and l['x'] > frame['width']*.71 and l['y'] > frame['height']*.94
               and len(l['text']) < 18 and re.search('精彩评论|精采评论',l['text']) for l in frame['lines']):
            return True
        for candidate in (path,path.with_name('empty-editor-blurred.png')):
            with Image.open(candidate) as template:
                if current.size == template.size and max(ImageStat.Stat(ImageChops.difference(current, template.convert('RGB'))).mean) < 12:
                    return True
        return False

    def _editor_text(self, frame):
        return ''.join(l['text'] for l in frame['lines'] if l['x'] > frame['width']*.71
                       and l['y'] > frame['height']*.92 and l.get('detail'))

    @staticmethod
    def _normalized(text):
        return re.sub(r'[^\w\u4e00-\u9fff]', '', compact(text))

    def _editor_value(self, copy_action=None, *, allow_empty=False):
        """Copy our editor only, restoring every original memory-backed clipboard format.

        Unsupported owner/display formats stop before Ctrl+C; private clipboard
        data is kept only in this stack frame and is never decoded, saved or sent.
        """
        u, _, _, _ = self._ready(focus=True)
        kernel = ctypes.WinDLL('kernel32')
        kernel.GlobalLock.argtypes = [w.HGLOBAL]
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [w.HGLOBAL]
        kernel.GlobalSize.argtypes = [w.HGLOBAL]
        kernel.GlobalSize.restype = ctypes.c_size_t
        kernel.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
        kernel.GlobalAlloc.restype = w.HGLOBAL
        kernel.GlobalFree.argtypes = [w.HGLOBAL]
        u.GetClipboardData.argtypes = [w.UINT]
        u.GetClipboardData.restype = w.HANDLE
        u.SetClipboardData.argtypes = [w.UINT,w.HANDLE]
        u.SetClipboardData.restype = w.HANDLE
        u.OpenClipboard.argtypes = [w.HWND]
        def open_clipboard():
            for _ in range(10):
                if u.OpenClipboard(self.window):
                    return
                time.sleep(.05)
            raise RuntimeError()
        def read_data(fmt, limit):
            handle = u.GetClipboardData(fmt)
            size = kernel.GlobalSize(handle) if handle else 0
            if not size or size > limit:
                raise RuntimeError()
            pointer = kernel.GlobalLock(handle)
            if not pointer:
                raise RuntimeError()
            try:
                return ctypes.string_at(pointer, size)
            finally:
                kernel.GlobalUnlock(handle)
        original, changed = [], False
        try:
            open_clipboard()
            try:
                fmt = 0
                while True:
                    fmt = u.EnumClipboardFormats(fmt)
                    if not fmt:
                        break
                    if len(original) >= 64 or fmt in {2,3,9,14,0x80}:
                        raise RuntimeError()
                    raw = read_data(fmt,16*1024*1024)
                    if sum(len(v) for _,v in original)+len(raw) > 16*1024*1024:
                        raise RuntimeError()
                    original.append((fmt,raw))
            finally:
                u.CloseClipboard()
            sequence = u.GetClipboardSequenceNumber()
            if copy_action:
                copy_action()
            else:
                self._key(0x41, ctrl=True)
                self._key(0x43, ctrl=True)
            changed = True
            for _ in range(20):
                if u.GetClipboardSequenceNumber() != sequence:
                    break
                time.sleep(.05)
            else:
                if allow_empty:
                    self._key(0x27)
                    return ''
                raise RuntimeError()
            open_clipboard()
            try:
                value = read_data(13,16384).decode('utf-16-le').split('\0',1)[0]
            finally:
                u.CloseClipboard()
            if not copy_action:
                self._key(0x27)  # collapse the editor selection, no submission
            return value
        except Exception:
            raise DesktopUnavailable('无法核对评论框的完整内容，未发送。') from None
        finally:
            if changed:
                allocations = []
                try:
                    for fmt,raw in original:
                        handle = kernel.GlobalAlloc(2,len(raw))
                        pointer = kernel.GlobalLock(handle) if handle else None
                        if not pointer:
                            raise RuntimeError()
                        ctypes.memmove(pointer,raw,len(raw))
                        kernel.GlobalUnlock(handle)
                        allocations.append([fmt,handle])
                    open_clipboard()
                    try:
                        if not u.EmptyClipboard():
                            raise RuntimeError()
                        for item in allocations:
                            if not u.SetClipboardData(item[0],item[1]):
                                raise RuntimeError()
                            item[1] = None  # ownership transferred to Windows
                    finally:
                        u.CloseClipboard()
                finally:
                    for _,handle in allocations:
                        if handle:
                            kernel.GlobalFree(handle)

    def current_source(self):
        """Use the app's normal copy-link control. Never click any recipient Share button."""
        frame = self.ensure_viewer()
        signature = self._work_signature(frame)
        u, rect, width, height = self._ready(focus=True)
        old = w.POINT(); u.GetCursorPos(ctypes.byref(old))
        try:
            u.SetCursorPos(rect.left+round(width*.69),rect.top+round(height*.835))
            time.sleep(.85)
            menu = self.capture()
            if not re.search('分享给朋友|制链接',menu['text']):
                raise DesktopUnavailable('作品链接菜单未确认，已暂停。')
            lines = [l for l in menu['lines'] if re.search('制链接',l['text'])
                     and width*.47 < l['x'] < width*.63 and height*.75 < l['y'] < height*.91]
            if len(lines) != 1:
                raise DesktopUnavailable('复制作品链接控件未确认，已暂停。')
            line = lines[0]
            copied = self._editor_value(lambda:self._click(line['x']+line['w']/2,line['y']+line['h']/2))
            match = re.search(r'https://(?:www\.)?douyin\.com/(?:video|note)/(\d{10,25})',copied)
            if not match:
                short = re.search(r'https://v\.douyin\.com/[A-Za-z0-9_-]+/',copied)
                if short:
                    import urllib.request, urllib.error
                    from urllib.parse import urlsplit
                    class NoRedirect(urllib.request.HTTPRedirectHandler):
                        def redirect_request(self,req,fp,code,msg,headers,newurl):
                            return None
                    try:
                        with urllib.request.build_opener(NoRedirect).open(short.group(),timeout=8) as response:
                            target = response.geturl()
                    except urllib.error.HTTPError as error:
                        if error.code not in (301,302,303,307,308):
                            raise DesktopUnavailable('作品短链接暂不可核对，未发布。') from None
                        target = error.headers.get('Location','')
                    if urlsplit(target).hostname in {'www.douyin.com','www.iesdouyin.com'}:
                        match = re.search(r'/(?:video|note)/(\d{10,25})',target)
            if not match:
                raise DesktopUnavailable('未取得可核对的抖音作品编号，未发布。')
            source='https://www.douyin.com/video/'+match.group(1)
            self._source_binding={'source':source,'signature':signature,'at':time.monotonic()}
            return source
        finally:
            u.SetCursorPos(old.x,old.y)
            time.sleep(.35)

    def next_video(self):
        self._source_binding = None
        self.ensure_viewer()
        frame = self.set_playing(False)
        # Defocus the editor by resuming the paused video before the app's next key.
        self._click(frame['width']*.35, frame['height']*.45)
        self._editor_focused = False
        time.sleep(.15)
        self._key(0x28)
        time.sleep(.9)
        return self.capture()

    def stage(self, text, *, father=None, expected_video=None):
        frame = self.ensure_viewer()
        frame = self.set_playing(False)
        signature = self._work_signature(frame)
        cached = self._source_binding
        source = (cached['source'] if cached and cached['signature']==signature
                  and time.monotonic()-cached['at']<180 else self.current_source())
        if expected_video and source != expected_video:
            raise DesktopUnavailable('视频已变化，本条未发布。')
        frame = self.capture()
        if not self._empty_editor(frame):
            raise DesktopUnavailable('评论框不是已核对的空白状态，草稿已保留，未输入或发布。')
        self._click(frame['width']*.77, frame['height']*.965)
        self._editor_focused = True
        time.sleep(.15)
        if self._editor_value(allow_empty=True):
            raise DesktopUnavailable('评论框已有草稿，已保留原文，未继续输入。')
        self._owns_draft = True
        if father:
            self._type('@')
            time.sleep(.6)
            self._select_father(father)
            self._type(' ')
        self._type(text)
        time.sleep(.5)
        staged = self.capture()
        value = self._editor_value()
        expected = ('@' + father + ' ' if father else '') + text
        if compact(value) != compact(expected):
            raise DesktopUnavailable('评论输入内容未核对成功；已停止，未发布。')
        self._staged = {'text': text, 'video': expected_video, 'mention': father}
        return staged

    def clear_staged(self):
        # Only clear a field we focused ourselves while Douyin still owns focus.
        if not self._editor_focused or not self._owns_draft:
            return
        u, _, _, _ = self._ready()
        if u.GetForegroundWindow() != self.window:
            raise DesktopUnavailable('窗口焦点已变化，草稿留在抖音中，请手动检查。')
        self._key(0x41, ctrl=True)
        self._key(0x08)
        time.sleep(.3)
        self._staged = None
        self._editor_focused = False
        self._owns_draft = False

    def submit(self, *, expected_video, text):
        before = self.ensure_viewer()
        if self.current_source() != expected_video:
            raise DesktopUnavailable('发布前视频变化，已停止。')
        self._click(before['width']*.77,before['height']*.965)
        if (not self._staged or self._staged['text'] != text or self._staged['video'] != expected_video
                or compact(self._editor_value()) != compact(('@'+self._staged['mention']+' ' if self._staged['mention'] else '')+text)):
            raise DesktopUnavailable('待发草稿已变化，未发送。')
        # Enter is used only after the input has been staged and checked by the worker.
        self._key(0x0D)
        self._staged = None
        time.sleep(1.1)
        after = self.capture()
        body = compact(after['text'])
        normalized = re.sub(r'[^\w\u4e00-\u9fff]', '', text)
        visible = re.sub(r'[^\w\u4e00-\u9fff]', '', body)
        input_empty = self._empty_editor(after)
        config = json.loads(self.binding_path.with_name('config.json').read_text(encoding='utf-8'))
        own_name = str(config.get('self_name') or '')
        own_receipt = bool(own_name) and compact(own_name) in body and bool(re.search('刚刚|\\d+秒前', body))
        return {'confirmed': input_empty and own_receipt and normalized in visible, 'frame': after}
