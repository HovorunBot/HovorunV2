"""Run on-demand NLLB caption inference inside the owning synchronous worker child."""

from os import getpid

lazy import ctranslate2
lazy import sentencepiece

from hovorunv2.application.translation.ports import TranslationEnginePort, TranslationError
from hovorunv2.application.translation.translation_rules import resolve_flores_code
from hovorunv2.infrastructure.translation.model_store import ModelStore

INFERENCE_INTER_THREADS = 1
INFERENCE_INTRA_THREADS = 2
BEAM_SIZE = 2
MAX_DECODING_TOKENS = 256
MAX_SOURCE_TOKENS = 512
MAX_CAPTION_CHARACTERS = 16384
MAX_CAPTION_LINES = 64


class CTranslate2Engine(TranslationEnginePort):
    """Allocate model resources only after a supported caption requires translation."""

    def __init__(self, store: ModelStore) -> None:
        self._store = store
        self._owner = getpid()
        self._closed = False
        self._translator: ctranslate2.Translator | None = None
        self._processor: sentencepiece.SentencePieceProcessor | None = None

    def _require_owner(self) -> None:
        if getpid() != self._owner:
            msg = "CTranslate2Engine cannot be shared across processes"
            raise ValueError(msg)

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate supported captions while preserving line breaks and blank lines.

        Args:
            text: Original caption with decoded HTML entities.
            source_lang: Source ISO language identifier.
            target_lang: Target ISO language identifier.

        Returns:
            Nonblank translated text preserving the original line structure.

        Raises:
            TranslationError: If provisioning, tokenization, or native inference fails.
        """
        self._require_owner()
        source, target = self._validate_request(text, source_lang, target_lang)
        try:
            translator, processor = self._load()
            return self._translate_lines(text, source, target, translator, processor)
        except TranslationError:
            raise
        except Exception:
            msg = "Local translation inference failed"
            raise TranslationError(msg) from None

    def _validate_request(self, text: str, source_lang: str, target_lang: str) -> tuple[str, str]:
        if self._closed:
            msg = "Translation engine is closed"
            raise ValueError(msg)
        if not text.strip() or len(text) > MAX_CAPTION_CHARACTERS or len(text.split("\n")) > MAX_CAPTION_LINES:
            msg = "Caption is empty or exceeds translation bounds"
            raise ValueError(msg)
        source, target = resolve_flores_code(source_lang), resolve_flores_code(target_lang)
        if source is None or target is None:
            msg = "Unsupported translation language pair"
            raise ValueError(msg)
        return source, target

    def _load(self) -> tuple[ctranslate2.Translator, sentencepiece.SentencePieceProcessor]:
        if self._translator is not None and self._processor is not None:
            return self._translator, self._processor
        directory = self._store.ensure()
        processor = sentencepiece.SentencePieceProcessor(model_file=str(directory / "sentencepiece.bpe.model"))
        translator = ctranslate2.Translator(
            str(directory),
            device="cpu",
            compute_type="int8",
            inter_threads=INFERENCE_INTER_THREADS,
            intra_threads=INFERENCE_INTRA_THREADS,
        )
        self._processor = processor
        self._translator = translator
        return translator, processor

    def _translate_lines(
        self,
        text: str,
        source: str,
        target: str,
        translator: ctranslate2.Translator,
        processor: sentencepiece.SentencePieceProcessor,
    ) -> str:
        lines = text.split("\n")
        indexes = [index for index, line in enumerate(lines) if line.strip()]
        tokens = [processor.encode(lines[index], out_type=str) for index in indexes]
        if any(len(pieces) > MAX_SOURCE_TOKENS for pieces in tokens):
            msg = "Caption line exceeds translation token bounds"
            raise TranslationError(msg)
        results = translator.translate_batch(
            [[source, *pieces, "</s>"] for pieces in tokens],
            target_prefix=[[target] for _index in indexes],
            beam_size=BEAM_SIZE,
            max_decoding_length=MAX_DECODING_TOKENS,
        )
        for index, result in zip(indexes, results, strict=True):
            pieces = result.hypotheses[0]
            if pieces and pieces[0] == target:
                pieces = pieces[1:]
            translated = processor.decode(pieces).strip()
            if not translated:
                msg = "Local translation produced an empty line"
                raise TranslationError(msg)
            lines[index] = translated
        return "\n".join(lines)

    def close(self) -> None:
        """Release native resources, requiring the owning process and raising ValueError otherwise."""
        self._require_owner()
        if self._translator is not None:
            self._translator.unload_model()
        self._translator = None
        self._processor = None
        self._closed = True
