#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert predicted morpheme-based constituency trees into eojeol-based trees
using the original CoNLL-U file as the pivot representation.

Pipeline implemented in one script:

  1. Read the original CoNLL-U file.
     - FORM  column 2 gives the eojeol surface.
     - UPOS  column 4 gives the eojeol preterminal label.
     - MORPH column 3 and XPOS column 5 determine how many morpheme leaves
       belong to each eojeol.

  2. Read a predicted morpheme-based .mrg file, one tree per line.

  3. Collapse each consecutive group of predicted morpheme leaves back to one
     eojeol leaf according to the CoNLL-U morpheme segmentation.

     The predicted phrase structure is preserved only when a predicted node's
     morpheme span aligns with eojeol boundaries. Predicted structure strictly
     internal to one eojeol is discarded, because it cannot be represented in an
     eojeol-terminal tree. XPOS preterminal labels are not kept; the output uses
     the CoNLL-U UPOS labels.

  4. Write an optional intermediate extended/added CoNLL-U file with LHS/RHS
     constituency columns, and write the final eojeol-based .mrg file.

Example:

  python morph_pred_to_eojeol_via_conllu.py \
      original.conllu predicted.morpheme.mrg \
      -o predicted.eojeol.mrg \
      --added-output predicted.added.conllu

The output .mrg uses:

  terminal = (UPOS FORM)

for each CoNLL-U token, while phrase-level brackets come from the predicted
morpheme tree after span projection onto eojeol boundaries.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union


# ---------------------------------------------------------------------
# Generic tree representation
# ---------------------------------------------------------------------

Tree = Union["Node", str]


@dataclass
class Node:
    label: str
    children: List[Tree]


@dataclass
class WordToken:
    raw_line: str
    cols: List[str]
    token_id: str
    form: str
    morphs: str
    upos: str
    xpos: str


@dataclass
class LeafInfo:
    index: int
    label: str
    word: str


# ---------------------------------------------------------------------
# Reading CoNLL-U
# ---------------------------------------------------------------------

def read_conllu_sentences(path: str) -> List[List[str]]:
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


def is_word_id(tok_id: str) -> bool:
    return tok_id.isdigit()


def is_word_line(line: str) -> bool:
    if not line or line.startswith("#"):
        return False
    # CoNLL-U should be tab-separated, but be tolerant for the first field.
    first = line.split("\t", 1)[0]
    if first == line:
        first = line.split(None, 1)[0]
    return is_word_id(first)


def sent_id_from_block(block: Sequence[str]) -> str:
    for line in block:
        if line.startswith("# sent_id ="):
            return line.split("=", 1)[1].strip()
    return "_"


def parse_word_tokens(block: Sequence[str], sent_no: int) -> List[WordToken]:
    tokens: List[WordToken] = []
    for line in block:
        if not is_word_line(line):
            continue
        cols = line.split("\t")
        if len(cols) != 10:
            raise ValueError(
                f"sentence {sent_no}: expected 10 CoNLL-U columns, "
                f"got {len(cols)} in line: {line}"
            )
        tokens.append(
            WordToken(
                raw_line=line,
                cols=cols,
                token_id=cols[0],
                form=cols[1],
                morphs=cols[2],
                upos=cols[3],
                xpos=cols[4],
            )
        )
    return tokens


# ---------------------------------------------------------------------
# Morpheme splitting and terminal escaping
# ---------------------------------------------------------------------

def split_morphs_and_xpos(morphs: str, xpos: str) -> Tuple[List[str], List[str]]:
    """
    Split CoNLL-U column 3/5. A literal plus-sign token is represented as
    morphs='+' with a single POS such as SSY; this is not a delimiter case.
    """
    if morphs == "+" and "+" not in xpos:
        return ["+"], [xpos]
    return (morphs.split("+") if morphs else [], xpos.split("+") if xpos else [])


def escape_terminal(s: str, escape_parens: bool = True) -> str:
    if s == "+":
        return "*PLUS*"
    if not escape_parens:
        return s
    return {
        "(": "-LRB-",
        ")": "-RRB-",
        "[": "-LSB-",
        "]": "-RSB-",
        "{": "-LCB-",
        "}": "-RCB-",
    }.get(s, s)


