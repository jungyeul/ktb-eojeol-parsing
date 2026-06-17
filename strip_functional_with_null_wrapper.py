#!/usr/bin/env python3
"""
strip_functional_with_null_wrapper.py

Self-contained wrapper for Penn-style one-line .mrg files.

It does three things in one pass:

  1. Converts null-element leaves to (-NONE- ...), e.g.
       (NP-SBJ *pro*)  ->  (-NONE- *pro*)
       (WHNP-1 *op*)   ->  (-NONE- *op*)
       (NP-SBJ *T*-1)  ->  (-NONE- *T*-1)

  2. Removes null elements, i.e. any leaf whose preterminal is -NONE-.

  3. Removes functional suffixes from constituent/preterminal labels:
       NP-SBJ -> NP
       WHNP-1 -> WHNP
       S-COMP -> S
       NP=2   -> NP

The implementation is independent; it does not import strip_functional.py.
It intentionally preserves symbols such as -LRB-, -RRB-, and -NONE-.

Usage:

  python strip_functional_with_null_wrapper.py input.mrg > output.clean.mrg

Optional examples:

  python strip_functional_with_null_wrapper.py --remove_root TOP input.mrg > output.mrg
  python strip_functional_with_null_wrapper.py --remove_symbols EDITED input.mrg > output.mrg
  python strip_functional_with_null_wrapper.py --keep_empty_lines input.mrg > output.mrg

By default, skipped/empty trees are not printed.
"""

from __future__ import annotations

import argparse
import fileinput
import re
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union

Child = Union["Tree", str]


# ----------------------------------------------------------------------
# Label normalization, modeled on the behavior of strip_functional.py
# ----------------------------------------------------------------------

def remove_symbol_functionals(symbol: str) -> str:
    """Remove functional suffixes from a tree label.

    Keeps Penn-style protected symbols such as -NONE-, -LRB-, -RRB-.
    Also respects labels containing morphological separators of the form ##...
    by stripping only the part before the first ##.
    """
    if len(symbol) >= 2 and symbol[0] == "-" and symbol[-1] == "-":
        return symbol

    morph_split = symbol.split("##")
    morph_split[0] = morph_split[0].split("-")[0]
    morph_split[0] = morph_split[0].split("=")[0]
    return "##".join(morph_split)


def _strip_tag_suffix(tag: str, sep: str = "#") -> str:
    """Strip a zpar-style suffix beginning with sep.

    Kept for compatibility with the original strip-functional utilities.
    """
    assert len(sep) == 1
    ix = tag.find(sep)
    return tag if ix < 0 else tag[:ix]


# ----------------------------------------------------------------------
# Null-element handling
# ----------------------------------------------------------------------

DEFAULT_NULL_TERMINALS = {
    "*",
    "0",
    "*pro*",
    "*PRO*",
    "*op*",
    "*OP*",
    "*T*",
    "*ICH*",
    "*EXP*",
    "*RNR*",
    "*PPA*",
    "*?*",
}

# Allows indexed variants such as *T*-1, *op*-2, *ICH*-3, *pro*-7.
INDEXED_NULL_RE = re.compile(
    r"^(?:\*|0|\*[A-Za-z?]+\*)(?:-\d+)?$"
)

# Tokens that look special but must not be treated as nulls.
NON_NULL_SPECIAL_TERMINALS = {
    "*PLUS*",
    "*STAR*",
}


def is_null_terminal(word: str, extra_nulls: Optional[set[str]] = None) -> bool:
    """Return True if a terminal should be converted to a -NONE- leaf."""
    if word in NON_NULL_SPECIAL_TERMINALS:
        return False
    if extra_nulls and word in extra_nulls:
        return True
    if word in DEFAULT_NULL_TERMINALS:
        return True
    if INDEXED_NULL_RE.match(word):
        return True
    return False


# ----------------------------------------------------------------------
# Tree representation and parser
# ----------------------------------------------------------------------

@dataclass
class Tree:
    symbol: str
    children: List[Child]

    def is_preterminal(self) -> bool:
        return len(self.children) == 1 and isinstance(self.children[0], str)

    def to_string(self) -> str:
        if not self.children:
            return f"({self.symbol})"
        return f"({self.symbol} {' '.join(child_to_string(c) for c in self.children)})"


