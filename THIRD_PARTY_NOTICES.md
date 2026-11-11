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

The local runtime uses CTranslate2 (MIT), SentencePiece (Apache-2.0), and py3langid (BSD-3-Clause, derived from BSD-2-Clause langid.py). Their upstream package notices remain applicable. None changes the license of the NLLB model assets.
