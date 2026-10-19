"""Parse a pasted folder design into a tree. Pure functions: no database.

Two formats are accepted and detected automatically.

1. Indented outline — one folder per line, children indented (a tab, or 2+ spaces, per level).
   A line with "Title | ref" is an item placed in the folder above it.

       Sales
         Daily
           TD-MTD sales | report:RXL_TDMTD
           Store dashboard | https://example/x
       Stock
         Ageing | portal:report-portal

2. Excel paste — tab-separated columns Level1, Level2, ...; each row is a full path. Blank
   leading cells repeat the row above (how a merged-looking sheet pastes). The last cell may
   be an item ("Title | ref").

       Sales<TAB>Daily<TAB>TD-MTD sales | report:RXL_TDMTD
       <TAB><TAB>Store dashboard | https://example/x

ref = report:<code> | portal:<code> | master:<code> | http(s)://url
"""
import re
from dataclasses import dataclass, field

BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
REF = re.compile(r"^(report|portal|master):([A-Za-z0-9_.:-]+)$|^(https?://\S+)$")
MAX_DEPTH = 12
MAX_NAME = 120


@dataclass
class Node:
    line: int
    type: str                       # folder | item
    name: str
    ref_kind: str | None = None     # report | portal | master | link
    ref: str | None = None
    error: str | None = None
    children: list = field(default_factory=list)


def _item(line_no: int, text: str) -> Node:
    title, _, ref = text.partition("|")
    title, ref = title.strip(), ref.strip()
    n = Node(line_no, "item", title or ref)
    m = REF.match(ref)
    if not ref:
        n.error = "Missing link after '|'."
    elif not m:
        n.error = "Link must be report:<code>, portal:<code>, master:<code> or an http(s) address."
    elif m.group(3):
        n.ref_kind, n.ref = "link", m.group(3)
    else:
        n.ref_kind, n.ref = m.group(1), m.group(2)
    if not title:
        n.error = n.error or "Item needs a title before '|'."
    if len(n.name) > MAX_NAME:
        n.error = "Title is longer than %d characters." % MAX_NAME
    return n


def _folder(line_no: int, name: str) -> Node:
    n = Node(line_no, "folder", name)
    if len(name) > MAX_NAME:
        n.error = "Folder name is longer than %d characters." % MAX_NAME
    return n


def is_excel(lines: list[str]) -> bool:
    """Excel mode when some row has text, a tab, then more text."""
    return any("\t" in ln.strip("\t\r\n ") for ln in lines)


def parse(text: str) -> list[Node]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if is_excel(lines):
        return _parse_excel(lines)
    return _parse_outline(lines)


def _parse_outline(lines: list[str]) -> list[Node]:
    raw = []
    for i, ln in enumerate(lines, 1):
        if not ln.strip() or ln.strip().startswith("#"):
            continue
        lead = ln[: len(ln) - len(ln.lstrip(" \t"))]
        raw.append((i, lead, BULLET.sub("", ln.strip())))
    space_widths = sorted({len(l) for _, l, _ in raw if l and "\t" not in l})
    unit = space_widths[0] if space_widths else 2
    roots: list[Node] = []
    stack: list[tuple[int, Node]] = []  # (depth, folder)
    for i, lead, body in raw:
        depth = lead.count("\t") + (len(lead.replace("\t", "")) // unit if unit else 0)
        node = _item(i, body) if "|" in body else _folder(i, body)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent_depth = stack[-1][0] if stack else -1
        if depth > parent_depth + 1:
            node.error = node.error or "Indented more than one level below the line above."
        if depth >= MAX_DEPTH:
            node.error = node.error or "Deeper than %d levels." % MAX_DEPTH
        (stack[-1][1].children if stack else roots).append(node)
        if node.type == "folder":
            stack.append((depth, node))
    return roots


def _parse_excel(lines: list[str]) -> list[Node]:
    roots: list[Node] = []
    prev: list[str] = []
    index: dict[tuple, Node] = {}
    for i, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        cells = [c.strip() for c in ln.rstrip("\n").split("\t")]
        while cells and not cells[-1]:
            cells.pop()
        # blank leading cells repeat the row above
        filled = [c if c else (prev[k] if k < len(prev) else "") for k, c in enumerate(cells)]
        if any(not c for c in filled):
            node = Node(i, "folder", " / ".join(c for c in cells if c), error="A blank cell inside the path with no value above it.")
            roots.append(node)
            continue
        item_cell = filled[-1] if "|" in filled[-1] else None
        path = filled[:-1] if item_cell else filled
        if len(path) > MAX_DEPTH:
            roots.append(Node(i, "folder", " / ".join(path), error="Deeper than %d levels." % MAX_DEPTH))
            continue
        parent_children, key = roots, ()
        for name in path:
            key = key + (name.lower(),)
            f = index.get(key)
            if f is None:
                f = _folder(i, name)
                index[key] = f
                parent_children.append(f)
            parent_children = f.children
        if item_cell:
            it = _item(i, item_cell)
            if not path:
                it.error = it.error or "An item needs at least one folder column before it."
            parent_children.append(it)
        prev = path
    return roots


def walk(nodes: list[Node], depth: int = 0):
    for n in nodes:
        yield depth, n
        yield from walk(n.children, depth + 1)


def merge_duplicates(nodes: list[Node]) -> list[Node]:
    """Outline mode can repeat a folder name among siblings; merge them so the result matches the DB rule."""
    out: list[Node] = []
    seen: dict[str, Node] = {}
    for n in nodes:
        if n.type == "folder" and not n.error:
            k = n.name.lower()
            if k in seen:
                seen[k].children.extend(n.children)
                continue
            seen[k] = n
        out.append(n)
    for n in out:
        n.children = merge_duplicates(n.children)
    return out
