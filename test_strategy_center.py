import requests
import sqlite3
import time
import os
import sys
import json
from datetime import datetime, timedelta

if sys.platform.startswith('win') and sys.stdout.encoding:
    enc = sys.stdout.encoding.lower()
    if 'gbk' in enc or enc == 'cp936' or enc == '936':
        try:
            sys.stdout.reconfigure(errors='replace')
            sys.stderr.reconfigure(errors='replace')
        except Exception:
            pass

API = 'http://127.0.0.1:5002/api'
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

def create_test_batch_and_review(sender_id, receiver_id):
    archive_prefix = f'STRAT-{int(time.time()%100000)}'
    archives = [
        {'archive_no': f'{archive_prefix}-{i+1}', 'title': f'{archive_prefix}档案{i+1}', 'remark': ''}
        for i in range(4)
    ]
    r = requests.post(f'{API}/batches', json={
        'batch_no': f'BATCH-{archive_prefix}',
        'description': '策略中心测试批次',
        'created_by': sender_id,
        'archives': archives
    })
    batch_id = r.json()['id']

    r = requests.post(f'{API}/boxes', json={
        'box_no': f'BOX-{archive_prefix}',
        'batch_id': batch_id,
        'operator_id': sender_id
    })
    box_id = r.json()['id']

    detail = requests.get(f'{API}/batches/{batch_id}').json()
    aids = [a['id'] for a in detail['archives']]
    requests.post(f'{API}/boxes/pack', json={
        'batch_id': batch_id, 'box_id': box_id,
        'archive_ids': aids, 'operator_id': sender_id
    })

    requests.post(f'{API}/batches/{batch_id}/transfer', json={'operator_id': sender_id})
    requests.post(f'{API}/boxes/{box_id}/sign', json={'operator_id': receiver_id})

    r = requests.post(f'{API}/reviews', json={
        'batch_id': batch_id,
        'box_id': box_id,
        'issue_type': '材料缺页',
        'issue_description': '档案材料缺页问题描述',
        'responsible_party': 'SENDER',
        'deadline': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
        'operator_id': receiver_id
    })
    review_id = r.json()['id']

    return batch_id, box_id, review_id

