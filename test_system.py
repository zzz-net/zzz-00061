import requests
import json
import os
import sys

# ===== Windows 控制台编码自动适配（不改变用户终端、不删字符、不改代码页）=====
if sys.platform.startswith('win') and sys.stdout.encoding:
    enc = sys.stdout.encoding.lower()
    if 'gbk' in enc or enc == 'cp936' or enc == '936':
        try:
            sys.stdout.reconfigure(errors='replace')
            sys.stderr.reconfigure(errors='replace')
        except Exception:
            pass
# ============================================================================

BASE_URL = 'http://127.0.0.1:5000/api'

def print_test(name, func):
    print('\n' + '='*60)
    print('测试: ' + name)
    print('='*60)
    try:
        result = func()
        if result:
            print('[OK] ' + name + ' - 通过')
        else:
            print('[FAIL] ' + name + ' - 失败')
        return result
    except Exception as e:
        print('[FAIL] ' + name + ' - 异常: ' + str(e))
        return False

def get_users():
    r = requests.get(BASE_URL + '/users')
    return r.json()

def test_1_main_flow():
    users = get_users()
    sender = [u for u in users if u['role'] == 'SENDER'][0]
    receiver = [u for u in users if u['role'] == 'RECEIVER'][0]
    print('发送方: ' + sender['username'] + ' (id=' + str(sender['id']) + ')')
    print('接收方: ' + receiver['username'] + ' (id=' + str(receiver['id']) + ')')

    batch_no = 'TEST-' + str(os.getpid())

    data = {
        'batch_no': batch_no,
        'description': '测试移交测试批次',
        'created_by': sender['id'],
        'archives': [
            {'archive_no': 'A001', 'title': '档案1', 'remark': '备注1'},
            {'archive_no': 'A002', 'title': '档案2', 'remark': '备注2'},
            {'archive_no': 'A003', 'title': '档案3', 'remark': '备注3'}
        ]
    }
    r = requests.post(BASE_URL + '/batches', json=data)
    assert r.status_code == 200, '登记失败: ' + str(r.json())
    batch = r.json()
    batch_id = batch['id']
    print('[OK] 批次登记成功: ' + batch_no + ' (id=' + str(batch_id) + ')')

    r = requests.get(BASE_URL + '/batches/' + str(batch_id))
    detail = r.json()
    archive_ids = [a['id'] for a in detail['archives']]
    print('[OK] 批次详情获取成功，共 ' + str(len(archive_ids)) + ' 份档案')

    r = requests.post(BASE_URL + '/boxes', json={
        'box_no': 'BOX-' + str(os.getpid()) + '-01',
        'batch_id': batch_id,
        'operator_id': sender['id']
    })
    assert r.status_code == 200, '创建盒子失败: ' + str(r.json())
    box1 = r.json()
    box1_id = box1['id']

    r = requests.post(BASE_URL + '/boxes', json={
        'box_no': 'BOX-' + str(os.getpid()) + '-02',
        'batch_id': batch_id,
        'operator_id': sender['id']
    })
    assert r.status_code == 200, '创建盒子失败: ' + str(r.json())
    box2 = r.json()
    box2_id = box2['id']
    print('[OK] 创建2个档案盒成功')

    r = requests.post(BASE_URL + '/boxes/pack', json={
        'batch_id': batch_id,
        'box_id': box1_id,
        'archive_ids': archive_ids[:2],
        'operator_id': sender['id']
    })
    assert r.status_code == 200, '装盒1失败: ' + str(r.json())

    r = requests.post(BASE_URL + '/boxes/pack', json={
        'batch_id': batch_id,
        'box_id': box2_id,
        'archive_ids': archive_ids[2:],
        'operator_id': sender['id']
    })
    assert r.status_code == 200, '装盒2失败: ' + str(r.json())
    print('[OK] 所有档案装盒成功')

    r = requests.post(BASE_URL + '/batches/' + str(batch_id) + '/transfer', json={
        'operator_id': sender['id']
    })
    assert r.status_code == 200, '移交失败: ' + str(r.json())
    print('[OK] 批次移交成功')

    r = requests.get(BASE_URL + '/batches/' + str(batch_id))
    detail = r.json()
    assert detail['batch']['status'] == 'TRANSFERRED', '状态错误: ' + detail['batch']['status']
    assert all(b['status'] == 'TRANSFERRED' for b in detail['boxes']), '盒子状态错误'
    print('[OK] 批次和盒子状态已更新为"已移交"')

    r = requests.post(BASE_URL + '/boxes/' + str(box1_id) + '/sign', json={
        'operator_id': receiver['id']
    })
    assert r.status_code == 200, '签收1失败: ' + str(r.json())
    print('[OK] 盒子1签收成功')

    r = requests.post(BASE_URL + '/boxes/' + str(box2_id) + '/sign', json={
        'operator_id': receiver['id']
    })
    assert r.status_code == 200, '签收2失败: ' + str(r.json())
    print('[OK] 盒子2签收成功')

    r = requests.get(BASE_URL + '/batches/' + str(batch_id))
    detail = r.json()
    assert detail['batch']['status'] == 'SIGNED', '批次状态错误: ' + detail['batch']['status']
    print('[OK] 批次状态已更新为"已签收"')

    r = requests.get(BASE_URL + '/history/' + str(batch_id))
    history = r.json()
    print('[OK] 流转历史共 ' + str(len(history)) + ' 条记录')
    for h in history[:3]:
        print('  - ' + h['timestamp'] + ': ' + h['action'] + ' by ' + h['operator_name'])

    r = requests.post(BASE_URL + '/batches/' + str(batch_id) + '/export', json={
        'operator_id': sender['id']
    })
    assert r.status_code == 200, '导出记录失败'
    print('[OK] 导出清单成功')

    r = requests.get(BASE_URL + '/export-records/' + str(batch_id))
    export_records = r.json()
    assert len(export_records) > 0, '没有导出记录'
    print('[OK] 导出记录已保存')

    r = requests.get(BASE_URL + '/batches/' + str(batch_id) + '/export')
    assert r.status_code == 200, '下载失败'
    assert '档案移交清单' in r.text, 'CSV内容不正确'
    print('[OK] CSV文件内容正确')

    return True

