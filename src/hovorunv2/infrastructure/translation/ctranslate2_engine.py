"""Run on-demand M2M100 caption inference inside the owning synchronous worker child."""

from functools import partial
from os import getpid

lazy import ctranslate2
lazy import sentencepiece

from hovorunv2.application.translation.document_segmentation import (
    DEFAULT_LOCAL_SEGMENT_TOKENS,
    MAX_DOCUMENT_CHARACTERS,
    assemble_translated_document,
    plan_document_segments,
)
from hovorunv2.application.translation.ports import TranslationEnginePort, TranslationError
from hovorunv2.application.translation.translation_rules import resolve_model_language
from hovorunv2.infrastructure.translation.model_store import ModelStore

INFERENCE_INTER_THREADS = 1
INFERENCE_INTRA_THREADS = 2
INFERENCE_BATCH_SIZE = 1
BEAM_SIZE = 2
MAX_DECODING_TOKENS = 256
MAX_SOURCE_TOKENS = 512
LOCAL_SEGMENT_TOKEN_LIMIT = DEFAULT_LOCAL_SEGMENT_TOKENS
END_OF_SENTENCE = "</s>"


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
        if not text.strip() or len(text) > MAX_DOCUMENT_CHARACTERS:
            msg = "Caption is empty or exceeds translation bounds"
            raise ValueError(msg)
        source, target = resolve_model_language(source_lang), resolve_model_language(target_lang)
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

    def _count_tokens(self, processor: sentencepiece.SentencePieceProcessor, segment: str) -> int:
        return len(processor.encode(segment, out_type=str))

    def _translate_lines(
        self,
        text: str,
        source: str,
        target: str,
        translator: ctranslate2.Translator,
        processor: sentencepiece.SentencePieceProcessor,
    ) -> str:
        planned = plan_document_segments(
            text,
            LOCAL_SEGMENT_TOKEN_LIMIT,
            measure_fn=partial(self._count_tokens, processor),
        )
        segments = [segment for line in planned for segment in line.segments]
        tokens = [processor.encode(segment, out_type=str) for segment in segments]
        if any(len(pieces) > MAX_SOURCE_TOKENS for pieces in tokens):
            msg = "Caption segment exceeds translation token bounds"
            raise TranslationError(msg)
        results = translator.translate_batch(
            [[source, *pieces, END_OF_SENTENCE] for pieces in tokens],
            target_prefix=[[target] for _segment in segments],
            beam_size=BEAM_SIZE,
            max_batch_size=INFERENCE_BATCH_SIZE,
            max_decoding_length=MAX_DECODING_TOKENS,
            return_end_token=True,
        )
        translated_segments: list[str] = []
        for result in results:
            pieces = result.hypotheses[0]
            if not pieces or pieces[-1] != END_OF_SENTENCE:
                msg = "Local translation exceeded the output limit"
                raise TranslationError(msg)
            pieces = pieces[:-1]
            if pieces and pieces[0] == target:
                pieces = pieces[1:]
            translated = processor.decode(pieces).strip()
            if not translated:
                msg = "Local translation produced an empty segment"
                raise TranslationError(msg)
            translated_segments.append(translated)
        return assemble_translated_document(planned, translated_segments)

    def close(self) -> None:
        """Release native resources, requiring the owning process and raising ValueError otherwise."""
        self._require_owner()
        if self._translator is not None:
            self._translator.unload_model()
        self._translator = None
        self._processor = None
        self._closed = True
