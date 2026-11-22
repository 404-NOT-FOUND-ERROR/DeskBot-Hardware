"""Compare the prior PPTX with the publication-style revision."""
import difflib
import html
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'artifacts/advisor-defense-20261028'
NS={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}

def paragraphs(z, name):
    return [''.join(t.text or '' for t in p.findall('.//a:t',NS)) for p in ET.fromstring(z.read(name)).findall('.//a:p',NS) if ''.join(t.text or '' for t in p.findall('.//a:t',NS)).strip()]

def reason(old,new):
    if any(s in old for s in ['风险','UI 冻结','并发','破坏']):
        return '删除事故复盘或风险先行的框架，保留可重复验证的开发方法。'
    if any(s in old for s in ['未完成','未形成','待验证','尚不能','不能','仍不','未闭环','不把','不支持','不等于','不代表','不得','非实机']):
        return '把否定清单改为当前成立的结果、明确条件或下一阶段任务。'
    if '边界' in old:
        return '主讲页说明研究职责与成果用途，必要的版本和条件保留在来源说明。'
    if '不是' in old or '不替代' in old:
        return '直接定义研究对象和方法作用，删除未被提出的反面比较。'
    return '同页必要的文字同步，保留原有版式和记录单位。'

rows=json.loads((OUT/'defensive-writing-edits.json').read_text(encoding='utf-8'))
for row in rows:
    if row.get('location')=='资料索引·目标架构':
        row['revised']='身体事实、客户端接口和角色绑定的依赖'
        row['reason']='准确说明材料来源用途。'
if any(r.get('kind')=='slide-diff' for r in rows):
    rows=[r for r in rows if r.get('kind')!='slide-diff']
rows=[r for r in rows if r.get('kind')!='companion-final']
extra=[
('第12页·讲稿','先冻结当前基线，然后完成内核测量和外壳工程设计，这部分不必等待底座维修。','先冻结当前基线，内核测量和外壳工程设计同步推进。','用可并行的工作安排替代否定式等待说明。'),
('第12页·讲稿','DeepSeek、语音识别和TTS替换放到最后，避免同时改变身体能力和对话服务。每一步都需要对应的离线、构建和实机验收记录。','DeepSeek、语音识别和TTS沿稳定身体接口接入。每一步都对应离线、构建和实机验收记录。','以稳定接口和对应记录说明接入方法。'),
('讲稿·演示建议1','先打开汇报网页或PPTX，避免把串口和开发终端作为主入口。','现场从汇报网页或PPTX开始，按主线依次展示研究问题、方法和证据。','给出正向展示顺序。'),
('讲稿·演示建议2','第8页后可展示E1.19节点工作台：查看惊讶旧/新边、资源预览与动作独立支路。','第8页后展示E1.19节点工作台：查看惊讶语义的旧/新连接、资源预览与动作独立支路。','同步现场讲解用词，指明语义连接。'),
('讲稿·演示建议3','底座未修复时只展示录制与离线证据；头部功能若需要演示，先做一次预演。','磁场身份按现场设备状态选择实时采样或录制回放；头部交互按演示顺序预演一次。','按实际设备状态选择展示方式，保留真实来源。'),
('讲稿·演示建议4','不展示私人网络配置、完整设备MAC、密钥或原始个人对话。','现场画面使用脱敏的网络信息、设备标识和对话内容。','这属于演示操作要求，以脱敏动作正向说明。'),
('讲稿·演示建议5','网络异常时继续用离线材料，不把合成示例称为当前实机观测。','使用离线材料演示时，说明录制与生成视觉素材各自的来源。','保留必要的素材来源说明，删除预设失误。'),
]
rows.extend(dict(location=l,original=o,revised=n,reason=r,kind='companion-final') for l,o,n,r in extra)
slide_changes=[]
with zipfile.ZipFile(OUT/'advisor-defense-20261028-v3.pptx') as before, zipfile.ZipFile(OUT/'advisor-defense-20261028-v8.pptx') as after:
    for page in range(1,17):
        old=paragraphs(before,f'ppt/slides/slide{page}.xml')
        new=paragraphs(after,f'ppt/slides/slide{page}.xml')
        for tag,i,j,a,b in difflib.SequenceMatcher(a=old,b=new,autojunk=False).get_opcodes():
            if tag=='equal': continue
            os=' / '.join(old[i:j])
            ns=' / '.join(new[a:b])
            slide_changes.append(dict(location=f'第{page}页·页内文字',original=os,revised=ns,reason=reason(os,ns),kind='slide-diff'))
rows=slide_changes+rows
(OUT/'defensive-writing-edits.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')

head='''# 导师汇报：逐句修改对照

修订对象：2026-10-28 导师汇报及其讲稿、问答、网页。论文工作稿 `draft.md` 保持原样。

本轮保留16页结构、原有图表、节点图与工作台截图。检查重点是主张、论证职责与措辞，正文中未涉及问题的句子沿用原文。部分页的标题和小字随同一主张同步调整，以下一并列出。

## 汇报主线

角色持续变化为什么需要实体外壳更新 → 来源、形态和内核约束怎样连成方法 → 磁性身份与表达工作台提供什么现有证据 → 用实体装配和用户任务完成终期评价。

## 保留的必要事实

- 磁场结果来自本次设备和环境下的五段连续录制，共742条样本。图中呈现输出计数，独立安装评价列为下一阶段任务。
- 移除磁铁的当前输出为 `unknown`。进一步区分空载与其他未知状态使用后续空载基线与重复安装任务。
- 表情图来自EAF解码参考帧，工作台图来自实际页面截图。对应的资源部署与本体回归有版本记录。
- 实时客户端、固定布局C、实体工程正式评价明确列在后续路线，按实际阶段叙述。

## 逐项对照

'''
md=head
for i,r in enumerate(rows,1):
    md+=f"### {i:02d}. {r['location']}\n\n**原句：** {r['original'] or '（无）'}\n\n**改为：** {r['revised'] or '（删除）'}\n\n**原因：** {r['reason']}\n\n"
md+='## 保留判断\n\n“未知近距”“只读”“内核约束”“来源条件”“未知输出”等是对象或机制的准确说明，按语境保留。“没有误认A/B”如实描述本次录制结果，作为结果可保留；以否认形式提出从未主张的泛化准确率，则改用录制条件和评价单位说明。\n'
(OUT/'defensive-writing-edit-audit-20261028.md').write_text(md,encoding='utf-8')
print(json.dumps({'slide_text_changes':len(slide_changes),'total_comparisons':len(rows),'draft_md_changed':False},ensure_ascii=False))
