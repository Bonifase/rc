"""Preserve the Crossref record already resolved by Bonifase's metadata module.

Lives at src/metadata/official_provenance.py. Used inside src.metadata.report.build_report (same run, no new API calls):
    from src.metadata.official_provenance import records_from_resolution
    ...after resolved_by_id is populated...
    payload['official_records'] = records_from_resolution(references, resolved_by_id)

The field is intended for the report output adapter, not a second resolver.
"""
import re
import unicodedata


def _norm_person(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').lower())
                   if c.isalnum())


def _get(x, k, default=None):
    return x.get(k, default) if isinstance(x, dict) else getattr(x, k, default)


def _author_string(author):
    family = _get(author, 'family')
    given = _get(author, 'given')
    if not family:
        return ''
    initials = ' '.join(v[0].upper() + '.' for v in re.split(r'[\s-]+', str(given or '')) if v)
    return f'{family}, {initials}' if initials else str(family)


def format_official_reference(actual):
    """Deterministic APA-like bibliographic representation from actual Crossref fields.

    Only used when a reasonably complete real record is present. Unknown
    fields are not invented; a missing essential field returns None.
    """
    if actual is None:
        return None
    title, year, journal = _get(actual, 'title'), _get(actual, 'year'), _get(actual, 'journal')
    authors = _get(actual, 'authors') or []
    doi = _get(actual, 'doi')
    if not (isinstance(title, str) and title.strip() and isinstance(year, int) and
            isinstance(journal, str) and journal.strip() and isinstance(doi, str) and doi.strip() and authors):
        return None
    aa = [_author_string(a) for a in authors]
    if not all(aa):
        return None
    if len(aa) > 1:
        author_label = ', '.join(aa[:-1]) + ', & ' + aa[-1]
    else:
        author_label = aa[0]
    vol, issue, pages = _get(actual, 'volume'), _get(actual, 'issue'), _get(actual, 'pages')
    pub = journal.strip()
    if vol:
        pub += ', ' + str(vol)
    if issue:
        pub += f'({issue})'
    if pages:
        pub += ', ' + str(pages)
    return f'{author_label} ({year}). {title.strip().rstrip(".")}. {pub}. https://doi.org/{doi}'


def _snapshot_actual(actual):
    if actual is None:
        return None
    keys = ('doi', 'title', 'year', 'journal', 'volume', 'issue', 'pages')
    snap = {k: _get(actual, k) for k in keys}
    snap['authors'] = [{
        'family': _get(a, 'family'),
        'given': _get(a, 'given')
    } for a in (_get(actual, 'authors') or [])]
    return snap


def records_from_resolution(references, resolved_by_id, title_similarity_function=None):
    """Generate provenance directly from ResolvedReference.actual objects."""
    out = {}
    for declared in references:
        rid = _get(declared, 'reference_id')
        resolved = resolved_by_id.get(rid)
        if resolved is None:
            raise ValueError(f'unresolved reference: {rid}')
        status = _get(resolved, 'status')
        declared_doi = _get(declared, 'doi')
        if status == 'wrong_article':
            out[str(rid)] = {
                'verified_wrong_doi': bool(declared_doi),
                'official_ref_text': None,
                'verified_title_match': False,
                'verified_first_author_match': False,
            }
            continue
        actual = _get(resolved, 'actual')
        if actual is None:
            out[str(rid)] = {
                'official_ref_text': None,
                'verified_title_match': False,
                'verified_first_author_match': False,
            }
            continue
        declared_title = _get(declared, 'title') or ''
        actual_title = _get(actual, 'title') or ''
        positive_resolution = status in ('_needs_field_comparison', 'well_cited', 'miscited')
        verified_title = (
            positive_resolution and callable(title_similarity_function)
            and bool(declared_title and actual_title)
            and title_similarity_function(declared_title, actual_title) >= 0.85
        )
        declared_authors = _get(declared, 'authors') or []
        actual_authors = _get(actual, 'authors') or []
        first_matches = bool(
            declared_authors and actual_authors and
            _norm_person(_get(declared_authors[0], 'family')) ==
            _norm_person(_get(actual_authors[0], 'family'))
        )
        out[str(rid)] = {
            'official_ref_text': format_official_reference(actual),
            'actual_fields': _snapshot_actual(actual),
            'verified_title_match': verified_title,
            'verified_first_author_match': first_matches,
            'resolved_doi': _get(actual, 'doi'),
            'declared_first_author': _get(declared_authors[0], 'family') if declared_authors else None,
            'official_first_author': _get(actual_authors[0], 'family') if actual_authors else None,
        }
    return out
