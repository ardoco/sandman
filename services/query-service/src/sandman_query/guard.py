"""Cypher scope guard (ADR-0009 amendment, D-2): parser for the allowlisted read subset plus project anchoring.

A small recursive-descent parser for exactly the allowlisted subset (RK-19 fallback, docs/notes/cypher-guard.md):
anything it does not understand is rejected. The statement sent to Memgraph is rebuilt from the parsed tokens
(comments removed), so the service and the database see the same tokens.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

MAX_LENGTH = 20000
MAX_DEPTH = 12
PARAM = "projectId"

# words that never name a variable; the first group are clauses/statements that are rejected by name
DENIED = {
    "CALL", "CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "FOREACH", "LOAD", "SHOW", "DUMP", "TERMINATE",
    "EXPLAIN", "PROFILE", "USING", "DROP", "START", "STOP", "USE", "FREE", "GRANT", "REVOKE", "DENY", "ALTER",
    "RECOVER", "ANALYZE", "LOCK", "UNLOCK", "COMMIT", "ROLLBACK", "BEGIN", "QUERY",
}  # fmt: skip
RESERVED = DENIED | {
    "MATCH", "OPTIONAL", "WHERE", "WITH", "UNWIND", "RETURN", "ORDER", "BY", "SKIP", "LIMIT", "UNION", "ALL",
    "DISTINCT", "AS", "AND", "OR", "XOR", "NOT", "IN", "IS", "NULL", "TRUE", "FALSE", "CASE", "WHEN", "THEN",
    "ELSE", "END", "STARTS", "ENDS", "CONTAINS", "ASC", "ASCENDING", "DESC", "DESCENDING", "EXISTS",
}  # fmt: skip
ESCAPES = set("\\'\"bBfFnNrRtT")
SYMBOLS2 = {"<=", ">=", "<>", "!=", "=~", ".."}
SYMBOLS1 = set("()[]{},:;.|+-*/%^=<>")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER = re.compile(r"0[xX][0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
PARAM_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*|[0-9]+)")


class GuardError(Exception):
    """The query is not accepted; the message is shown to the caller."""


@dataclass(frozen=True)
class Tok:
    kind: str  # name | qname | str | num | param | sym | eof
    text: str  # name: as written; qname/str: content; param: name; sym: the symbol
    pos: int
    end: int


@dataclass(frozen=True)
class Checked:
    text: str  # statement to execute, without comments and trailing semicolon
    has_limit: bool  # the final RETURN has its own LIMIT


def tokenize(src: str) -> list[Tok]:
    out: list[Tok] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in " \t\n\r\f":
            i += 1
        elif src.startswith("//", i):
            ends = [j for j in (src.find("\n", i), src.find("\r", i)) if j >= 0]
            i = min(ends) if ends else n
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise GuardError("syntax error: unterminated comment")
            i = j + 2
        elif c.isascii() and (c.isalpha() or c == "_"):
            m = IDENT.match(src, i)
            assert m
            out.append(Tok("name", m.group(), i, m.end()))
            i = m.end()
        elif c in "0123456789":
            m = NUMBER.match(src, i)
            assert m
            out.append(Tok("num", m.group(), i, m.end()))
            i = m.end()
        elif c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    e = src[j + 1 : j + 2]
                    width = 4 if e == "u" else 8 if e == "U" else 0
                    if e not in ESCAPES and not width:
                        raise GuardError(f"syntax error: invalid escape at {j}")
                    if width and not re.fullmatch(f"[0-9a-fA-F]{{{width}}}", src[j + 2 : j + 2 + width]):
                        raise GuardError(f"syntax error: invalid unicode escape at {j}")
                    j += 2 + width
                else:
                    j += 1
            if j >= n:
                raise GuardError("syntax error: unterminated string")
            out.append(Tok("str", src[i + 1 : j], i, j + 1))
            i = j + 1
        elif c == "`":
            j = i + 1
            while True:
                j = src.find("`", j)
                if j < 0:
                    raise GuardError("syntax error: unterminated quoted name")
                if src[j + 1 : j + 2] == "`":
                    j += 2
                    continue
                break
            out.append(Tok("qname", src[i + 1 : j].replace("``", "`"), i, j + 1))
            i = j + 1
        elif c == "$":
            m = PARAM_RE.match(src, i)
            if not m:
                raise GuardError(f"syntax error: invalid parameter at {i}")
            out.append(Tok("param", m.group(1), i, m.end()))
            i = m.end()
        elif src[i : i + 2] in SYMBOLS2:
            out.append(Tok("sym", src[i : i + 2], i, i + 2))
            i += 2
        elif c in SYMBOLS1:
            out.append(Tok("sym", c, i, i + 1))
            i += 1
        else:
            raise GuardError(f"syntax error: unexpected character {c!r} at {i}")
    out.append(Tok("eof", "", n, n))
    return out


@dataclass
class Node:
    var: str | None
    anchored: bool


@dataclass
class Part:
    nodes: list[Node]
    rel_vars: list[str]
    rels: int


class Parser:
    def __init__(self, src: str, scoped: bool) -> None:
        if len(src) > MAX_LENGTH:
            raise GuardError(f"query longer than {MAX_LENGTH} characters")
        self.src, self.scoped = src, scoped
        self.toks = tokenize(src)
        self.i = 0
        self.scope: set[str] = set()  # variables bound by anchored patterns
        self.depth = 0

    # ------------------------------------------------------------ token helpers

    @property
    def tok(self) -> Tok:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Tok:
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def fail(self, what: str) -> GuardError:
        t = self.tok
        return GuardError(f"syntax error at {t.pos}: expected {what}, found {src_text(t) or 'end of query'}")

    def is_sym(self, s: str, k: int = 0) -> bool:
        t = self.peek(k) if k else self.tok
        return t.kind == "sym" and t.text == s

    def accept_sym(self, s: str) -> bool:
        if self.is_sym(s):
            self.i += 1
            return True
        return False

    def expect_sym(self, s: str) -> None:
        if not self.accept_sym(s):
            raise self.fail(repr(s))

    def is_kw(self, word: str, k: int = 0) -> bool:
        t = self.peek(k) if k else self.tok
        return t.kind == "name" and t.text.upper() == word

    def accept_kw(self, *words: str) -> bool:
        if all(self.is_kw(w, k) for k, w in enumerate(words)):
            self.i += len(words)
            return True
        return False

    def expect_kw(self, *words: str) -> None:
        if not self.accept_kw(*words):
            raise self.fail(" ".join(words))

    def name_any(self) -> str:
        """Property key, label or relationship type: any word."""
        t = self.tok
        if t.kind not in ("name", "qname"):
            raise self.fail("a name")
        self.i += 1
        return t.text

    def is_var(self, k: int = 0) -> bool:
        t = self.peek(k) if k else self.tok
        return t.kind == "qname" or (t.kind == "name" and t.text.upper() not in RESERVED)

    def var(self) -> str:
        if not self.is_var():
            raise self.fail("a variable")
        return self.name_any()

    @contextmanager
    def shadow(self, *names: str) -> Iterator[None]:
        """Variables of a comprehension hide outer ones; patterns see the scope as it was afterwards restored."""
        saved = set(self.scope)
        self.scope -= set(names)
        try:
            yield
        finally:
            self.scope = saved

    @contextmanager
    def nested(self) -> Iterator[None]:
        """Copy of the scope for subqueries and comprehensions."""
        saved = set(self.scope)
        try:
            yield
        finally:
            self.scope = saved

    # ------------------------------------------------------------------- query

    def query(self) -> Checked:
        has_limit = self.single_query(top=True)
        while self.accept_kw("UNION"):
            self.accept_kw("ALL")
            has_limit = self.single_query(top=True)
        self.accept_sym(";")
        if self.tok.kind != "eof":
            if self.is_kw("QUERY"):
                raise GuardError("QUERY MEMORY LIMIT is set by the service")
            raise self.fail("end of query")
        return Checked(self.rebuild(), has_limit)

    def rebuild(self) -> str:
        """Tokens of the statement; comments become one space, adjacency of tokens is kept."""
        toks = [t for t in self.toks[:-1]]
        if toks and toks[-1].text == ";" and toks[-1].kind == "sym":
            toks.pop()
        out, prev = [], 0
        for t in toks:
            if out and t.pos > prev:
                out.append(" ")
            out.append(self.src[t.pos : t.end])
            prev = t.end
        return "".join(out)

    def single_query(self, top: bool) -> bool:
        """Clauses up to and including RETURN (subqueries may end at '}' instead); True if RETURN has LIMIT."""
        self.scope = set() if top else self.scope
        clauses = 0
        while True:
            if self.is_kw("OPTIONAL") or self.is_kw("MATCH"):
                self.match_clause()
            elif self.is_kw("WITH"):
                self.i += 1
                self.projection(with_clause=True)
            elif self.is_kw("UNWIND"):
                self.i += 1
                self.expression()
                self.expect_kw("AS")
                self.scope.discard(self.var())
            elif self.is_kw("RETURN"):
                self.i += 1
                return self.projection(with_clause=False)
            elif not top and clauses and self.is_sym("}"):
                return False
            elif self.tok.kind == "name" and self.tok.text.upper() in DENIED:
                raise GuardError(f"{self.tok.text.upper()} is not allowed: only read queries are accepted")
            else:
                raise self.fail("MATCH, OPTIONAL MATCH, WITH, UNWIND or RETURN")
            clauses += 1

    def match_clause(self) -> None:
        self.accept_kw("OPTIONAL")
        self.expect_kw("MATCH")
        self.patterns()
        if self.accept_kw("WHERE"):
            self.expression()

    def projection(self, with_clause: bool) -> bool:
        """Items, ORDER BY, SKIP, LIMIT (and WHERE for WITH); the scope becomes the projected anchored names."""
        self.accept_kw("DISTINCT")
        projected: set[str] = set()
        while True:
            if self.accept_sym("*"):
                projected |= self.scope
            else:
                start = self.i
                self.expression()
                bare = self.tok_is_var(start) and self.i == start + 1
                name = self.toks[start].text if bare else None
                if self.accept_kw("AS"):
                    alias = self.var()
                    if name in self.scope:
                        projected.add(alias)
                elif name in self.scope and name is not None:
                    projected.add(name)
            if not self.accept_sym(","):
                break
        if with_clause:
            self.scope = projected
        if self.accept_kw("ORDER", "BY"):
            while True:
                self.expression()
                any(self.accept_kw(w) for w in ("ASC", "ASCENDING", "DESC", "DESCENDING"))
                if not self.accept_sym(","):
                    break
        if self.accept_kw("SKIP"):
            self.expression()
        has_limit = self.accept_kw("LIMIT")
        if has_limit:
            self.expression()
        if with_clause and self.accept_kw("WHERE"):
            self.expression()
        return has_limit

    def tok_is_var(self, index: int) -> bool:
        t = self.toks[index]
        return t.kind == "qname" or (t.kind == "name" and t.text.upper() not in RESERVED)

    # ---------------------------------------------------------------- patterns

    def patterns(self) -> None:
        """Comma-separated pattern parts of a MATCH (or EXISTS) with the anchoring check."""
        parts = [self.pattern_part()]
        while self.accept_sym(","):
            parts.append(self.pattern_part())
        self.anchor(parts)

    def anchor(self, parts: list[Part]) -> None:
        """Every connected component needs an anchored node or a variable bound by an anchored pattern."""
        groups: list[tuple[set[str], bool]] = []
        for part in parts:
            names = {n.var for n in part.nodes if n.var} | set(part.rel_vars)
            anchored = any(n.anchored for n in part.nodes) or bool(names & self.scope)
            for g in [g for g in groups if g[0] & names]:
                groups.remove(g)
                names, anchored = names | g[0], anchored or g[1]
            groups.append((names, anchored))
        if self.scoped and not all(a for _, a in groups):
            raise GuardError(
                "pattern is not anchored to the project: use {projectId: $projectId}, (:Project {id: $projectId}) "
                "or a variable bound by an anchored pattern in every MATCH, comprehension and subquery"
            )
        for names, _ in groups:
            self.scope |= names

    def pattern_part(self) -> Part:
        if self.is_var() and self.is_sym("=", 1):
            self.i += 2  # path variable
        nodes = [self.node()]
        rel_vars: list[str] = []
        rels = 0
        while self.is_sym("-") or self.is_sym("<"):
            var = self.relationship()
            rels += 1
            if var:
                rel_vars.append(var)
            nodes.append(self.node())
        return Part(nodes, rel_vars, rels)

    def node(self) -> Node:
        self.expect_sym("(")
        var = self.var() if self.is_var() else None
        labels: list[str] = []
        while self.accept_sym(":"):
            labels.append(self.name_any())
        anchored = False
        if self.is_sym("{"):
            keys = self.map_literal()
            anchored = keys.get(PARAM) == "param:" + PARAM or (
                "Project" in labels and keys.get("id") == "param:" + PARAM
            )
        self.expect_sym(")")
        return Node(var, anchored)

    def relationship(self) -> str | None:
        self.accept_sym("<")
        self.expect_sym("-")
        var = None
        if self.accept_sym("["):
            if self.is_var():
                var = self.var()
            if self.accept_sym(":"):
                self.name_any()
                while self.accept_sym("|"):
                    self.accept_sym(":")
                    self.name_any()
            if self.accept_sym("*"):
                if self.tok.kind == "num":
                    self.i += 1
                if self.accept_sym(".."):
                    if self.tok.kind == "num":
                        self.i += 1
            if self.is_sym("{"):
                self.map_literal()
            self.expect_sym("]")
        self.expect_sym("-")
        self.accept_sym(">")
        return var

    def map_literal(self) -> dict[str, str]:
        """{key: expr, ...}; maps key -> 'param:<name>' for a bare parameter value, else '' (duplicates rejected)."""
        self.expect_sym("{")
        keys: dict[str, str] = {}
        if not self.accept_sym("}"):
            while True:
                key = self.name_any()
                if key in keys:
                    raise GuardError(f"duplicate map key {key}")
                self.expect_sym(":")
                start = self.i
                self.expression()
                t = self.toks[start]
                keys[key] = "param:" + t.text if t.kind == "param" and self.i == start + 1 else ""
                if not self.accept_sym(","):
                    break
            self.expect_sym("}")
        return keys

    def try_pattern(self) -> Part | None:
        """A pattern with at least one relationship at the current position, else None (position restored)."""
        start, scope = self.i, set(self.scope)
        try:
            part = self.pattern_part()
        except GuardError:
            part = None
        if part is None or part.rels == 0:
            self.i, self.scope = start, scope
            return None
        return part

    # -------------------------------------------------------------- expressions

    def expression(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise GuardError("expression nested too deeply")
        try:
            self.or_expr()
        finally:
            self.depth -= 1

    def or_expr(self) -> None:
        self.and_expr()
        while self.accept_kw("OR") or self.accept_kw("XOR"):
            self.and_expr()

    def and_expr(self) -> None:
        self.not_expr()
        while self.accept_kw("AND"):
            self.not_expr()

    def not_expr(self) -> None:
        while self.accept_kw("NOT"):
            pass
        self.comparison()

    def comparison(self) -> None:
        self.additive()
        while True:
            t = self.tok
            if t.kind == "sym" and t.text in {"=", "<>", "!=", "<", ">", "<=", ">=", "=~"}:
                self.i += 1
                self.additive()
            elif self.accept_kw("IS"):
                self.accept_kw("NOT")
                self.expect_kw("NULL")
            elif self.accept_kw("IN") or self.accept_kw("CONTAINS"):
                self.additive()
            elif self.accept_kw("STARTS", "WITH") or self.accept_kw("ENDS", "WITH"):
                self.additive()
            else:
                return

    def additive(self) -> None:
        self.multiplicative()
        while self.is_sym("+") or self.is_sym("-"):
            self.i += 1
            self.multiplicative()

    def multiplicative(self) -> None:
        self.power()
        while self.is_sym("*") or self.is_sym("/") or self.is_sym("%"):
            self.i += 1
            self.power()

    def power(self) -> None:
        self.unary()
        while self.accept_sym("^"):
            self.unary()

    def unary(self) -> None:
        while self.is_sym("+") or self.is_sym("-"):
            self.i += 1
        self.postfix()

    def postfix(self) -> None:
        self.atom()
        while True:
            if self.accept_sym("."):
                self.name_any()
            elif self.accept_sym("["):
                if not self.is_sym(".."):
                    self.expression()
                if self.accept_sym(".."):
                    if not self.is_sym("]"):
                        self.expression()
                self.expect_sym("]")
            elif self.is_sym(":") and self.peek().kind in ("name", "qname"):
                self.i += 2  # label predicate n:Label
            else:
                return

    def atom(self) -> None:
        t = self.tok
        if t.kind in ("num", "str", "param"):
            self.i += 1
        elif t.kind == "qname":
            self.i += 1
        elif t.kind == "sym" and t.text == "[":
            self.list_or_comprehension()
        elif t.kind == "sym" and t.text == "{":
            self.map_literal()
        elif t.kind == "sym" and t.text == "(":
            part = self.try_pattern()
            if part is not None:  # pattern predicate: anchored like any other pattern, binds nothing outside
                with self.nested():
                    self.anchor([part])
            else:
                self.i += 1
                self.expression()
                self.expect_sym(")")
        elif t.kind == "name":
            self.word(t.text.upper())
        else:
            raise self.fail("an expression")

    def word(self, upper: str) -> None:
        nxt_paren = self.is_sym("(", 1)
        if upper in ("TRUE", "FALSE", "NULL"):
            self.i += 1
        elif upper == "CASE":
            self.i += 1
            if not self.is_kw("WHEN"):
                self.expression()
            while self.accept_kw("WHEN"):
                self.expression()
                self.expect_kw("THEN")
                self.expression()
            if self.accept_kw("ELSE"):
                self.expression()
            self.expect_kw("END")
        elif upper == "EXISTS" and self.is_sym("{", 1):
            self.i += 2
            self.exists_subquery()
        elif upper in ("ALL", "ANY", "NONE", "SINGLE") and nxt_paren:
            self.i += 2
            name = self.var()
            self.expect_kw("IN")
            self.expression()
            if self.accept_kw("WHERE"):
                with self.shadow(name):
                    self.expression()
            self.expect_sym(")")
        elif upper == "REDUCE" and nxt_paren:
            self.i += 2
            acc = self.var()
            self.expect_sym("=")
            self.expression()
            self.expect_sym(",")
            name = self.var()
            self.expect_kw("IN")
            self.expression()
            self.expect_sym("|")
            with self.shadow(acc, name):
                self.expression()
            self.expect_sym(")")
        elif nxt_paren and (upper not in RESERVED or upper == "EXISTS"):
            self.i += 2
            if not self.accept_sym(")"):
                if not self.accept_sym("*"):
                    self.accept_kw("DISTINCT")
                    self.expression()
                    while self.accept_sym(","):
                        self.expression()
                self.expect_sym(")")
        elif self.is_var():
            self.i += 1
        else:
            raise self.fail("an expression")

    def exists_subquery(self) -> None:
        """EXISTS { MATCH ... [RETURN ...] } or EXISTS { pattern [WHERE ...] } (the '{' is consumed)."""
        with self.nested():
            if any(self.is_kw(w) for w in ("MATCH", "OPTIONAL", "WITH", "UNWIND", "RETURN")):
                self.single_query(top=False)
            else:
                self.patterns()
                if self.accept_kw("WHERE"):
                    self.expression()
            self.expect_sym("}")

    def list_or_comprehension(self) -> None:
        self.expect_sym("[")
        if self.is_var() and self.is_kw("IN", 1):  # [x IN list WHERE cond | expr]
            name = self.var()
            self.i += 1
            self.expression()
            with self.shadow(name):
                if self.accept_kw("WHERE"):
                    self.expression()
                if self.accept_sym("|"):
                    self.expression()
            self.expect_sym("]")
            return
        if self.is_sym("(") or (self.is_var() and self.is_sym("=", 1)):
            start, scope = self.i, set(self.scope)
            part = self.try_pattern()
            if part is not None and (self.is_kw("WHERE") or self.is_sym("|")):
                with self.nested():
                    self.anchor([part])
                    if self.accept_kw("WHERE"):
                        self.expression()
                    self.expect_sym("|")
                    self.expression()
                self.expect_sym("]")
                return
            self.i, self.scope = start, scope
        if not self.accept_sym("]"):
            self.expression()
            while self.accept_sym(","):
                self.expression()
            self.expect_sym("]")


def src_text(t: Tok) -> str:
    return t.text if t.kind != "eof" else ""


def check(cypher: str, scoped: bool = True) -> Checked:
    """Parse and check a query; scoped=False (global admin without project) skips only the anchoring rule."""
    try:
        return Parser(cypher, scoped).query()
    except RecursionError:
        raise GuardError("query nested too deeply") from None
