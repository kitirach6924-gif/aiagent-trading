"""Static guard against undefined free variables (NameError waiting to happen).

THE BUG CLASS
    Two separate incidents in app/core/loop.py shipped to the VPS because a
    name was read as a free variable that never existed in any enclosing scope:

      * `_execute(... entry_features=entry_features)` — the caller `_v1_cycle`
        built the dict and even passed it INTO `_act_on_decision`, but that
        function's signature never accepted it. Every BUY/SELL that cleared the
        risk gate raised NameError after passing every check.
      * `_execute(... detail)` — same shape, on the ORDER_SENT journal line, so
        it raised only AFTER the broker had already filled the order.

    Python does not catch these at import or compile time. Both survived review
    and reached a running container, because the failing path required a live
    stochastic turn to reach.

WHY A STATIC TEST
    A runtime test only covers the path it happens to walk. This walks every
    function in app/ and asserts each name it reads resolves to a parameter, a
    local, an enclosing function's binding (a closure), a module global, or a
    builtin. That catches the whole class, including the paths no test touches.

    Nested-function handling matters: most "undefined" hits in a naive scan are
    legitimate closures, so each nested function is checked with its parent's
    bindings visible.
"""
import ast
import builtins
import os

BUILTINS = set(dir(builtins))

# Names bound at module scope by the interpreter, not by an assignment we can see.
IMPLICIT_GLOBALS = {"__file__", "__name__", "__doc__", "__package__", "__spec__"}

APP_ROOT = os.path.join(os.path.dirname(__file__), "..", "app")


def _module_bindings(tree: ast.Module) -> set[str]:
    """Every name bound at MODULE scope.

    Deliberately does not descend into function bodies: a local assignment
    inside `_v1_cycle` does not make the name visible to `_act_on_decision`,
    and treating it as module-level is precisely what let the original
    entry_features NameError slip past this check.
    """
    names: set[str] = set()

    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if isinstance(node, ast.ClassDef):
                stack.extend(node.body)   # class body executes in module scope
            continue                        # function bodies do NOT
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        else:
            stack.extend(ast.iter_child_nodes(node))
    return names


def _function_bindings(fn) -> set[str]:
    """Names bound inside this function, excluding nested function bodies.

    Nested bodies are skipped because their bindings are local to them, and
    their *reads* of the parent's names are legal closures.
    """
    args = fn.args
    names = {a.arg for a in (args.args + args.posonlyargs + args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)

    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue  # do not descend: their internals are their own scope
        if isinstance(node, ast.Lambda):
            for a in (node.args.args + node.args.posonlyargs + node.args.kwonlyargs):
                names.add(a.arg)
            if node.args.vararg:
                names.add(node.args.vararg.arg)
            if node.args.kwarg:
                names.add(node.args.kwarg.arg)
            continue  # anonymous: binds no name of its own
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # Imports inside a function (or inside try/except at module level)
            # bind real, local names. Missing these produced false positives on
            # every function-local `import json as _json` style line.
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.Global):
            names.update(node.names)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _self_referential_reads(fn):
    """Locals assigned from THEMSELVES, where the name was never a parameter.

    `detail = detail or {}` at the top of a function reads `detail` before
    binding it, so it is an UnboundLocalError on every execution -- yet a scope
    scan sees a perfectly ordinary local binding and calls the name defined.
    That is the shape which hid the second loop.py bug behind the fix for the
    first one.

    Restricted to names that are NOT parameters, because `salt = salt.encode()`
    and `lot = lot * mult` are ordinary and correct: the parameter binding is
    already in scope before the assignment runs.
    """
    params = set()
    for a in fn.args.args + fn.args.posonlyargs + fn.args.kwonlyargs:
        params.add(a.arg)
    if fn.args.vararg:
        params.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        params.add(fn.args.kwarg.arg)

    # A name is only dangerous if the self-referential assignment is its FIRST
    # binding in this function. `peak = max(peak, equity)` after `peak = 0.0` is
    # an ordinary accumulator; `detail = detail or {}` with no prior binding is
    # an UnboundLocalError. So walk in source order and stop caring about a name
    # once anything has bound it.
    bound_before: set[str] = set(params)
    hits = []
    def _pos(n):
        # ast.arguments has no position; sort it first so it never wins.
        return (getattr(n, "lineno", -1), getattr(n, "col_offset", -1))

    for node in sorted(ast.walk(fn), key=_pos):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            tuple_names = set()
            for t in node.targets:
                if isinstance(t, (ast.Tuple, ast.List)):
                    tuple_names |= {e.id for e in ast.walk(t)
                                    if isinstance(e, ast.Name) and isinstance(e.ctx, ast.Store)}
            for name in targets:
                if name in bound_before:
                    continue
                for sub in ast.walk(node.value):
                    if (isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
                            and sub.id == name):
                        hits.append((name, sub.lineno))
            bound_before.update(targets)
            bound_before.update(tuple_names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound_before.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound_before.add((alias.asname or alias.name).split(".")[0])
    return hits


def _scoped_nodes(fn):
    """Yield nodes belonging to THIS function's own scope.

    Nested defs, lambdas and classes are separate scopes and are skipped here;
    the caller recurses into them with the right visible set. Without this,
    `lambda z: z.strength` looked like an undefined read of `z`.
    """
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _undefined_reads(fn, visible: set[str]):
    """(name, lineno) for every read that resolves in no visible scope."""
    bound_here = _function_bindings(fn) | visible
    missing = []
    for node in _scoped_nodes(fn):
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                and node.id not in bound_here
                and node.id not in BUILTINS
                and node.id not in IMPLICIT_GLOBALS):
            missing.append((node.id, node.lineno))
    for name, lineno in _self_referential_reads(fn):
        entry = (name, lineno)
        if entry not in missing:
            missing.append(entry)
    return missing


