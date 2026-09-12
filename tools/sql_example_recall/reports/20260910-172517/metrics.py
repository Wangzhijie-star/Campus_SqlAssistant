"""Score SQL-example retrieval with alternative IDs per required intent.

Each gold group is one required intent; retrieving any member satisfies it.
Ranking metrics must receive vector ranking, not unordered source output.
"""


def score(required_groups, returned_ids):
    groups = [set(g) for g in required_groups]
    if any(not g for g in groups):
        raise ValueError('Gold groups must not be empty')
    allowed = set().union(*groups) if groups else set()
    if sum(map(len, groups)) != len(allowed):
        raise ValueError('An example cannot belong to multiple required intents')
    returned = set(returned_ids)
    hits = sum(bool(g & returned) for g in groups)
    return {
        'positive': bool(groups),
        'multi': len(groups) > 1,
        'recall': hits / len(groups) if groups else None,
        'precision': len(returned & allowed) / len(returned) if groups and returned else (0.0 if groups else None),
        'all_hit': hits == len(groups) if groups else None,
        'exact': hits == len(groups) and not (returned - allowed),
        'negative_fp': bool(returned) if not groups else None,
        'missing_groups': [sorted(g) for g in groups if not g & returned],
        'extra_ids': sorted(returned - allowed),
        'returned_count': len(returned),
    }


def summarize(rows):
    def mean(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None
    return {
        'count': len(rows),
        'positive_count': sum(r['positive'] for r in rows),
        'negative_count': sum(not r['positive'] for r in rows),
        **{k: mean(r[k] for r in rows) for k in
           ['recall', 'precision', 'exact', 'negative_fp', 'returned_count']},
        'multi_all_hit': mean(r['all_hit'] for r in rows if r['multi']),
    }