def test_2_duplicate_archive_no():
    users = get_users()
    sender = [u for u in users if u['role'] == 'SENDER'][0]

    data = {
        'batch_no': 'DUP-' + str(os.getpid()),
        'description': '重复档号测试',
        'created_by': sender['id'],
        'archives': [
            {'archive_no': 'D001', 'title': '档案1'},
            {'archive_no': 'D001', 'title': '档案2'}
        ]
    }
    r = requests.post(BASE_URL + '/batches', json=data)
    assert r.status_code == 400, '应该返回400，实际: ' + str(r.status_code)
    result = r.json()
    assert '重复档号' in result['error'], '错误信息不正确: ' + result['error']
    print('[OK] 重复档号被正确拒绝: ' + result['error'])
    return True

def test_3_sender_cannot_sign():
    users = get_users()
    sender = [u for u in users if u['role'] == 'SENDER'][0]
    receiver = [u for u in users if u['role'] == 'RECEIVER'][0]

    data = {
        'batch_no': 'SIGN-' + str(os.getpid()),
        'created_by': sender['id'],
        'archives': [{'archive_no': 'S001', 'title': '测试'}]
    }
    r = requests.post(BASE_URL + '/batches', json=data)
    batch_id = r.json()['id']

    r = requests.post(BASE_URL + '/boxes', json={
        'box_no': 'SBOX-' + str(os.getpid()),
        'batch_id': batch_id,
        'operator_id': sender['id']
    })
    box_id = r.json()['id']

    r = requests.get(BASE_URL + '/batches/' + str(batch_id))
    archive_id = r.json()['archives'][0]['id']

    r = requests.post(BASE_URL + '/boxes/pack', json={
        'batch_id': batch_id,
        'box_id': box_id,
        'archive_ids': [archive_id],
        'operator_id': sender['id']
    })

    r = requests.post(BASE_URL + '/batches/' + str(batch_id) + '/transfer', json={
        'operator_id': sender['id']
    })

    r = requests.post(BASE_URL + '/boxes/' + str(box_id) + '/sign', json={
        'operator_id': sender['id']
    })
    assert r.status_code == 400, '应该返回400，实际: ' + str(r.status_code)
    result = r.json()
    assert '发送方不能代替接收方签收' in result['error'], '错误信息不正确: ' + result['error']
    print('[OK] 发送方签收被正确拒绝: ' + result['error'])
    return True

