"""JSON-lines local translation worker. It has no network or paid-model path."""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import re
import socket
import sys

NETWORK_ATTEMPTS = 0


class TranslationValidationError(ValueError):
    pass


def configure(root):
    # Keep model files, auxiliary data, settings and caches on the requested disk.
    os.environ.update(
        ARGOS_PACKAGES_DIR=str(root / "packages"), ARGOS_MODEL_PROVIDER="OPENNMT",
        ARGOS_DEVICE_TYPE="cpu", ARGOS_CHUNK_TYPE="STANZA",
        ARGOS_INTER_THREADS="1", ARGOS_INTRA_THREADS="4", ARGOS_COMPUTE_TYPE="int8",
        XDG_DATA_HOME=str(root / "data"), XDG_CONFIG_HOME=str(root / "config"),
        XDG_CACHE_HOME=str(root / "cache"), STANZA_RESOURCES_DIR=str(root / "stanza"),
        HF_HOME=str(root / "cache/huggingface"), TORCH_HOME=str(root / "cache/torch"),
        TEMP=str(root / "tmp"), TMP=str(root / "tmp"))


def block_network():
    def offline(*args, **kwargs):
        global NETWORK_ATTEMPTS
        NETWORK_ATTEMPTS += 1
        raise RuntimeError("offline translation: network is disabled")
    socket.socket.connect = offline
    socket.socket.connect_ex = offline
    socket.create_connection = offline


class LocalSentenceSplitter:
    """spaCy's bundled rules avoid incompatible legacy Stanza model downloads."""
    def __init__(self):
        from spacy.lang.en import English
        self.pipeline = English()
        self.pipeline.add_pipe("sentencizer")

    def split_sentences(self, text):
        return [sentence.text for sentence in self.pipeline(text).sents]


# Placeholders keep names in their sentence context. Translating the
# incomplete English fragments on either side of a name produces poor results.
PRESERVED = re.compile(r"[\u3400-\u9fff]+|\b(?:QQ|API|Python|DeepSeek|Douyin|OpenAI|ChatGPT)\b")


def _restore_names(output, names):
    # Numeric markers must match whole numbers: 9991 is not part of 99910.
    pattern = re.compile("|".join(
        r"(?<!\d)" + re.escape(token) + r"(?!\d)" if token.isdigit() else re.escape(token)
        for token in sorted(names, key=len, reverse=True)))
    found = pattern.findall(output)
    if any(found.count(token) != 1 for token in names):
        raise TranslationValidationError("local translation did not preserve a name")
    return pattern.sub(lambda match: names[match.group(0)], output)


def _translate_fragment(text, engine):
    if not re.search(r"[A-Za-z]", text):
        return text
    leading = text[:len(text) - len(text.lstrip())]
    trailing = text[len(text.rstrip()):]
    output = engine.translate(text.strip()).strip()
    if not output or re.search(r"[\ue000-\uf8ff]", output):
        raise TranslationValidationError("local translation contains invalid display characters")
    return leading + output + trailing


def _translate_prose(prose, engine):
    if not PRESERVED.search(prose):
        return engine.translate(prose).strip()
    # Prefer sentence context. A literal-span fallback keeps existing Chinese
    # and product names when the local model rewrites either marker format.
    for template in ("[NAME%d]", "999%d"):
        names = {}
        counter = 0

        def preserve(match):
            nonlocal counter
            while template % counter in prose or template % counter in names:
                counter += 1
            token = template % counter
            names[token] = match.group(0)
            counter += 1
            return token

        masked = PRESERVED.sub(preserve, prose)
        output = engine.translate(masked).strip()
        try:
            return _restore_names(output, names)
        except TranslationValidationError:
            pass
    output = []
    start = 0
    for match in PRESERVED.finditer(prose):
        output.append(_translate_fragment(prose[start:match.start()], engine))
        output.append(match.group(0))
        start = match.end()
    output.append(_translate_fragment(prose[start:], engine))
    return "".join(output)


def translate_text(text, engine):
    translated = []
    for part in re.split(r"(\r\n|\r|\n)", text):
        if not re.search(r"[A-Za-z]", part):
            translated.append(part)
            continue
        leading = part[:len(part) - len(part.lstrip())]
        trailing = part[len(part.rstrip()):]
        prose = part.strip()
        if prose:
            output = _translate_prose(prose, engine)
            if not output or re.search(r"[\ue000-\uf8ff]", output):
                raise TranslationValidationError("local translation contains invalid display characters")
            translated.append(leading + output + trailing)
        else:
            translated.append(part)
    return "".join(translated)


def main():
    # Frozen Python ignores PYTHONIOENCODING on some Windows installations.
    # The parent and worker exchange UTF-8 JSON, independently of console locale.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='strict')
    root = Path(os.environ["QQBOT_LOCAL_TRANSLATOR_ROOT"])
    configure(root)
    block_network()
    engine = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if engine is None:
                    engine = ReleaseTranslation(root / 'packages/translate-en_zh-1_9')
                result = {}
                for item in request["items"]:
                    try:
                        result[item["id"]] = translate_text(item["text"], engine)
                    except TranslationValidationError:
                        # One problematic record must not discard other good
                        # translations in the same viewed page.
                        continue
            response = {"items": result, "engine": "opus-ctranslate2-local", "network_attempts": NETWORK_ATTEMPTS}
        except Exception as exc:
            # Avoid writing private source text or credentials into logs/errors.
            response = {"error": str(exc) if isinstance(exc, TranslationValidationError) else type(exc).__name__}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


class ReleaseTranslation:
    def __init__(self, package):
        import ctranslate2
        import sentencepiece
        self.translator = ctranslate2.Translator(str(package / 'model'), device='cpu', compute_type='int8', inter_threads=1, intra_threads=4)
        self.tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(package / 'sentencepiece.model'))
    def translate(self, text):
        sentences = [piece.strip() for piece in re.split(r'(?<=[.!?。！？])\s+', text) if piece.strip()]
        batches = [self.tokenizer.encode(sentence, out_type=str) for sentence in sentences]
        output = self.translator.translate_batch(batches, replace_unknowns=True, max_batch_size=32,
                    batch_type='tokens', beam_size=4, length_penalty=0.2)
        return ' '.join(self.tokenizer.decode(item.hypotheses[0]).replace('\u2581', ' ').strip()
                        for item in output)

if __name__ == "__main__":
    main()
