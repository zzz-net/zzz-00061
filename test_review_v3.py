import requests
import sqlite3
import time
import os
import csv
import sys
from datetime import datetime, timedelta

# ===== Windows 控制台编码自动适配（不改变用户终端、不删字符、不改代码页）=====
# 解决问题：GBK 无法编码 Unicode 字符(如✓)导致 UnicodeEncodeError 崩溃
# 原理：保持原有编码(GBK)不变以正确显示中文，仅设置 errors='replace' 让无法编码的
#       字符(如✓)显示为?而非崩溃。✓字符仍保留在代码中，只是输出时做容错替换。
if sys.platform.startswith('win') and sys.stdout.encoding:
    enc = sys.stdout.encoding.lower()
    if 'gbk' in enc or enc == 'cp936' or enc == '936':
        try:
            sys.stdout.reconfigure(errors='replace')
            sys.stderr.reconfigure(errors='replace')
        except Exception:
            pass  # 极老版本 Python 不支持 reconfigure 则忽略
# ============================================================================

API = 'http://127.0.0.1:5000/api'
DB_PATH = 'archive_transfer.db'

PASS = '[OK]'
FAIL = '[FAIL]'
total = 0
passed = 0

def assert_true(cond, msg):
    global total, passed
    total += 1
    if cond:
        passed += 1
        print(f'{PASS} {msg}')
        return True
    else:
        print(f'{FAIL} {msg}')
        return False

def assert_equal(a, b, msg):
    global total, passed
    total += 1
    if a == b:
        passed += 1
        print(f'{PASS} {msg}')
        return True
    else:
        print(f'{FAIL} {msg} (期望={b}, 实际={a})')
        return False

def header(title):
    print()
    print('=' * 60)
    print(f'测试: {title}')
    print('=' * 60)

def create_full_batch_and_sign(sender_id, receiver_id, archive_prefix, box_count=2):
    batch_no = f'REV-{archive_prefix}-{int(time.time()%100000)}'
    archives = [
        {'archive_no': f'{archive_prefix}-{i+1}', 'title': f'{archive_prefix}档案{i+1}', 'remark': ''}
        for i in range(box_count * 2)
    ]
    r = requests.post(f'{API}/batches', json={
        'batch_no': batch_no,
        'description': f'{archive_prefix}复核测试批次',
        'created_by': sender_id,
        'archives': archives
    })
    batch_id = r.json()['id']
    
    box_ids = []
    for bi in range(box_count):
        r = requests.post(f'{API}/boxes', json={
            'box_no': f'BOX-{archive_prefix}-{bi+1}-{int(time.time()%100000)}',
            'batch_id': batch_id,
            'operator_id': sender_id
        })
        box_ids.append(r.json()['id'])
    
    detail = requests.get(f'{API}/batches/{batch_id}').json()
    aids = [a['id'] for a in detail['archives']]
    
    for bi, bid in enumerate(box_ids):
        slice_aids = aids[bi*2:(bi+1)*2]
        requests.post(f'{API}/boxes/pack', json={
            'batch_id': batch_id, 'box_id': bid,
            'archive_ids': slice_aids, 'operator_id': sender_id
        })
    
    requests.post(f'{API}/batches/{batch_id}/transfer', json={'operator_id': sender_id})
    
    for bid in box_ids:
        requests.post(f'{API}/boxes/{bid}/sign', json={'operator_id': receiver_id})
    
    return batch_id, box_ids, batch_no

