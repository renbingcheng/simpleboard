"""Development diagnostic: check imported symbols against bundled DLLs."""
from pathlib import Path
import sys
import pefile

root = Path(sys.argv[1])
paths = list(root.rglob('*.dll')) + list(root.rglob('*.pyd'))
lookup = {}
for path in sorted(paths, key=lambda p: len(p.parts)):
    lookup.setdefault(path.name.lower(), path)
exports = {}
for path in paths:
    try:
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[0, 1])
        for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
            target = lookup.get(entry.dll.decode().lower())
            if target is None:
                continue
            if target not in exports:
                dependency = pefile.PE(str(target), fast_load=True, max_symbol_exports=65536)
                dependency.parse_data_directories(directories=[0])
                symbols = getattr(dependency, 'DIRECTORY_ENTRY_EXPORT', None)
                exports[target] = ({s.name for s in symbols.symbols}, {s.ordinal for s in symbols.symbols}) if symbols else (set(), set())
                dependency.close()
            names, ordinals = exports[target]
            missing = [symbol.name.decode() if symbol.name else str(symbol.ordinal) for symbol in entry.imports
                       if (symbol.name not in names if symbol.name else symbol.ordinal not in ordinals)]
            if missing:
                print(f'{path.relative_to(root)} -> {target.relative_to(root)} missing {missing[:12]}')
        pe.close()
    except pefile.PEFormatError:
        pass