def normalize_leaf_for_compare(s: str) -> str:
    # Normalize both raw terminals and already-escaped Penn terminals.
    if s == "+":
        return "*PLUS*"
    return {
        "(": "-LRB-",
        ")": "-RRB-",
        "[": "-LSB-",
        "]": "-RSB-",
        "{": "-LCB-",
        "}": "-RCB-",
    }.get(s, s)


def expected_morpheme_groups(tokens: Sequence[WordToken]) -> Tuple[List[List[str]], List[List[str]]]:
    groups: List[List[str]] = []
    pos_groups: List[List[str]] = []
    for tok in tokens:
        morphs, xpos = split_morphs_and_xpos(tok.morphs, tok.xpos)
        if len(morphs) != len(xpos):
            raise ValueError(
                f"morpheme/POS count mismatch at token {tok.token_id}: "
                f"morphs={tok.morphs!r}, xpos={tok.xpos!r}"
            )
        groups.append([normalize_leaf_for_compare(m) for m in morphs])
        pos_groups.append(xpos)
    return groups, pos_groups


# ---------------------------------------------------------------------
# Penn tree parsing
# ---------------------------------------------------------------------

def tokenize_tree(s: str) -> List[str]:
    return re.findall(r"\(|\)|[^\s()]+", s)


def parse_tree(tokens: Sequence[str], pos: int = 0) -> Tuple[Tree, int]:
    if pos >= len(tokens):
        raise ValueError("unexpected end of tree")
    tok = tokens[pos]
    if tok != "(":
        return tok, pos + 1
    pos += 1
    if pos >= len(tokens):
        raise ValueError("missing node label")
    label = tokens[pos]
    pos += 1
    children: List[Tree] = []
    while pos < len(tokens) and tokens[pos] != ")":
        child, pos = parse_tree(tokens, pos)
        children.append(child)
    if pos >= len(tokens) or tokens[pos] != ")":
        raise ValueError(f"missing closing parenthesis for node {label}")
    return Node(label, children), pos + 1


def read_mrg_lines(path: str) -> List[str]:
    lines: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                lines.append(s)
    return lines


def is_preterminal(t: Tree) -> bool:
    return isinstance(t, Node) and len(t.children) == 1 and isinstance(t.children[0], str)


def collect_preterminal_leaves(t: Tree, leaves: Optional[List[LeafInfo]] = None) -> List[LeafInfo]:
    if leaves is None:
        leaves = []
    if isinstance(t, str):
        return leaves
    if is_preterminal(t):
        leaves.append(LeafInfo(len(leaves), t.label, normalize_leaf_for_compare(t.children[0])))
        return leaves
    for child in t.children:
        collect_preterminal_leaves(child, leaves)
    return leaves


# ---------------------------------------------------------------------
# Span projection from morphemes to eojeols
# ---------------------------------------------------------------------

def group_boundaries(groups: Sequence[Sequence[str]]) -> Tuple[List[int], set[int], List[int]]:
    """
    Returns:
      starts: eojeol index -> first morph index
      boundary_set: all legal eojeol boundary morph positions
      morph_to_eojeol: morph index -> eojeol index
    """
    starts: List[int] = []
    boundary_set = {0}
    morph_to_eojeol: List[int] = []
    pos = 0
    for i, g in enumerate(groups):
        starts.append(pos)
        for _ in g:
            morph_to_eojeol.append(i)
            pos += 1
        boundary_set.add(pos)
    return starts, boundary_set, morph_to_eojeol


