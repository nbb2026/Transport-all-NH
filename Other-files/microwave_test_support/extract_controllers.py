"""Reproducibly copy only the required controller definitions from integrated dev."""
import ast, hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
source=ROOT.parent/'Transport_all_NH_dev.py'
text=source.read_text(); tree=ast.parse(text); lines=text.splitlines(keepends=True)
# Retain only the MFLI method closure, including properties and static helpers.
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='NonLocalACController')
methods={n.name:n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
needed={'__init__','connect_mfli','disconnect_mfli','mfli_connected','configure_mfli','read_stfmr_mfli','read_mfli'}
while True:
 more={n.attr for k in needed for n in ast.walk(methods[k]) if isinstance(n,ast.Attribute) and isinstance(n.value,ast.Name) and n.value.id in ('self','cls') and n.attr in methods}
 if more<=needed:break
 needed|=more
cls.body=[n for n in cls.body if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) or n.name in needed]
# Top-level name bindings, including optional import try/except blocks.
def bound(node):
 if isinstance(node,(ast.FunctionDef,ast.ClassDef)):return {node.name}
 if isinstance(node,(ast.Import,ast.ImportFrom)):return {a.asname or a.name.split('.')[0] for a in node.names}
 if isinstance(node,(ast.Assign,ast.AnnAssign)):
  targets=node.targets if isinstance(node,ast.Assign) else [node.target]
  return {n.id for t in targets for n in ast.walk(t) if isinstance(n,ast.Name)}
 if isinstance(node,ast.Try):return set().union(*(bound(n) for n in node.body+node.orelse+node.finalbody+[n for h in node.handlers for n in h.body]))
 return set()
lookup={name:i for i,n in enumerate(tree.body) for name in bound(n)}
roots={'STFMRKepco4886Controller','STFMRGauss475Controller','STFMRSR830Controller','STFMRSMB100AController','NonLocalACController','STFMRScanRecorder','STFMR_LOCKIN_DEFAULT_RESOURCES','NONLOCAL_MFLI_REFERENCE_INPUTS','STFMR_LOCKIN_SR830_REFERENCE_INPUTS','configure_tk_font_system'}
selected=set();todo=list(roots)
while todo:
 name=todo.pop()
 if name not in lookup:continue
 i=lookup[name]
 if i in selected:continue
 selected.add(i)
 todo.extend(n.id for n in ast.walk(tree.body[i]) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load))
chunks=['from __future__ import annotations\n']
for i in sorted(selected):
 n=tree.body[i]
 if isinstance(n,ast.ImportFrom) and n.module=='__future__':continue
 if n is cls:
  parts=['class NonLocalACController:\n']
  for member in n.body:
   start=min([member.lineno]+[d.lineno for d in getattr(member,'decorator_list',[])])-1
   parts.append(''.join(lines[start:member.end_lineno])+'\n')
  chunks.append(''.join(parts))
 else: chunks.append(''.join(lines[n.lineno-1:n.end_lineno]))
header='"""Microwave_Transport_Test_NH_dev. Standalone fixed-field two-frequency test.\nInstrument controllers copied from integrated development source; no hardware on import.\n"""\n'
controller=header+'\n\n'.join(chunks)
# Split optional numeric/image imports from the unrelated camera dependency.
start=controller.index('try:\n    import numpy as np')
end=controller.index('\n\n',controller.index('    CAMERA_IMPORT_ERROR = error',start))
controller=controller[:start]+'''try:
    import numpy as np
except ImportError:
    np = None
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None
'''+controller[end:]
(ROOT/'microwave_test_support/controller_snapshot.py.txt').write_text(controller)
(ROOT/'microwave_test_support/controller_provenance.json').write_text(json.dumps({'source':source.name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'top_level_names':sorted(set().union(*(bound(tree.body[i]) for i in selected))),'mfli_methods':sorted(needed)},indent=2))
print('Controller lines',len(controller.splitlines()),'MFLI methods',len(needed))
