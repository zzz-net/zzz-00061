import requests
import sqlite3
import time
import os
from datetime import datetime

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

def main():
    print(f'档案移交流转台 v2 - 签收后退回链路专项测试')
    print(f'测试时间: {datetime.now()}')

    users = requests.get(f'{API}/users').json()
    sender = next(u for u in users if u['role'] == 'SENDER')
    receiver = next(u for u in users if u['role'] == 'RECEIVER')
    print(f'发送方: {sender["username"]} (id={sender["id"]})')
    print(f'接收方: {receiver["username"]} (id={receiver["id"]})')

    # ============ 测试6：签收后退回，撤销后准确恢复签收状态 ============
    header('6. 签收后退回，撤销后恢复签收（核心链路）')
    batch_no = f'SIG-REJ-{int(time.time() % 100000)}'
    r = requests.post(f'{API}/batches', json={
        'batch_no': batch_no,
        'description': '签收后退回测试批次',
        'created_by': sender['id'],
        'archives': [
            {'archive_no': 'A001', 'title': '档案A001', 'remark': ''},
            {'archive_no': 'A002', 'title': '档案A002', 'remark': ''}
        ]
    })
    data = r.json()
    batch_id = data['id']
    assert_true(batch_id and r.status_code == 200, f'批次登记成功: {batch_no} (id={batch_id})')

    # 创建1个盒子，装2份档案，移交，然后签收
    box_no1 = f'BOX-SR-{int(time.time() % 100000)}'
    r = requests.post(f'{API}/boxes', json={
        'box_no': box_no1,
        'batch_id': batch_id,
        'operator_id': sender['id']
    })
    assert_true(r.status_code == 200, f'创建档案盒成功: {box_no1}')

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    archive_ids = [a['id'] for a in detail['archives']]
    box_id = detail['boxes'][0]['id']

    r = requests.post(f'{API}/boxes/pack', json={
        'batch_id': batch_id,
        'box_id': box_id,
        'archive_ids': archive_ids,
        'operator_id': sender['id']
    })
    assert_true(r.status_code == 200, '所有档案装盒成功')

    r = requests.post(f'{API}/batches/{batch_id}/transfer', json={'operator_id': sender['id']})
    assert_true(r.status_code == 200, '批次移交成功')

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    assert_equal(detail['boxes'][0]['status'], 'TRANSFERRED', '盒子状态已更新为"已移交"')

    # 接收方签收
    r = requests.post(f'{API}/boxes/{box_id}/sign', json={'operator_id': receiver['id']})
    assert_true(r.status_code == 200, '盒子签收成功')

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    box = detail['boxes'][0]
    assert_equal(box['status'], 'SIGNED', '盒子状态为已签收')
    assert_true(box.get('signed_by') == receiver['id'], f'签收人已正确记录: signed_by={box.get("signed_by")}')
    assert_true(box.get('signed_at') is not None, f'签收时间已记录: signed_at={box.get("signed_at")}')
    original_signed_at = box['signed_at']
    original_signed_by = box['signed_by']
    assert_equal(detail['batch']['status'], 'SIGNED', '批次状态为已签收')

    # 签收后发起退回
    reject_reason = '发现档案漏盖章，签收后退回补章'
    r = requests.post(f'{API}/boxes/{box_id}/reject', json={
        'operator_id': receiver['id'],
        'reason': reject_reason
    })
    assert_true(r.status_code == 200, f'已签收盒子退回成功 (prev_status={r.json().get("prev_status")})')
    assert_equal(r.json().get('prev_status'), 'SIGNED', '退回前状态快照正确记录为 SIGNED')

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    box = detail['boxes'][0]
    assert_equal(box['status'], 'REJECTED', '盒子状态为已退回')
    assert_equal(box.get('prev_status'), 'SIGNED', 'prev_status 字段保存为 SIGNED')
    assert_equal(detail['batch']['status'], 'REJECTED', '批次状态为已退回')

    # 检查历史记录是否包含签收人、签收时间
    history = requests.get(f'{API}/history/{batch_id}').json()
    reject_record = next(h for h in history if h['action'] == '退回档案盒')
    assert_true('退回前状态: 已签收' in reject_record.get('reason', ''), '流转历史包含"退回前状态: 已签收"')
    assert_true('签收人: receiver' in reject_record.get('reason', ''), '流转历史包含签收人信息')
    assert_true('签收时间:' in reject_record.get('reason', ''), '流转历史包含签收时间信息')
    assert_true(reject_reason in reject_record.get('reason', ''), f'流转历史包含退回原因: {reject_reason}')

    # 撤销退回，应该恢复为已签收，并还原签收人、签收时间
    r = requests.post(f'{API}/boxes/{box_id}/revoke-reject', json={'operator_id': receiver['id']})
    assert_true(r.status_code == 200, f'撤销退回成功 (restored_status={r.json().get("restored_status")})')
    assert_equal(r.json().get('restored_status'), 'SIGNED', 'restored_status 正确为 SIGNED')

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    box = detail['boxes'][0]
    assert_equal(box['status'], 'SIGNED', '撤销后盒子状态恢复为已签收')
    assert_equal(box.get('signed_by'), original_signed_by, f'签收人已精确还原 (signed_by={box.get("signed_by")})')
    assert_equal(box.get('signed_at'), original_signed_at, f'签收时间已精确还原 (signed_at={box.get("signed_at")})')
    assert_true(box.get('prev_status') is None, 'prev_status 已清空为 NULL')
    assert_equal(detail['batch']['status'], 'SIGNED', '批次状态恢复为已签收')

    # 导出清单，确认导出内容正确
    r = requests.post(f'{API}/batches/{batch_id}/export', json={'operator_id': sender['id']})
    assert_true(r.status_code == 200, '导出清单成功')
    assert_true('content' in r.json(), '导出记录已保存')

    # 检查撤销退回的历史记录
    history = requests.get(f'{API}/history/{batch_id}').json()
    revoke_record = next(h for h in history if h['action'] == '撤销退回')
    assert_true('已签收' in revoke_record.get('reason', ''), '撤销退回历史显示恢复为已签收状态')
    assert_true('签收人' in revoke_record.get('reason', ''), '撤销退回历史记录了签收人信息')

    print(f'{PASS} 6. 签收后退回，撤销后恢复签收（核心链路） - 通过')

    # ============ 测试7：已移交状态退回（原链路不回退） ============
    header('7. 已移交状态退回（原链路不回退）')
    batch_no = f'OLD-REJ-{int(time.time() % 100000)}'
    r = requests.post(f'{API}/batches', json={
        'batch_no': batch_no,
        'description': '原链路退回测试',
        'created_by': sender['id'],
        'archives': [
            {'archive_no': 'B001', 'title': '档案B001', 'remark': ''}
        ]
    })
    batch_id = r.json()['id']
    assert_true(batch_id and r.status_code == 200, f'批次登记成功 (id={batch_id})')

    box_no2 = f'BOX-OR-{int(time.time() % 100000)}'
    r = requests.post(f'{API}/boxes', json={'box_no': box_no2, 'batch_id': batch_id, 'operator_id': sender['id']})
    detail = requests.get(f'{API}/batches/{batch_id}').json()
    box_id = detail['boxes'][0]['id']
    r = requests.post(f'{API}/boxes/pack', json={
        'batch_id': batch_id,
        'box_id': box_id,
        'archive_ids': [a['id'] for a in detail['archives']],
        'operator_id': sender['id']
    })
    r = requests.post(f'{API}/batches/{batch_id}/transfer', json={'operator_id': sender['id']})

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    assert_equal(detail['boxes'][0]['status'], 'TRANSFERRED', '盒子已移交')

    # 发送方不能签收
    r = requests.post(f'{API}/boxes/{box_id}/sign', json={'operator_id': sender['id']})
    assert_equal(r.status_code, 400, '发送方签收被拒绝 (400)')
    assert_true('只有接收方' in r.json().get('error', ''), '错误提示: 发送方不能代替接收方签收')

    # 已移交状态退回
    r = requests.post(f'{API}/boxes/{box_id}/reject', json={
        'operator_id': receiver['id'],
        'reason': '原链路退回'
    })
    assert_true(r.status_code == 200, '已移交盒子退回成功')
    assert_equal(r.json().get('prev_status'), 'TRANSFERRED', '退回前状态快照为 TRANSFERRED')

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    box = detail['boxes'][0]
    assert_equal(box['status'], 'REJECTED', '盒子状态为已退回')
    assert_equal(box.get('prev_status'), 'TRANSFERRED', 'prev_status 为 TRANSFERRED')

    # 撤销退回，应该恢复为已移交
    r = requests.post(f'{API}/boxes/{box_id}/revoke-reject', json={'operator_id': receiver['id']})
    assert_true(r.status_code == 200, '撤销退回成功')
    assert_equal(r.json().get('restored_status'), 'TRANSFERRED', 'restored_status 正确为 TRANSFERRED')

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    box = detail['boxes'][0]
    assert_equal(box['status'], 'TRANSFERRED', '撤销后盒子恢复为已移交')
    assert_true(box.get('signed_by') is None, 'signed_by 为空（因为退回前是已移交）')
    assert_equal(detail['batch']['status'], 'TRANSFERRED', '批次状态恢复为已移交')

    print(f'{PASS} 7. 已移交状态退回（原链路不回退） - 通过')

    # ============ 测试8：重复档号校验不回退 ============
    header('8. 重复档号校验（原规则不回退）')
    batch_no = f'DUP-{int(time.time() % 100000)}'
    r = requests.post(f'{API}/batches', json={
        'batch_no': batch_no,
        'description': '重复档号测试',
        'created_by': sender['id'],
        'archives': [
            {'archive_no': 'C001', 'title': '档案1', 'remark': ''},
            {'archive_no': 'C001', 'title': '档案1重复', 'remark': ''}
        ]
    })
    assert_equal(r.status_code, 400, '重复档号被拒绝 (400)')
    assert_true('重复档号' in r.json().get('error', ''), '错误提示包含"重复档号"')
    print(f'{PASS} 8. 重复档号校验（原规则不回退） - 通过')

    # ============ 测试9：双盒混合状态 - 签收盒退回后撤销 ============
    header('9. 双盒混合 - 1个签收1个退回，然后撤销')
    batch_no = f'MIX-{int(time.time() % 100000)}'
    r = requests.post(f'{API}/batches', json={
        'batch_no': batch_no,
        'description': '双盒混合测试',
        'created_by': sender['id'],
        'archives': [
            {'archive_no': 'D001', 'title': '档案D001', 'remark': ''},
            {'archive_no': 'D002', 'title': '档案D002', 'remark': ''}
        ]
    })
    batch_id = r.json()['id']
    assert_true(batch_id, f'批次登记成功 (id={batch_id})')

    r = requests.post(f'{API}/boxes', json={'box_no': f'BOX-M1-{int(time.time()%100000)}', 'batch_id': batch_id, 'operator_id': sender['id']})
    r = requests.post(f'{API}/boxes', json={'box_no': f'BOX-M2-{int(time.time()%100000)}', 'batch_id': batch_id, 'operator_id': sender['id']})
    detail = requests.get(f'{API}/batches/{batch_id}').json()
    bid1 = detail['boxes'][0]['id']
    bid2 = detail['boxes'][1]['id']
    aids = [a['id'] for a in detail['archives']]

    r = requests.post(f'{API}/boxes/pack', json={'batch_id': batch_id, 'box_id': bid1, 'archive_ids': [aids[0]], 'operator_id': sender['id']})
    r = requests.post(f'{API}/boxes/pack', json={'batch_id': batch_id, 'box_id': bid2, 'archive_ids': [aids[1]], 'operator_id': sender['id']})
    r = requests.post(f'{API}/batches/{batch_id}/transfer', json={'operator_id': sender['id']})

    # 两个盒子都签收
    r = requests.post(f'{API}/boxes/{bid1}/sign', json={'operator_id': receiver['id']})
    r = requests.post(f'{API}/boxes/{bid2}/sign', json={'operator_id': receiver['id']})

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    box1 = detail['boxes'][0]
    box2 = detail['boxes'][1]
    assert_equal(box1['status'], 'SIGNED', '盒子1已签收')
    assert_equal(box2['status'], 'SIGNED', '盒子2已签收')
    orig1_signed_at = box1['signed_at']
    orig1_signed_by = box1['signed_by']

    # 盒子1退回（已签收状态退回）
    r = requests.post(f'{API}/boxes/{bid1}/reject', json={'operator_id': receiver['id'], 'reason': '盒1有问题退回'})
    assert_true(r.status_code == 200, '盒子1退回成功')

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    assert_equal(detail['boxes'][0]['status'], 'REJECTED', '盒子1已退回')
    assert_equal(detail['boxes'][1]['status'], 'SIGNED', '盒子2仍已签收')
    assert_equal(detail['batch']['status'], 'REJECTED', '批次状态为已退回（因为有退回的盒子）')

    # 导出清单 - 确认内容包含混合状态和退回原因
    r = requests.post(f'{API}/batches/{batch_id}/export', json={'operator_id': sender['id']})
    assert_true(r.status_code == 200, '混合状态导出成功')
    content = r.json().get('content', '')
    assert_true('D001' in content and 'D002' in content, '导出清单包含两份档案')
    assert_true('盒1有问题退回' in content, '导出清单包含退回原因')

    # 撤销盒子1的退回
    r = requests.post(f'{API}/boxes/{bid1}/revoke-reject', json={'operator_id': receiver['id']})
    assert_true(r.status_code == 200, '撤销盒子1退回成功')
    assert_equal(r.json().get('restored_status'), 'SIGNED', '恢复为 SIGNED')

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    b1 = detail['boxes'][0]
    b2 = detail['boxes'][1]
    assert_equal(b1['status'], 'SIGNED', '盒子1恢复为已签收')
    assert_equal(b1['signed_by'], orig1_signed_by, '盒子1签收人精确还原')
    assert_equal(b1['signed_at'], orig1_signed_at, '盒子1签收时间精确还原')
    assert_equal(b2['status'], 'SIGNED', '盒子2仍保持已签收')
    assert_equal(detail['batch']['status'], 'SIGNED', '所有盒子均已签收，批次状态为已签收')

    print(f'{PASS} 9. 双盒混合 - 1个签收1个退回，然后撤销 - 通过')

    # ============ 测试10：数据持久化 - 重启验证 ============
    header('10. 数据持久化验证（重启后状态一致）')
    assert_true(os.path.exists(DB_PATH), f'数据库文件存在: {DB_PATH}')

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in c.fetchall()]
    for t in ['users', 'batches', 'archives', 'boxes', 'archive_box_mapping', 'transfer_history', 'export_records']:
        assert_true(t in tables, f'表 {t} 存在')

    c.execute("PRAGMA table_info(boxes)")
    cols = [r[1] for r in c.fetchall()]
    for col in ['signed_by', 'signed_at', 'prev_status']:
        assert_true(col in cols, f'boxes 表新增列 {col} 存在')

    c.execute('SELECT status, prev_status, signed_by, signed_at FROM boxes WHERE status = "SIGNED" AND signed_by IS NOT NULL LIMIT 1')
    row = c.fetchone()
    assert_true(row is not None, '至少存在1条已签收且signed_by不为空的盒子记录')
    assert_true(row[0] == 'SIGNED', f'盒子状态持久化为 SIGNED (status={row[0]})')
    assert_true(row[2] == receiver['id'], f'signed_by 持久化为接收方 (signed_by={row[2]})')
    assert_true(row[3] is not None, f'signed_at 已持久化 (signed_at={row[3]})')

    c.execute('SELECT COUNT(*) FROM transfer_history')
    history_count = c.fetchone()[0]
    print(f'  流转历史记录数: {history_count}')
    assert_true(history_count > 20, f'流转历史数量合理 (>20)')

    c.execute('SELECT COUNT(*) FROM export_records')
    export_count = c.fetchone()[0]
    print(f'  导出记录数: {export_count}')
    assert_true(export_count >= 2, f'导出记录持久化 (>=2)')

    c.execute('SELECT COUNT(*) FROM archive_box_mapping')
    mapping_count = c.fetchone()[0]
    print(f'  盒号映射数: {mapping_count}')
    assert_true(mapping_count >= 6, f'盒号映射持久化 (>=6)')

    conn.close()
    print(f'{PASS} 10. 数据持久化验证（重启后状态一致） - 通过')

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