def verify_morpheme_sequence(
    predicted_leaves: Sequence[LeafInfo],
    expected_groups: Sequence[Sequence[str]],
    sent_label: str,
    warn: bool,
) -> None:
    expected = [m for group in expected_groups for m in group]
    predicted = [leaf.word for leaf in predicted_leaves]
    if len(expected) != len(predicted):
        raise ValueError(
            f"{sent_label}: predicted morpheme leaf count {len(predicted)} "
            f"!= CoNLL-U morpheme count {len(expected)}"
        )
    mismatches = [
        (i + 1, p, e)
        for i, (p, e) in enumerate(zip(predicted, expected))
        if p != e
    ]
    if mismatches:
        msg = "; ".join(f"#{i}: pred={p!r}, conllu={e!r}" for i, p, e in mismatches[:8])
        if warn:
            print(f"WARNING: {sent_label}: morpheme sequence differs: {msg}", file=sys.stderr)
        else:
            raise ValueError(f"{sent_label}: morpheme sequence differs: {msg}")


def compute_morph_spans(t: Tree, counter: List[int]) -> Tuple[int, int]:
    """Annotate spans externally by returning span over preterminal leaves."""
    if isinstance(t, str):
        return counter[0], counter[0]
    if is_preterminal(t):
        start = counter[0]
        counter[0] += 1
        return start, counter[0]
    start: Optional[int] = None
    end: Optional[int] = None
    for child in t.children:
        c_start, c_end = compute_morph_spans(child, counter)
        if start is None:
            start = c_start
        end = c_end
    if start is None:
        return counter[0], counter[0]
    assert end is not None
    return start, end


def project_tree_to_eojeol_sentinels(
    t: Tree,
    boundary_set: set[int],
    morph_to_eojeol: Sequence[int],
    first_morph_of_eojeol: set[int],
    force_keep_root: bool = True,
) -> Tree:
    """
    Project a morpheme tree onto eojeol leaves.

    Preterminal XPOS nodes are removed. Only the first morpheme of each eojeol
    emits a sentinel leaf @@EOJ_i@@. Other morphemes are deleted. Nonterminal
    nodes are kept only if their morph span coincides with eojeol boundaries;
    otherwise they are removed and their projected children are promoted.
    """
    counter = [0]

    def helper(node: Tree, is_root: bool = False) -> Tuple[List[Tree], Tuple[int, int]]:
        if isinstance(node, str):
            return [], (counter[0], counter[0])

        if is_preterminal(node):
            idx = counter[0]
            counter[0] += 1
            if idx in first_morph_of_eojeol:
                eoj = morph_to_eojeol[idx]
                return [f"@@EOJ_{eoj}@@"], (idx, idx + 1)
            return [], (idx, idx + 1)

        projected_children: List[Tree] = []
        start: Optional[int] = None
        end: Optional[int] = None
        for child in node.children:
            child_proj, (c_start, c_end) = helper(child, False)
            if start is None:
                start = c_start
            end = c_end
            projected_children.extend(child_proj)

        if start is None:
            start = counter[0]
            end = counter[0]
        assert end is not None

        # Remove empty nodes.
        if not projected_children:
            return [], (start, end)

        aligned = start in boundary_set and end in boundary_set
        if aligned or (is_root and force_keep_root):
            return [Node(node.label, projected_children)], (start, end)
        else:
            return projected_children, (start, end)

    projected, _span = helper(t, True)
    if not projected:
        return Node("TOP", [])
    if len(projected) == 1:
        return projected[0]
    # This should rarely happen; wrap multiple top-level projections.
    return Node("TOP", projected)


# ---------------------------------------------------------------------
# Extract LHS/RHS from sentinel tree and build output .mrg
# ---------------------------------------------------------------------

Event = Tuple[str, Optional[str]]  # open label / close / leaf sentinel


def tree_events_no_preterminals(t: Tree) -> Iterable[Event]:
    if isinstance(t, str):
        yield ("leaf", t)
        return
    yield ("open", t.label)
    for child in t.children:
        yield from tree_events_no_preterminals(child)
    yield ("close", None)


def render_events(events: Sequence[Event]) -> str:
    if not events:
        return "_"
    pieces: List[str] = []
    for kind, value in events:
        if kind == "open":
            pieces.append(f"({value}")
        elif kind == "close":
            pieces.append(")")
        elif kind == "leaf":
            # Leaf material is never rendered into LHS/RHS.
            continue
        else:
            raise ValueError(f"unknown event kind: {kind}")
    s = " ".join(pieces).strip()
    while " )" in s:
        s = s.replace(" )", ")")
    return s if s else "_"


