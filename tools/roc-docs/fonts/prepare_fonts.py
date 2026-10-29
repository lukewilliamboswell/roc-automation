#!/usr/bin/env python3
"""Authoring-time reproducible assets: fontTools 4.61.1; no runtime font work.

Inputs: rsms/inter v4.1 release Inter-4.1.zip and roc-font 0.1.0-rc3
package/fonts/Merriweather-Variable.ttf. Run twice and compare output digests.
"""
import argparse
import hashlib
from pathlib import Path
import zipfile
import fontTools
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
assert fontTools.__version__ == '4.61.1'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("inputs", type=Path, help="directory with Inter-4.1.zip and extracted roc-font rc3 under font/")
parser.add_argument("output", type=Path, help="new or existing prepared-font directory")
args = parser.parse_args()
ROOT = args.inputs
OUTPUT = args.output
OUTPUT.mkdir(exist_ok=True)
def require(data, digest):
    assert hashlib.sha256(data).hexdigest() == digest
    return data
archive = require((ROOT/'Inter-4.1.zip').read_bytes(), '9883fdd4a49d4fb66bd8177ba6625ef9a64aa45899767dde3d36aa425756b11e')
with zipfile.ZipFile(ROOT/'Inter-4.1.zip') as bundle:
    for style in ('Regular', 'Bold', 'Italic', 'BoldItalic'):
        name='Inter-'+style+'.ttf'
        (OUTPUT/name).write_bytes(bundle.read('extras/ttf/'+name))
    (OUTPUT/'Inter-OFL.txt').write_bytes(bundle.read('LICENSE.txt'))
source=ROOT/'font/fonts/Merriweather-Variable.ttf'
require(source.read_bytes(), '22e924e4ea00ef19a5b30dd2c5c32674009badb39bb4611336960eb32ad7a422')
font=TTFont(source, recalcTimestamp=False)
font=instantiateVariableFont(font, {'wght':700,'wdth':100,'opsz':18}, inplace=True)
# The upstream reserved font name must not name this derivative. Remove
# all language/platform variants of family, unique ID, full, PostScript,
# typographic family/subfamily and variations prefix before adding ours.
name_table=font['name']
ids={1,2,3,4,6,16,17,21,22,25}
name_table.names=[r for r in name_table.names if r.nameID not in ids]
replacements={1:'Roc Docs Serif',2:'Bold',3:'RocDocsSerif-Bold-1.0',4:'Roc Docs Serif Bold',6:'RocDocsSerif-Bold',16:'Roc Docs Serif',17:'Bold',21:'Roc Docs Serif',22:'Bold'}
for name_id,value in replacements.items():
    for platform,encoding,language in [(3,1,0x409),(1,0,0)]:
        name_table.setName(value,name_id,platform,encoding,language)
font.save(OUTPUT/'RocDocsSerif-Bold.ttf', reorderTables=True)
(OUTPUT/'Merriweather-OFL.txt').write_bytes((ROOT/'font/fonts/Merriweather-OFL.txt').read_bytes())
for path in sorted(OUTPUT.iterdir()):
 print(hashlib.sha256(path.read_bytes()).hexdigest(),path.name)
