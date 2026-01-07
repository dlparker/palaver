#!/usr/bin/env python3
import sys, os
from pathlib import Path
in_file = Path(sys.argv[1])
out_suffix = sys.argv[2] if len(sys.argv) > 2 else ".pdf"

stripped_file = in_file.resolve().with_suffix('.mmd')
out_file = in_file.resolve().with_suffix(out_suffix)
print(f"making {stripped_file} from {in_file}")
with open(in_file) as ifile:
    buffer = ifile.read()
    lines = buffer.split('\n')
    obuffer = []
    for line in lines:
        if line.strip().startswith("```"):
            continue
        obuffer.append(line)
    with open(stripped_file, 'w') as ofile:
        ofile.write("\n".join(obuffer))

os.system(f"aa-exec --profile=chrome npx @mermaid-js/mermaid-cli -i {stripped_file} -o {out_file}")
os.system(f"xdg-open {out_file}")
