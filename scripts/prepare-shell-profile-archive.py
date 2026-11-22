"""Create or reload a project-local magnetic registration archive offline."""
import argparse
import html
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.shell_profile_archive import (
    build_archive, canonical, digest, read_archive, restore_archive,
    save_archive, verify_replay,
)
from protocol.shell_repeatability import evaluate_suite


def project_file(path):
    path = path.resolve(strict=True)
    if not path.is_relative_to(ROOT):
        raise ValueError('input must be inside project')
    return path


def preview(result, archive):
    esc = lambda value: html.escape(str(value))
    cards = ''.join('<tr><td>' + '</td><td>'.join(esc(x) for x in (
        p['profile_id'], p['kind_name'], ', '.join(str(x) for x in p['center']),
        p['registered_at'])) + '</td></tr>' for p in archive['profiles'])
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>外壳档案保存</title>
<style>body{background:#f5f8fc;color:#20334c;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;margin:0}
main{max-width:1000px;margin:auto;padding:40px 24px}section{background:white;border:1px solid #e4eaf1;
border-radius:20px;padding:24px;margin:20px 0;overflow:auto}.badge{color:#087e79;background:#dff7ee;padding:6px 14px;border-radius:20px}
table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:12px;border-bottom:1px solid #e8edf4}
p,small{color:#5b6c80}.flow{display:flex;flex-wrap:wrap;gap:10px}.flow span{background:#eff6ff;padding:10px;border-radius:12px}
</style><main><small>DESKBOT · S1b</small><h1>外壳档案：保存与重新加载</h1>
<span class="badge">''' + esc('已由新进程加载并复播' if result['mode'] == 'restore' else '档案已保存并复播') + '''</span>
<p>历史滑块示例 · ''' + esc(archive['context']['sensor_id']) + ''' · ''' + esc(archive['context']['frame_id']) + '''</p>
<div class="flow"><span>登记数据</span><span>→ 文件保存</span><span>→ 空状态加载</span><span>→ 新采样确认</span><span>→ 后续绑定角色</span></div>
<section><h2>保存的登记档案</h2><table><thead><tr><th>编号</th><th>传感器</th><th>中心 XYZ</th><th>登记时间</th></tr></thead>
<tbody>''' + cards + '''</tbody></table><p>加载后为 missing / no_sample。采样窗口和连续确认从头开始。</p></section>
<section><h2>验证结果</h2><p>参数、档案与来源摘要核对通过，装回和取下回放与保存前逐项一致。</p>
<p>每个固定外壳的重复拆装采集接续双角色验收。当前仅保存电脑档案，历史示例标记为 offline_replay。</p></section>
</main></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--sensor-id', required=True)
    parser.add_argument('--frame-id', required=True)
    parser.add_argument('--sensor-kind', type=int, choices=(1, 2), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--restore', type=Path)
    args = parser.parse_args(argv)
    try:
        suite = project_file(args.suite)
        target = args.output.resolve()
        if not any(target.is_relative_to(ROOT / folder) and target != ROOT / folder
                   for folder in ('tmp', 'artifacts')):
            raise ValueError('output must be a new directory inside project tmp or artifacts')
        if target.exists():
            raise ValueError('output directory already exists')
        if args.restore:
            archive_path = project_file(args.restore)
            # The expected identity is supplied independently of the saved file.
            context = dict(sensor_id=args.sensor_id, frame_id=args.frame_id,
                sensor_kind=args.sensor_kind, units='transport_counts')
            archive, archive_sha = read_archive(archive_path, context)
        else:
            report = evaluate_suite(suite, ROOT)
            context = dict(sensor_id=args.sensor_id, frame_id=args.frame_id,
                sensor_kind=args.sensor_kind, units='transport_counts')
            archive = build_archive(report, context)
            archive_sha = None
        restored = restore_archive(archive, context)
        if restored.last_result.status != 'missing' or restored.window:
            raise ValueError('loaded recognizer must start with an empty stream')
        report_after = verify_replay(archive, context, suite, ROOT)
        result = dict(schema='deskbot.magnetic.profile_archive_verification/v1',
            mode='restore' if args.restore else 'create', result='passed',
            profiles=len(restored.profiles), empty_stream_on_load=True,
            initial_state=restored.last_result.as_dict(),
            replay_matches_source=digest(canonical(report_after)) == archive['provenance']['report_sha256'],
            coverage=report_after['coverage'], qualification=archive['qualification'],
            process_id=os.getpid(), hardware_accessed=False,
            manual_acceptance='pending', archive_file_sha256=archive_sha)
        target.mkdir(parents=True, exist_ok=False)
        if not args.restore:
            result['archive_file_sha256'] = save_archive(target / 'profiles.json', archive)
        (target / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (target / 'index.html').write_text(preview(result, archive), encoding='utf-8')
        print(json.dumps(dict(result='passed', mode=result['mode'], profiles=result['profiles'], output=str(target))))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