def extract_lhs_rhs_from_sentinel_tree(t: Tree, expected_eojeols: int, sent_label: str) -> Tuple[List[str], List[str]]:
    lhs_list: List[str] = []
    rhs_list: List[str] = []
    gap: List[Event] = []
    prev_index: Optional[int] = None
    seen: List[int] = []

    for event in tree_events_no_preterminals(t):
        kind, value = event
        if kind == "leaf" and value is not None and value.startswith("@@EOJ_"):
            m = re.match(r"@@EOJ_(\d+)@@", value)
            if not m:
                raise ValueError(f"{sent_label}: malformed sentinel leaf {value!r}")
            current_index = int(m.group(1))
            seen.append(current_index)

            if prev_index is None:
                lhs_list.append(render_events(gap))
                rhs_list.append("_")
            else:
                split = 0
                while split < len(gap) and gap[split][0] == "close":
                    split += 1
                rhs_list[prev_index] = render_events(gap[:split])
                lhs_list.append(render_events(gap[split:]))
                rhs_list.append("_")
            gap = []
            prev_index = current_index
        else:
            gap.append(event)

    if prev_index is not None:
        rhs_list[prev_index] = render_events(gap)

    if len(lhs_list) != expected_eojeols:
        raise ValueError(
            f"{sent_label}: projected eojeol leaf count {len(lhs_list)} "
            f"!= CoNLL-U word count {expected_eojeols}; seen={seen[:20]}..."
        )
    if seen != list(range(expected_eojeols)):
        raise ValueError(
            f"{sent_label}: projected eojeol leaves are not in sentence order: "
            f"seen begins {seen[:20]}"
        )
    return lhs_list, rhs_list


def make_preterminal(label: str, terminal: str, escape_parens: bool) -> str:
    return f"({label} {escape_terminal(terminal, escape_parens)})"


