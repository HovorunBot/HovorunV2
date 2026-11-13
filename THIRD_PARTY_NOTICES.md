# Third-party components and model assets

HovorunV2 application source is licensed under [BSD-3-Clause](LICENSE). Dependency packages retain their own licenses. Dependency names are declared in `pyproject.toml`; exact resolved versions and artifacts are recorded in `uv.lock`.

## Local caption translation

The default translation backend uses **NLLB-200-distilled-600M**, created by Meta / Facebook AI Research (NLLB Team), described in *No Language Left Behind: Scaling Human-Centered Machine Translation* (2022).

- Original model and model card: https://huggingface.co/facebook/nllb-200-distilled-600M
- CTranslate2 int8 conversion by JustFrederik: https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8
- Pinned model revision: `302d78f00e6fdb50a1064059df7c392b735e9d05`.
- Changes: JustFrederik converted and quantized the model to CTranslate2 int8. HovorunV2 downloads the pinned files without changing them.
- Model license: **Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)**, https://creativecommons.org/licenses/by-nc/4.0/.
- Full license: [CC-BY-NC-4.0.txt](src/hovorunv2/infrastructure/translation/licenses/CC-BY-NC-4.0.txt).
- Attribution shipped with cached models: [NOTICE.txt](src/hovorunv2/infrastructure/translation/licenses/NOTICE.txt).

Model weights are downloaded separately on first needed translation, stored in the persistent model volume, and excluded from application wheels and container image layers. The full license and attribution notice are included in the application package and copied beside downloaded model files. Preserve these notices when redistributing model caches or volumes. No endorsement by the model authors or conversion author is implied.

The application BSD license does **not** relicense these weights or grant commercial rights to them. The current model is selected for noncommercial use. Before commercial use, replace it with a suitably licensed model or obtain appropriate permission. A free service can still have a commercial purpose; absence of user payments alone does not establish noncommercial use.

The original model card identifies research intended use and excludes production deployment from its intended scope. This statement is distinct from the model license; local quality, reliability, latency, and memory require verification for the intended workload.

The online translation adapter uses `deep-translator` (MIT) and `tenacity` (Apache-2.0) to query the account-free public Google Translate web endpoint with bounded retries and an operator offline switch (`TRANSLATION_ONLINE_ENABLED`). The local fallback runtime uses CTranslate2 (MIT), SentencePiece (Apache-2.0), and py3langid (BSD-3-Clause, derived from BSD-2-Clause langid.py). Their upstream package notices remain applicable. None changes the license of the NLLB model assets.

## Local speech recognition

The optional CPU speech worker uses [whisper.cpp](https://github.com/ggml-org/whisper.cpp), pinned to release **v1.9.5** (source revision `d1be6fde11ac6e0407606b4e42fe72d34add8037`), and the multilingual OpenAI Whisper small model converted to `ggml-small-q8_0.bin` by Georgi Gerganov. The model repository is [ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp), revision `5359861c739e955e79d9a303bcbc70fb988958b1`. Source and model retain the MIT license; this is compatible with the application BSD-3-Clause license. Full [MIT terms](src/hovorunv2/infrastructure/speech/licenses/MIT.txt) and [attribution](src/hovorunv2/infrastructure/speech/licenses/NOTICE.txt) ship with the package and prepared model inventory. The image also retains the pinned whisper.cpp source license. Model weights are separately prepared and excluded from the image.

The public Ukrainian and English speech fixtures in `tests/utils/speech_uk.ogg` and `speech_en.ogg` derive from the [Google FLEURS dataset](https://huggingface.co/datasets/google/fleurs), revision `168de341b3db6859a9bac1c50a2ef5e3b47647e0`, validation splits `uk_ua` and `en_us` (English sample ID 1548). FLEURS is released under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). Audio was converted to Ogg/Opus for transport tests. Credit: Google and FLEURS contributors, *FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech* (2022). Synthetic silence contains no recorded speech. These fixtures contain no bot-user recordings.

`tests/utils/speech_uk_short.ogg` is a synthetic Ukrainian test recording generated with Google Translate text-to-speech through gTTS and converted to mono Ogg/Opus. Its complete source text is: "Привіт! Це короткий тест розпізнавання української мови." It is approximately 5.33 seconds long and contains no user recording or personal data.
