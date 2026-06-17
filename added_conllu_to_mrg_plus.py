#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Convert an extended CoNLL-U file with constituency LHS/RHS columns into
one-line Penn-style .mrg files for constituency parsing.

The input is the "added" CoNLL-U format produced by add_const_to_conllu.py:

    ID FORM LEMMA/SEG UPOS XPOS FEATS HEAD DEPREL DEPS MISC LHS RHS

For each successfully converted sentence, this script writes two trees:

  1. Eojeol-based tree:
       terminal = (UPOS FORM), using columns 2 and 4.

  2. Morpheme-based tree:
       terminals = (XPOS_i MORPH_i), using columns 3 and 5 split on '+'.

Sentences skipped by the previous converter are expected to be commented with
lines beginning with '##'.  Those commented skipped blocks are ignored and do
not produce trees.

Usage:

    python added_conllu_to_mrg.py input.added.conllu eojeol.mrg morph.mrg

By default, literal parentheses in terminals are escaped as -LRB- and -RRB-
so that the output remains valid bracketed .mrg.  Use --no-escape-parens if
raw surface parentheses are required.  A literal plus-sign token is written as
*PLUS* so that it is not confused with the morpheme delimiter '+'.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass
class TokenLine:
    token_id: str
    form: str
    morphs: str
    upos: str
    xpos: str
    lhs: str
    rhs: str
    raw: str


def read_sentence_blocks(path: str) -> List[List[str]]:
    """Read a CoNLL-U-like file as blank-line separated sentence blocks."""
    blocks: List[List[str]] = []
    current: List[str] = []

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                if current:
                    blocks.append(current)
                    current = []
            else:
                current.append(line)

    if current:
        blocks.append(current)

    return blocks


def is_integer_token_id(token_id: str) -> bool:
    """True for ordinary CoNLL-U word IDs; false for 1-2 and 3.1."""
    return token_id.isdigit()


def is_word_line(line: str) -> bool:
    if not line or line.startswith("#"):
        return False
    first = line.split("\t", 1)[0]
    if first == line:
        first = line.split(None, 1)[0]
    return is_integer_token_id(first)


def is_skipped_comment_block(block: Sequence[str]) -> bool:
    """
    Return True for blocks preserving mismatched sentences from the previous
    converter.  Such blocks begin with comments like:

        ## skipped_sentence = word_count_mismatch
    """
    return any(line.startswith("## skipped_sentence") for line in block)


def parse_extended_word_line(line: str, sent_no: int) -> TokenLine:
    """
    Parse one extended CoNLL-U word line.

    The reliable format is tab-separated, because LHS/RHS may contain spaces.
    A conservative whitespace fallback is provided only for lines whose added
    columns contain no internal whitespace.
    """
    cols = line.split("\t")

    if len(cols) < 12:
        # Fallback for unusually space-separated files.  This only works when
        # LHS and RHS themselves do not contain unescaped whitespace, so normal
        # tab-separated extended CoNLL-U is strongly preferred.
        cols = re.split(r"\s+", line, maxsplit=11)

    if len(cols) < 12:
        raise ValueError(
            f"sentence block {sent_no}: expected at least 12 columns in "
            f"extended CoNLL-U line, got {len(cols)}: {line}"
        )

    if not is_integer_token_id(cols[0]):
        raise ValueError(
            f"sentence block {sent_no}: unexpected non-integer word ID "
            f"in line: {line}"
        )

    return TokenLine(
        token_id=cols[0],
        form=cols[1],
        morphs=cols[2],
        upos=cols[3],
        xpos=cols[4],
        lhs=cols[10],
        rhs=cols[11],
        raw=line,
    )


def escape_terminal(s: str, escape_parens: bool = True) -> str:
    """Escape tokens that would break or confuse Penn-style bracket notation."""
    # A literal plus sign can also be the morpheme delimiter in column 3/5.
    # Write the token itself as *PLUS* in both eojeol and morpheme output.
    if s == "+":
        return "*PLUS*"

    if not escape_parens:
        return s

    mapping = {
        "(": "-LRB-",
        ")": "-RRB-",
        "[": "-LSB-",
        "]": "-RSB-",
        "{": "-LCB-",
        "}": "-RCB-",
    }
    return mapping.get(s, s)


def make_preterminal(label: str, terminal: str, escape_parens: bool) -> str:
    return f"({label} {escape_terminal(terminal, escape_parens)})"


def eojeol_terminal(tok: TokenLine, escape_parens: bool) -> List[str]:
    return [make_preterminal(tok.upos, tok.form, escape_parens)]


def split_morphs_and_xpos(tok: TokenLine) -> Tuple[List[str], List[str]]:
    """
    Split the morpheme and XPOS fields.

    The plus sign is normally the delimiter, but it can also be an actual
    sentence token, e.g. morphs='+' with xpos='SSY'.  In that case it must be
    treated as one morpheme token, not as two empty fields.
    """
    if tok.morphs == "+" and "+" not in tok.xpos:
        return ["+"], [tok.xpos]

    morphs = tok.morphs.split("+") if tok.morphs else []
    xpos_tags = tok.xpos.split("+") if tok.xpos else []
    return morphs, xpos_tags


