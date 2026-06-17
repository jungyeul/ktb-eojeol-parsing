# KTB eojeol/morpheme constituency conversion utilities

This directory contains four scripts used to build eojeol- and morpheme-based constituency parsing files from the Penn Korean Treebank / CoNLL-U conversion pipeline.

> **Note.** These scripts assume access to LDC's Penn Korean Treebank (`ktb2`, LDC2006T09) and its UD/CoNLL-U release (`penn_korean_tbnk`, LDC2023T05).

The central idea is to use **CoNLL-U as the pivot representation**.  CoNLL-U fixes the eojeol boundary, surface form, UPOS label, morpheme sequence, and XPOS sequence.  Constituency bracketing is then added as two extra columns, and `.mrg` files are extracted from that extended representation.

This avoids direct tree-to-tree projection between morpheme-based and eojeol-based `.mrg` files, which is difficult because Korean functional morphemes can have non-local structural behavior in the original treebank.

## Scripts

1. `add_const_to_conllu_skip_mismatch.py`  
   Add gold constituency bracketing from a KTB `*.fid.utf8` file to a CoNLL-U file.

2. `added_conllu_to_mrg_plus.py`  
   Extract eojeol-based and morpheme-based one-line `.mrg` files from an extended added CoNLL-U file.

3. `strip_functional_with_null_wrapper.py`  
   Strip functional labels and remove null elements from `.mrg` trees.

4. `morph_pred_to_eojeol_via_conllu.py`  
   Convert predicted morpheme-based `.mrg` trees into predicted eojeol-based `.mrg` trees using the original CoNLL-U file as the pivot.

---

## 1. `add_const_to_conllu_skip_mismatch.py`

### Goal

Add constituency information from a KTB `*.fid.utf8` file to a CoNLL-U file.

The output is an extended CoNLL-U file with two additional columns:

```text
11  LHS
12  RHS
```

For each CoNLL-U token, `LHS` contains opening constituency material immediately before that token, and `RHS` contains closing constituency material immediately after that token.

### Usage

```bash
python add_const_to_conllu_skip_mismatch.py \
  input.fid.conllu \
  input.fid.utf8 \
  output.fid.conllu.added
```

Example:

```bash
python add_const_to_conllu_skip_mismatch.py \
  306007.fid.conllu \
  306007.fid.utf8 \
  306007.fid.conllu.added
```

### Input

A standard 10-column CoNLL-U file:

```text
1   이르면   이르+으면   ADJ   VJ+ECS   _   2   advcl   _   _
```

A KTB `*.fid.utf8` file containing the corresponding original constituency trees.

### Output

An extended CoNLL-U file:

```text
1   이르면   이르+으면   ADJ   VJ+ECS   _   2   advcl   _   _   (S ... (ADJP   ))
```

### Mismatch handling

If the CoNLL-U word count and constituency overt-terminal count do not match, the script does **not** abort.  It writes the problematic sentence as a commented diagnostic block beginning with:

```text
## skipped_sentence = word_count_mismatch
```

The original dependency and constituency information are preserved under `##` comments, and the script continues processing the rest of the file.

### Reasoning

The KTB and CoNLL-U versions may occasionally disagree in overt terminal count.  For corpus-level conversion, it is better to preserve these cases as diagnostics and continue producing the full file, rather than stopping at the first mismatch.

---

## 2. `added_conllu_to_mrg_plus.py`

### Goal

Convert an extended added CoNLL-U file into two one-line `.mrg` files:

1. an **eojeol-based** constituency file;
2. a **morpheme-based** constituency file.

The script skips diagnostic comment blocks produced by `add_const_to_conllu_skip_mismatch.py`.

### Usage

```bash
python added_conllu_to_mrg_plus.py \
  input.fid.conllu.added \
  output.eojeol.mrg \
  output.morpheme.mrg
```

Example:

```bash
python added_conllu_to_mrg_plus.py \
  306007.fid.conllu.added \
  306007.fid.conllu.added-eojeol.mrg \
  306007.fid.conllu.added-morpheme.mrg
```

### Eojeol-based output

The eojeol-based tree uses:

```text
FORM = column 2
UPOS = column 4
```

For example:

```text
1   이르면   이르+으면   ADJ   VJ+ECS   ...
```

becomes:

```lisp
(ADJ 이르면)
```

### Morpheme-based output

The morpheme-based tree uses:

```text
MORPHS = column 3, split by +
XPOS   = column 5, split by +
```

For example:

