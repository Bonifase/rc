"""Assemble metadata cards from ORIGINAL Bonifase metadata, bibliography and citations.

Do not call models or Crossref. Each official reference comes from the same
resolution operation as the metadata findings, not a separately written mock.
"""
from metadata_status_mapping import build_metadata_plan


def _readable_field(f):
    field=f.get('field')
    old=f.get('declared')
    new=f.get('actual')
    if field=='authors':
        return f'The official record lists {new} authors; the reference lists {old}.'
    return f'The correct {field} is {new}, not {old}.'


def _mark(text, fragments):
    """Highlight only substrings that actually occur in the literal source text."""
    s=str(text)
    spans=[]
    for frag in fragments:
        if frag is None: continue
        frag=str(frag)
        if not frag:continue
        start=s.find(frag)
        if start < 0:continue
        end=start+len(frag)
        if any(start < e and end > b for b,e in spans):continue
        spans.append((start,end))
    for begin,end in sorted(spans,reverse=True):
        s=s[:begin]+'**'+s[begin:end]+'**'+s[end:]
    return s


def build_cards_from_metadata(metadata_output, manuscript_manifest):
    """Return payload for report renderer; no hand-entered official citations.

    Missing Crossref provenance or a missing passage blocks publication, rather
    than substituting synthetic data. Identity-unconfirmed references remain
    internal as requested, except missing records for 'resolved' status
    which is an integration defect and triggers DRAFT.
    """
    if not isinstance(metadata_output,dict) or not isinstance(manuscript_manifest,dict):
        raise ValueError('metadata output and manuscript manifest required')
    refs=manuscript_manifest.get('bibliography')
    citations=manuscript_manifest.get('citations')
    orphan=manuscript_manifest.get('orphan_citations')
    if not all(isinstance(v,list) for v in (refs,citations,orphan)):
        raise ValueError('manuscript manifest lists required')
    parsed={str(r['reference_id']):r for r in refs}
    by_ref={}
    for c in citations:
        by_ref.setdefault(str(c['reference_id']),[]).append(c)
    records=metadata_output.get('official_records')
    if not isinstance(records,dict):
        raise ValueError('same-run official_records missing from metadata output')
    doi_by_id={r['reference_id']:r.get('doi') for r in refs}
    plan=build_metadata_plan(metadata_output,doi_by_id,records)
    cards=[]
    for dec in plan['decisions']:
        if not dec.cards:continue
        rid=str(dec.reference_id)
        ref=parsed.get(rid)
        if ref is None:
            raise ValueError('missing bibliography entry '+rid)
        raw=ref.get('reference')
        if not isinstance(raw,str) or not raw.strip():raise ValueError('missing original reference '+rid)
        citations_for_ref=by_ref.get(rid)
        if not citations_for_ref:
            raise ValueError('no cited sentence for metadata '+rid)
        citation=citations_for_ref[0]
        record=records.get(rid)
        for metadata_card in dec.cards:
            kind=metadata_card.error_type
            findings=list(metadata_card.findings)
            base={'reference_id':dec.reference_id,'kind':'metadata','marker':citation['marker'],
                  'source_label':ref.get('source_label') or raw,
                  'error_type':kind,'claim':citation['claim'],
                  'reference_as_generated':raw,'doi':ref.get('doi')}
            if kind in ('wrong_reference_details','retracted_article') and record:
                # Links point to the resolved article, not a wrong declared DOI.
                base['doi']=record.get('resolved_doi')
            if kind=='wrong_reference_details':
                if not (record and record.get('official_ref_text') and record.get('verified_first_author_match')):
                    raise ValueError('official record or identity missing for '+rid)
                base['reference_as_generated']=_mark(raw,[f.get('declared') for f in findings])
                base['reference_official']=_mark(record['official_ref_text'],[f.get('actual') for f in findings])
                base['observation']='The cited article exists. These details do not match the official record:'
                base['findings']=[_readable_field(f) for f in findings]
            elif kind=='retracted_article':
                if not (record and record.get('official_ref_text') and record.get('verified_first_author_match')):
                    raise ValueError('retraction without identified source '+rid)
                base['reference_official']=record['official_ref_text']
                base['observation']='The cited article was retracted.'
                base['findings']=[
                    'Crossref registers a retraction notice for this article: '+str(f.get('actual') or f.get('message') or 'notice identified')+'.'
                    for f in findings]
            elif kind=='doi_leads_elsewhere':
                if not (record and record.get('verified_wrong_doi') is True):
                    raise ValueError('DOI mismatch without source resolution proof '+rid)
                base['reference_as_generated']=_mark(raw,[ref.get('doi')])
                base['reference_official']=None
                base['doi_dead']=True
                base['observation']='The DOI in the reference belongs to a different article.'
                base['findings']=['The DOI '+str(ref.get('doi'))+' does not lead to the cited article.']
            else:
                raise ValueError('unknown mapping '+str(kind))
            cards.append(base)
    for number, findings in plan['orphan_cards']:
        marker=number if number.startswith('[') else '['+number+']'
        occurrences=[o for o in orphan if o.get('marker')==marker]
        if not occurrences: raise ValueError('orphan does not match manifest '+marker)
        cards.append({'reference_id':'orphan:'+marker.strip('[]'),
            'kind':'metadata','marker':marker,'source_label':'Not in the reference list',
            'error_type':'citation_without_reference','claim':occurrences[0]['claim'],
            'reference_as_generated':'**'+marker+'** is not in the reference list.',
            'reference_official':None,'count_as_reference':False,
            'findings':[marker+' is cited in '+str(len(occurrences))+' sentence(s) and does not appear in the reference list.']})
    return {'cards':cards,'references_reviewed':plan['references_reviewed'],
            'blocks_final':plan['blocks_final'],'decisions':plan['decisions']}
