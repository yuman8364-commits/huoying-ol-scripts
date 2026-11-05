"""Read-only comparison of frozen Python modules against current project source."""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
from pathlib import Path
import types


def normalized_constant(value):
    if isinstance(value, types.CodeType):
        return normalized_code(value)
    if isinstance(value, tuple):
        return ('tuple', tuple(normalized_constant(item) for item in value))
    if isinstance(value, frozenset):
        return ('frozenset', tuple(sorted(repr(normalized_constant(item)) for item in value)))
    return (type(value).__name__, repr(value))


def normalized_code(code):
    """Exclude build-directory filenames, while retaining bytecode and all constants."""
    return (code.co_code.hex(), code.co_names, code.co_varnames, code.co_freevars,
            code.co_cellvars, code.co_argcount, code.co_posonlyargcount,
            code.co_kwonlyargcount, code.co_flags, code.co_name, code.co_qualname,
            code.co_firstlineno, code.co_linetable.hex(), code.co_exceptiontable.hex(),
            tuple(normalized_constant(item) for item in code.co_consts))


def compare_source(root, module, embedded):
    base = Path(root).joinpath(*module.split('.'))
    path = base.with_suffix('.py')
    if not path.is_file() and base.is_dir():
        path = base/'__init__.py'
    if not path.is_file():
        return dict(module=module, source_path=str(path), source_exists=False,
                    code_matches_current_source=False)
    current = compile(path.read_text(encoding='utf-8-sig'), embedded.co_filename,
                      'exec', dont_inherit=True, optimize=0)
    return dict(module=module, source_path=str(path), source_exists=True,
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                code_matches_current_source=normalized_code(embedded) == normalized_code(current))


def verify_package(root, executable, *, archive_factory=None):
    if archive_factory is None:
        from PyInstaller.archive.readers import CArchiveReader
        archive_factory = CArchiveReader
    archive = archive_factory(str(executable))
    pyz = archive.open_embedded_archive('PYZ.pyz')
    def project_module(module):
        return (module.startswith('hyol_') or module == 'tools.preflight_live' or module == 'daruma_standalone_gui'
                or module == 'daruma_standalone' or module.startswith('daruma_standalone.'))
    checks = [dict(compare_source(root, module, pyz.extract(module)), location='PYZ')
              for module in sorted(pyz.toc) if project_module(module)]
    for module, row in sorted(archive.toc.items()):
        if project_module(module) and row[-1] == 's':
            checks.append(dict(compare_source(root, module, marshal.loads(archive.extract(module))),
                               location='entrypoint'))
    if not any(row['location'] == 'entrypoint' for row in checks):
        raise ValueError('No project entrypoint found in frozen executable')
    return {'executable': str(executable), 'modules_checked': len(checks),
            'source_code_matches': all(row['code_matches_current_source'] for row in checks),
            'checks': checks, 'input_operations': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = verify_package(args.root, args.executable)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['source_code_matches'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