```text
이르+으면   VJ+ECS
```

becomes:

```lisp
(VJ 이르) (ECS 으면)
```

### Literal plus-sign handling

A literal plus token in the sentence is written as:

```text
*PLUS*
```

For example:

```text
morphs='+'   xpos='SSY'
```

becomes:

```lisp
(SSY *PLUS*)
```

This prevents confusion between the morpheme delimiter `+` and an actual plus-sign token.

### Options

Keep literal bracket tokens instead of Penn Treebank-style escapes:

```bash
python added_conllu_to_mrg_plus.py \
  input.added.conllu eojeol.mrg morph.mrg \
  --no-escape-parens
```

Use a fallback terminal when column 3 and column 5 have different `+` segment counts:

```bash
python added_conllu_to_mrg_plus.py \
  input.added.conllu eojeol.mrg morph.mrg \
  --non-strict-morph
```

### Reasoning

This script gives parallel eojeol and morpheme views of the same constituency bracketing.  The eojeol view is used for surface-oriented parsing, while the morpheme view allows experiments over internally segmented Korean terminals.

---

## 3. `strip_functional_with_null_wrapper.py`

### Goal

Clean one-line `.mrg` trees by:

1. converting null-like terminals to `-NONE-`;
2. removing `-NONE-` leaves;
3. stripping functional labels such as `NP-SBJ`, `S-COMP`, and `WHNP-1`.

This is a self-contained wrapper: it does not require an external copy of `strip_functional.py`.

### Usage

```bash
python strip_functional_with_null_wrapper.py input.mrg > output.stripped.mrg
```

For eojeol and morpheme files:

```bash
python strip_functional_with_null_wrapper.py \
  ktb.eojeol.mrg \
  > ktb.eojeol.mrg.stripped

python strip_functional_with_null_wrapper.py \
  ktb.morpheme.mrg \
  > ktb.morpheme.mrg.stripped
```

### Functional-label stripping

Examples:

```text
NP-SBJ    -> NP
S-COMP    -> S
WHNP-1    -> WHNP
NP=2      -> NP
```

Protected symbols such as `-NONE-`, `-LRB-`, and `-RRB-` are not stripped as ordinary functional labels.

### Null handling

The script treats KTB/PTB-style null terminals as removable nulls, including forms such as:

```text
*pro*
*T*-1
*op*
*ICH*
*EXP*
0
```

For example:

```lisp
(S (NP-SBJ *pro*) (VP (ADJP (ADJ 이르면))))
```

is internally converted through `-NONE-` and then cleaned to:

```lisp
(S (VP (ADJP (ADJ 이르면))))
```

### Useful options

Remove a root wrapper such as `TOP` if present:

```bash
python strip_functional_with_null_wrapper.py \
  --remove_root TOP \
  input.mrg > output.mrg
```

Require the root to be `TOP` and fail otherwise:

```bash
python strip_functional_with_null_wrapper.py \
  --remove_root_must_have TOP \
  input.mrg > output.mrg
```

Treat an additional terminal string as null:

```bash
python strip_functional_with_null_wrapper.py \
  --extra_null_terminal '*NULL*' \
  input.mrg > output.mrg
```

### Reasoning

Parsing evaluation normally ignores null elements and functional suffixes.  This script prepares KTB-derived `.mrg` files for evaluation by removing empty categories and normalizing phrase labels to their basic categories.

---

## 4. `morph_pred_to_eojeol_via_conllu.py`

### Goal

Convert predicted morpheme-based constituency trees into predicted eojeol-based constituency trees, using the original CoNLL-U file as a pivot.

This is the recommended way to evaluate morpheme-based predictions under an eojeol-based evaluation target.

### Usage

```bash
python morph_pred_to_eojeol_via_conllu.py \
  original.conllu \
  predicted.morpheme.mrg \
  -o predicted.eojeol.mrg
```

Optionally write the intermediate predicted added CoNLL-U file:

```bash
python morph_pred_to_eojeol_via_conllu.py \
  original.conllu \
  predicted.morpheme.mrg \
  -o predicted.eojeol.mrg \
  --added-output predicted.added.conllu
```

### Input

The original 10-column CoNLL-U file provides:

```text
FORM   = column 2
MORPH  = column 3
UPOS   = column 4
XPOS   = column 5
```

The predicted morpheme `.mrg` file contains one predicted morpheme-based tree per line.

### Output

A predicted eojeol-based `.mrg` file.  It uses:

```text
terminal = (UPOS FORM)
```

