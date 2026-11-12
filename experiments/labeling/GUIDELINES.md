# Rater guidelines: labeling study T3.6

Version: 1 · Date: 2026-10-17 · Spec: `docs/tasks/T3.6-labeling-study.md`

## 1. Purpose

You decide for each item if a reported inconsistency between the architecture documentation and the architecture model is real. Your labels measure the precision of the detectors. Label each item on its own. Do not try to guess which tool reported the item.

## 2. Material

You get these files:

- One rater sheet (`rater-a.csv` or `rater-b.csv`).
- This guideline.
- For each project in the sheet: the architecture documentation and the architecture model (as files or as a diagram).

Caution: Do not open `key.json`. If you receive it by mistake, tell the study lead and stop labeling.

## 3. Sheet columns

| Column | Content |
|---|---|
| `item` | Item id. Do not change it. |
| `project` | Project name. |
| `type` | Finding type (section 5). |
| `artifact` | File of the sentence or of the model element. |
| `sentence_no` | Number of the sentence in the documentation (1 = first sentence). Empty for model findings. |
| `sentence` | Text of the sentence. Empty for model findings. |
| `element` | Name of the model element. Empty for sentence findings. |
| `element_kind` | Kind of the model element, for example `BasicComponent`. |
| `terms` | Terms in the sentence that the finding names. Empty for model findings. |
| `label` | Your label (section 4). |
| `note` | Your reason in one short sentence. Optional for `true` and `false`. Required for `unclear`. |

## 4. Labels

Use exactly one of these values in the column `label`:

| Label | Use it when |
|---|---|
| `true` | The inconsistency is real. The claim of the finding type (section 5) holds. |
| `false` | The claim does not hold. The finding is a false positive. |
| `unclear` | You cannot decide with the given documents, also after the checks in section 5. |

Rules:

1. Decide from the documentation and the model of the project only. Do not use knowledge of other versions of the project.
2. Do not consider the severity or the importance of the problem. A small but real inconsistency is `true`.
3. Use `unclear` only after you did all checks of section 5. Write the open question in `note`.
4. Do not change a label after you discuss items with the other rater. Discussion starts only after both sheets are complete.

## 5. Decision rules per finding type

### 5.1 `undocumented-model-element`

Claim: the model contains the element in `element`, but the documentation does not describe it.

Procedure:

1. Search the documentation for the element name. Also search for the parts of a compound name (`MediaAccess`: "media access").
2. Search for synonyms and descriptions of the same responsibility, for example "the component that stores the files" for `FileStorage`.
3. Look at diagrams in the documentation if the project has them.

Label:

- `true` if no sentence and no diagram describes the element.
- `false` if at least one sentence or diagram describes the element, with its name or with an unambiguous description.
- `unclear` if a sentence possibly describes the element, but you cannot tell if it means this element or another one.

Examples (constructed):

| element | Documentation | Label | Reason |
|---|---|---|---|
| `Cache` | "Requests for popular files go through the Cache." | `false` | The name occurs. |
| `UserDBAdapter` | "An adapter connects the user management to the database." | `false` | Unambiguous description. |
| `AudioWatermarking` | No sentence on watermarks or audio processing. | `true` | No description. |
| `Packaging` | "Files are prepared before the download." | `unclear` | "prepared" can mean packaging or reencoding. |

### 5.2 `missing-model-element`

Claim: the sentence names at least one architecture element (for example a component, a service, an interface or a subsystem) that the model does not contain. `terms` lists the terms that the finding names.

Procedure:

1. Read the sentence and its neighbors in the documentation.
2. For each term in `terms`, decide if the term names an architecture element in this sentence. A term does not name an architecture element if it is a general word ("component", "data", "it"), a technology without its own element, an actor (a user), or an action.
3. For each term that names an architecture element, search the model for that element. Accept other spellings and synonyms.

Label:

- `true` if at least one term names an architecture element that the model does not contain.
- `false` if every term is not an architecture element, or the model contains every named element.
- `unclear` if you cannot decide for at least one term, and no other term makes the item `true`.

Examples (constructed):

| sentence | terms | Model | Label | Reason |
|---|---|---|---|---|
| "The ReEncoder converts uploaded files to MP3." | ReEncoder | no ReEncoder, no converter | `true` | Named component is not in the model. |
| "The ReEncoder converts uploaded files to MP3." | ReEncoder | contains `Reencoding` | `false` | Same element, other spelling. |
| "It sends the data to the server." | it; data | – | `false` | No term names an architecture element. |
| "The facade uses a cache and the TagWatermarking component." | cache; TagWatermarking | contains `Cache`, no `TagWatermarking` | `true` | One term is missing in the model. |
| "The media layer handles all requests." | media | – | `unclear` | "media layer" can be a subsystem or a general phrase. Write the reason in `note`. |

### 5.3 `programming-language-mismatch`

Claim: the sentence states a programming language for the system, and the code of the project does not use that language. `statements` lists the claims of the finding with the explanation of the detector.

Procedure:

1. Read the sentence and its neighbors in the documentation.
2. Check the file extensions and build files in the code of the project.
3. Decide if the named language applies to the part of the system that the sentence describes.

Label:

- `true` if the part of the system that the sentence describes does not use the named language.
- `false` if the code uses the named language for that part.
- `unclear` if the sentence does not say which part it describes, and the code uses several languages.

Examples (constructed):

| sentence | Code | Label | Reason |
|---|---|---|---|
| "The backend is written in Python." | Java files and a `pom.xml` only | `true` | No Python code. |
| "The backend is written in Java." | Java files and a `pom.xml` | `false` | The language matches. |
| "The tools use Go." | Java backend, Go only in one build helper | `unclear` | "tools" can mean the helper or the backend. |

### 5.4 `architectural-style-mismatch`

Claim: the sentence states an architectural style or pattern (for example layers, microservices, MVC), and the code does not follow it. `statements` lists the claims of the finding with the explanation of the detector.

Procedure:

1. Read the sentence and its neighbors in the documentation.
2. Look at the directory structure and the dependencies between packages in the code.
3. Decide if the code follows the named style.

Label:

- `true` if the structure or the dependencies of the code contradict the named style.
- `false` if the code follows the named style.
- `unclear` if the style cannot be decided from the files that you can see.

Examples (constructed):

| sentence | Code | Label | Reason |
|---|---|---|---|
| "The system has a strict layered architecture." | A data package imports the UI package | `true` | The dependencies break the layers. |
| "The system uses MVC." | Packages `model`, `view` and `controller` with matching dependencies | `false` | The code follows MVC. |
| "The system is event driven." | A message bus library, but mostly direct calls | `unclear` | Both styles are visible. |

## 6. Calibration

Before the study, both raters label the same 10 calibration findings. These findings are not in the sample. Then both raters and the study lead discuss differences. If the discussion shows a gap in these guidelines, the study lead updates the guidelines before the sample is drawn.

## 7. Procedure

1. Read sections 1 to 5.
2. Label the calibration items (section 6).
3. Open your rater sheet in a spreadsheet tool. Save it as CSV in UTF-8.
4. Label each item in the order of the sheet.
5. Do not add, remove or reorder rows. Do not change columns other than `label` and `note`.
6. Return the sheet to the study lead.

Caution: Do not talk about items with the other rater before both sheets are returned.

## 8. Disagreements

After both sheets are returned, the study lead gives both raters an adjudication sheet with the items of different labels. The raters discuss each item and write one agreed label in `label`. If they do not agree, the study lead decides and writes "lead decision" in `note`.
