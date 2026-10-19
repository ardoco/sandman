# Third-party material in the extraction-agent (T2.7)

The skill `derive-architecture-model` ports the component extraction of ExArch from ArDoCo.
The port is a Python reimplementation. No ArDoCo code runs in this service.

| Material | Source | License | Note |
|---|---|---|---|
| Prompt texts `prompts/exarch-doc-1.txt` and `prompts/exarch-doc-2.txt` | ArDoCo 2.1.0, ExArch (`ExtractionDependentOccurrence` informant) | MIT | verbatim, the placeholder `%s` takes the document text |
| Name post-processing in `derive.py` | ArDoCo 2.1.0, ExArch | MIT | same rules; one documented deviation: names with `::` or a line break are dropped |

Copyright (c) the ArDoCo authors, KASTEL, Karlsruhe Institute of Technology.
The MIT license text is at https://github.com/ArDoCo/Core/blob/main/LICENSE.