def normalize_tree_line(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    while " )" in s:
        s = s.replace(" )", ")")
    return s


def append_material(parts: List[str], material: str) -> None:
    if material and material != "_":
        parts.append(material)


def build_eojeol_tree_from_added(tokens: Sequence[WordToken], lhs: Sequence[str], rhs: Sequence[str], escape_parens: bool) -> str:
    parts: List[str] = []
    for tok, l, r in zip(tokens, lhs, rhs):
        append_material(parts, l)
        parts.append(make_preterminal(tok.upos, tok.form, escape_parens))
        append_material(parts, r)
    tree = normalize_tree_line(" ".join(parts))
    balance = tree.count("(") - tree.count(")")
    if balance != 0:
        raise ValueError(f"unbalanced eojeol output tree: balance={balance}; begins: {tree[:200]}")
    return tree


def make_added_block(original_block: Sequence[str], lhs: Sequence[str], rhs: Sequence[str]) -> List[str]:
    output: List[str] = []
    word_index = 0
    for line in original_block:
        if not is_word_line(line):
            output.append(line)
            continue
        cols = line.split("\t")
        cols.append(lhs[word_index])
        cols.append(rhs[word_index])
        output.append("\t".join(cols))
        word_index += 1
    return output


# ---------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------

def convert(
    conllu_path: str,
    predicted_morph_mrg_path: str,
    eojeol_output_path: str,
    added_output_path: Optional[str] = None,
    escape_parens: bool = True,
    warn_terminal_mismatch: bool = False,
    on_error: str = "raise",
) -> Tuple[int, int]:
    conllu_blocks = read_conllu_sentences(conllu_path)
    mrg_lines = read_mrg_lines(predicted_morph_mrg_path)

    if len(conllu_blocks) != len(mrg_lines):
        raise ValueError(
            f"sentence count mismatch: CoNLL-U has {len(conllu_blocks)} blocks, "
            f"predicted .mrg has {len(mrg_lines)} lines"
        )

    written = 0
    skipped = 0
    added_blocks: List[List[str]] = []

    with open(eojeol_output_path, "w", encoding="utf-8") as out_mrg:
        for sent_no, (block, mrg_line) in enumerate(zip(conllu_blocks, mrg_lines), start=1):
            sent_id = sent_id_from_block(block)
            sent_label = f"sentence {sent_no} / sent_id={sent_id}"
            try:
                word_tokens = parse_word_tokens(block, sent_no)
                groups, _pos_groups = expected_morpheme_groups(word_tokens)
                starts, boundaries, morph_to_eojeol = group_boundaries(groups)
                first_morphs = set(starts)

                toks = tokenize_tree(mrg_line)
                tree, pos = parse_tree(toks)
                if pos != len(toks):
                    raise ValueError(f"unparsed predicted tree tokens remain from position {pos}")

                leaves = collect_preterminal_leaves(tree)
                verify_morpheme_sequence(leaves, groups, sent_label, warn_terminal_mismatch)

                projected = project_tree_to_eojeol_sentinels(
                    tree,
                    boundary_set=boundaries,
                    morph_to_eojeol=morph_to_eojeol,
                    first_morph_of_eojeol=first_morphs,
                )
                lhs, rhs = extract_lhs_rhs_from_sentinel_tree(projected, len(word_tokens), sent_label)
                eojeol_tree = build_eojeol_tree_from_added(word_tokens, lhs, rhs, escape_parens)

            except Exception as e:
                if on_error == "raise":
                    raise ValueError(f"failed at {sent_label}: {e}") from e
                skipped += 1
                print(f"WARNING: skipping {sent_label}: {e}", file=sys.stderr)
                if added_output_path is not None:
                    added_blocks.append([
                        "## skipped_sentence = projection_error",
                        f"## {sent_label}",
                        f"## error = {e}",
                        "## original_dependency_conllu_begin",
                        *["## " + line for line in block],
                        "## original_dependency_conllu_end",
                        "## predicted_morpheme_tree_begin",
                        "## " + mrg_line,
                        "## predicted_morpheme_tree_end",
                    ])
                continue

            out_mrg.write(eojeol_tree + "\n")
            written += 1
            if added_output_path is not None:
                added_blocks.append(make_added_block(block, lhs, rhs))

    if added_output_path is not None:
        with open(added_output_path, "w", encoding="utf-8") as added_out:
            for block in added_blocks:
                for line in block:
                    added_out.write(line + "\n")
                added_out.write("\n")

    return written, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert predicted morpheme-based .mrg trees into eojeol-based .mrg "
            "trees using original CoNLL-U as the pivot. Optionally writes an "
            "intermediate added CoNLL-U file with predicted LHS/RHS columns."
        )
    )
    parser.add_argument("conllu", help="Original 10-column CoNLL-U file")
    parser.add_argument("predicted_morph_mrg", help="Predicted morpheme-based .mrg, one tree per line")
    parser.add_argument("-o", "--output", required=True, help="Output predicted eojeol-based .mrg")
    parser.add_argument("--added-output", help="Optional intermediate predicted added CoNLL-U output")
    parser.add_argument(
        "--no-escape-parens",
        action="store_true",
        help="Keep literal bracket terminals instead of Penn-style -LRB-/-RRB- escapes.",
    )
    parser.add_argument(
        "--warn-terminal-mismatch",
        action="store_true",
        help="Warn rather than fail if predicted morpheme terminal strings differ from CoNLL-U; counts must still match.",
    )
    parser.add_argument(
        "--on-error",
        choices=["raise", "skip"],
        default="raise",
        help="Whether to abort or skip/comment sentences with projection errors.",
    )
    args = parser.parse_args()

    written, skipped = convert(
        conllu_path=args.conllu,
        predicted_morph_mrg_path=args.predicted_morph_mrg,
        eojeol_output_path=args.output,
        added_output_path=args.added_output,
        escape_parens=not args.no_escape_parens,
        warn_terminal_mismatch=args.warn_terminal_mismatch,
        on_error=args.on_error,
    )
    print(
        f"Wrote {written} eojeol tree(s) to {args.output}; skipped {skipped} sentence(s).",
        file=sys.stderr,
    )
    if args.added_output:
        print(f"Wrote intermediate added CoNLL-U to {args.added_output}.", file=sys.stderr)


if __name__ == "__main__":
    main()
