import ast
import io
import keyword
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

# 可出现在括号化元组字面量或解包目标前的 Python 关键字，如 `for (x,) in ...`、`return (x,)`、`assert (x,)`。
# 当 `(` 之前的 token 是这些关键字时，不能把 `(x,)` 当作函数调用——它是 1 元素元组，尾逗号不可去。
_KEYWORDS = frozenset(keyword.kwlist)


def clean_trailing_commas_in_source(source: str) -> str:
    """移除实参列表、调用、下标、列表、字典、≥2 元素元组中多余的尾逗号，保留 1 元素元组 `(x,)`。"""
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(source.encode("utf-8")).readline))
    except Exception:
        return source

    commas_to_remove = set()
    stack = []

    for idx, tok in enumerate(tokens):
        if tok.type == tokenize.OP:
            if tok.string in ("(", "[", "{"):
                stack.append({"bracket": tok.string, "idx": idx, "comma_count": 0})
            elif tok.string in (")", "]", "}"):
                if stack:
                    stack.pop()
            elif tok.string == ",":
                if stack:
                    stack[-1]["comma_count"] += 1
                    next_idx = idx + 1
                    while next_idx < len(tokens) and tokens[next_idx].type in (
                        tokenize.NL,
                        tokenize.NEWLINE,
                        tokenize.COMMENT,
                        tokenize.INDENT,
                        tokenize.DEDENT,
                    ):
                        next_idx += 1
                    if next_idx < len(tokens) and tokens[next_idx].type == tokenize.OP and tokens[next_idx].string in (")", "]", "}"):
                        opening = stack[-1]
                        # 保留 1 元素元组字面量 `(x,)` 与解包 `for (x,) in ...`。
                        if opening["bracket"] == "(" and opening["comma_count"] == 1:
                            prev_to_open = opening["idx"] - 1
                            is_tuple_literal = True
                            if prev_to_open >= 0:
                                prev_tok = tokens[prev_to_open]
                                # `(` 前的 NAME token 通常表示函数调用 `func(x,)`——不是元组字面量；
                                # 但该 NAME 是 Python 关键字（for / return / assert 等）时，括号进入元组上下文。
                                if prev_tok.type == tokenize.NAME and prev_tok.string not in _KEYWORDS:
                                    is_tuple_literal = False
                                elif prev_tok.type == tokenize.OP and prev_tok.string in (")", "]", "}"):
                                    is_tuple_literal = False
                            if is_tuple_literal:
                                continue

                        commas_to_remove.add(idx)

    if not commas_to_remove:
        return source

    tokens_sorted = sorted([tokens[i] for i in commas_to_remove], key=lambda t: (t.start[0], t.start[1]), reverse=True)
    lines = source.splitlines(keepends=True)
    for tok in tokens_sorted:
        r, c = tok.start[0] - 1, tok.start[1]
        line = lines[r]
        lines[r] = line[:c] + line[c + 1 :]

    return "".join(lines)


def main() -> int:
    files = [Path(p) for p in sys.argv[1:] if p.endswith(".py")]
    modified_paths: list[Path] = []
    original_contents: dict[Path, str] = {}

    for path in files:
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
            stripped = clean_trailing_commas_in_source(content)
            if stripped != content:
                ast.parse(stripped)
                original_contents[path] = content
                path.write_text(stripped, encoding="utf-8")
                modified_paths.append(path)
        except Exception as exc:
            print(f"clean_trailing_commas: skipped {path}: {exc}", file=sys.stderr)

    if not modified_paths:
        return 0

    # 一次 ruff 调用批量格式化所有被动过的文件（毫秒级）。
    ruff_bin = shutil.which("ruff") or "ruff"
    try:
        subprocess.run(
            [ruff_bin, "format", "--line-length=180", *[str(p) for p in modified_paths]],
            capture_output=True,
            check=False,
        )
    except Exception:
        pass

    # 统计真正发生改动的文件数（去除 ruff 格式化但内容未变的）。
    really_modified = 0
    for path, orig in original_contents.items():
        try:
            now = path.read_text(encoding="utf-8")
            if now != orig:
                really_modified += 1
        except Exception:
            pass

    if really_modified > 0:
        print(f"clean_trailing_commas: collapsed trailing commas in {really_modified} file(s).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
