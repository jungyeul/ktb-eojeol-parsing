#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Add constituency bracketing information from a KTB *.fid.utf8 file
to a CoNLL-U *.fid.conllu file.

The output is an extended CoNLL-U file with two additional columns:

    11 LHS
    12 RHS

For each CoNLL-U word token, LHS contains the opening constituency material
immediately before that token, and RHS contains the closing constituency
material immediately after that token.

If a sentence has a word-count mismatch between the CoNLL-U block and the
constituency tree, the converter does not abort.  Instead, it writes that
sentence as commented diagnostic material prefixed with ## and continues with
the next sentence.

Null elements such as *pro*, *T*-1, *op* are kept in the bracketing string
and do not consume a CoNLL-U token.

Example:

    프랑스/NPR+의/PAN

may yield:

    LHS = (S (S-COMP-1 (NP-SBJ *pro*) (VP (S-OBJ (NP-SBJ (NP
    RHS = )

Usage:

    python add_const_to_conllu.py 302000.fid.conllu 302000.fid.utf8 output.ext.conllu
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import List, Tuple, Union, Optional


# ----------------------------------------------------------------------
# Tree representation
# ----------------------------------------------------------------------

Tree = Union["Node", str]


@dataclass
class Node:
    label: str
    children: List[Tree]


# ----------------------------------------------------------------------
# Reading the *.fid.utf8 file
# ----------------------------------------------------------------------

UTF8_HEADER_RE = re.compile(r"^;;([^:]+):([^:]+):\s*(.*)$")


def normalize_surface_sentence(sent: str) -> str:
    """
    Remove KTB surface marker '~'.

    Example:
        밝혔다 ~.  -> 밝혔다 .
        장 ~- ~마르크 -> 장 - 마르크
    """
    return sent.strip().replace("~", "")


def read_utf8_records(path: str) -> List[Tuple[str, str, str]]:
    """
    Read records from a KTB *.fid.utf8 file.

    Returns:
        [(record_id, surface_sentence, tree_string), ...]

    where record_id has the form:
        3020001:1
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    records: List[Tuple[str, str, str]] = []

    current_id: Optional[str] = None
    current_sent: Optional[str] = None
    tree_lines: List[str] = []
    balance = 0
    reading_tree = False

    def flush() -> None:
        nonlocal current_id, current_sent, tree_lines, balance, reading_tree

        if current_id is not None and current_sent is not None and tree_lines:
            records.append(
                (current_id, current_sent, "\n".join(tree_lines).strip())
            )

        current_id = None
        current_sent = None
        tree_lines = []
        balance = 0
        reading_tree = False

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        m = UTF8_HEADER_RE.match(line)

        if m:
            if current_id is not None and tree_lines:
                flush()

            doc_id, sent_no, sent = m.groups()
            current_id = f"{doc_id}:{sent_no}"
            current_sent = normalize_surface_sentence(sent)
            tree_lines = []
            balance = 0
            reading_tree = False
            continue

        if current_id is None:
            continue

        if line.startswith("(") or reading_tree:
            reading_tree = True
            tree_lines.append(line)
            balance += line.count("(") - line.count(")")

            if balance == 0 and tree_lines:
                flush()

    if current_id is not None and tree_lines:
        flush()

    return records


# ----------------------------------------------------------------------
# Parsing Penn-style constituency trees
# ----------------------------------------------------------------------

def tokenize_tree(tree_str: str) -> List[str]:
    """
    Tokenize a Penn-style tree into parentheses and atoms.
    """
    return re.findall(r"\(|\)|[^\s()]+", tree_str)


def parse_tree(tokens: List[str], pos: int = 0) -> Tuple[Tree, int]:
    """
    Recursive descent parser for Penn-style bracketed trees.
    """
    if pos >= len(tokens):
        raise ValueError("Unexpected end of tree")

    tok = tokens[pos]

    if tok != "(":
        return tok, pos + 1

    pos += 1

    if pos >= len(tokens):
        raise ValueError("Missing node label")

    label = tokens[pos]
    pos += 1

    children: List[Tree] = []

    while pos < len(tokens) and tokens[pos] != ")":
        child, pos = parse_tree(tokens, pos)
        children.append(child)

    if pos >= len(tokens) or tokens[pos] != ")":
        raise ValueError(f"Missing closing parenthesis for node {label}")

    pos += 1

    return Node(label, children), pos


# ----------------------------------------------------------------------
# Terminal and event utilities
# ----------------------------------------------------------------------

def is_null_terminal(x: str) -> bool:
    """
    Empty categories / traces do not correspond to CoNLL-U word tokens.

    Examples:
        *pro*
        *T*-1
        *op*
        *ICH*-2
    """
    return x.startswith("*") and "*" in x[1:]


def is_overt_terminal(x: str) -> bool:
    """
    Overt terminals normally contain a POS slash.

    Examples:
        프랑스/NPR+의/PAN
        밝히/VV+었/EPF+다/EFN
        ./SFN
        -/SSY
    """
    return "/" in x and not is_null_terminal(x)


Event = Tuple[str, Optional[str]]
# Event types:
#   ("open", label)
#   ("close", None)
#   ("leaf", terminal)


def tree_events(tree: Tree):
    """
    Yield a linearized stream of tree events.
    """
    if isinstance(tree, str):
        yield ("leaf", tree)
        return

    yield ("open", tree.label)

    for child in tree.children:
        yield from tree_events(child)

    yield ("close", None)


def render_events(events: List[Event]) -> str:
    """
    Render events as a compact bracket string.

    Empty material is represented as '_'.

    Example:
        [('open','NP'), ('leaf','*pro*'), ('close',None)]
        -> '(NP *pro*)'
    """
    if not events:
        return "_"

    pieces: List[str] = []

    for kind, value in events:
        if kind == "open":
            pieces.append(f"({value}")
        elif kind == "close":
            pieces.append(")")
        elif kind == "leaf":
            assert value is not None
            pieces.append(value)
        else:
            raise ValueError(f"Unknown event kind: {kind}")

    s = " ".join(pieces)

    # Compact spaces before closing parentheses:
    #   (NP *pro* )  -> (NP *pro*)
    while " )" in s:
        s = s.replace(" )", ")")

    return s


def extract_lhs_rhs(tree: Tree) -> Tuple[List[str], List[str], List[str]]:
    """
    Extract LHS/RHS bracketing information for each overt terminal.

    Returns:
        terminals, lhs_list, rhs_list

    The number of returned terminals should equal the number of CoNLL-U
    word tokens for the same sentence.

    The split rule between two overt terminals is:

      - leading close parentheses after the previous terminal go to RHS
        of the previous token;
      - remaining material, including new open parentheses and intervening
        null elements, goes to LHS of the current token.

    This gives:

        프랑스의: LHS = ... (NP
                  RHS = )
        르노:    LHS = (NP
                  RHS = _
        자동차:  LHS = _
                  RHS = _
        그룹은:  LHS = _
                  RHS = ))
    """
    terminals: List[str] = []
    lhs_list: List[str] = []
    rhs_list: List[str] = []

    gap: List[Event] = []
    prev_overt_index: Optional[int] = None

    for event in tree_events(tree):
        kind, value = event

        if kind == "leaf" and value is not None and is_overt_terminal(value):
            current_index = len(terminals)
            terminals.append(value)

            if prev_overt_index is None:
                lhs_list.append(render_events(gap))
                rhs_list.append("_")
            else:
                # Split the material between previous overt terminal and
                # current overt terminal.
                split = 0
                while split < len(gap) and gap[split][0] == "close":
                    split += 1

                rhs_list[prev_overt_index] = render_events(gap[:split])
                lhs_list.append(render_events(gap[split:]))
                rhs_list.append("_")

            gap = []
            prev_overt_index = current_index

        else:
            gap.append(event)

    if prev_overt_index is not None:
        rhs_list[prev_overt_index] = render_events(gap)

    return terminals, lhs_list, rhs_list


# ----------------------------------------------------------------------
# CoNLL-U reading/writing
# ----------------------------------------------------------------------

def read_conllu_sentences(path: str) -> List[List[str]]:
    """
    Read a CoNLL-U file as a list of sentence blocks.
    Each block is a list of raw lines without trailing newlines.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    blocks: List[List[str]] = []
    current: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")

        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue

        current.append(line)

    if current:
        blocks.append(current)

    return blocks


def is_word_line(line: str) -> bool:
    """
    Return True for ordinary CoNLL-U word lines.

    We skip:
      - comments
      - multiword token lines such as 1-2
      - empty node lines such as 3.1
    """
    if not line or line.startswith("#"):
        return False

    cols = line.split("\t")

    if not cols:
        return False

    tok_id = cols[0]

    if "-" in tok_id or "." in tok_id:
        return False

    return tok_id.isdigit()


def extend_conllu_sentence(
    conllu_lines: List[str],
    lhs_list: List[str],
    rhs_list: List[str],
    sent_label: str = "",
) -> List[str]:
    """
    Add LHS/RHS columns to one CoNLL-U sentence.
    """
    word_count = sum(1 for line in conllu_lines if is_word_line(line))

    if word_count != len(lhs_list):
        raise ValueError(
            f"{sent_label}: CoNLL-U word count = {word_count}, "
            f"constituency overt terminal count = {len(lhs_list)}"
        )

    output: List[str] = []
    word_index = 0

    for line in conllu_lines:
        if not is_word_line(line):
            output.append(line)
            continue

        cols = line.split("\t")

        if len(cols) != 10:
            raise ValueError(
                f"{sent_label}: expected 10 CoNLL-U columns, "
                f"got {len(cols)} in line: {line}"
            )

        cols.append(lhs_list[word_index])
        cols.append(rhs_list[word_index])

        output.append("\t".join(cols))
        word_index += 1

    return output


def sent_id_from_conllu_block(block: List[str]) -> str:
    """
    Extract # sent_id from a CoNLL-U block, if present.
    """
    for line in block:
        if line.startswith("# sent_id ="):
            return line.split("=", 1)[1].strip()
    return "_"


def comment_lines(lines: List[str], prefix: str = "## ") -> List[str]:
    """
    Prefix raw lines so that they remain visible but are ignored as comments.
    """
    return [prefix + line if line else prefix.rstrip() for line in lines]


def skipped_mismatch_block(
    conllu_block: List[str],
    utf8_id: str,
    surface: str,
    tree_str: str,
    sent_label: str,
    conllu_word_count: int,
    const_terminal_count: int,
) -> List[str]:
    """
    Preserve a mismatching sentence as commented diagnostic material.

    The original dependency block and the original constituency tree are both
    copied with a leading ## so that the output file remains machine-readable
    for successfully converted sentences while still documenting skipped cases.
    """
    block: List[str] = []

    block.append("## skipped_sentence = word_count_mismatch")
    block.append(f"## {sent_label}")
    block.append(
        "## mismatch = "
        f"CoNLL-U word count {conllu_word_count} != "
        f"constituency overt terminal count {const_terminal_count}"
    )
    block.append(f"## utf8_id = {utf8_id}")
    block.append(f"## utf8_surface = {surface}")

    block.append("## original_dependency_conllu_begin")
    block.extend(comment_lines(conllu_block))
    block.append("## original_dependency_conllu_end")

    block.append("## original_constituency_utf8_begin")
    block.extend(comment_lines(tree_str.splitlines()))
    block.append("## original_constituency_utf8_end")

    return block


# ----------------------------------------------------------------------
# Main conversion
# ----------------------------------------------------------------------

def convert_files(conllu_path: str, utf8_path: str, output_path: str) -> None:
    conllu_blocks = read_conllu_sentences(conllu_path)
    utf8_records = read_utf8_records(utf8_path)

    if len(conllu_blocks) != len(utf8_records):
        raise ValueError(
            f"Sentence count mismatch: CoNLL-U has {len(conllu_blocks)} blocks, "
            f"UTF8 has {len(utf8_records)} records"
        )

    output_blocks: List[List[str]] = []

    for i, (conllu_block, utf8_record) in enumerate(
        zip(conllu_blocks, utf8_records), start=1
    ):
        utf8_id, surface, tree_str = utf8_record
        conllu_id = sent_id_from_conllu_block(conllu_block)
        sent_label = f"sentence {i} / conllu={conllu_id} / utf8={utf8_id}"

        tokens = tokenize_tree(tree_str)
        tree, pos = parse_tree(tokens)

        if pos != len(tokens):
            raise ValueError(
                f"{sent_label}: unparsed tree tokens remain from position {pos}"
            )

        terminals, lhs_list, rhs_list = extract_lhs_rhs(tree)

        # The terminal strings are not directly compared to CoNLL-U forms,
        # because Korean surface forms may differ from morpheme concatenation.
        # The crucial check here is the number of overt terminals.
        conllu_word_count = sum(1 for line in conllu_block if is_word_line(line))
        const_terminal_count = len(lhs_list)

        if conllu_word_count != const_terminal_count:
            print(
                f"WARNING: skipping {sent_label}: "
                f"CoNLL-U word count = {conllu_word_count}, "
                f"constituency overt terminal count = {const_terminal_count}",
                file=sys.stderr,
            )
            output_blocks.append(
                skipped_mismatch_block(
                    conllu_block=conllu_block,
                    utf8_id=utf8_id,
                    surface=surface,
                    tree_str=tree_str,
                    sent_label=sent_label,
                    conllu_word_count=conllu_word_count,
                    const_terminal_count=const_terminal_count,
                )
            )
            continue

        extended = extend_conllu_sentence(
            conllu_block,
            lhs_list,
            rhs_list,
            sent_label=sent_label,
        )

        output_blocks.append(extended)

    with open(output_path, "w", encoding="utf-8") as out:
        for block in output_blocks:
            for line in block:
                out.write(line + "\n")
            out.write("\n")


def main() -> None:
    if len(sys.argv) != 4:
        print(
            "Usage: python add_const_to_conllu.py input.conllu input.fid.utf8 output.ext.conllu",
            file=sys.stderr,
        )
        sys.exit(1)

    conllu_path = sys.argv[1]
    utf8_path = sys.argv[2]
    output_path = sys.argv[3]

    convert_files(conllu_path, utf8_path, output_path)


if __name__ == "__main__":
    main()