def child_to_string(child: Child) -> str:
    return child.to_string() if isinstance(child, Tree) else child


def tokenize_tree(line: str) -> List[str]:
    """Tokenize PTB-style bracket notation into parentheses and atoms."""
    return re.findall(r"\(|\)|[^\s()]+", line)


def parse_tree(line: str) -> Tree:
    tokens = tokenize_tree(line)
    if not tokens:
        raise ValueError("empty input line")

    tree, index = _parse_tree_tokens(tokens, 0)
    if index != len(tokens):
        suffix = " ".join(tokens[index:index + 20])
        raise ValueError(f"unparsed suffix begins with: {suffix}")
    return tree


def _parse_tree_tokens(tokens: Sequence[str], index: int) -> Tuple[Tree, int]:
    if index >= len(tokens) or tokens[index] != "(":
        got = tokens[index] if index < len(tokens) else "<EOF>"
        raise ValueError(f"expected '(' at token {index}, got {got!r}")
    index += 1

    if index >= len(tokens):
        raise ValueError("missing node label after '('")
    symbol = tokens[index]
    index += 1

    children: List[Child] = []
    while index < len(tokens) and tokens[index] != ")":
        if tokens[index] == "(":
            child, index = _parse_tree_tokens(tokens, index)
            children.append(child)
        else:
            children.append(tokens[index])
            index += 1

    if index >= len(tokens) or tokens[index] != ")":
        raise ValueError(f"missing ')' for node {symbol!r}")
    index += 1
    return Tree(symbol, children), index


# ----------------------------------------------------------------------
# Tree transformations
# ----------------------------------------------------------------------

def convert_null_preterminals(tree: Tree, extra_nulls: Optional[set[str]] = None) -> Tree:
    """Rewrite preterminal labels to -NONE- when their terminal is null-like."""
    if tree.is_preterminal():
        word = tree.children[0]
        assert isinstance(word, str)
        if is_null_terminal(word, extra_nulls=extra_nulls):
            return Tree("-NONE-", [word])
        return Tree(tree.symbol, [word])

    return Tree(
        tree.symbol,
        [convert_null_preterminals(c, extra_nulls) if isinstance(c, Tree) else c for c in tree.children],
    )


def strip_functionals_and_remove_nulls(tree: Tree) -> Optional[Tree]:
    """Remove -NONE- leaves and strip functional labels.

    Empty ancestors are removed. If the whole tree becomes empty, returns None.
    """
    if tree.symbol == "-NONE-":
        return None

    if tree.is_preterminal():
        word = tree.children[0]
        assert isinstance(word, str)
        return Tree(remove_symbol_functionals(tree.symbol), [word])

    new_children: List[Child] = []
    for child in tree.children:
        if isinstance(child, Tree):
            new_child = strip_functionals_and_remove_nulls(child)
            if new_child is not None:
                new_children.append(new_child)
        else:
            # Direct terminal under a phrase node. This is unusual in strict PTB,
            # but the KTB conversion may create nodes such as (NP-SBJ *pro*).
            # If it survived convert_null_preterminals, keep it.
            new_children.append(child)

    if not new_children:
        return None
    return Tree(remove_symbol_functionals(tree.symbol), new_children)


def remove_nodes(tree: Tree, symbols: set[str]) -> List[Tree]:
    """Remove any node whose symbol is in symbols, splicing its children upward."""
    processed_children: List[Child] = []
    for child in tree.children:
        if isinstance(child, Tree):
            processed_children.extend(remove_nodes(child, symbols))
        else:
            processed_children.append(child)

    if tree.symbol in symbols:
        return [c for c in processed_children if isinstance(c, Tree)]
    return [Tree(tree.symbol, processed_children)]


def dedup_punct(tree: Tree, symbols: set[str]) -> Tree:
    """Collapse repeated punctuation terminals for selected preterminal labels.

    Example: (PU ......) -> (PU .), if PU is in symbols.
    """
    if tree.is_preterminal():
        word = tree.children[0]
        assert isinstance(word, str)
        if tree.symbol in symbols and len(word) > 1 and all(ch == word[0] for ch in word[1:]):
            return Tree(tree.symbol, [word[0]])
        return tree

    return Tree(
        tree.symbol,
        [dedup_punct(c, symbols) if isinstance(c, Tree) else c for c in tree.children],
    )


