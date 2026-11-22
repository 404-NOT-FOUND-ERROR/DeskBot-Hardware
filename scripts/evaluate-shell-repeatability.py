"""Replay labelled shell remounts and write a new project-local report."""
import argparse
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.shell_repeatability import evaluate_suite


def preview(report):
    def esc(value):
        return html.escape(str(value))
    rows = ''.join('<tr><td>' + '</td><td>'.join(esc(x) for x in (
        row['profile_id'] or {'removed': '取下对照', 'unknown_layout': '未登记布局'}[row['scenario']], row['independent_mounts'],
        row['matched_mounts'], row['additional_mounts_needed'])) + '</td></tr>'
        for row in report['coverage'])
    results = ''.join('<tr><td>' + '</td><td>'.join(esc(x) for x in (
        row['id'], row['expected_profile_id'] or {'removed': '取下拒识', 'unknown_layout': '未知拒识', None: '仅观察'}[row['scenario']], row['final']['status'],
        row['final']['profile_id'] or '—',
        {'matched': '符合预期', 'mismatch': '出现误认', 'unconfirmed': '尚未确认',
         'observed': '仅观察'}[row['outcome']])) + '</td></tr>' for row in report['windows'])
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>外壳识别评估</title><style>
body{margin:0;background:#f5f8fc;color:#20334c;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}
main{max-width:1000px;margin:auto;padding:44px 24px}h1{font-size:32px;line-height:1.3}
section{background:white;border:1px solid #e4eaf1;border-radius:20px;padding:22px;margin:20px 0;overflow:auto}
small,p{color:#5b6c80}.badge{display:inline-block;color:#087e79;background:#dff7ee;padding:5px 14px;border-radius:18px}
table{border-collapse:collapse;width:100%;white-space:nowrap}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #e8edf4}
.flow{display:flex;gap:12px;flex-wrap:wrap}.flow span{padding:12px;background:#eff6ff;border-radius:12px}
@media(prefers-color-scheme:dark){body{background:#152131;color:#e0ebf7}section{background:#1d2d42;border-color:#30445d}p,small{color:#acbfd3}.flow span{background:#293c55}th,td{border-color:#30445d}}
</style><main><small>DESKBOT · S1a</small><h1>''' + esc(report['title']) + '''</h1>
<span class="badge">电脑回放已完成</span><p>''' + esc(report['sensor_kind']) + ''' · 独立装配编号来自采集标记</p>
<div class="flow"><span>双角色晚间验收</span><span>→ 固定布局重复装回</span><span>→ 识别评估</span><span>→ 外壳绑定角色</span></div>
<section><h2>样本进度</h2><p>每个已登记布局、取下对照、未登记布局目标各 10 次。一次装配的多段记录计一次。</p>
<table><thead><tr><th>布局</th><th>已评估</th><th>符合预期</th><th>待补次数</th></tr></thead><tbody>''' + rows + '''</tbody></table></section>
<section><h2>回放结果</h2><table><thead><tr><th>记录</th><th>人工预期</th><th>末次状态</th><th>识别档案</th><th>结果</th></tr></thead><tbody>''' + results + '''</tbody></table>
<p>相似度沿用现有算法；圆屏效果和角色交互按实机验收步骤观察。</p></section></main></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        target = args.output.resolve()
        if not any(target.is_relative_to(ROOT / folder) and target != ROOT / folder
                   for folder in ('tmp', 'artifacts')):
            raise ValueError('output must be a new directory inside project tmp or artifacts')
        report = evaluate_suite(args.suite, ROOT)
        target.mkdir(parents=True, exist_ok=False)
        (target / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (target / 'index.html').write_text(preview(report), encoding='utf-8')
        print(json.dumps(dict(result=report['result'], output=str(target),
            coverage_complete=report['coverage_complete'],
            all_evaluated_mounts_matched=report['all_evaluated_mounts_matched'])))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
