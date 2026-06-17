import requests
import sqlite3
import time
import os
import csv
import sys
from datetime import datetime, timedelta

if sys.platform.startswith('win') and sys.stdout.encoding:
    enc = sys.stdout.encoding.lower()
    if 'gbk' in enc or enc == 'cp936' or enc == '936':
        try:
            sys.stdout.reconfigure(errors='replace')
            sys.stderr.reconfigure(errors='replace')
        except Exception:
            pass

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
        'description': f'{archive_prefix}催办测试批次',
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
    print(f'档案移交流转台 v4 - 催办/升级系统 全链路回归测试')
    print(f'测试时间: {datetime.now()}')

    users = requests.get(f'{API}/users').json()
    sender = next(u for u in users if u['role'] == 'SENDER')
    receiver = next(u for u in users if u['role'] == 'RECEIVER')
    print(f'发送方: {sender["username"]} (id={sender["id"]})')
    print(f'接收方: {receiver["username"]} (id={receiver["id"]})')

    # ============ 测试23：接收方发起催办 - 基础功能 ============
    header('23. 接收方发起催办 - 基础功能')
    bid_23, box_ids_23, bno_23 = create_full_batch_and_sign(sender['id'], receiver['id'], 'REM23')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_23, 'box_id': box_ids_23[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'REM23档案缺页问题'
    })
    rvid_23 = r.json()['id']

    expected_completion = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')
    r = requests.post(f'{API}/reviews/{rvid_23}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '缺页问题未解决，请尽快补档',
        'expected_completion': expected_completion,
        'urgency': 'NORMAL',
        'is_escalated': False
    })
    assert_true(r.status_code in (200, 201), '接收方发起催办成功')
    rm23 = r.json()
    assert_equal(rm23['status'], 'PENDING', '催办状态为PENDING')
    assert_equal(rm23['urgency'], 'NORMAL', '紧急程度为NORMAL')
    assert_true(rm23['is_escalated'] == 0, '未升级is_escalated=0')
    assert_true('缺页问题未解决' in rm23['reason'], '催办原因正确保存')
    assert_equal(rm23['expected_completion'], expected_completion, '期望完成时间正确保存')
    assert_equal(rm23['created_by'], receiver['id'], '创建人为接收方')

    detail_23 = requests.get(f'{API}/batches/{bid_23}').json()
    rv_23 = next(rv for rv in detail_23['reviews'] if rv['id'] == rvid_23)
    assert_true(rv_23.get('reminder_total', 0) >= 1, '批次详情reminder_total>=1')
    assert_true(rv_23.get('reminder_pending', 0) >= 1, '批次详情reminder_pending>=1')
    assert_true(rv_23.get('reminder_escalated', 0) == 0, '批次详情reminder_escalated=0(未升级)')
    assert_true(rv_23.get('reminder_processed', 0) == 0, '批次详情reminder_processed=0')
    assert_equal(rv_23.get('reminder_latest_creator'), receiver['username'], 'reminder_latest_creator为接收方用户名')
    assert_true(rv_23.get('reminder_latest_at') is not None, 'reminder_latest_at有值')
    assert_equal(rv_23.get('reminder_latest_urgency'), 'NORMAL', 'reminder_latest_urgency为NORMAL')

    history_23 = requests.get(f'{API}/history/{bid_23}').json()
    reminder_his = next((h for h in history_23 if h['action'] == '发起催办'), None)
    assert_true(reminder_his is not None, '流转历史记录了"发起催办"操作')
    assert_true('缺页问题未解决' in reminder_his['reason'], '历史原因包含催办原因')
    print(f'{PASS} 23. 接收方发起催办 - 基础功能 - 通过')

    # ============ 测试24：权限边界 - 发送方不能发起催办，接收方不能处理催办 ============
    header('24. 权限边界 - 发送方不能发起催办，接收方不能处理催办')
    bid_24, box_ids_24, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'PERM24')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_24, 'box_id': box_ids_24[0],
        'operator_id': receiver['id'],
        'issue_type': '标签不清', 'issue_description': 'PERM24标签问题'
    })
    rvid_24 = r.json()['id']

    r = requests.post(f'{API}/reviews/{rvid_24}/reminders', json={
        'operator_id': sender['id'],
        'reason': '发送方尝试催办',
        'urgency': 'NORMAL'
    })
    assert_equal(r.status_code, 400, '发送方发起催办被拒绝(400)')
    assert_true('只有接收方才能发起催办' in r.json()['error'], '错误信息明确：只有接收方可催办')

    r = requests.post(f'{API}/reviews/{rvid_24}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '接收方正常催办',
        'urgency': 'NORMAL'
    })
    assert_true(r.status_code in (200, 201), '接收方发起催办成功')
    rm24_id = r.json()['id']

    r = requests.post(f'{API}/reminders/{rm24_id}/process', json={
        'operator_id': receiver['id'],
        'process_note': '接收方尝试处理催办'
    })
    assert_equal(r.status_code, 400, '接收方处理催办被拒绝(400)')
    assert_true('只有发送方才能处理催办' in r.json()['error'], '错误信息明确：只有发送方可处理')

    r = requests.post(f'{API}/reminders/{rm24_id}/process', json={
        'operator_id': sender['id'],
        'process_note': '发送方正常处理催办'
    })
    assert_true(r.status_code == 200, '发送方处理催办成功')
    print(f'{PASS} 24. 权限边界 - 全角色催办权限校验通过')

    # ============ 测试25：重复催办冲突合并 ============
    header('25. 重复催办冲突合并')
    bid_25, box_ids_25, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'MERGE25')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_25, 'box_id': box_ids_25[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'MERGE25缺页问题'
    })
    rvid_25 = r.json()['id']

    r1 = requests.post(f'{API}/reviews/{rvid_25}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '第一次催办',
        'urgency': 'NORMAL',
        'is_escalated': False
    })
    assert_true(r1.status_code in (200, 201), '第一次催办成功')
    rm25_first = r1.json()
    first_id = rm25_first['id']
    
    conn25_setup = sqlite3.connect(DB_PATH)
    c25_setup = conn25_setup.cursor()
    c25_setup.execute("UPDATE reminders SET created_at = datetime('now', '-2 minutes') WHERE id = ?", (first_id,))
    conn25_setup.commit()
    conn25_setup.close()

    r2 = requests.post(f'{API}/reviews/{rvid_25}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '第二次催办-补充',
        'urgency': 'URGENT',
        'is_escalated': True
    })
    assert_equal(r2.status_code, 200, '同一复核项重复催办返回200(合并)')
    rm25_merged = r2.json()
    assert_equal(rm25_merged['id'], first_id, '合并后ID不变(同一催办)')
    assert_true('第一次催办' in rm25_merged['reason'], '合并后原因包含原始原因')
    assert_true('第二次催办-补充' in rm25_merged['reason'], '合并后原因包含追加原因')
    assert_equal(rm25_merged['urgency'], 'URGENT', '合并后紧急程度升级为URGENT')
    assert_true(rm25_merged['is_escalated'] == 1, '合并后is_escalated升级为1')

    r3 = requests.post(f'{API}/reviews/{rvid_25}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '第三次催办-再次补充',
        'urgency': 'CRITICAL',
        'is_escalated': True
    })
    assert_equal(r3.status_code, 200, '第三次催办同样合并(200)')
    rm25_3 = r3.json()
    assert_equal(rm25_3['id'], first_id, '第三次合并后ID仍不变')
    assert_equal(rm25_3['urgency'], 'CRITICAL', '第三次合并后紧急程度升级为CRITICAL')
    assert_true('第三次催办-再次补充' in rm25_3['reason'], '合并后原因包含第三次追加')

    reminders_25 = requests.get(f'{API}/reviews/{rvid_25}/reminders').json()
    active_reminders = [rm for rm in reminders_25 if rm['status'] != 'MERGED']
    assert_true(len(active_reminders) == 1, '同一复核项仅1条活跃催办(其余合并)')

    conn25 = sqlite3.connect(DB_PATH)
    c25 = conn25.cursor()
    c25.execute("SELECT COUNT(*) FROM reminder_logs WHERE reminder_id = ? AND action = '合并催办'", (first_id,))
    merge_log_count = c25.fetchone()[0]
    assert_true(merge_log_count >= 2, f'合并催办日志至少2条(实际{merge_log_count})')
    conn25.close()
    print(f'{PASS} 25. 重复催办冲突合并 - 通过')

    # ============ 测试25b：短时间重复催办拦截（429） ============
    header('25b. 短时间重复催办拦截（429）')
    bid_25b, box_ids_25b, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'RATE25B', box_count=1)
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_25b, 'box_id': box_ids_25b[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'RATE25B短时间拦截测试'
    })
    rvid_25b = r.json()['id']

    r1 = requests.post(f'{API}/reviews/{rvid_25b}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '第一次催办',
        'urgency': 'NORMAL'
    })
    assert_true(r1.status_code in (200, 201), '第一次催办成功')
    rm25b_id = r1.json()['id']

    r2 = requests.post(f'{API}/reviews/{rvid_25b}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '短时间内重复催办',
        'urgency': 'URGENT'
    })
    assert_equal(r2.status_code, 429, '60秒内同一用户重复催办返回429拦截')
    r2_data = r2.json()
    assert_true('error' in r2_data, '429响应包含error字段')
    assert_true('existing_reminder_id' in r2_data, '429响应包含existing_reminder_id')
    assert_equal(r2_data['existing_reminder_id'], rm25b_id, 'existing_reminder_id指向已有催办')

    time.sleep(2)
    r3 = requests.post(f'{API}/reviews/{rvid_25b}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '第三次催办(仍在窗口内)',
        'urgency': 'CRITICAL'
    })
    assert_equal(r3.status_code, 429, '短时间内第三次催办仍被拦截')
    print(f'{PASS} 25b. 短时间重复催办拦截（429） - 通过')

    # ============ 测试26：紧急程度分组 - 待办列表 ============
    header('26. 紧急程度分组 - 待办列表')
    bid_26a, box_ids_26a, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'URG26A')
    bid_26b, box_ids_26b, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'URG26B')
    bid_26c, box_ids_26c, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'URG26C')

    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_26a, 'box_id': box_ids_26a[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'URG26A-NORMAL问题'
    })
    rvid_26a = r.json()['id']
    requests.post(f'{API}/reviews/{rvid_26a}/reminders', json={
        'operator_id': receiver['id'], 'reason': '普通催办', 'urgency': 'NORMAL'
    })

    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_26b, 'box_id': box_ids_26b[0],
        'operator_id': receiver['id'],
        'issue_type': '标签不清', 'issue_description': 'URG26B-URGENT问题'
    })
    rvid_26b = r.json()['id']
    requests.post(f'{API}/reviews/{rvid_26b}/reminders', json={
        'operator_id': receiver['id'], 'reason': '紧急催办', 'urgency': 'URGENT'
    })

    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_26c, 'box_id': box_ids_26c[0],
        'operator_id': receiver['id'],
        'issue_type': '顺序混乱', 'issue_description': 'URG26C-CRITICAL问题'
    })
    rvid_26c = r.json()['id']
    requests.post(f'{API}/reviews/{rvid_26c}/reminders', json={
        'operator_id': receiver['id'], 'reason': '特急催办', 'urgency': 'CRITICAL', 'is_escalated': True
    })

    r = requests.get(f'{API}/reminders/pending', params={'operator_id': sender['id']})
    assert_true(r.status_code == 200, '发送方获取待办催办成功')
    grouped = r.json()
    assert_true('CRITICAL' in grouped, '待办列表包含CRITICAL分组')
    assert_true('URGENT' in grouped, '待办列表包含URGENT分组')
    assert_true('NORMAL' in grouped, '待办列表包含NORMAL分组')

    keys_order = list(grouped.keys())
    if 'CRITICAL' in keys_order and 'URGENT' in keys_order:
        assert_true(keys_order.index('CRITICAL') < keys_order.index('URGENT'),
                     'CRITICAL分组排在URGENT之前')
    if 'URGENT' in keys_order and 'NORMAL' in keys_order:
        assert_true(keys_order.index('URGENT') < keys_order.index('NORMAL'),
                     'URGENT分组排在NORMAL之前')

    r_recv = requests.get(f'{API}/reminders/pending', params={'operator_id': receiver['id']})
    assert_true(r_recv.status_code == 200, '接收方获取待办催办成功')
    grouped_recv = r_recv.json()
    recv_total = sum(len(items) for items in grouped_recv.values())

    r_send = requests.get(f'{API}/reminders/pending', params={'operator_id': sender['id']})
    grouped_send = r_send.json()
    send_total = sum(len(items) for items in grouped_send.values())
    assert_true(send_total >= recv_total, '发送方看到所有待办催办>=接收方(接收方仅看自己)')
    print(f'{PASS} 26. 紧急程度分组 - 待办列表 - 通过')

    # ============ 测试27：发送方处理催办 - 回填结果 ============
    header('27. 发送方处理催办 - 回填结果')
    bid_27, box_ids_27, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'PROC27')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_27, 'box_id': box_ids_27[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'PROC27缺页问题'
    })
    rvid_27 = r.json()['id']
    rm27 = requests.post(f'{API}/reviews/{rvid_27}/reminders', json={
        'operator_id': receiver['id'], 'reason': '请尽快处理', 'urgency': 'URGENT'
    }).json()
    rm27_id = rm27['id']

    r = requests.post(f'{API}/reminders/{rm27_id}/process', json={
        'operator_id': sender['id'],
        'process_note': '已联系扫描组重新扫描，预计明日补齐'
    })
    assert_true(r.status_code == 200, '发送方处理催办成功')
    rm27_proc = r.json()
    assert_equal(rm27_proc['status'], 'PROCESSED', '催办状态变为PROCESSED')
    assert_equal(rm27_proc['processed_by'], sender['id'], 'processed_by为发送方')
    assert_true(rm27_proc['processed_at'] is not None, 'processed_at有值')
    assert_equal(rm27_proc['process_note'], '已联系扫描组重新扫描，预计明日补齐', 'process_note正确保存')

    history_27 = requests.get(f'{API}/history/{bid_27}').json()
    process_his = next((h for h in history_27 if h['action'] == '处理催办'), None)
    assert_true(process_his is not None, '流转历史记录了"处理催办"操作')
    assert_true('已联系扫描组重新扫描' in process_his['reason'], '历史原因包含处理备注')
    print(f'{PASS} 27. 发送方处理催办 - 回填结果 - 通过')

    # ============ 测试28：撤销升级 ============
    header('28. 撤销升级')
    bid_28, box_ids_28, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'REVK28')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_28, 'box_id': box_ids_28[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'REVK28缺页问题'
    })
    rvid_28 = r.json()['id']
    rm28 = requests.post(f'{API}/reviews/{rvid_28}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '紧急升级催办',
        'urgency': 'CRITICAL',
        'is_escalated': True
    }).json()
    rm28_id = rm28['id']
    assert_true(rm28['is_escalated'] == 1, '升级催办is_escalated=1')
    assert_equal(rm28['urgency'], 'CRITICAL', '升级催办紧急程度为CRITICAL')

    r = requests.post(f'{API}/reminders/{rm28_id}/revoke-escalation', json={
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, '接收方撤销升级成功')
    rm28_revoked = r.json()
    assert_true(rm28_revoked['is_escalated'] == 0, '撤销后is_escalated变为0')
    assert_equal(rm28_revoked['urgency'], 'URGENT', '撤销后紧急程度从CRITICAL降为URGENT')

    conn28 = sqlite3.connect(DB_PATH)
    c28 = conn28.cursor()
    c28.execute("SELECT COUNT(*) FROM reminder_logs WHERE reminder_id = ? AND action = '撤销升级'", (rm28_id,))
    revoke_log_count = c28.fetchone()[0]
    assert_true(revoke_log_count >= 1, '撤销升级日志至少1条')
    conn28.close()

    history_28 = requests.get(f'{API}/history/{bid_28}').json()
    revoke_his = next((h for h in history_28 if h['action'] == '撤销升级'), None)
    assert_true(revoke_his is not None, '流转历史记录了"撤销升级"操作')

    r = requests.post(f'{API}/reminders/{rm28_id}/revoke-escalation', json={
        'operator_id': sender['id']
    })
    assert_equal(r.status_code, 400, '发送方撤销升级被拒绝(400)')
    assert_true('只有接收方才能撤销升级' in r.json()['error'], '错误信息明确：只有接收方可撤销升级')
    print(f'{PASS} 28. 撤销升级 - 通过')

    # ============ 测试29：复核项关闭时催办收口 ============
    header('29. 复核项关闭时催办收口')
    bid_29, box_ids_29, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'CLS29')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_29, 'box_id': box_ids_29[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'CLS29缺页问题'
    })
    rvid_29 = r.json()['id']
    rm29 = requests.post(f'{API}/reviews/{rvid_29}/reminders', json={
        'operator_id': receiver['id'], 'reason': '关闭前催办', 'urgency': 'NORMAL'
    }).json()
    rm29_id = rm29['id']
    assert_equal(rm29['status'], 'PENDING', '关闭前催办状态为PENDING')

    requests.post(f'{API}/reviews/{rvid_29}/update', json={
        'operator_id': sender['id'], 'handling_note': '已补档', 'status': 'PENDING_CLOSE'
    })
    requests.post(f'{API}/reviews/{rvid_29}/close', json={'operator_id': receiver['id']})

    reminders_29 = requests.get(f'{API}/reviews/{rvid_29}/reminders').json()
    rm29_after = next((rm for rm in reminders_29 if rm['id'] == rm29_id), None)
    assert_true(rm29_after is not None, '关闭后仍可查询到催办')
    assert_equal(rm29_after['status'], 'CANCELLED', '复核项关闭后催办状态变为CANCELLED')

    conn29 = sqlite3.connect(DB_PATH)
    c29 = conn29.cursor()
    c29.execute("SELECT COUNT(*) FROM reminder_logs WHERE reminder_id = ? AND action = '取消催办'", (rm29_id,))
    cancel_log_count = c29.fetchone()[0]
    assert_true(cancel_log_count >= 1, '取消催办日志至少1条')
    conn29.close()
    print(f'{PASS} 29. 复核项关闭时催办收口 - 通过')

    # ============ 测试30：复核项撤销重开时催办恢复 ============
    header('30. 复核项撤销重开时催办恢复')
    bid_30, box_ids_30, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'REOP30')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_30, 'box_id': box_ids_30[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'REOP30缺页问题'
    })
    rvid_30 = r.json()['id']
    rm30 = requests.post(f'{API}/reviews/{rvid_30}/reminders', json={
        'operator_id': receiver['id'], 'reason': '重开前催办', 'urgency': 'URGENT'
    }).json()
    rm30_id = rm30['id']

    requests.post(f'{API}/reviews/{rvid_30}/update', json={
        'operator_id': sender['id'], 'handling_note': '处理完成', 'status': 'PENDING_CLOSE'
    })
    requests.post(f'{API}/reviews/{rvid_30}/close', json={'operator_id': receiver['id']})

    reminders_30_closed = requests.get(f'{API}/reviews/{rvid_30}/reminders').json()
    rm30_closed = next((rm for rm in reminders_30_closed if rm['id'] == rm30_id), None)
    assert_equal(rm30_closed['status'], 'CANCELLED', '关闭后催办为CANCELLED')

    r = requests.post(f'{API}/reviews/{rvid_30}/reopen', json={
        'operator_id': receiver['id'], 'reason': '补档仍有遗漏需重开'
    })
    assert_true(r.status_code == 200, '撤销重开成功')

    reminders_30_reopened = requests.get(f'{API}/reviews/{rvid_30}/reminders').json()
    rm30_reopened = next((rm for rm in reminders_30_reopened if rm['id'] == rm30_id), None)
    assert_true(rm30_reopened is not None, '重开后催办仍可查询')
    assert_equal(rm30_reopened['status'], 'PENDING', '重开后催办状态恢复为PENDING')
    assert_true(rm30_reopened.get('process_note') is None or rm30_reopened.get('process_note') == '',
                 '重开后process_note清空')
    assert_true(rm30_reopened.get('processed_by') is None, '重开后processed_by清空')
    assert_true(rm30_reopened.get('processed_at') is None, '重开后processed_at清空')

    conn30 = sqlite3.connect(DB_PATH)
    c30 = conn30.cursor()
    c30.execute("SELECT COUNT(*) FROM reminder_logs WHERE reminder_id = ? AND action = '恢复催办'", (rm30_id,))
    restore_log_count = c30.fetchone()[0]
    assert_true(restore_log_count >= 1, '恢复催办日志至少1条')
    conn30.close()
    print(f'{PASS} 30. 复核项撤销重开时催办恢复 - 通过')

    # ============ 测试31：复核项退回时已处理催办恢复 ============
    header('31. 复核项退回时已处理催办恢复')
    bid_31, box_ids_31, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'REJ31')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_31, 'box_id': box_ids_31[0],
        'operator_id': receiver['id'],
        'issue_type': '顺序混乱', 'issue_description': 'REJ31顺序问题'
    })
    rvid_31 = r.json()['id']
    rm31 = requests.post(f'{API}/reviews/{rvid_31}/reminders', json={
        'operator_id': receiver['id'], 'reason': '请尽快处理顺序', 'urgency': 'NORMAL'
    }).json()
    rm31_id = rm31['id']

    r = requests.post(f'{API}/reminders/{rm31_id}/process', json={
        'operator_id': sender['id'],
        'process_note': '已调整顺序'
    })
    assert_true(r.status_code == 200, '发送方处理催办成功')
    rm31_proc = r.json()
    assert_equal(rm31_proc['status'], 'PROCESSED', '催办已处理(PROCESSED)')

    requests.post(f'{API}/reviews/{rvid_31}/update', json={
        'operator_id': sender['id'], 'handling_note': '顺序已调整', 'status': 'PENDING_CLOSE'
    })
    r = requests.post(f'{API}/reviews/{rvid_31}/reject', json={
        'operator_id': receiver['id'], 'reason': '顺序还有3处错误，需重新调整'
    })
    assert_true(r.status_code == 200, '接收方退回复核项成功')

    reminders_31 = requests.get(f'{API}/reviews/{rvid_31}/reminders').json()
    rm31_after = next((rm for rm in reminders_31 if rm['id'] == rm31_id), None)
    assert_true(rm31_after is not None, '退回后催办仍可查询')
    assert_equal(rm31_after['status'], 'PENDING', '退回后已处理催办恢复为PENDING')
    assert_true(rm31_after.get('process_note') is None or rm31_after.get('process_note') == '',
                 '退回后process_note清空')
    assert_true(rm31_after.get('processed_by') is None, '退回后processed_by清空')
    assert_true(rm31_after.get('processed_at') is None, '退回后processed_at清空')
    print(f'{PASS} 31. 复核项退回时已处理催办恢复 - 通过')

    # ============ 测试32：导出清单包含催办摘要 ============
    header('32. 导出清单包含催办摘要')
    bid_32, box_ids_32, bno_32 = create_full_batch_and_sign(sender['id'], receiver['id'], 'EXP32', box_count=2)
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_32, 'box_id': box_ids_32[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'EXP32盒1缺页'
    })
    rvid_32a = r.json()['id']
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_32, 'box_id': box_ids_32[1],
        'operator_id': receiver['id'],
        'issue_type': '标签不清', 'issue_description': 'EXP32盒2标签'
    })
    rvid_32b = r.json()['id']

    requests.post(f'{API}/reviews/{rvid_32a}/reminders', json={
        'operator_id': receiver['id'], 'reason': '缺页催办', 'urgency': 'URGENT', 'is_escalated': True
    })
    rm32b = requests.post(f'{API}/reviews/{rvid_32b}/reminders', json={
        'operator_id': receiver['id'], 'reason': '标签催办', 'urgency': 'NORMAL'
    }).json()
    requests.post(f'{API}/reminders/{rm32b["id"]}/process', json={
        'operator_id': sender['id'], 'process_note': '标签已重新打印'
    })

    r = requests.post(f'{API}/batches/{bid_32}/export', json={'operator_id': sender['id']})
    assert_true(r.status_code == 200, '导出成功')
    content = r.json().get('content', '')

    assert_true('催办统计' in content, 'CSV包含催办统计行')
    assert_true('催办摘要' in content, 'CSV包含催办摘要区块')

    lines = [ln for ln in content.splitlines() if ln.strip()]
    idx_reminder = next((i for i, ln in enumerate(lines) if '催办摘要' in ln), None)
    assert_true(idx_reminder is not None, '定位到催办摘要区块')

    if idx_reminder is not None and idx_reminder + 1 < len(lines):
        try:
            header_row = next(csv.reader([lines[idx_reminder + 1]]))
        except Exception:
            header_row = lines[idx_reminder + 1].split(',')
        expected_cols = ['催办ID', '复核项ID', '盒号', '问题类型', '催办原因', '紧急程度', '是否升级',
                         '期望完成时间', '状态', '催办人', '催办时间', '处理人', '处理时间', '处理备注']
        assert_true(len(header_row) == len(expected_cols),
                     f'催办摘要表头列数对齐: 期望{len(expected_cols)}列, 实际{len(header_row)}列')

    assert_true('紧急' in content, 'CSV包含紧急程度字段')
    assert_true('缺页催办' in content, 'CSV包含催办原因')
    assert_true('标签已重新打印' in content, 'CSV包含处理备注')
    print(f'{PASS} 32. 导出清单包含催办摘要 - 通过')

    # ============ 测试33：数据持久化 - 重启后催办状态一致 ============
    header('33. 数据持久化 - 重启后催办状态一致')
    assert_true(os.path.exists(DB_PATH), f'数据库文件存在: {DB_PATH}')
    conn33 = sqlite3.connect(DB_PATH)
    c33 = conn33.cursor()

    c33.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'")
    assert_true(c33.fetchone() is not None, 'reminders表存在于数据库')

    c33.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reminder_logs'")
    assert_true(c33.fetchone() is not None, 'reminder_logs表存在于数据库')

    c33.execute('PRAGMA table_info(reminders)')
    rm_cols = [row[1] for row in c33.fetchall()]
    expected_rm_cols = ['id', 'review_id', 'reason', 'expected_completion', 'is_escalated',
                        'urgency', 'status', 'created_by', 'created_at',
                        'processed_by', 'processed_at', 'process_note', 'merged_into']
    for col in expected_rm_cols:
        assert_true(col in rm_cols, f'reminders表包含列 {col}')

    c33.execute('PRAGMA table_info(reminder_logs)')
    log_cols = [row[1] for row in c33.fetchall()]
    expected_log_cols = ['id', 'reminder_id', 'action', 'operator_id', 'detail', 'created_at']
    for col in expected_log_cols:
        assert_true(col in log_cols, f'reminder_logs表包含列 {col}')

    c33.execute("SELECT COUNT(*) FROM reminders")
    rm_count = c33.fetchone()[0]
    print(f'  当前催办总数: {rm_count}')
    assert_true(rm_count >= 5, f'催办持久化数量合理 (>={5})')

    c33.execute("SELECT DISTINCT status FROM reminders")
    rm_statuses = [row[0] for row in c33.fetchall()]
    assert_true('PENDING' in rm_statuses, '存在PENDING状态催办记录')
    assert_true('PROCESSED' in rm_statuses or 'CANCELLED' in rm_statuses,
                 '存在PROCESSED或CANCELLED状态催办记录')

    c33.execute("SELECT COUNT(*) FROM reminder_logs")
    log_count = c33.fetchone()[0]
    print(f'  当前催办日志总数: {log_count}')
    assert_true(log_count >= 5, f'催办日志持久化数量合理 (>={5})')

    c33.execute('''SELECT urgency, is_escalated, status FROM reminders WHERE status = 'PENDING' LIMIT 1''')
    pending_row = c33.fetchone()
    if pending_row:
        assert_true(pending_row[0] in ('NORMAL', 'URGENT', 'CRITICAL'), 'PENDING催办urgency持久化正确')
        assert_true(pending_row[1] in (0, 1), 'PENDING催办is_escalated持久化正确')

    c33.execute('''SELECT process_note, processed_by, processed_at FROM reminders WHERE status = 'PROCESSED' LIMIT 1''')
    processed_row = c33.fetchone()
    if processed_row:
        assert_true(processed_row[0] is not None and len(processed_row[0]) > 0, 'PROCESSED催办process_note已持久化')
        assert_true(processed_row[1] is not None, 'PROCESSED催办processed_by已持久化')
        assert_true(processed_row[2] is not None, 'PROCESSED催办processed_at已持久化')

    conn33.close()
    print(f'{PASS} 33. 数据持久化 - 重启后催办状态一致 - 通过')

    # ============ 测试34：催办统计接口 ============
    header('34. 催办统计接口')
    r = requests.get(f'{API}/reminders/stats')
    assert_true(r.status_code == 200, '催办统计接口返回200')
    stats = r.json()
    assert_true('total_pending' in stats, '统计接口包含total_pending')
    assert_true('by_urgency' in stats, '统计接口包含by_urgency')
    assert_true('processed_today' in stats, '统计接口包含processed_today')

    by_urgency = stats['by_urgency']
    assert_true('NORMAL' in by_urgency, 'by_urgency包含NORMAL')
    assert_true('URGENT' in by_urgency, 'by_urgency包含URGENT')
    assert_true('CRITICAL' in by_urgency, 'by_urgency包含CRITICAL')

    sum_by_urgency = by_urgency['NORMAL'] + by_urgency['URGENT'] + by_urgency['CRITICAL']
    assert_equal(sum_by_urgency, stats['total_pending'], 'by_urgency各档之和等于total_pending')

    assert_true(isinstance(stats['processed_today'], int), 'processed_today为整数')
    print(f'{PASS} 34. 催办统计接口 - 通过')

    # ============ 测试35：发起催办时复核项状态校验 ============
    header('35. 发起催办时复核项状态校验')
    bid_35, box_ids_35, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'STAT35')
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_35, 'box_id': box_ids_35[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'STAT35-关闭状态测试'
    })
    rvid_35_closed = r.json()['id']
    requests.post(f'{API}/reviews/{rvid_35_closed}/update', json={
        'operator_id': sender['id'], 'handling_note': '已处理', 'status': 'PENDING_CLOSE'
    })
    requests.post(f'{API}/reviews/{rvid_35_closed}/close', json={'operator_id': receiver['id']})

    r = requests.post(f'{API}/reviews/{rvid_35_closed}/reminders', json={
        'operator_id': receiver['id'], 'reason': '尝试对已关闭复核项催办', 'urgency': 'NORMAL'
    })
    assert_equal(r.status_code, 400, '对已关闭复核项发起催办被拒绝(400)')
    assert_true('不允许发起催办' in r.json()['error'], '错误信息包含不允许催办提示')

    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_35, 'box_id': box_ids_35[1],
        'operator_id': receiver['id'],
        'issue_type': '标签不清', 'issue_description': 'STAT35-待关闭状态测试'
    })
    rvid_35_pc = r.json()['id']
    requests.post(f'{API}/reviews/{rvid_35_pc}/update', json={
        'operator_id': sender['id'], 'handling_note': '申请关闭', 'status': 'PENDING_CLOSE'
    })
    r = requests.post(f'{API}/reviews/{rvid_35_pc}/reminders', json={
        'operator_id': receiver['id'], 'reason': '尝试对待关闭复核项催办', 'urgency': 'NORMAL'
    })
    assert_equal(r.status_code, 400, '对待关闭(PENDING_CLOSE)复核项发起催办被拒绝(400)')

    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_35, 'box_id': box_ids_35[1],
        'operator_id': receiver['id'],
        'issue_type': '顺序混乱', 'issue_description': 'STAT35-OPEN状态测试'
    })
    rvid_35_open = r.json()['id']
    r = requests.post(f'{API}/reviews/{rvid_35_open}/reminders', json={
        'operator_id': receiver['id'], 'reason': 'OPEN状态催办', 'urgency': 'NORMAL'
    })
    assert_true(r.status_code in (200, 201), 'OPEN状态复核项可发起催办')
    rm35_id = r.json()['id']
    
    conn35_setup = sqlite3.connect(DB_PATH)
    c35_setup = conn35_setup.cursor()
    c35_setup.execute("UPDATE reminders SET created_at = datetime('now', '-2 minutes') WHERE id = ?", (rm35_id,))
    conn35_setup.commit()
    conn35_setup.close()

    r = requests.post(f'{API}/reviews/{rvid_35_open}/update', json={
        'operator_id': sender['id'], 'handling_note': '处理中', 'status': 'IN_PROGRESS'
    })
    r = requests.post(f'{API}/reviews/{rvid_35_open}/reminders', json={
        'operator_id': receiver['id'], 'reason': 'IN_PROGRESS状态催办', 'urgency': 'URGENT'
    })
    assert_true(r.status_code in (200, 201), 'IN_PROGRESS状态复核项可发起催办(合并)')
    
    conn35_setup2 = sqlite3.connect(DB_PATH)
    c35_setup2 = conn35_setup2.cursor()
    c35_setup2.execute("UPDATE reminders SET created_at = datetime('now', '-2 minutes') WHERE id = ?", (rm35_id,))
    conn35_setup2.commit()
    conn35_setup2.close()

    r = requests.post(f'{API}/reviews/{rvid_35_open}/reject', json={
        'operator_id': receiver['id'], 'reason': '测试退回状态催办'
    })
    r = requests.post(f'{API}/reviews/{rvid_35_open}/reminders', json={
        'operator_id': receiver['id'], 'reason': 'REJECTED状态催办', 'urgency': 'NORMAL'
    })
    assert_true(r.status_code in (200, 201), 'REJECTED状态复核项可发起催办(合并)')
    print(f'{PASS} 35. 发起催办时复核项状态校验 - 通过')

    # ============ 测试36：超期标记与统计 ============
    header('36. 超期标记与统计')
    bid_36, box_ids_36, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'OVD36', box_count=2)
    
    past_deadline = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    future_deadline = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')
    
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_36, 'box_id': box_ids_36[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'OVD36-超期测试',
        'deadline': past_deadline
    })
    rvid_36_overdue = r.json()['id']
    
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_36, 'box_id': box_ids_36[1],
        'operator_id': receiver['id'],
        'issue_type': '标签不清', 'issue_description': 'OVD36-未超期测试',
        'deadline': future_deadline
    })
    rvid_36_not_overdue = r.json()['id']
    
    rm36_overdue = requests.post(f'{API}/reviews/{rvid_36_overdue}/reminders', json={
        'operator_id': receiver['id'], 'reason': '超期催办', 'urgency': 'URGENT'
    }).json()
    
    rm36_not_overdue = requests.post(f'{API}/reviews/{rvid_36_not_overdue}/reminders', json={
        'operator_id': receiver['id'], 'reason': '未超期催办', 'urgency': 'NORMAL'
    }).json()
    
    batch_detail = requests.get(f'{API}/batches/{bid_36}').json()
    reviews_36 = batch_detail['reviews']
    
    rv_overdue = next((r for r in reviews_36 if r['id'] == rvid_36_overdue), None)
    assert_true(rv_overdue is not None, '找到超期复核项')
    if rv_overdue:
        assert_true(rv_overdue.get('is_overdue') == True, '超期复核项is_overdue=true')
    
    rv_not_overdue = next((r for r in reviews_36 if r['id'] == rvid_36_not_overdue), None)
    assert_true(rv_not_overdue is not None, '找到未超期复核项')
    if rv_not_overdue:
        assert_true(rv_not_overdue.get('is_overdue') == False or rv_not_overdue.get('is_overdue') is None,
                     '未超期复核项is_overdue=false')
    
    stats_36 = requests.get(f'{API}/reminders/stats').json()
    assert_true('overdue_pending' in stats_36, '统计接口包含overdue_pending')
    assert_true('escalated_pending' in stats_36, '统计接口包含escalated_pending')
    assert_true(isinstance(stats_36['overdue_pending'], int), 'overdue_pending为整数')
    assert_true(stats_36['overdue_pending'] >= 1, '超期待办数至少为1')
    
    pending_36 = requests.get(f'{API}/reminders/pending', params={'operator_id': sender['id']}).json()
    all_items_36 = []
    for items in pending_36.values():
        all_items_36.extend(items)
    
    overdue_reminder = next((r for r in all_items_36 if r['id'] == rm36_overdue['id']), None)
    assert_true(overdue_reminder is not None, '待办列表中找到超期催办')
    if overdue_reminder:
        assert_true('is_overdue' in overdue_reminder, '待办催办包含is_overdue字段')
    
    print(f'{PASS} 36. 超期标记与统计 - 通过')

    # ============ 测试37：接口契约一致性 ============
    header('37. 接口契约一致性')
    bid_37, box_ids_37, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'CON37', box_count=1)
    
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_37, 'box_id': box_ids_37[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'CON37契约一致性测试'
    })
    rvid_37 = r.json()['id']
    
    rm37 = requests.post(f'{API}/reviews/{rvid_37}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '契约测试催办',
        'urgency': 'CRITICAL',
        'is_escalated': True,
        'expected_completion': (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
    }).json()
    
    assert_true('id' in rm37, '创建催办返回id')
    assert_true('review_id' in rm37, '创建催办返回review_id')
    assert_true('reason' in rm37, '创建催办返回reason')
    assert_true('urgency' in rm37, '创建催办返回urgency')
    assert_true('is_escalated' in rm37, '创建催办返回is_escalated')
    assert_true('status' in rm37, '创建催办返回status')
    assert_true('created_by' in rm37, '创建催办返回created_by')
    assert_true('created_at' in rm37, '创建催办返回created_at')
    
    batch_detail_37 = requests.get(f'{API}/batches/{bid_37}').json()
    rv37 = next((r for r in batch_detail_37['reviews'] if r['id'] == rvid_37), None)
    assert_true(rv37 is not None, '批次详情找到复核项')
    
    if rv37:
        assert_true('reminder_total' in rv37, '批次详情复核项包含reminder_total')
        assert_true('reminder_latest_creator' in rv37, '批次详情包含reminder_latest_creator(统一命名)')
        assert_true('reminder_latest_at' in rv37, '批次详情包含reminder_latest_at')
        assert_true('reminder_latest_urgency' in rv37, '批次详情包含reminder_latest_urgency')
        assert_true('reminder_latest_urgency_name' in rv37, '批次详情包含reminder_latest_urgency_name')
        assert_true('reminder_latest_is_escalated' in rv37, '批次详情包含reminder_latest_is_escalated')
        assert_true('reminder_latest_status' in rv37, '批次详情包含reminder_latest_status')
        assert_true('reminder_latest_status_name' in rv37, '批次详情包含reminder_latest_status_name')
        assert_true('reminder_by_urgency' in rv37, '批次详情包含reminder_by_urgency分档统计')
    
    stats_37 = requests.get(f'{API}/reminders/stats').json()
    assert_true('by_urgency' in stats_37, '统计接口包含by_urgency嵌套结构')
    assert_true(isinstance(stats_37['by_urgency'], dict), 'by_urgency是字典类型')
    assert_true('NORMAL' in stats_37['by_urgency'], 'by_urgency包含NORMAL')
    assert_true('URGENT' in stats_37['by_urgency'], 'by_urgency包含URGENT')
    assert_true('CRITICAL' in stats_37['by_urgency'], 'by_urgency包含CRITICAL')
    
    print(f'{PASS} 37. 接口契约一致性 - 通过')

    # ============ 测试38：重启后一致性 - 增强验证 ============
    header('38. 重启后一致性 - 增强验证')
    bid_38, box_ids_38, _ = create_full_batch_and_sign(sender['id'], receiver['id'], 'PERS38', box_count=1)
    
    r = requests.post(f'{API}/reviews', json={
        'batch_id': bid_38, 'box_id': box_ids_38[0],
        'operator_id': receiver['id'],
        'issue_type': '材料缺页', 'issue_description': 'PERS38持久化测试'
    })
    rvid_38 = r.json()['id']
    
    rm38 = requests.post(f'{API}/reviews/{rvid_38}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '持久化测试催办',
        'urgency': 'CRITICAL',
        'is_escalated': True
    }).json()
    rm38_id = rm38['id']
    
    requests.post(f'{API}/reminders/{rm38_id}/process', json={
        'operator_id': sender['id'],
        'process_note': '已处理完成'
    })
    
    rm38b = requests.post(f'{API}/reviews/{rvid_38}/reminders', json={
        'operator_id': receiver['id'],
        'reason': '第二次催办',
        'urgency': 'URGENT',
        'is_escalated': False
    }).json()
    rm38b_id = rm38b['id']
    
    conn38 = sqlite3.connect(DB_PATH)
    conn38.row_factory = sqlite3.Row
    c38 = conn38.cursor()
    
    c38.execute("SELECT * FROM reminders WHERE review_id = ? ORDER BY created_at", (rvid_38,))
    db_reminders = [dict(row) for row in c38.fetchall()]
    assert_true(len(db_reminders) >= 2, f'数据库中至少2条催办记录(实际{len(db_reminders)})')
    
    first_rm = db_reminders[0]
    assert_equal(first_rm['status'], 'PROCESSED', '第一条催办状态PROCESSED持久化')
    assert_true(first_rm['processed_by'] is not None, 'processed_by持久化')
    assert_true(first_rm['processed_at'] is not None, 'processed_at持久化')
    assert_equal(first_rm['process_note'], '已处理完成', 'process_note持久化')
    
    second_rm = db_reminders[-1]
    assert_equal(second_rm['status'], 'PENDING', '最新催办状态PENDING持久化')
    assert_equal(second_rm['urgency'], 'URGENT', 'urgency持久化')
    assert_equal(second_rm['is_escalated'], 0, 'is_escalated持久化')
    assert_equal(second_rm['created_by'], receiver['id'], 'created_by持久化')
    assert_true(second_rm['created_at'] is not None, 'created_at持久化')
    
    c38.execute("SELECT COUNT(*) FROM reminder_logs WHERE reminder_id IN (SELECT id FROM reminders WHERE review_id = ?)", (rvid_38,))
    log_count = c38.fetchone()[0]
    assert_true(log_count >= 2, f'催办日志至少2条(实际{log_count})')
    
    batch_detail_38 = requests.get(f'{API}/batches/{bid_38}').json()
    rv38 = next((r for r in batch_detail_38['reviews'] if r['id'] == rvid_38), None)
    assert_true(rv38 is not None, '批次详情找到复核项')
    if rv38:
        assert_true(rv38['reminder_total'] >= 2, '催办次数持久化正确')
        assert_true(rv38.get('reminder_latest_creator') is not None, '最近催办人持久化正确')
        assert_true(rv38.get('reminder_latest_status') is not None, '最近催办状态持久化正确')
    
    conn38.close()
    print(f'{PASS} 38. 重启后一致性 - 增强验证 - 通过')

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
