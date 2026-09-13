"""Read rendered Markdown fact tables; a sidecar is never evidence of page content."""
import json
from markdown_it import MarkdownIt
from artifact_schema import FACT_ARRAYS

OMIT={'id','source_refs','type_evidence','page_id'}


def expected_claims(facts):
    return {(fact['id'],field):value for group in FACT_ARRAYS for fact in facts[group]
            for field,value in fact.items() if field not in OMIT}


def read_claims(page):
    tokens=MarkdownIt('commonmark').enable('table').parse(page)
    rows=[]; row=None; cell=None
    for token in tokens:
        if token.type=='tr_open': row=[]
        elif token.type=='td_open': cell=''
        elif token.type=='inline' and cell is not None:
            cell+=''.join(t.content for t in token.children or () if t.type in ('text','code_inline'))
        elif token.type=='td_close': row.append(cell); cell=None
        elif token.type=='tr_close' and row is not None: rows.append(row); row=None
    claims={}; errors=[]
    for row in rows:
        if len(row)!=3: continue
        fid,field,value=row
        if not fid.startswith(('obj_','def_','op_','col_','formula_','cond_','unknown_')): continue
        key=(fid,field)
        if key in claims: errors.append(f'Duplicate page claim: {fid}.{field}')
        try: claims[key]=json.loads(value)
        except ValueError: errors.append(f'Invalid rendered claim value: {fid}.{field}')
    return claims,errors


def check_page_claims(facts,page):
    actual,errors=read_claims(page)
    expected=expected_claims(facts)
    for key,value in expected.items():
        if key not in actual or actual[key]!=value: errors.append('Page claim differs/missing: '+'.'.join(key))
    for key in actual.keys()-expected.keys(): errors.append('Unsupported page claim: '+'.'.join(key))
    return errors
