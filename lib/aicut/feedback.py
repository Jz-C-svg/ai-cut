"""Bind explicitly supplied account metrics to one production, without guessing."""
import csv
import hashlib
from pathlib import Path


def load_feedback(path, project, match_column=None, match_value=None):
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
    elif path.suffix.lower() == '.xlsx':
        import openpyxl
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            values = iter(workbook.active.iter_rows(values_only=True))
            header = [str(x or '') for x in next(values)]
            rows = [dict(zip(header, row)) for row in values if any(x is not None for x in row)]
        finally:
            workbook.close()
    else:
        raise ValueError('后台数据仅支持 CSV / XLSX')
    if not rows:
        raise ValueError('后台数据为空')
    candidates = [match_column] if match_column else ['project', '工程名', '名称', '标题', '作品名称', '视频标题']
    column = next((c for c in candidates if c in rows[0]), None)
    if not column:
        raise ValueError('无法匹配作品，请传 --match-column 和 --match-value')
    value = str(match_value if match_value is not None else project)
    hits = [r for r in rows if str(r.get(column, '')).strip() == value.strip()]
    if len(hits) != 1:
        raise ValueError(f'后台数据必须精确匹配一条作品，当前匹配 {len(hits)} 条')
    return {'source': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'match_column': column, 'match_value': value,
            'row': {str(k): str(v) if v is not None else '' for k, v in hits[0].items()},
            'note': '用户提供的单条作品观察数据；不推断因果，不当作实验结论或账号基线'}
