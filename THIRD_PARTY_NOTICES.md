# Third-party components and model assets

HovorunV2 application source is licensed under [BSD-3-Clause](LICENSE). Dependency packages retain their own licenses. Dependency names are declared in `pyproject.toml`; exact resolved versions and artifacts are recorded in `uv.lock`.

## Local caption translation

The local fallback uses **M2M100-418M**, created by Meta / Facebook AI Research, described in *Beyond English-Centric Multilingual Machine Translation* (2020). The upstream [model card](https://huggingface.co/facebook/m2m100_418M) identifies its license as MIT.

- Original model: https://huggingface.co/facebook/m2m100_418M
- CTranslate2 int8 conversion: https://huggingface.co/auralmira/m2m100-418M-ct2-int8
- Pinned conversion revision: `e205afefce2fd6933a1dca3b92b5063894f2f2bb`.
- Changes: the conversion repository supplies CTranslate2 int8 assets; HovorunV2 downloads the pinned, size/SHA-256-verified files without changing them.
- Full model license: [MIT.txt](src/hovorunv2/infrastructure/translation/licenses/MIT.txt).
- Attribution shipped with cached models: [NOTICE.txt](src/hovorunv2/infrastructure/translation/licenses/NOTICE.txt).

Model weights are downloaded separately on first needed local translation, stored in the persistent model volume, and excluded from application wheels and container image layers. The full license and attribution notice are included in the application package and copied beside downloaded model files. Preserve them when redistributing model caches or volumes. Model metadata and licensing do not establish translation quality, reliability, latency or memory requirements for a particular workload.

Older deployments used NLLB-200-distilled-600M. Any retained legacy NLLB assets keep their original [CC BY-NC 4.0 terms](https://huggingface.co/facebook/nllb-200-distilled-600M); switching the configured model does not relicense previously downloaded weights.

The account-free online adapter uses `deep-translator` (MIT), `tenacity` (Apache-2.0), and the existing `curl-cffi` native HTTP dependency to query the public Google Translate web endpoint with bounded retries and an offline switch (`TRANSLATION_ONLINE_ENABLED`). The local fallback runtime uses CTranslate2 (MIT), SentencePiece (Apache-2.0), and py3langid (BSD-3-Clause, derived from BSD-2-Clause langid.py). Their upstream package notices remain applicable.

## Local speech recognition

The optional CPU speech worker uses [whisper.cpp](https://github.com/ggml-org/whisper.cpp), pinned to release **v1.9.5** (source revision `d1be6fde11ac6e0407606b4e42fe72d34add8037`), and the multilingual OpenAI Whisper small model converted to `ggml-small-q8_0.bin` by Georgi Gerganov. The model repository is [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp), revision `5359861c739e955e79d9a303bcbc70fb988958b1`. Source and model retain the MIT license; this is compatible with the application BSD-3-Clause license. Full [MIT terms](src/hovorunv2/infrastructure/speech/licenses/MIT.txt) and [attribution](src/hovorunv2/infrastructure/speech/licenses/NOTICE.txt) ship with the package and prepared model inventory. The image also retains the pinned whisper.cpp source license. Model weights are separately prepared and excluded from the image.

The public Ukrainian and English speech fixtures in `tests/utils/speech_uk.ogg` and `speech_en.ogg` derive from the [Google FLEURS dataset](https://huggingface.co/datasets/google/fleurs), revision `168de341b3db6859a9bac1c50a2ef5e3b47647e0`, validation splits `uk_ua` and `en_us` (English sample ID 1548). FLEURS is released under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). Audio was converted to Ogg/Opus for transport tests. Credit: Google and FLEURS contributors, *FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech* (2022). Synthetic silence contains no recorded speech. These fixtures contain no bot-user recordings.

`tests/utils/speech_uk_short.ogg` is a synthetic Ukrainian test recording generated with Google Translate text-to-speech through gTTS and converted to mono Ogg/Opus. Its complete source text is: "Привіт! Це короткий тест розпізнавання української мови." It is approximately 5.33 seconds long and contains no user recording or personal data.