def test_4_reject_and_revoke():
    users = get_users()
    sender = [u for u in users if u['role'] == 'SENDER'][0]
    receiver = [u for u in users if u['role'] == 'RECEIVER'][0]

    data = {
        'batch_no': 'REJ-' + str(os.getpid()),
        'created_by': sender['id'],
        'archives': [{'archive_no': 'R001', 'title': '测试'}]
    }
    r = requests.post(BASE_URL + '/batches', json=data)
    batch_id = r.json()['id']

    r = requests.post(BASE_URL + '/boxes', json={
        'box_no': 'RBOX-' + str(os.getpid()),
        'batch_id': batch_id,
        'operator_id': sender['id']
    })
    box_id = r.json()['id']

    r = requests.get(BASE_URL + '/batches/' + str(batch_id))
    archive_id = r.json()['archives'][0]['id']

    r = requests.post(BASE_URL + '/boxes/pack', json={
        'batch_id': batch_id,
        'box_id': box_id,
        'archive_ids': [archive_id],
        'operator_id': sender['id']
    })

    r = requests.post(BASE_URL + '/batches/' + str(batch_id) + '/transfer', json={
        'operator_id': sender['id']
    })

    r = requests.post(BASE_URL + '/boxes/' + str(box_id) + '/reject', json={
        'operator_id': receiver['id'],
        'reason': ''
    })
    assert r.status_code == 400, '空原因应该被拒绝'
    print('[OK] 空退回原因被正确拒绝')

    r = requests.post(BASE_URL + '/boxes/' + str(box_id) + '/reject', json={
        'operator_id': receiver['id'],
        'reason': '档案有缺失'
    })
    assert r.status_code == 200, '退回失败: ' + str(r.json())
    print('[OK] 退回成功')

    r = requests.get(BASE_URL + '/batches/' + str(batch_id))
    detail = r.json()
    assert detail['boxes'][0]['status'] == 'REJECTED', '盒子状态错误'
    assert detail['batch']['status'] == 'REJECTED', '批次状态错误'
    print('[OK] 批次和盒子状态已更新为"已退回"')

    r = requests.post(BASE_URL + '/boxes/' + str(box_id) + '/revoke-reject', json={
        'operator_id': receiver['id']
    })
    assert r.status_code == 200, '撤销退回失败: ' + str(r.json())
    print('[OK] 撤销退回成功')

    r = requests.get(BASE_URL + '/batches/' + str(batch_id))
    detail = r.json()
    assert detail['boxes'][0]['status'] == 'TRANSFERRED', '盒子状态未恢复'
    assert detail['batch']['status'] == 'TRANSFERRED', '批次状态未恢复'
    print('[OK] 批次和盒子状态已恢复为"已移交"')

    r = requests.get(BASE_URL + '/history/' + str(batch_id))
    history = r.json()
    reject_record = [h for h in history if '退回原因' in (h.get('reason') or '')]
    revoke_record = [h for h in history if '撤销退回' in h['action']]
    assert len(reject_record) > 0 and len(revoke_record) > 0, '历史记录不完整'
    print('[OK] 退回和撤销退回历史记录完整')

    return True

def test_5_data_persistence():
    db_file = 'archive_transfer.db'
    assert os.path.exists(db_file), '数据库文件不存在'
    print('[OK] 数据库文件存在: ' + db_file)

    import sqlite3
    conn = sqlite3.connect(db_file)
    c = conn.cursor()

    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in c.fetchall()]
    expected_tables = ['users', 'batches', 'archives', 'boxes', 'archive_box_mapping', 'transfer_history', 'export_records']
    for t in expected_tables:
        assert t in tables, '缺少表: ' + t
    print('[OK] 所有数据库表存在')

    c.execute('SELECT COUNT(*) FROM transfer_history')
    history_count = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM export_records')
    export_count = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM archive_box_mapping')
    mapping_count = c.fetchone()[0]
    print('[OK] 流转历史: ' + str(history_count) + ' 条, 导出记录: ' + str(export_count) + ' 条, 盒号映射: ' + str(mapping_count) + ' 条')

    conn.close()
    return True

if __name__ == '__main__':
    print('档案移交流转台 - 系统功能测试')
    print('测试时间: ' + str(__import__('datetime').datetime.now()))

    results = []
    results.append(print_test('1. 主流程（登记->装盒->移交->签收->导出）', test_1_main_flow))
    results.append(print_test('2. 失败链路：重复档号被拒绝', test_2_duplicate_archive_no))
    results.append(print_test('3. 失败链路：发送方不能代替接收方签收', test_3_sender_cannot_sign))
    results.append(print_test('4. 失败链路：退回与撤销退回', test_4_reject_and_revoke))
    results.append(print_test('5. 数据持久化验证', test_5_data_persistence))

    print('\n' + '='*60)
    passed = sum(results)
    total = len(results)
    print('测试结果: ' + str(passed) + '/' + str(total) + ' 项通过')
    if passed == total:
        print('[OK] 所有测试通过！')
    else:
        print('[WARN] 有 ' + str(total - passed) + ' 项测试失败')
    print('='*60)
    sys.exit(0 if passed == total else 1)