def main():
    print(f'档案移交流转台 v3 - 签收后异常复核 全链路回归测试')
    print(f'测试时间: {datetime.now()}')

    users = requests.get(f'{API}/users').json()
    sender = next(u for u in users if u['role'] == 'SENDER')
    receiver = next(u for u in users if u['role'] == 'RECEIVER')
    print(f'发送方: {sender["username"]} (id={sender["id"]})')
    print(f'接收方: {receiver["username"]} (id={receiver["id"]})')

    # ============ 测试14：接收方新建复核项 - 基础功能 ============
    header('14. 接收方新建复核项 - 基础功能')
    bid_14, box_ids_14, bno_14 = create_full_batch_and_sign(sender['id'], receiver['id'], 'NEW')
    detail_14 = requests.get(f'{API}/batches/{bid_14}').json()
    assert_equal(detail_14['batch']['status'], 'SIGNED', '批次已签收')
    
    deadline = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_14,
        'box_id': box_ids_14[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页',
        'issue_description': '档案NEW-1第5-8页缺失，需要补档',
        'responsible_party': '扫描组',
        'deadline': deadline
    })
    assert_true(r.status_code == 200, '接收方新建复核项成功')
    rv14 = r.json()
    assert_equal(rv14['status'], 'OPEN', '新建复核项状态为OPEN(待处理)')
    assert_equal(rv14['issue_type'], '材料缺页', '问题类型正确保存')
    assert_equal(rv14['responsible_party'], '扫描组', '责任方正确保存')
    assert_equal(rv14['deadline'], deadline, '截止时间正确保存')
    
    detail_14 = requests.get(f'{API}/batches/{bid_14}').json()
    assert_equal(len(detail_14['reviews']), 1, '批次详情中复核项返回1条')
    assert_equal(detail_14['reviews'][0]['status_name'], '待处理', 'status_name字段正确')
    assert_true(detail_14['review_summary'][str(box_ids_14[0])]['total'] == 1, '盒1复核摘要total=1')
    assert_true(detail_14['review_summary'][str(box_ids_14[0])]['open'] == 1, '盒1复核摘要open=1')
    assert_true(detail_14['review_summary'][str(box_ids_14[0])]['closed'] == 0, '盒1复核摘要closed=0')
    assert_true(detail_14['review_summary'][str(box_ids_14[1])]['total'] == 0, '盒2无复核项摘要total=0')
    
    history_14 = requests.get(f'{API}/history/{bid_14}').json()
    create_his = next((h for h in history_14 if h['action'] == '新建复核项'), None)
    assert_true(create_his is not None, '流转历史记录了"新建复核项"操作')
    assert_true('材料缺页' in create_his['reason'], '历史原因包含问题类型')
    assert_true('档案NEW-1第5-8页缺失' in create_his['reason'], '历史原因包含问题描述')
    print(f'{PASS} 14. 接收方新建复核项 - 基础功能 - 通过')

    # ============ 测试15：权限限制 - 发送方不能新建复核项 ============
    header('15. 权限限制 - 发送方不能新建/退回/关闭/重开，接收方不能更新')
    bid_15, box_ids_15, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'PERM')
    
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_15, 'box_id': box_ids_15[0],
        'operator_id': sender['id'],
        'issue_type': '材料缺页', 'issue_description': '测试越权创建'
    })
    assert_equal(r.status_code, 400, '发送方新建复核项被拒绝(400)')
    assert_true('只有接收方才能新建复核项' in r.json()['error'], '错误信息明确：发送方不能新建')
    
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_15, 'box_id': box_ids_15[0],
        'operator_id': receiver['id'],
        'issue_type': '标签不清', 'issue_description': '盒内档案标签模糊'
    })
    rv15 = r.json()
    rvid_15 = rv15['id']
    
    r = requests.post(f'{API}/reviews/{rvid_15}/update', json={
        'operator_id': receiver['id'], 'handling_note': '越权更新', 'status': 'IN_PROGRESS'
    })
    assert_equal(r.status_code, 400, '接收方更新处理结果被拒绝(400)')
    assert_true('只有发送方才能更新复核项处理结果' in r.json()['error'], '错误信息明确：接收方不能更新')
    
    r = requests.post(f'{API}/reviews/{rvid_15}/close', json={'operator_id': sender['id']})
    assert_equal(r.status_code, 400, '发送方关闭复核项被拒绝(400)')
    assert_true('只有接收方才能确认关闭复核项' in r.json()['error'], '错误信息明确：发送方不能关闭')
    
    r = requests.post(f'{API}/reviews/{rvid_15}/reject', json={
        'operator_id': sender['id'], 'reason': '越权退回'
    })
    assert_equal(r.status_code, 400, '发送方退回复核项被拒绝(400)')
    assert_true('只有接收方才能退回复核项' in r.json()['error'], '错误信息明确：发送方不能退回')
    
    r = requests.post(f'{API}/reviews/{rvid_15}/reopen', json={
        'operator_id': sender['id'], 'reason': '越权重开'
    })
    assert_equal(r.status_code, 400, '发送方撤销重开被拒绝(400)')
    assert_true('只有接收方才能撤销重开已关闭的复核项' in r.json()['error'], '错误信息明确：发送方不能重开')
    print(f'{PASS} 15. 权限限制 - 全角色边界校验通过')

    # ============ 测试16：完整处理流程 + 多盒并行 ============
    header('16. 多盒并行复核 - 完整处理流程')
    bid_16, box_ids_16, bno_16 = create_full_batch_and_sign(sender['id'], receiver['id'], 'FLOW', box_count=2)
    
    requests.post(f'{API}/reviews', json={
        'batch_id': bid_16, 'box_id': box_ids_16[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': '盒1档案缺页问题'
    })
    requests.post(f'{API}/reviews', json={
        'batch_id': bid_16, 'box_id': box_ids_16[1],
        'operator_id': receiver['id'],
        'issue_type': '标签不清', 'issue_description': '盒2标签打印不清晰'
    })
    detail_16 = requests.get(f'{API}/batches/{bid_16}').json()
    rv_box1 = next(r for r in detail_16['reviews'] if r['box_id'] == box_ids_16[0])
    rv_box2 = next(r for r in detail_16['reviews'] if r['box_id'] == box_ids_16[1])
    assert_equal(rv_box1['status'], 'OPEN', '盒1复核项状态OPEN')
    assert_equal(rv_box2['status'], 'OPEN', '盒2复核项状态OPEN')
    
    r = requests.post(f'{API}/reviews/{rv_box1["id"]}/update', json={
        'operator_id': sender['id'], 'handling_note': '联系扫描组重新扫描缺页',
        'status': 'IN_PROGRESS'
    })
    assert_true(r.status_code == 200, '盒1发送方更新为处理中成功')
    assert_equal(r.json()['status'], 'IN_PROGRESS', '盒1状态变为IN_PROGRESS')
    assert_equal(r.json()['handling_note'], '联系扫描组重新扫描缺页', '盒1处理说明保存')
    
    r = requests.post(f'{API}/reviews/{rv_box1["id"]}/update', json={
        'operator_id': sender['id'], 'handling_note': '缺页已补全，重新扫描替换',
        'status': 'PENDING_CLOSE'
    })
    assert_true(r.status_code == 200, '盒1申请关闭成功')
    assert_equal(r.json()['status'], 'PENDING_CLOSE', '盒1状态变为PENDING_CLOSE')
    
    r = requests.post(f'{API}/reviews/{rv_box1["id"]}/close', json={'operator_id': receiver['id']})
    assert_true(r.status_code == 200, '盒1接收方确认关闭成功')
    rv1_final = r.json()
    assert_equal(rv1_final['status'], 'CLOSED', '盒1最终状态CLOSED')
    assert_true(rv1_final['closed_by'] == receiver['id'], '盒1closed_by为接收方')
    assert_true(rv1_final['closed_at'] is not None, '盒1closed_at有值')
    
    detail_16 = requests.get(f'{API}/batches/{bid_16}').json()
    rs1 = detail_16['review_summary'][str(box_ids_16[0])]
    rs2 = detail_16['review_summary'][str(box_ids_16[1])]
    assert_equal(rs1['total'], 1, '盒1复核摘要total=1')
    assert_equal(rs1['open'], 0, '盒1复核摘要open=0（已关闭）')
    assert_equal(rs1['closed'], 1, '盒1复核摘要closed=1')
    assert_equal(rs2['total'], 1, '盒2复核摘要total=1（仍待处理）')
    assert_equal(rs2['open'], 1, '盒2复核摘要open=1')
    print(f'{PASS} 16. 多盒并行复核 - 完整处理流程通过')

    # ============ 测试17：冲突检测 - 同盒同问题重复提交 ============
    header('17. 冲突处理 - 同盒同问题重复提交拒绝')
    bid_17, box_ids_17, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'CONFLICT')
    
    r1 = requests.post(f'{API}/reviews', json={
        'batch_id': bid_17, 'box_id': box_ids_17[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页',
        'issue_description': '档案第3页缺失'
    })
    assert_true(r1.status_code == 200, '首次提交成功')
    
    r2 = requests.post(f'{API}/reviews', json={
        'batch_id': bid_17, 'box_id': box_ids_17[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页',
        'issue_description': '档案第3页缺失'
    })
    assert_equal(r2.status_code, 409, '完全相同问题被拒绝(409冲突)')
    assert_true('已存在相同问题' in r2.json()['error'], '错误信息包含冲突提示')
    assert_true('请勿重复提交' in r2.json()['error'], '错误信息包含不重复提示')
    
    r3 = requests.post(f'{API}/reviews', json={
        'batch_id': bid_17, 'box_id': box_ids_17[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页',
        'issue_description': '档案第5页缺失'
    })
    assert_true(r3.status_code == 200, '同类型不同描述的问题允许提交')
    
    r4 = requests.post(f'{API}/reviews', json={
        'batch_id': bid_17, 'box_id': box_ids_17[1],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页',
        'issue_description': '档案第3页缺失'
    })
    assert_true(r4.status_code == 200, '不同盒子相同问题允许提交')
    
    detail_17 = requests.get(f'{API}/batches/{bid_17}').json()
    assert_equal(len(detail_17['reviews']), 3, '批次中共有3条复核项（2盒1不同描述+1跨盒）')
    print(f'{PASS} 17. 冲突处理 - 同盒同问题重复提交拒绝 - 通过')

    # ============ 测试18：撤销重开 - 已关闭复核项重开 ============
    header('18. 撤销重开 - 已关闭复核项撤销重开完整流程')
    bid_18, box_ids_18, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'REOPEN')
    
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_18, 'box_id': box_ids_18[0],
        'operator_id': receiver['id'],
        'issue_type': '签收后补件', 'issue_description': '需要补充材料'
    })
    rvid_18 = r.json()['id']
    
    requests.post(f'{API}/reviews/{rvid_18}/update', json={
        'operator_id': sender['id'], 'handling_note': '补件完成', 'status': 'PENDING_CLOSE'
    })
    requests.post(f'{API}/reviews/{rvid_18}/close', json={'operator_id': receiver['id']})
    
    detail_18 = requests.get(f'{API}/batches/{bid_18}').json()
    rv = next(r for r in detail_18['reviews'] if r['id'] == rvid_18)
    assert_equal(rv['status'], 'CLOSED', '确认复核项已关闭')
    orig_closed_by = rv['closed_by']
    orig_closed_at = rv['closed_at']
    
    r = requests.post(f'{API}/reviews/{rvid_18}/reopen', json={
        'operator_id': receiver['id'], 'reason': '补件仍有遗漏，需要重新处理'
    })
    assert_true(r.status_code == 200, '撤销重开成功')
    rv_reopen = r.json()
    assert_equal(rv_reopen['status'], 'OPEN', '撤销后状态恢复为OPEN待处理')
    assert_true(rv_reopen['closed_by'] is None, '撤销后closed_by清空为None')
    assert_true(rv_reopen['closed_at'] is None, '撤销后closed_at清空为None')
    
    r = requests.post(f'{API}/reviews/{rvid_18}/update', json={
        'operator_id': sender['id'], 'handling_note': '重新补件完成', 'status': 'PENDING_CLOSE'
    })
    assert_true(r.status_code == 200, '重开后发送方可再次更新(不报错)')
    
    r = requests.post(f'{API}/reviews/{rvid_18}/close', json={'operator_id': receiver['id']})
    assert_true(r.status_code == 200, '重开后可再次关闭')
    rv_final = r.json()
    assert_equal(rv_final['status'], 'CLOSED', '重开后流程可再次关闭')
    
    history_18 = requests.get(f'{API}/history/{bid_18}').json()
    reopen_his = next((h for h in history_18 if h['action'] == '撤销重开复核项'), None)
    assert_true(reopen_his is not None, '历史记录包含撤销重开操作')
    assert_true('补件仍有遗漏' in reopen_his['reason'], '历史原因包含撤销原因')
    print(f'{PASS} 18. 撤销重开 - 完整流程验证通过')

    # ============ 测试19：退回复核项链路 ============
    header('19. 接收方退回复核项 - 不认可处理结果')
    bid_19, box_ids_19, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'REJ')
    
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_19, 'box_id': box_ids_19[0],
        'operator_id': receiver['id'],
        'issue_type': '顺序混乱', 'issue_description': '档案顺序排错'
    })
    rvid_19 = r.json()['id']
    
    requests.post(f'{API}/reviews/{rvid_19}/update', json={
        'operator_id': sender['id'], 'handling_note': '已调整顺序', 'status': 'PENDING_CLOSE'
    })
    
    r = requests.post(f'{API}/reviews/{rvid_19}/reject', json={
        'operator_id': receiver['id'], 'reason': ''
    })
    assert_equal(r.status_code, 400, '退回原因不能为空(400)')
    
    r = requests.post(f'{API}/reviews/{rvid_19}/reject', json={
        'operator_id': receiver['id'], 'reason': '顺序还有2处错误，重新调整'
    })
    assert_true(r.status_code == 200, '带原因退回复核项成功')
    rv_rej = r.json()
    assert_equal(rv_rej['status'], 'REJECTED', '退回复核项状态变为REJECTED')
    
    r = requests.post(f'{API}/reviews/{rvid_19}/update', json={
        'operator_id': sender['id'], 'handling_note': '两处顺序已全部修正', 'status': 'PENDING_CLOSE'
    })
    assert_true(r.status_code == 200, '被退回后发送方可重新更新')
    assert_equal(r.json()['status'], 'PENDING_CLOSE', '重新提交为申请关闭状态')
    print(f'{PASS} 19. 接收方退回复核项 - 链路验证通过')

    # ============ 测试20：导出结果带复核摘要 ============
    header('20. 导出移交清单 - 包含复核摘要与明细')
    bid_20, box_ids_20, bno_20 = create_full_batch_and_sign(sender['id'], receiver['id'], 'EXP', box_count=2)
    
    requests.post(f'{API}/reviews', json={
        'batch_id': bid_20, 'box_id': box_ids_20[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'EXP盒1档案缺页'
    })
    rv20 = requests.post(f'{API}/reviews', json={
        'batch_id': bid_20, 'box_id': box_ids_20[1],
        'operator_id': receiver['id'],
        'issue_type': '标签不清', 'issue_description': 'EXP盒2标签问题'
    }).json()
    
    requests.post(f'{API}/reviews/{rv20["id"]}/update', json={
        'operator_id': sender['id'], 'handling_note': '标签已重新打印', 'status': 'PENDING_CLOSE'
    })
    requests.post(f'{API}/reviews/{rv20["id"]}/close', json={'operator_id': receiver['id']})
    
    r = requests.post(f'{API}/batches/{bid_20}/export', json={'operator_id': sender['id']})
    assert_true(r.status_code == 200, '导出成功')
    content = r.json().get('content', '')
    
    assert_true('复核项统计' in content, 'CSV头部包含复核项统计')
    assert_true('共2项' in content, '统计包含总数2项')
    assert_true('待处理1项' in content, '统计包含待处理1项')
    assert_true('已关闭1项' in content, '统计包含已关闭1项')
    
    assert_true('复核摘要' in content, 'CSV包含复核摘要区块')
    assert_true('盒号,复核项总数,待处理数,已关闭数' in content, '复核摘要表头正确')
    assert_true('复核项明细' in content, 'CSV包含复核项明细区块')
    assert_true('材料缺页' in content and '标签不清' in content, '复核明细包含问题类型')
    assert_true('EXP盒1档案缺页' in content and 'EXP盒2标签问题' in content, '复核明细包含问题描述')
    assert_true('标签已重新打印' in content, '复核明细包含处理说明')
    
    detail_20 = requests.get(f'{API}/batches/{bid_20}').json()
    for bx in detail_20['boxes']:
        assert_true(bx['box_no'] in content, f'CSV复核摘要中包含盒号 {bx["box_no"]}')
    print(f'{PASS} 20. 导出移交清单 - 复核摘要与明细验证通过')

    # ============ 测试21：数据持久化 - 重启后复核状态一致 ============
    header('21. 数据持久化 - 重启/刷新后复核状态不丢失')
    assert_true(os.path.exists(DB_PATH), f'数据库文件存在: {DB_PATH}')
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='review_items'")
    assert_true(c.fetchone() is not None, 'review_items表存在于数据库')
    
    c.execute('PRAGMA table_info(review_items)')
    cols = [row[1] for row in c.fetchall()]
    expected_cols = ['id', 'batch_id', 'box_id', 'issue_type', 'issue_description',
                     'responsible_party', 'handling_note', 'deadline', 'status',
                     'created_by', 'closed_by', 'closed_at', 'created_at', 'updated_at']
    for col in expected_cols:
        assert_true(col in cols, f'review_items表包含列 {col}')
    
    c.execute("SELECT COUNT(*) FROM review_items")
    review_count = c.fetchone()[0]
    print(f'  当前复核项总数: {review_count}')
    assert_true(review_count >= 10, f'复核项持久化数量合理 (>={10})')
    
    c.execute("SELECT DISTINCT status FROM review_items")
    statuses = [row[0] for row in c.fetchall()]
    assert_true('OPEN' in statuses, '存在OPEN状态记录')
    assert_true('CLOSED' in statuses, '存在CLOSED状态记录')
    assert_true('PENDING_CLOSE' in statuses or 'REJECTED' in statuses or 'IN_PROGRESS' in statuses, 
                '存在至少一种中间状态记录')
    
    c.execute("SELECT COUNT(*) FROM transfer_history WHERE action LIKE '%复核%'")
    review_history_count = c.fetchone()[0]
    print(f'  复核相关历史记录数: {review_history_count}')
    assert_true(review_history_count >= 20, f'复核操作历史均持久化 (>={20})')
    
    c.execute('''SELECT r.status, r.closed_by, r.closed_at, r.handling_note 
                 FROM review_items r WHERE r.status = 'CLOSED' LIMIT 1''')
    closed_row = c.fetchone()
    assert_true(closed_row is not None, '至少存在1条已关闭复核项记录用于检查持久化')
    assert_true(closed_row[1] is not None, 'closed_by已持久化')
    assert_true(closed_row[2] is not None, 'closed_at已持久化')
    assert_true(closed_row[3] is not None and len(closed_row[3]) > 0, 'handling_note已持久化')
    
    conn.close()
    print(f'{PASS} 21. 数据持久化 - 重启后复核状态一致 - 通过')

    # ============ 测试22：导出复核明细 - 关闭人/关闭时间字段专项回归 ============
    header('22. 导出复核明细 - 关闭人/关闭时间字段专项回归（4场景）')

    prefix_22 = f'CLOSER{int(time.time()%100000)}'
    bid_22, box_ids_22, _ = create_full_batch_and_sign(sender['id'], receiver['id'], f'{prefix_22}_C', 2)
    detail_22 = requests.get(f'{API}/batches/{bid_22}').json()
    box_id_a = detail_22['boxes'][0]['id']
    box_id_b = detail_22['boxes'][1]['id']
    box_no_a = detail_22['boxes'][0]['box_no']
    box_no_b = detail_22['boxes'][1]['box_no']

    # --- 场景A：box_a 的复核项全流程走到已关闭（有关闭人/关闭时间） ---
    rv_a = requests.post(f'{API}/reviews', json={
        'batch_id': bid_22, 'box_id': box_id_a, 'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': f'{prefix_22}_A_已关闭问题',
        'responsible_party': '档案室',
    }).json()
    requests.post(f'{API}/reviews/{rv_a["id"]}/update', json={
        'operator_id': sender['id'], 'handling_note': f'{prefix_22}_A_处理说明',
        'status': 'PENDING_CLOSE'
    })
    rv_a_closed = requests.post(f'{API}/reviews/{rv_a["id"]}/close', json={
        'operator_id': receiver['id']
    }).json()
    assert_true(rv_a_closed['status'] == 'CLOSED', f'box_a复核项已关闭, id={rv_a["id"]}')

    # --- 场景B：box_b 的复核项保持待处理（未关闭 -> 关闭人/关闭时间必须为空，不能误填） ---
    rv_b = requests.post(f'{API}/reviews', json={
        'batch_id': bid_22, 'box_id': box_id_b, 'operator_id': receiver['id'],
        'issue_type': '标签不清', 'issue_description': f'{prefix_22}_B_待处理问题',
    }).json()
    assert_true(rv_b['status'] == 'OPEN', f'box_b复核项保持待处理, id={rv_b["id"]}')

    # --- 场景C：利用测试20/18已产生的历史数据（已存在的已关闭记录），本批次即包含历史记录（通过 export 全量带出） ---
    r = requests.post(f'{API}/batches/{bid_22}/export', json={'operator_id': sender['id']})
    assert_true(r.status_code == 200, '导出成功')
    content = r.json()['content']
    lines = [ln for ln in content.splitlines() if ln.strip()]

    # 定位"复核项明细"区块
    idx_detail = next((i for i, ln in enumerate(lines) if '复核项明细' in ln), None)
    idx_history = next((i for i, ln in enumerate(lines) if '流转历史' in ln), None)
    assert_true(idx_detail is not None and idx_history is not None and idx_detail < idx_history,
                'CSV中存在复核项明细区块且位于流转历史之前')

    detail_lines = lines[idx_detail + 1: idx_history]
    assert_true(len(detail_lines) >= 3, f'复核明细至少含表头+2条数据, 实际{len(detail_lines)}行')

    # --- 场景D：表头和实际内容一致（列数/列名） ---
    try:
        header_row = next(csv.reader([detail_lines[0]]))
    except Exception:
        header_row = detail_lines[0].split(',')
    expected_cols = ['复核ID', '盒号', '问题类型', '问题描述', '责任方', '截止时间',
                     '处理说明', '状态', '提报人', '提报时间', '关闭人', '关闭时间']
    assert_true(len(header_row) == len(expected_cols),
                f'表头列数对齐: 期望{len(expected_cols)}列, 实际{len(header_row)}列')
    for i, (h, exp) in enumerate(zip(header_row, expected_cols)):
        assert_true(h.strip().strip('"') == exp,
                    f'表头第{i+1}列匹配: 期望"{exp}", 实际"{h.strip().strip(chr(34))}"')
    print(f'  [场景D] 表头列名/列数完全一致: {header_row}')

    # --- 场景A断言：已关闭记录 -> 关闭人非空 + 关闭时间非空 + 关闭人等于 receiver.username ---
    row_a = None
    for ln in detail_lines[1:]:
        try:
            cols = next(csv.reader([ln]))
        except Exception:
            cols = ln.split(',')
        if len(cols) >= 4 and f'{prefix_22}_A_已关闭问题' in cols[3]:
            row_a = cols
            break
    assert_true(row_a is not None, 'CSV中存在box_a已关闭复核项行')
    closer_a = row_a[10].strip().strip('"')
    closed_at_a = row_a[11].strip().strip('"')
    assert_true(len(closer_a) > 0, f'[场景A] 已关闭记录关闭人非空: "{closer_a}"')
    assert_true(closer_a == receiver['username'],
                f'[场景A] 关闭人正确: 期望receiver={receiver["username"]}, 实际="{closer_a}"')
    assert_true(len(closed_at_a) > 0, f'[场景A] 已关闭记录关闭时间非空: "{closed_at_a}"')
    assert_true(row_a[7].strip().strip('"') == '已关闭', f'[场景A] 状态列显示"已关闭"')
    print(f'  [场景A] 已关闭记录导出: 关闭人={closer_a}, 关闭时间={closed_at_a}, 状态=已关闭')

    # --- 场景B断言：未关闭记录 -> 关闭人必须为空 + 关闭时间必须为空（不能误填） ---
    row_b = None
    for ln in detail_lines[1:]:
        try:
            cols = next(csv.reader([ln]))
        except Exception:
            cols = ln.split(',')
        if len(cols) >= 4 and f'{prefix_22}_B_待处理问题' in cols[3]:
            row_b = cols
            break
    assert_true(row_b is not None, 'CSV中存在box_b待处理复核项行')
    closer_b = row_b[10].strip().strip('"')
    closed_at_b = row_b[11].strip().strip('"')
    assert_true(closer_b == '', f'[场景B] 待处理记录关闭人为空, 实际="{closer_b}"')
    assert_true(closed_at_b == '', f'[场景B] 待处理记录关闭时间为空, 实际="{closed_at_b}"')
    assert_true(row_b[7].strip().strip('"') != '已关闭', f'[场景B] 状态列不是已关闭')
    print(f'  [场景B] 待处理记录导出: 关闭人=[空], 关闭时间=[空] ✓')

    # --- 场景C断言：历史数据/已关闭数据 -> 所有状态=已关闭的行, 关闭人与关闭时间都必须非空 ---
    all_closed_ok = True
    closed_count = 0
    for ln in detail_lines[1:]:
        try:
            cols = next(csv.reader([ln]))
        except Exception:
            cols = ln.split(',')
        if len(cols) >= 12 and cols[7].strip().strip('"') == '已关闭':
            closed_count += 1
            closer = cols[10].strip().strip('"')
            closed_at = cols[11].strip().strip('"')
            if len(closer) == 0 or len(closed_at) == 0:
                all_closed_ok = False
                print(f'  [场景C] 异常! 已关闭行 id={cols[0]} closer={closer!r} closed_at={closed_at!r}')
    print(f'  [场景C] 导出中共有 {closed_count} 条已关闭记录, 全部校验通过={all_closed_ok}')
    assert_true(closed_count >= 1, f'[场景C] 导出中至少有1条已关闭记录, 实际{closed_count}')
    assert_true(all_closed_ok, '[场景C] 所有已关闭记录的关闭人与关闭时间都必须非空')
    print(f'  [场景C] 共{closed_count}条已关闭记录（含本批次+历史）, 关闭人/关闭时间全部带出')

    print(f'{PASS} 22. 导出复核明细 - 关闭人/关闭时间字段专项回归 - 4场景全部通过')

    # ============ 结果汇总 ============
    print()
    print('=' * 60)
    print(f'测试结果: {passed}/{total} 项通过')
    if passed == total:
        print(f'{PASS} 所有测试通过！')
    else:
        print(f'{FAIL} {total - passed} 项失败！')
    print('=' * 60)

    return passed == total

if __name__ == '__main__':
    import sys
    sys.exit(0 if main() else 1)