def morpheme_terminals(tok: TokenLine, escape_parens: bool, strict: bool) -> List[str]:
    """
    Build morpheme preterminals from columns 3 and 5.

    Example:
        morphs = 이르+으면
        xpos   = VJ+ECS
        -> (VJ 이르) (ECS 으면)

    Literal '+' tokens are output as *PLUS*:
        morphs = +
        xpos   = SSY
        -> (SSY *PLUS*)
    """
    morphs, xpos_tags = split_morphs_and_xpos(tok)

    if len(morphs) != len(xpos_tags):
        msg = (
            f"morpheme/POS count mismatch at token {tok.token_id}: "
            f"morphs={tok.morphs!r}, xpos={tok.xpos!r}"
        )
        if strict:
            raise ValueError(msg)
        print(f"WARNING: {msg}; falling back to one ({tok.xpos} {tok.morphs}) terminal", file=sys.stderr)
        return [make_preterminal(tok.xpos, tok.morphs, escape_parens)]

    return [
        make_preterminal(tag, morph, escape_parens)
        for morph, tag in zip(morphs, xpos_tags)
    ]


def append_constituency_material(parts: List[str], material: str) -> None:
    if material and material != "_":
        parts.append(material)


def normalize_tree_line(s: str) -> str:
    """Normalize spacing in a one-line bracketed tree."""
    s = re.sub(r"\s+", " ", s).strip()
    while " )" in s:
        s = s.replace(" )", ")")
    return s


def paren_balance(s: str) -> int:
    return s.count("(") - s.count(")")


def build_tree(
    tokens: Sequence[TokenLine],
    mode: str,
    escape_parens: bool,
    strict_morph: bool,
) -> str:
    parts: List[str] = []

    for tok in tokens:
        append_constituency_material(parts, tok.lhs)

        if mode == "eojeol":
            parts.extend(eojeol_terminal(tok, escape_parens))
        elif mode == "morpheme":
            parts.extend(morpheme_terminals(tok, escape_parens, strict_morph))
        else:
            raise ValueError(f"unknown mode: {mode}")

        append_constituency_material(parts, tok.rhs)

    tree = normalize_tree_line(" ".join(parts))

    balance = paren_balance(tree)
    if balance != 0:
        raise ValueError(
            f"unbalanced output tree in {mode} mode: "
            f"open-close balance = {balance}; tree begins: {tree[:200]}"
        )

    return tree


def sent_id_from_block(block: Sequence[str]) -> str:
    for line in block:
        if line.startswith("# sent_id ="):
            return line.split("=", 1)[1].strip()
    return "_"


def convert_added_conllu_to_mrg(
    input_path: str,
    eojeol_output_path: str,
    morph_output_path: str,
    escape_parens: bool = True,
    strict_morph: bool = True,
) -> Tuple[int, int]:
    """
    Convert an extended CoNLL-U file into eojeol- and morpheme-based .mrg.

    Returns:
        (written_sentence_count, skipped_comment_block_count)
    """
    blocks = read_sentence_blocks(input_path)
    written = 0
    skipped = 0

    with open(eojeol_output_path, "w", encoding="utf-8") as eojeol_out, \
         open(morph_output_path, "w", encoding="utf-8") as morph_out:

        for sent_no, block in enumerate(blocks, start=1):
            if is_skipped_comment_block(block):
                skipped += 1
                continue

            word_lines = [line for line in block if is_word_line(line)]
            if not word_lines:
                # Ignore pure comment/empty metadata blocks.
                continue

            sent_id = sent_id_from_block(block)
            try:
                tokens = [parse_extended_word_line(line, sent_no) for line in word_lines]
                eojeol_tree = build_tree(
                    tokens,
                    mode="eojeol",
                    escape_parens=escape_parens,
                    strict_morph=strict_morph,
                )
                morph_tree = build_tree(
                    tokens,
                    mode="morpheme",
                    escape_parens=escape_parens,
                    strict_morph=strict_morph,
                )
            except Exception as e:
                raise ValueError(f"failed at sentence block {sent_no} / sent_id={sent_id}: {e}") from e

            eojeol_out.write(eojeol_tree + "\n")
            morph_out.write(morph_tree + "\n")
            written += 1

    return written, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert extended added CoNLL-U into eojeol- and morpheme-based one-line .mrg files."
    )
    parser.add_argument("input", help="Input extended/added CoNLL-U file")
    parser.add_argument("eojeol_output", help="Output eojeol-based .mrg file")
    parser.add_argument("morph_output", help="Output morpheme-based .mrg file")
    parser.add_argument(
        "--no-escape-parens",
        action="store_true",
        help="Keep literal (, ), [, ], {, } terminals instead of Penn-style escapes.",
    )
    parser.add_argument(
        "--non-strict-morph",
        action="store_true",
        help="Do not abort when column 3 and column 5 have different '+' segment counts; use one fallback terminal instead.",
    )

    args = parser.parse_args()

    written, skipped = convert_added_conllu_to_mrg(
        input_path=args.input,
        eojeol_output_path=args.eojeol_output,
        morph_output_path=args.morph_output,
        escape_parens=not args.no_escape_parens,
        strict_morph=not args.non_strict_morph,
    )

    print(
        f"Wrote {written} trees to each output file; "
        f"skipped {skipped} commented mismatch block(s).",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