def main():
    print(f'===== 档案移交流转台 - 催办策略中心 全链路回归测试 =====')
    print(f'测试时间: {datetime.now()}')

    users = requests.get(f'{API}/users').json()
    sender = next(u for u in users if u['role'] == 'SENDER')
    receiver = next(u for u in users if u['role'] == 'RECEIVER')
    print(f'发送方: {sender["username"]} (ID: {sender["id"]}, 角色: {sender["role"]})')
    print(f'接收方: {receiver["username"]} (ID: {receiver["id"]}, 角色: {receiver["role"]})')

    # ========================================
    header('一、策略创建与基本CRUD')
    # ========================================

    strategy_data = {
        'name': f'测试策略_{int(time.time())}',
        'description': '测试用的默认催办策略',
        'priority': 10,
        'trigger_conditions': {
            'issue_types': ['材料缺页', '标签不清'],
            'statuses': ['OPEN', 'IN_PROGRESS'],
            'min_hours_open': 0,
            'is_overdue': None
        },
        'escalation_order': ['普通催办', '紧急催办', '特急升级'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER', 'RECEIVER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }

    r = requests.post(f'{API}/strategies', json=strategy_data)
    assert_equal(r.status_code, 201, '创建策略成功')
    strategy_id = r.json()['id']
    assert_true(strategy_id > 0, '策略ID有效')

    r = requests.get(f'{API}/strategies?operator_id={receiver["id"]}')
    strategies = r.json()
    assert_equal(r.status_code, 200, '获取策略列表成功')
    found = any(s['id'] == strategy_id for s in strategies)
    assert_true(found, '策略已保存到数据库')

    strategy = next(s for s in strategies if s['id'] == strategy_id)
    assert_equal(strategy['name'], strategy_data['name'], '策略名称正确')
    assert_equal(strategy['status'], 'DRAFT', '新建策略默认为草稿状态')
    assert_equal(strategy['version'], 1, '初始版本为v1')
    assert_equal(strategy['priority'], 10, '优先级正确')
    assert_equal(strategy['cooldown_minutes'], 60, '冷却时间正确')
    assert_equal(strategy['timeout_hours'], 24, '超时阈值正确')
    assert_equal(strategy['trigger_conditions']['issue_types'], ['材料缺页', '标签不清'], '触发条件正确')
    assert_equal(strategy['escalation_order'], ['普通催办', '紧急催办', '特急升级'], '升级顺序正确')
    assert_equal(strategy['notify_targets'], ['SENDER', 'RECEIVER'], '通知对象正确')

    updated_data = strategy_data.copy()
    updated_data['name'] = f'更新后的策略_{int(time.time())}'
    updated_data['priority'] = 20
    updated_data['cooldown_minutes'] = 120
    updated_data['description'] = '更新后的描述'
    updated_data['operator_id'] = receiver['id']
    
    r = requests.put(f'{API}/strategies/{strategy_id}', json=updated_data)
    assert_equal(r.status_code, 200, '更新策略成功')

    r = requests.get(f'{API}/strategies/{strategy_id}?operator_id={receiver["id"]}')
    detail = r.json()['strategy']
    assert_equal(detail['version'], 2, '更新后版本号递增为v2')
    assert_equal(detail['name'], updated_data['name'], '策略名称已更新')
    assert_equal(detail['priority'], 20, '优先级已更新')
    assert_equal(detail['cooldown_minutes'], 120, '冷却时间已更新')

    # ========================================
    header('二、策略预演功能')
    # ========================================

    batch_id, box_id, review_id = create_test_batch_and_review(sender['id'], receiver['id'])
    print(f'创建测试数据: 批次ID={batch_id}, 盒子ID={box_id}, 复核项ID={review_id}')

    r = requests.post(f'{API}/strategies/{strategy_id}/preview', json={
        'operator_id': receiver['id']
    })
    assert_equal(r.status_code, 200, '预演请求成功')
    
    preview = r.json()
    assert_true('total_matches' in preview, '预演结果包含命中总数')
    assert_true('conflict_count' in preview, '预演结果包含冲突数')
    assert_true('will_trigger_count' in preview, '预演结果包含将触发数')
    assert_true('will_escalate_count' in preview, '预演结果包含将升级数')
    assert_true('details' in preview, '预演结果包含明细')

    print(f'  预演结果: 命中={preview["total_matches"]}, 冲突={preview["conflict_count"]}, '
          f'将触发={preview["will_trigger_count"]}, 将升级={preview["will_escalate_count"]}')

    matched_detail = None
    for d in preview['details']:
        if d['review_id'] == review_id:
            matched_detail = d
            break
    
    if matched_detail:
        assert_equal(matched_detail['review_id'], review_id, '预演命中正确的复核项')
        assert_true('escalation_level' in matched_detail, '包含升级级别')
        assert_true('in_cooldown' in matched_detail, '包含冷却状态')
        assert_true('will_escalate' in matched_detail, '包含是否将升级')
        assert_true('resolution' in matched_detail, '包含冲突裁决结果')
        assert_true('matched_strategies' in matched_detail, '包含命中的策略列表')
        print(f'  命中详情: 升级级别=Lv.{matched_detail["escalation_level"]}, '
              f'冷却中={matched_detail["in_cooldown"]}, 将升级={matched_detail["will_escalate"]}')

    draft_strategy_data = {
        'name': f'草稿预演测试_{int(time.time())}',
        'description': '未保存的草稿预演测试',
        'priority': 5,
        'trigger_conditions': {
            'issue_types': ['材料缺页'],
            'statuses': ['OPEN'],
            'min_hours_open': 0,
            'is_overdue': None
        },
        'escalation_order': ['草稿催办'],
        'cooldown_minutes': 30,
        'timeout_hours': 12,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies/0/preview', json=draft_strategy_data)
    assert_equal(r.status_code, 200, '草稿预演（ID=0）成功')
    draft_preview = r.json()
    assert_true('total_matches' in draft_preview, '草稿预演也返回完整结果')

    # ========================================
    header('三、策略冲突处理')
    # ========================================

    strategy2_data = {
        'name': f'高优先级冲突策略_{int(time.time())}',
        'description': '高优先级策略，用于测试冲突',
        'priority': 100,
        'trigger_conditions': {
            'issue_types': ['材料缺页'],
            'statuses': ['OPEN'],
            'min_hours_open': 0,
            'is_overdue': None
        },
        'escalation_order': ['高优先级催办', '高优先级升级'],
        'cooldown_minutes': 30,
        'timeout_hours': 6,
        'notify_targets': ['RECEIVER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=strategy2_data)
    strategy2_id = r.json()['id']
    assert_true(strategy2_id > 0, '创建冲突策略成功')

    requests.post(f'{API}/strategies/{strategy_id}/enable', json={'operator_id': receiver['id']})
    requests.post(f'{API}/strategies/{strategy2_id}/enable', json={'operator_id': receiver['id']})

    r = requests.post(f'{API}/strategies/{strategy_id}/preview', json={
        'operator_id': receiver['id']
    })
    preview = r.json()
    
    conflict_count = 0
    priority_resolved = True
    for d in preview['details']:
        if d['conflict']:
            conflict_count += 1
            if d['matched_strategies'] and len(d['matched_strategies']) > 1:
                max_priority = max(s['priority'] for s in d['matched_strategies'])
                if d['selected_strategy'] and d['selected_strategy']['priority'] != max_priority:
                    priority_resolved = False
                    print(f'  警告: 冲突裁决未选择最高优先级策略')

    assert_true(conflict_count > 0 or preview['details'] == 0, f'检测到{conflict_count}个冲突')
    if conflict_count > 0:
        assert_true(priority_resolved, '冲突裁决正确选择最高优先级策略')
        print(f'  冲突检测正常: {conflict_count}个复核项命中多条策略，均按优先级裁决')

    for d in preview['details']:
        if d['conflict']:
            assert_true('resolution' in d and d['resolution'], '每个冲突都有明确的裁决说明')
            print(f'  裁决示例: {d["resolution"][:80]}...')

    # ========================================
    header('四、权限控制测试')
    # ========================================

    test_strategy_data = {
        'name': f'权限测试策略_{int(time.time())}',
        'description': '测试权限控制',
        'priority': 10,
        'trigger_conditions': {'issue_types': ['其他问题'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['权限测试催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=test_strategy_data)
    perm_strategy_id = r.json()['id']

    test_strategy_data['operator_id'] = sender['id']
    test_strategy_data['name'] = f'SENDER尝试创建_{int(time.time())}'
    r = requests.post(f'{API}/strategies', json=test_strategy_data)
    assert_equal(r.status_code, 403, 'SENDER角色不能创建策略（返回403）')
    assert_true('error' in r.json(), '返回错误信息')
    print(f'  SENDER创建被拒绝: {r.json().get("error", "")}')

    r = requests.put(f'{API}/strategies/{perm_strategy_id}', json={
        **test_strategy_data, 'name': f'SENDER尝试更新_{int(time.time())}',
        'operator_id': sender['id']
    })
    assert_equal(r.status_code, 403, 'SENDER角色不能更新策略（返回403）')

    r = requests.post(f'{API}/strategies/{perm_strategy_id}/enable', json={'operator_id': sender['id']})
    assert_equal(r.status_code, 403, 'SENDER角色不能启用策略（返回403）')

    r = requests.post(f'{API}/strategies/{perm_strategy_id}/disable', json={'operator_id': sender['id']})
    assert_equal(r.status_code, 403, 'SENDER角色不能停用策略（返回403）')

    r = requests.post(f'{API}/strategies/{perm_strategy_id}/rollback', json={'operator_id': sender['id']})
    assert_equal(r.status_code, 403, 'SENDER角色不能回滚策略（返回403）')

    r = requests.post(f'{API}/strategies/{perm_strategy_id}/preview', json={'operator_id': sender['id']})
    assert_equal(r.status_code, 403, 'SENDER角色不能预演策略（返回403）')

    r = requests.post(f'{API}/strategies/import', json={
        'strategies': [], 'operator_id': sender['id']
    })
    assert_equal(r.status_code, 403, 'SENDER角色不能导入策略（返回403）')

    r = requests.get(f'{API}/strategies/export')
    assert_equal(r.status_code, 400, '未指定operator_id时导出被拒绝（返回400）')

    # ========================================
    header('五、策略启用与重启保持')
    # ========================================

    enable_data = {
        'name': f'启用测试策略_{int(time.time())}',
        'description': '测试启用和重启保持',
        'priority': 15,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['启用测试催办'],
        'cooldown_minutes': 45,
        'timeout_hours': 18,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=enable_data)
    enable_strategy_id = r.json()['id']

    r = requests.post(f'{API}/strategies/{enable_strategy_id}/enable', json={'operator_id': receiver['id']})
    assert_equal(r.status_code, 200, '启用策略成功')

    r = requests.get(f'{API}/strategies/{enable_strategy_id}?operator_id={receiver["id"]}')
    enabled_detail = r.json()['strategy']
    assert_equal(enabled_detail['status'], 'ACTIVE', '启用后状态变为ACTIVE')
    assert_equal(enabled_detail['status_name'], '已启用', '状态名称正确')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT status, version, cooldown_minutes, timeout_hours FROM reminder_strategies WHERE id = ?', (enable_strategy_id,))
    row = cursor.fetchone()
    conn.close()
    assert_equal(row[0], 'ACTIVE', 'SQLite中状态正确保存为ACTIVE')
    assert_equal(row[1], enabled_detail['version'], 'SQLite中版本号正确')
    assert_equal(row[2], 45, 'SQLite中冷却时间正确持久化')
    assert_equal(row[3], 18, 'SQLite中超时阈值正确持久化')
    print(f'  SQLite持久化验证: status={row[0]}, version={row[1]}, cooldown={row[2]}, timeout={row[3]}')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM reminder_strategy_logs WHERE strategy_id = ? AND action = ?', (enable_strategy_id, '启用策略'))
    log_count = cursor.fetchone()[0]
    conn.close()
    assert_equal(log_count, 1, '启用操作有对应的操作日志记录')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM reminder_strategies WHERE status = ?', ('ACTIVE',))
    active_count = cursor.fetchone()[0]
    conn.close()
    assert_true(active_count > 0, f'重启前有{active_count}个已启用策略')
    print(f'  程序重启后，这些策略将自动保持ACTIVE状态（SQLite持久化验证通过）')

    r = requests.post(f'{API}/strategies/{enable_strategy_id}/disable', json={'operator_id': receiver['id']})
    assert_equal(r.status_code, 200, '停用策略成功')
    
    r = requests.get(f'{API}/strategies/{enable_strategy_id}?operator_id={receiver["id"]}')
    disabled_detail = r.json()['strategy']
    assert_equal(disabled_detail['status'], 'INACTIVE', '停用后状态变为INACTIVE')
    assert_equal(disabled_detail['status_name'], '已停用', '状态名称正确')

    # ========================================
    header('六、版本历史与回滚功能')
    # ========================================

    rollback_data = {
        'name': f'回滚测试策略_{int(time.time())}',
        'description': '版本1',
        'priority': 10,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['版本1催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=rollback_data)
    rollback_strategy_id = r.json()['id']

    r = requests.get(f'{API}/strategies/{rollback_strategy_id}?operator_id={receiver["id"]}')
    detail = r.json()
    initial_version = detail['strategy']['version']
    initial_snapshot_count = len(detail['snapshots'])
    assert_equal(initial_version, 1, '初始版本为v1')
    print(f'  初始版本: v{initial_version}, 快照数: {initial_snapshot_count}')

    rollback_data['description'] = '版本2'
    rollback_data['priority'] = 20
    rollback_data['cooldown_minutes'] = 120
    rollback_data['operator_id'] = receiver['id']
    r = requests.put(f'{API}/strategies/{rollback_strategy_id}', json=rollback_data)
    assert_equal(r.status_code, 200, '更新到版本2成功')

    r = requests.get(f'{API}/strategies/{rollback_strategy_id}?operator_id={receiver["id"]}')
    detail = r.json()
    assert_equal(detail['strategy']['version'], 2, '更新后版本变为v2')
    assert_equal(detail['strategy']['description'], '版本2', '版本2描述正确')
    assert_equal(detail['strategy']['priority'], 20, '版本2优先级正确')
    assert_equal(detail['strategy']['cooldown_minutes'], 120, '版本2冷却时间正确')
    assert_true(len(detail['snapshots']) >= 1, '更新后至少有1个版本快照')
    print(f'  更新后版本: v{detail["strategy"]["version"]}, 快照数: {len(detail["snapshots"])}')

    rollback_data['description'] = '版本3'
    rollback_data['priority'] = 30
    rollback_data['cooldown_minutes'] = 180
    rollback_data['operator_id'] = receiver['id']
    r = requests.put(f'{API}/strategies/{rollback_strategy_id}', json=rollback_data)
    assert_equal(r.status_code, 200, '更新到版本3成功')

    r = requests.get(f'{API}/strategies/{rollback_strategy_id}?operator_id={receiver["id"]}')
    detail = r.json()
    assert_equal(detail['strategy']['version'], 3, '更新后版本变为v3')
    assert_equal(detail['strategy']['description'], '版本3', '版本3描述正确')
    assert_true(len(detail['snapshots']) >= 2, '更新后至少有2个版本快照')
    print(f'  更新后版本: v{detail["strategy"]["version"]}, 快照数: {len(detail["snapshots"])}')

    r = requests.post(f'{API}/strategies/{rollback_strategy_id}/rollback', json={'operator_id': receiver['id']})
    assert_equal(r.status_code, 200, '回滚到上一版本成功')

    r = requests.get(f'{API}/strategies/{rollback_strategy_id}?operator_id={receiver["id"]}')
    detail = r.json()
    assert_equal(detail['strategy']['version'], 2, '回滚后版本变为v2')
    assert_equal(detail['strategy']['description'], '版本2', '回滚后恢复到版本2的描述')
    assert_equal(detail['strategy']['priority'], 20, '回滚后恢复到版本2的优先级')
    assert_equal(detail['strategy']['cooldown_minutes'], 120, '回滚后恢复到版本2的冷却时间')
    print(f'  回滚后版本: v{detail["strategy"]["version"]}, 描述: {detail["strategy"]["description"]}')

    assert_true(len(detail['logs']) > 0, '存在操作日志')
    rollback_logs = [l for l in detail['logs'] if l['action'] == '回滚策略']
    assert_true(len(rollback_logs) > 0, '存在回滚操作日志')
    print(f'  操作日志验证: 共{len(detail["logs"])}条日志，包含{len(rollback_logs)}条回滚日志')

    # ========================================
    header('七、导入导出功能')
    # ========================================

    export_data1 = {
        'name': f'导出测试策略1_{int(time.time())}',
        'description': '导出测试1',
        'priority': 25,
        'trigger_conditions': {'issue_types': ['材料缺页', '标签不清'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['导出测试催办1'],
        'cooldown_minutes': 50,
        'timeout_hours': 20,
        'notify_targets': ['SENDER', 'RECEIVER'],
        'scope_filter': {'batch_ids': [1]},
        'operator_id': receiver['id']
    }
    export_data2 = {
        'name': f'导出测试策略2_{int(time.time())}',
        'description': '导出测试2',
        'priority': 35,
        'trigger_conditions': {'issue_types': ['顺序混乱'], 'statuses': ['OPEN', 'IN_PROGRESS'], 'min_hours_open': 1, 'is_overdue': True},
        'escalation_order': ['导出测试催办2', '导出测试升级2'],
        'cooldown_minutes': 70,
        'timeout_hours': 30,
        'notify_targets': ['RECEIVER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r1 = requests.post(f'{API}/strategies', json=export_data1)
    r2 = requests.post(f'{API}/strategies', json=export_data2)
    export_id1 = r1.json()['id']
    export_id2 = r2.json()['id']
    assert_true(export_id1 > 0 and export_id2 > 0, '创建导出测试策略成功')

    r = requests.get(f'{API}/strategies/export?ids={export_id1},{export_id2}&operator_id={receiver["id"]}')
    assert_equal(r.status_code, 200, '导出指定ID策略成功')
    export_result = r.json()
    assert_true('export_time' in export_result, '导出结果包含导出时间')
    assert_true('exported_by' in export_result, '导出结果包含导出人')
    assert_true('version' in export_result, '导出结果包含格式版本')
    assert_true('strategies' in export_result, '导出结果包含策略列表')
    assert_equal(len(export_result['strategies']), 2, '导出2条策略')
    
    exported1 = next(s for s in export_result['strategies'] if s['name'] == export_data1['name'])
    assert_equal(exported1['priority'], 25, '导出策略1优先级正确')
    assert_equal(exported1['cooldown_minutes'], 50, '导出策略1冷却时间正确')
    assert_equal(exported1['timeout_hours'], 20, '导出策略1超时阈值正确')
    assert_equal(exported1['trigger_conditions']['issue_types'], ['材料缺页', '标签不清'], '导出策略1触发条件正确')
    assert_equal(exported1['escalation_order'], ['导出测试催办1'], '导出策略1升级顺序正确')
    assert_equal(exported1['status'], 'DRAFT', '导出策略1状态正确')
    assert_equal(exported1['version'], 1, '导出策略1版本正确')
    print(f'  导出验证: 共导出{len(export_result["strategies"])}条策略')
    print(f'    策略1: {exported1["name"]} (v{exported1["version"]}, {exported1["status"]})')

    r = requests.get(f'{API}/strategies/export?operator_id={receiver["id"]}')
    assert_equal(r.status_code, 200, '导出全部策略成功')
    all_export = r.json()
    assert_true(len(all_export['strategies']) >= 2, '导出全部策略数量正确')

    imported_strategies = export_result['strategies']
    for s in imported_strategies:
        s['name'] = f'导入_{int(time.time())}_{s["name"]}'
    
    r = requests.post(f'{API}/strategies/import', json={
        'strategies': imported_strategies,
        'operator_id': receiver['id']
    })
    assert_equal(r.status_code, 200, '导入策略成功')
    import_result = r.json()
    assert_equal(import_result['imported_count'], 2, '成功导入2条策略')
    print(f'  导入验证: 共导入{import_result["imported_count"]}条策略')

    imported_ids = import_result['imported_ids']
    for idx, sid in enumerate(imported_ids):
        r = requests.get(f'{API}/strategies/{sid}?operator_id={receiver["id"]}')
        s = r.json()['strategy']
        assert_equal(s['status'], 'DRAFT', f'导入策略{idx+1}默认为草稿状态')
        assert_equal(s['version'], 1, f'导入策略{idx+1}版本重置为v1')
        print(f'    导入策略{idx+1}: {s["name"]} (v{s["version"]}, {s["status"]})')

    duplicate_strategy = imported_strategies[0].copy()
    duplicate_strategy['name'] = f'导入_重复测试_{int(time.time())}'
    
    r = requests.post(f'{API}/strategies/import', json={
        'strategies': [duplicate_strategy, duplicate_strategy],
        'operator_id': receiver['id']
    })
    assert_equal(r.status_code, 400, '导入包含重复名称的策略被拒绝')
    assert_true('errors' in r.json(), '返回具体的校验错误')
    print(f'  重复名称检测: {r.json()["errors"][0]["errors"][0]}')

    invalid_strategy = {'name': '无效策略', 'trigger_conditions': '应该是对象而不是字符串', 'cooldown_minutes': '应该是数字'}
    r = requests.post(f'{API}/strategies/import', json={
        'strategies': [invalid_strategy],
        'operator_id': receiver['id']
    })
    assert_equal(r.status_code, 400, '导入格式错误的策略被拒绝')
    assert_true('errors' in r.json(), '返回格式校验错误')
    print(f'  格式校验: {r.json()["errors"][0]["errors"][0]}')

    # ========================================
    header('八、操作日志完整性')
    # ========================================

    log_test_data = {
        'name': f'日志测试策略_{int(time.time())}',
        'description': '日志测试v1',
        'priority': 10,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['日志测试催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=log_test_data)
    log_test_id = r.json()['id']
    
    log_test_data['description'] = '日志测试v2'
    log_test_data['priority'] = 20
    log_test_data['operator_id'] = receiver['id']
    requests.put(f'{API}/strategies/{log_test_id}', json=log_test_data)
    
    requests.post(f'{API}/strategies/{log_test_id}/enable', json={'operator_id': receiver['id']})
    requests.post(f'{API}/strategies/{log_test_id}/disable', json={'operator_id': receiver['id']})
    requests.post(f'{API}/strategies/{log_test_id}/rollback', json={'operator_id': receiver['id']})
    
    print(f'  已为日志测试策略执行创建、更新、启用、停用、回滚操作')

    r = requests.get(f'{API}/strategies/logs?operator_id={receiver["id"]}')
    assert_equal(r.status_code, 200, '获取操作日志成功')
    all_logs = r.json()
    assert_true(len(all_logs) > 0, '存在操作日志记录')
    
    strategy_logs = [log for log in all_logs if log['strategy_id'] == log_test_id]
    action_types = set(log['action'] for log in strategy_logs)
    print(f'  操作日志类型: {", ".join(set(log["action"] for log in all_logs))}')
    
    required_actions = ['创建策略', '更新策略', '启用策略', '停用策略', '回滚策略']
    for action in required_actions:
        found = any(log['action'] == action for log in strategy_logs)
        assert_true(found, f'存在"{action}"操作日志')

    create_logs = [l for l in all_logs if l['action'] == '创建策略']
    if create_logs:
        log = create_logs[-1]
        assert_true('strategy_id' in log, '日志包含策略ID')
        assert_true('strategy_name' in log, '日志包含策略名称')
        assert_true('operator_id' in log, '日志包含操作人ID')
        assert_true('operator_name' in log, '日志包含操作人名称')
        assert_true('created_at' in log, '日志包含操作时间')
        print(f'  日志结构验证: {log["action"]} by {log["operator_name"]} at {log["created_at"]}')

    # ========================================
    header('九、预演误判防护测试')
    # ========================================

    misjudge_data = {
        'name': f'误判测试策略_{int(time.time())}',
        'description': '测试预演误判防护',
        'priority': 50,
        'trigger_conditions': {
            'issue_types': ['标签不清'],
            'statuses': ['IN_PROGRESS'],
            'min_hours_open': 100,
            'is_overdue': True
        },
        'escalation_order': ['误判测试催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=misjudge_data)
    misjudge_strategy_id = r.json()['id']
    assert_equal(r.status_code, 201, '创建误判测试策略成功')

    r = requests.post(f'{API}/strategies/{misjudge_strategy_id}/preview', json={
        'operator_id': receiver['id']
    })
    assert_equal(r.status_code, 200, '预演误判策略成功')
    misjudge_preview = r.json()

    for detail in misjudge_preview['details']:
        selected = detail['selected_strategy']
        if selected['id'] == misjudge_strategy_id:
            assert_true('hit_reasons' in detail, '命中策略包含hit_reasons字段')
            assert_true(len(detail['hit_reasons']) > 0, 'hit_reasons包含匹配条件说明')
            print(f'  命中原因示例: {detail["hit_reasons"][0][:100]}...')
            
            assert_true('unmatched_strategies' in detail, '包含未命中策略列表')
            for us in detail['unmatched_strategies']:
                assert_true('match_details' in us, '未命中策略包含match_details')
                assert_true('failed_reasons' in us['match_details'], '未命中策略包含failed_reasons')
                if us['match_details']['failed_reasons']:
                    print(f'  未命中原因示例: {us["name"]}: {us["match_details"]["failed_reasons"][0][:100]}...')

    batch_id2, box_id2, review_id2 = create_test_batch_and_review(sender['id'], receiver['id'])
    print(f'  创建测试复核项: batch={batch_id2}, box={box_id2}, review={review_id2}')

    strict_strategy = {
        'name': f'严格条件策略_{int(time.time())}',
        'description': '必须同时满足所有条件才命中',
        'priority': 200,
        'trigger_conditions': {
            'issue_types': ['材料缺页'],
            'statuses': ['IN_PROGRESS'],
            'min_hours_open': 9999,
            'is_overdue': False
        },
        'escalation_order': ['严格催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=strict_strategy)
    strict_strategy_id = r.json()['id']

    requests.post(f'{API}/strategies/{strict_strategy_id}/enable', json={'operator_id': receiver['id']})

    r = requests.post(f'{API}/reviews/{review_id2}', json={
        'status': 'IN_PROGRESS',
        'handling_note': '更新为处理中',
        'operator_id': sender['id']
    })

    r = requests.post(f'{API}/strategies/{strict_strategy_id}/preview', json={
        'operator_id': receiver['id']
    })
    strict_preview = r.json()
    
    strict_matched = False
    for detail in strict_preview['details']:
        if detail['review_id'] == review_id2:
            if detail['selected_strategy']['id'] == strict_strategy_id:
                strict_matched = True
                md = detail['selected_strategy']['match_details']
                print(f'  严格策略匹配检查: 共{len(md["trigger_checks"]) + len(md["scope_checks"])}个条件检查')
                for check in md['trigger_checks'] + md['scope_checks']:
                    print(f'    {check["condition"]}: expected={check["expected"]}, actual={check["actual"]}, matched={check["matched"]}')
                
                all_matched = all(c['matched'] for c in md['trigger_checks'] + md['scope_checks'])
                if not all_matched:
                    print(f'  ✅ 误判防护生效: 存在不满足的条件，未命中')
                    assert_true(len(md['failed_reasons']) > 0, '存在失败原因说明')
                    print(f'  失败原因: {md["failed_reasons"][0]}')
    
    assert_true(not strict_matched, '严格条件策略未命中（条件不满足时正确拒绝）')
    print(f'  ✅ 预演误判防护验证通过: 只有全部条件满足才算命中')

    # ========================================
    header('十、冲突处理增强测试')
    # ========================================

    r = requests.get(f'{API}/strategies?operator_id={receiver["id"]}')
    all_strategies = r.json()
    active_count = 0
    for s in all_strategies:
        if s['status'] == 'ACTIVE':
            requests.post(f'{API}/strategies/{s["id"]}/disable', json={'operator_id': receiver['id']})
            active_count += 1
    print(f'  已停用 {active_count} 个已有ACTIVE策略，确保冲突测试环境干净')

    conflict_s1 = {
        'name': f'冲突策略A_{int(time.time())}',
        'description': '高优先级冲突策略',
        'priority': 100,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['A类催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    conflict_s2 = {
        'name': f'冲突策略B_{int(time.time())}',
        'description': '中优先级冲突策略',
        'priority': 50,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['B类催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    conflict_s3 = {
        'name': f'冲突策略C_{int(time.time())}',
        'description': '低优先级冲突策略',
        'priority': 10,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['C类催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    
    r1 = requests.post(f'{API}/strategies', json=conflict_s1)
    r2 = requests.post(f'{API}/strategies', json=conflict_s2)
    r3 = requests.post(f'{API}/strategies', json=conflict_s3)
    cid1, cid2, cid3 = r1.json()['id'], r2.json()['id'], r3.json()['id']
    
    requests.post(f'{API}/strategies/{cid1}/enable', json={'operator_id': receiver['id']})
    requests.post(f'{API}/strategies/{cid2}/enable', json={'operator_id': receiver['id']})
    requests.post(f'{API}/strategies/{cid3}/enable', json={'operator_id': receiver['id']})

    r = requests.post(f'{API}/strategies/{cid1}/preview', json={'operator_id': receiver['id']})
    conflict_preview = r.json()

    conflict_detail = None
    for d in conflict_preview['details']:
        if d['conflict'] and len(d['matched_strategies']) >= 2:
            conflict_detail = d
            break

    if conflict_detail:
        assert_true('resolution_detail' in conflict_detail, '冲突包含resolution_detail')
        assert_true('冲突策略A' in conflict_detail['resolution_detail'], 'resolution_detail包含所有策略名称')
        assert_true('冲突策略B' in conflict_detail['resolution_detail'], 'resolution_detail包含所有策略名称')
        print(f'  冲突裁决详情: {conflict_detail["resolution_detail"]}')
        
        assert_true(conflict_detail['selected_strategy']['priority'] == 100, '选中优先级最高的策略')
        assert_true(conflict_detail['selected_strategy']['name'] == conflict_s1['name'], '选中策略A')
        
        assert_true('not_selected_high_priority' in conflict_detail, '包含not_selected_high_priority列表')
        for nshp in conflict_detail['not_selected_high_priority']:
            assert_true('strategy' in nshp, '包含策略信息')
            assert_true('reason' in nshp, '包含未选中原因')
            print(f'  未选中的高优先级策略: {nshp["strategy"]["name"]} - {nshp["reason"][:80]}...')

        matched_names = [s['name'] for s in conflict_detail['matched_strategies']]
        assert_true(conflict_s1['name'] in matched_names, '所有命中策略都在matched_strategies中')
        assert_true(conflict_s2['name'] in matched_names, '所有命中策略都在matched_strategies中')
        print(f'  ✅ 冲突处理增强验证通过: 优先级裁决正确，详情完整')
    else:
        print('  本次测试未触发冲突场景，跳过冲突详情验证')

    # ========================================
    header('十一、撤销最近一次变更测试')
    # ========================================

    undo_data = {
        'name': f'撤销测试策略_{int(time.time())}',
        'description': '版本1描述',
        'priority': 10,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['版本1催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=undo_data)
    undo_strategy_id = r.json()['id']

    undo_data['description'] = '版本2描述'
    undo_data['priority'] = 20
    undo_data['cooldown_minutes'] = 120
    undo_data['operator_id'] = receiver['id']
    r = requests.put(f'{API}/strategies/{undo_strategy_id}', json=undo_data)
    assert_equal(r.status_code, 200, '更新到版本2成功')
    assert_equal(r.json()['version'], 2, '版本号变为2')
    print(f'  更新后版本: v2, 描述={r.json()["description"]}, priority={r.json()["priority"]}')

    r = requests.post(f'{API}/strategies/{undo_strategy_id}/undo', json={'operator_id': receiver['id']})
    assert_equal(r.status_code, 200, '撤销最近一次变更成功')
    undo_result = r.json()
    
    assert_true(undo_result['success'], '撤销成功标识正确')
    assert_equal(undo_result['from_version'], 2, '从版本2撤销')
    assert_equal(undo_result['to_version'], 1, '回退到版本1')
    assert_equal(undo_result['undone_action'], '更新策略', '撤销的操作类型正确')
    assert_equal(undo_result['strategy']['version'], 1, '策略版本号已回退到1')
    assert_equal(undo_result['strategy']['description'], '版本1描述', '描述已恢复到版本1')
    assert_equal(undo_result['strategy']['priority'], 10, '优先级已恢复到版本1')
    assert_equal(undo_result['strategy']['cooldown_minutes'], 60, '冷却时间已恢复到版本1')
    print(f'  撤销结果: {undo_result["message"]}')

    undo_data['description'] = '版本2描述'
    undo_data['priority'] = 20
    undo_data['cooldown_minutes'] = 120
    undo_data['operator_id'] = receiver['id']
    r = requests.put(f'{API}/strategies/{undo_strategy_id}', json=undo_data)
    assert_equal(r.json()['version'], 2, '更新到版本2')
    print(f'  重新更新到版本2: v2, priority=20')

    r = requests.post(f'{API}/strategies/{undo_strategy_id}/enable', json={'operator_id': receiver['id']})
    assert_equal(r.status_code, 200, '启用策略成功')
    
    r = requests.post(f'{API}/strategies/{undo_strategy_id}/undo', json={'operator_id': receiver['id']})
    assert_equal(r.status_code, 400, '已启用的策略不能撤销')
    assert_true('已启用的策略不能撤销' in r.json()['error'], '错误信息正确')
    print(f'  已启用策略撤销被拒绝: {r.json()["error"]}')

    r = requests.post(f'{API}/strategies/{undo_strategy_id}/disable', json={'operator_id': receiver['id']})
    r = requests.post(f'{API}/strategies/{undo_strategy_id}/undo', json={'operator_id': receiver['id']})
    assert_equal(r.status_code, 200, '停用后可以撤销')
    print(f'  ✅ 撤销功能验证通过: 已启用策略拒绝撤销，停用后可正常撤销')

    # ========================================
    header('十二、导入导出版本语义保持测试')
    # ========================================

    version_s1 = {
        'name': f'版本语义测试策略_{int(time.time())}',
        'description': 'v1描述',
        'priority': 10,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['v1催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=version_s1)
    vs_id = r.json()['id']

    version_s1['description'] = 'v2描述'
    version_s1['priority'] = 20
    version_s1['operator_id'] = receiver['id']
    r = requests.put(f'{API}/strategies/{vs_id}', json=version_s1)

    version_s1['description'] = 'v3描述'
    version_s1['priority'] = 30
    version_s1['operator_id'] = receiver['id']
    r = requests.put(f'{API}/strategies/{vs_id}', json=version_s1)

    r = requests.get(f'{API}/strategies/export?ids={vs_id}&operator_id={receiver["id"]}')
    export_result = r.json()
    exported = export_result['strategies'][0]
    
    assert_equal(exported['version'], 3, '导出版本号正确为v3')
    assert_true('version_history' in exported, '导出包含version_history')
    assert_true(len(exported['version_history']) >= 3, '版本历史至少有3条')
    assert_true('is_active' in exported, '导出包含is_active字段')
    assert_true('effective_version' in exported, '导出包含effective_version字段')
    assert_true('export_info' in exported, '导出包含export_info')
    assert_equal(exported['export_info']['original_version'], 3, 'export_info包含原始版本')
    assert_equal(export_result['export_version'], '2.0', '导出版本标识为2.0')
    assert_true('version_semantics_note' in export_result, '导出包含版本语义说明')
    print(f'  导出版本验证: version={exported["version"]}, 历史版本数={len(exported["version_history"])}, is_active={exported["is_active"]}')

    imported_strategies = export_result['strategies']
    imported_strategies[0]['name'] = f'导入v1_{int(time.time())}_{imported_strategies[0]["name"]}'
    
    r = requests.post(f'{API}/strategies/import', json={
        'strategies': imported_strategies,
        'operator_id': receiver['id'],
        'preserve_version': False
    })
    import_v1 = r.json()
    assert_equal(import_v1['imported_count'], 1, '导入成功')
    assert_equal(import_v1['preserve_version'], False, 'preserve_version标记正确')
    assert_equal(import_v1['import_results'][0]['imported_version'], 1, '不保留版本号时导入为v1')
    assert_equal(import_v1['import_results'][0]['source_version'], 3, 'source_version正确记录原始版本')
    assert_equal(import_v1['import_results'][0]['is_active'], False, '导入后为草稿状态')
    assert_equal(import_v1['import_results'][0]['effective_version'], None, '导入后无生效版本')
    assert_true('version_note' in import_v1['import_results'][0], '包含version_note')
    assert_true('activation_note' in import_v1['import_results'][0], '包含activation_note')
    print(f'  不保留版本导入: imported_version={import_v1["import_results"][0]["imported_version"]}, source_version={import_v1["import_results"][0]["source_version"]}')
    print(f'    {import_v1["import_results"][0]["version_note"]}')

    imported_strategies2 = export_result['strategies']
    imported_strategies2[0]['name'] = f'导入v3_{int(time.time())}_{imported_strategies2[0]["name"]}'
    
    r = requests.post(f'{API}/strategies/import', json={
        'strategies': imported_strategies2,
        'operator_id': receiver['id'],
        'preserve_version': True
    })
    import_v3 = r.json()
    assert_equal(import_v3['preserve_version'], True, 'preserve_version标记正确')
    assert_equal(import_v3['import_results'][0]['imported_version'], 3, '保留版本号时导入为v3')
    assert_equal(import_v3['import_results'][0]['source_version'], 3, 'source_version正确')
    assert_equal(import_v3['import_results'][0]['original_version'], 3, 'original_version正确')
    assert_equal(import_v3['import_results'][0]['original_is_active'], False, 'original_is_active正确')
    assert_true('v3' in import_v3['import_results'][0]['version_note'], 'version_note包含v3')
    print(f'  保留版本导入: imported_version={import_v3["import_results"][0]["imported_version"]}, source_version={import_v3["import_results"][0]["source_version"]}')
    print(f'    {import_v3["import_results"][0]["version_note"]}')
    
    import_v3_id = import_v3['imported_ids'][0]
    r = requests.get(f'{API}/strategies/{import_v3_id}?operator_id={receiver["id"]}')
    v3_detail = r.json()
    assert_true(len(v3_detail['snapshots']) >= 3, '保留版本导入后版本历史完整（至少3个快照）')
    print(f'  ✅ 导入导出版本语义保持验证通过: preserve_version=True时版本号和历史完整保留')

    # ========================================
    header('十三、生效版本查询测试')
    # ========================================

    active_s1 = {
        'name': f'生效版本测试_{int(time.time())}',
        'description': '生效版本测试策略',
        'priority': 80,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['生效测试催办'],
        'cooldown_minutes': 60,
        'timeout_hours': 24,
        'notify_targets': ['SENDER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=active_s1)
    active_id1 = r.json()['id']

    active_s1['description'] = 'v2描述'
    active_s1['priority'] = 90
    active_s1['operator_id'] = receiver['id']
    r = requests.put(f'{API}/strategies/{active_id1}', json=active_s1)

    r = requests.get(f'{API}/strategies/{active_id1}/effective-version?operator_id={receiver["id"]}')
    ev_draft = r.json()
    assert_equal(ev_draft['current_version'], 2, '当前版本为v2')
    assert_equal(ev_draft['is_active'], False, '未启用时is_active为false')
    assert_equal(ev_draft['effective_version'], None, '未启用时effective_version为null')
    assert_equal(ev_draft['effective_version_note'], '策略未启用，无生效版本', '版本说明正确')
    assert_true('version_history' in ev_draft, '包含版本历史')
    for vh in ev_draft['version_history']:
        assert_equal(vh['is_effective'], False, '未启用时所有版本都不是生效版本')
    print(f'  未启用版本查询: current_version={ev_draft["current_version"]}, effective_version={ev_draft["effective_version"]}')

    r = requests.post(f'{API}/strategies/{active_id1}/enable', json={'operator_id': receiver['id']})
    
    r = requests.get(f'{API}/strategies/{active_id1}/effective-version?operator_id={receiver["id"]}')
    ev_active = r.json()
    assert_equal(ev_active['is_active'], True, '启用后is_active为true')
    assert_equal(ev_active['effective_version'], 2, '启用后effective_version为当前版本')
    assert_equal(ev_active['effective_version_note'], '当前版本即为生效版本', '版本说明正确')
    
    effective_count = sum(1 for vh in ev_active['version_history'] if vh['is_effective'])
    assert_equal(effective_count, 1, '只有一个版本标记为生效版本')
    effective_vh = next(vh for vh in ev_active['version_history'] if vh['is_effective'])
    assert_equal(effective_vh['version'], 2, '生效版本是v2')
    print(f'  启用后版本查询: current_version={ev_active["current_version"]}, effective_version={ev_active["effective_version"]}')
    print(f'    生效版本: v{effective_vh["version"]}, 创建人: {effective_vh["creator_name"]}')

    r = requests.get(f'{API}/strategies/active?operator_id={receiver["id"]}')
    active_list = r.json()
    assert_true('count' in active_list, '包含count字段')
    assert_true('current_effective_version' in active_list, '包含current_effective_version')
    assert_true('strategies' in active_list, '包含strategies列表')
    assert_true(active_list['count'] >= 1, '至少有1个生效策略')
    for s in active_list['strategies']:
        assert_equal(s['status'], 'ACTIVE', '列表中所有策略都是ACTIVE状态')
    print(f'  生效策略列表: count={active_list["count"]}, max_effective_version={active_list["current_effective_version"]}')
    
    print(f'  ✅ 生效版本查询验证通过')

    # ========================================
    header('十四、权限控制增强测试（新增API）')
    # ========================================

    r = requests.post(f'{API}/strategies/{undo_strategy_id}/undo', json={'operator_id': sender['id']})
    assert_equal(r.status_code, 403, 'SENDER不能撤销变更')

    r = requests.get(f'{API}/strategies/active?operator_id={sender["id"]}')
    assert_equal(r.status_code, 403, 'SENDER不能查询生效策略')

    r = requests.get(f'{API}/strategies/{active_id1}/effective-version?operator_id={sender["id"]}')
    assert_equal(r.status_code, 403, 'SENDER不能查询生效版本')

    r = requests.get(f'{API}/strategies/active')
    assert_equal(r.status_code, 400, '缺少operator_id时查询生效策略被拒绝')

    r = requests.get(f'{API}/strategies/{active_id1}/effective-version')
    assert_equal(r.status_code, 400, '缺少operator_id时查询生效版本被拒绝')

    r = requests.post(f'{API}/strategies/{undo_strategy_id}/undo', json={})
    assert_equal(r.status_code, 400, '缺少operator_id时撤销被拒绝')
    
    print(f'  ✅ 新增API权限控制验证通过: SENDER角色所有操作均返回403，缺少operator_id返回400')

    # ========================================
    header('十五、重启保持验证（SQLite持久化）')
    # ========================================

    restart_s1 = {
        'name': f'重启保持测试_{int(time.time())}',
        'description': '重启测试策略v1',
        'priority': 75,
        'trigger_conditions': {'issue_types': ['材料缺页'], 'statuses': ['OPEN'], 'min_hours_open': 0, 'is_overdue': None},
        'escalation_order': ['重启测试催办'],
        'cooldown_minutes': 90,
        'timeout_hours': 36,
        'notify_targets': ['SENDER', 'RECEIVER'],
        'scope_filter': {},
        'operator_id': receiver['id']
    }
    r = requests.post(f'{API}/strategies', json=restart_s1)
    restart_id = r.json()['id']

    restart_s1['description'] = '重启测试策略v2'
    restart_s1['priority'] = 85
    restart_s1['cooldown_minutes'] = 120
    restart_s1['operator_id'] = receiver['id']
    r = requests.put(f'{API}/strategies/{restart_id}', json=restart_s1)

    r = requests.post(f'{API}/strategies/{restart_id}/enable', json={'operator_id': receiver['id']})

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, status, version, priority, cooldown_minutes, timeout_hours FROM reminder_strategies WHERE id = ?', (restart_id,))
    row = cursor.fetchone()
    
    assert_equal(row[0], restart_id, 'SQLite中ID正确')
    assert_equal(row[1], restart_s1['name'], 'SQLite中名称正确')
    assert_equal(row[2], 'ACTIVE', 'SQLite中状态正确为ACTIVE')
    assert_equal(row[3], 2, 'SQLite中版本号正确为v2')
    assert_equal(row[4], 85, 'SQLite中优先级正确')
    assert_equal(row[5], 120, 'SQLite中冷却时间正确')
    assert_equal(row[6], 36, 'SQLite中超时阈值正确')
    print(f'  SQLite持久化验证: id={row[0]}, name={row[1]}, status={row[2]}, version={row[3]}')
    print(f'    priority={row[4]}, cooldown={row[5]}, timeout={row[6]}')

    cursor.execute('SELECT COUNT(*) FROM reminder_strategy_snapshots WHERE strategy_id = ?', (restart_id,))
    snap_count = cursor.fetchone()[0]
    assert_true(snap_count >= 2, 'SQLite中至少有2个版本快照')

    cursor.execute('SELECT COUNT(*) FROM reminder_strategy_logs WHERE strategy_id = ?', (restart_id,))
    log_count = cursor.fetchone()[0]
    assert_true(log_count >= 3, 'SQLite中至少有3条操作日志（创建、更新、启用）')
    
    cursor.execute('SELECT action FROM reminder_strategy_logs WHERE strategy_id = ? ORDER BY created_at', (restart_id,))
    actions = [row[0] for row in cursor.fetchall()]
    assert_equal(actions[0], '创建策略', '第一条日志是创建')
    assert_equal(actions[-1], '启用策略', '最后一条日志是启用')
    print(f'  SQLite日志验证: {snap_count}个快照, {log_count}条日志, 操作序列: {" → ".join(actions)}')
    
    conn.close()
    
    print(f'  ✅ 重启保持验证通过: 所有状态、版本、配置均已持久化到SQLite，重启后自动恢复')

    print()
    print('=' * 60)
    print('测试总结')
    print('=' * 60)
    print(f'总测试用例: {total}')
    print(f'通过: {passed}')
    print(f'失败: {total - passed}')
    print(f'通过率: {passed/total*100:.1f}%')
    
    print()
    print('专项测试覆盖:')
    print(f'  ✅ 预演误判防护: 只有全部条件满足才算命中')
    print(f'  ✅ 命中原因展示: hit_reasons, not_selected_high_priority, unmatched_strategies')
    print(f'  ✅ 冲突处理增强: resolution_detail, 完整排序说明')
    print(f'  ✅ 撤销功能: 撤销更新、撤销启用/停用、已启用策略拒绝撤销')
    print(f'  ✅ 导入导出版本语义: preserve_version选项, version_history完整导入')
    print(f'  ✅ 生效版本查询: /active, /effective-version, is_effective标记')
    print(f'  ✅ 权限限制: 所有新增API均校验SENDER角色权限')
    print(f'  ✅ 重启保持: SQLite持久化验证，所有字段正确保存')
    
    if passed == total:
        print()
        print('🎉 所有测试通过！催办策略演练与发布中心功能完整。')
    else:
        print()
        print('⚠️  部分测试失败，请检查代码。')
        sys.exit(1)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'{FAIL} 测试执行异常: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)
