"""Builds both notebooks: source-specific cells + shared analytics/rendering/app cells (split on '# %%')."""
import os, re, nbformat as nbf

here = os.path.dirname(os.path.abspath(__file__))


def read(name):
    return open(os.path.join(here, name), encoding='utf-8').read()


def build(parts, out):
    nb = nbf.v4.new_notebook()
    for chunk in re.split(r'^# %%', '\n'.join(read(p) for p in parts), flags=re.M)[1:]:
        head, _, body = chunk.partition('\n')
        body = body.strip('\n')
        if '[markdown]' in head:
            nb.cells.append(nbf.v4.new_markdown_cell('\n'.join(l[2:] if l.startswith('# ') else l.lstrip('#') for l in body.split('\n'))))
        else:
            nb.cells.append(nbf.v4.new_code_cell(body))
    nb.metadata['kernelspec'] = {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'}
    out = os.path.abspath(os.path.join(here, '..', out))
    nbf.write(nb, out)
    print('wrote', out, len(nb.cells), 'cells')


build(['cells_bql.py', 'cells_common.py'], 'PA_Terminal.ipynb')
build(['cells_futu.py', 'cells_common.py'], 'PA_Terminal_Futu.ipynb')