while phrase-level predicted structure is projected from the predicted morpheme tree onto eojeol boundaries fixed by CoNLL-U.

Example CoNLL-U:

```text
1   브뤼셀과   브뤼셀+과      PROPN   NPR+PCJ   _   _   _   _   _
2   말했다     말하+었+다     VERB    VV+EPF+EFN _   _   _   _   _
3   .          .              PUNCT   SFN       _   _   _   _   _
```

Predicted morpheme tree:

```lisp
(S (NP (NPR 브뤼셀) (PCJ 과)) (VP (VV 말하) (EPF 었) (EFN 다)) (SFN .))
```

Predicted eojeol output:

```lisp
(S (NP (PROPN 브뤼셀과)) (VP (VERB 말했다)) (PUNCT .))
```

### Optional intermediate output

With `--added-output`, the script writes a predicted added CoNLL-U file with LHS/RHS columns:

```text
1   브뤼셀과   브뤼셀+과      PROPN   NPR+PCJ     ...   (S (NP   )
2   말했다     말하+었+다     VERB    VV+EPF+EFN  ...   (VP      )
3   .          .              PUNCT   SFN         ...   _        )
```

### Options

Warn instead of failing when predicted morpheme terminal strings differ from CoNLL-U terminal strings.  Terminal counts must still match.

```bash
python morph_pred_to_eojeol_via_conllu.py \
  original.conllu predicted.morpheme.mrg \
  -o predicted.eojeol.mrg \
  --warn-terminal-mismatch
```

Skip/comment problematic sentences instead of aborting:

```bash
python morph_pred_to_eojeol_via_conllu.py \
  original.conllu predicted.morpheme.mrg \
  -o predicted.eojeol.mrg \
  --on-error skip
```

Keep literal bracket terminals instead of Penn-style escapes:

```bash
python morph_pred_to_eojeol_via_conllu.py \
  original.conllu predicted.morpheme.mrg \
  -o predicted.eojeol.mrg \
  --no-escape-parens
```

### Reasoning

Direct `.mrg`-to-`.mrg` projection between morpheme and eojeol trees is unstable, because KTB morpheme-level trees are not always a simple local refinement of eojeol-level trees.  Functional morphemes such as particles and endings may not correspond to a local eojeol-internal subtree.

The safer strategy is:

```text
predicted morpheme .mrg
+ original CoNLL-U
-> predicted added CoNLL-U
-> predicted eojeol .mrg
```

This keeps eojeol boundaries fixed by CoNLL-U and extracts the final evaluation tree using UPOS and surface eojeol forms.

---

## Recommended workflows

### A. Build gold eojeol and morpheme `.mrg` files

```bash
python add_const_to_conllu_skip_mismatch.py \
  gold.fid.conllu \
  gold.fid.utf8 \
  gold.fid.conllu.added

python added_conllu_to_mrg_plus.py \
  gold.fid.conllu.added \
  gold.eojeol.mrg \
  gold.morpheme.mrg

python strip_functional_with_null_wrapper.py \
  gold.eojeol.mrg \
  > gold.eojeol.mrg.stripped

python strip_functional_with_null_wrapper.py \
  gold.morpheme.mrg \
  > gold.morpheme.mrg.stripped
```

### B. Convert predicted morpheme trees to predicted eojeol trees

```bash
python morph_pred_to_eojeol_via_conllu.py \
  original.conllu \
  predicted.morpheme.mrg \
  -o predicted.eojeol.mrg \
  --added-output predicted.added.conllu

python strip_functional_with_null_wrapper.py \
  predicted.eojeol.mrg \
  > predicted.eojeol.mrg.stripped
```

### C. Evaluate

Evaluate the stripped predicted eojeol tree against the stripped gold eojeol tree:

```bash
EVALB gold.eojeol.mrg.stripped predicted.eojeol.mrg.stripped
```

The exact EVALB command may vary depending on the evaluation script and parameter file used in the experiment.

---

## Notes

- All `.mrg` files are expected to contain one tree per line.
- Literal plus signs are represented as `*PLUS*` to avoid confusion with morpheme delimiters.
- Parentheses are escaped by default as `-LRB-` and `-RRB-` where relevant.
- Sentences commented with `## skipped_sentence = word_count_mismatch` are diagnostic records and are not converted into `.mrg` trees.
- The recommended evaluation target for morpheme-based predictions under eojeol evaluation is produced by `morph_pred_to_eojeol_via_conllu.py`, not by direct projection between independent `.mrg` files.