def process_tree(
    tree: Tree,
    *,
    remove_root: Optional[str] = None,
    remove_root_must_have: Optional[str] = None,
    root_removed_replacement: Optional[str] = None,
    remove_symbols_arg: Optional[set[str]] = None,
    dedup_punct_symbols: Optional[set[str]] = None,
    extra_nulls: Optional[set[str]] = None,
) -> List[Tree]:
    """Apply null conversion, null removal, functional stripping, and options."""
    tree = convert_null_preterminals(tree, extra_nulls=extra_nulls)
    stripped = strip_functionals_and_remove_nulls(tree)
    if stripped is None:
        return []

    if remove_root is not None or remove_root_must_have is not None:
        if remove_root is not None and remove_root_must_have is not None:
            raise ValueError("use only one of --remove_root and --remove_root_must_have")
        symbol_to_remove = remove_root_must_have if remove_root_must_have is not None else remove_root
        assert symbol_to_remove is not None
        if remove_root_must_have is not None and stripped.symbol != symbol_to_remove:
            raise ValueError(
                f"root is {stripped.symbol!r}, expected {remove_root_must_have!r}"
            )
        if stripped.symbol == symbol_to_remove:
            trees = [c for c in stripped.children if isinstance(c, Tree)]
        else:
            trees = [stripped]
    else:
        trees = [stripped]

    if remove_symbols_arg:
        trees = [t2 for t in trees for t2 in remove_nodes(t, remove_symbols_arg)]

    if len(trees) > 1:
        if root_removed_replacement:
            trees = [Tree(root_removed_replacement, list(trees))]
        else:
            raise ValueError(
                "root removal produced multiple trees; pass --root_removed_replacement LABEL"
            )

    if dedup_punct_symbols:
        trees = [dedup_punct(t, dedup_punct_symbols) for t in trees]

    return trees


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert null leaves to -NONE-, remove them, and strip functional "
            "labels from one-line .mrg trees."
        )
    )
    parser.add_argument(
        "--remove_symbols",
        nargs="*",
        help="remove these nonterminal symbols from anywhere in the tree",
    )
    parser.add_argument(
        "--remove_root",
        help="remove this symbol from the root if it is present",
    )
    parser.add_argument(
        "--remove_root_must_have",
        help="remove this root symbol and fail if the root is different",
    )
    parser.add_argument(
        "--root_removed_replacement",
        help="wrap multiple root-removed children in this replacement root",
    )
    parser.add_argument(
        "--dedup_punct_symbols",
        nargs="*",
        help="for these preterminal labels, collapse repeated-char punctuation terminals",
    )
    parser.add_argument(
        "--extra_null_terminal",
        action="append",
        default=[],
        help="additional terminal string to treat as null; may be repeated",
    )
    parser.add_argument(
        "--keep_empty_lines",
        action="store_true",
        help="print a blank line when a tree becomes empty after null removal",
    )
    parser.add_argument(
        "files",
        metavar="FILE",
        nargs="*",
        help="files to read; stdin is used if omitted",
    )
    args = parser.parse_args(argv)

    remove_symbols_arg = set(args.remove_symbols) if args.remove_symbols else set()
    dedup_symbols = set(args.dedup_punct_symbols) if args.dedup_punct_symbols else set()
    extra_nulls = set(args.extra_null_terminal) if args.extra_null_terminal else set()

    ok = True
    input_files: Iterable[str] = args.files if args.files else ("-",)

    for line_no, line in enumerate(fileinput.input(files=input_files), start=1):
        line = line.strip()
        if not line:
            if args.keep_empty_lines:
                print()
            continue

        try:
            tree = parse_tree(line)
            trees = process_tree(
                tree,
                remove_root=args.remove_root,
                remove_root_must_have=args.remove_root_must_have,
                root_removed_replacement=args.root_removed_replacement,
                remove_symbols_arg=remove_symbols_arg,
                dedup_punct_symbols=dedup_symbols,
                extra_nulls=extra_nulls,
            )
        except Exception as exc:  # keep line/file diagnostics useful for batch runs
            filename = fileinput.filename()
            print(
                f"ERROR: failed at {filename}:{line_no}: {exc}",
                file=sys.stderr,
            )
            ok = False
            continue

        if not trees:
            if args.keep_empty_lines:
                print()
            continue

        for t in trees:
            print(t.to_string())

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