def _python_files():
    for dirpath, dirnames, filenames in os.walk(APP_ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_no_module_reads_undefined_names():
    """Every name read in app/ must resolve somewhere.

    This is the guard that would have caught both loop.py incidents before they
    reached the VPS.
    """
    problems = []

    for path in _python_files():
        rel = os.path.relpath(path, os.path.dirname(APP_ROOT))
        tree = ast.parse(open(path, encoding="utf-8").read())

        module_names = _module_bindings(tree)

        def check(fn, visible, qualname):
            for name, lineno in _undefined_reads(fn, visible):
                problems.append(f"{rel}:{lineno}: {qualname}() reads undefined '{name}'")
            # A nested function sees its parent's bindings as a closure, so
            # recurse with those added. Lambdas included: they have their own
            # parameters but close over the enclosing scope.
            nested_visible = visible | _function_bindings(fn)
            for child in ast.walk(fn):
                if child is fn:
                    continue
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    check(child, nested_visible, f"{qualname}.{child.name}")
                elif isinstance(child, ast.Lambda):
                    for name, lineno in _undefined_reads(child, nested_visible):
                        problems.append(
                            f"{rel}:{lineno}: {qualname}() lambda reads undefined '{name}'")

        def check_methods(cls, qual):
            for node in cls.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    check(node, module_names, f"{qual}.{node.name}")

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                check(node, module_names, node.name)
            elif isinstance(node, ast.ClassDef):
                # Methods are the overwhelmingly common case in this codebase:
                # TradingLoop._act_on_decision is where the first NameError lived,
                # and skipping class bodies is exactly why a module-body-only scan
                # reported the codebase clean while it shipped broken.
                check_methods(node, node.name)

    assert not problems, (
        "undefined free variables (each is a NameError on some code path):\n  "
        + "\n  ".join(sorted(problems))
    )


def test_loop_execution_path_binds_its_parameters():
    """Targeted check on the order path that shipped broken.

    _execute is the highest-consequence function in the system: it sends real
    orders. Assert its free variables resolve, so a refactor cannot silently
    reintroduce a post-fill NameError.
    """
    import app.core.loop as loop_mod

    path = loop_mod.__file__
    tree = ast.parse(open(path, encoding="utf-8").read())
    module_names = _module_bindings(tree)

    execute = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.AsyncFunctionDef) and n.name == "_execute")

    missing = _undefined_reads(execute, module_names)
    assert not missing, (
        f"_execute() reads undefined names {missing} — an order could be sent "
        "and then fail before being recorded"
    )