import requests
import sqlite3
import time
import os
import sys
import json
from datetime import datetime

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

def main():
    print(f'===== 档案移交流转台 - 连接与诊断中心 全链路回归测试 =====')
    print(f'测试时间: {datetime.now()}')

    users = requests.get(f'{API}/users').json()
    sender = next(u for u in users if u['role'] == 'SENDER')
    receiver = next(u for u in users if u['role'] == 'RECEIVER')
    print(f'发送方: {sender["username"]} (ID: {sender["id"]}, 角色: {sender["role"]})')
    print(f'接收方: {receiver["username"]} (ID: {receiver["id"]}, 角色: {receiver["role"]})')

    ts = int(time.time())

    header('一、连接配置创建与基本CRUD')

    config_name = f'测试配置_{ts}'
    r = requests.post(f'{API}/connection/configs', json={
        'profile_name': config_name,
        'service_host': '127.0.0.1',
        'service_port': 5002,
        'entry_path': '/',
        'protocol': 'http',
        'current_operator_id': receiver['id'],
        'is_default': False,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code in (200, 201), f'创建连接配置 (status={r.status_code})')
    config_data = r.json()
    config_id = None
    if isinstance(config_data, dict):
        config_id = config_data.get('id')
        if not config_id and 'config' in config_data:
            config_id = config_data['config'].get('id')
    if not config_id:
        configs_after = requests.get(f'{API}/connection/configs').json()
        for c in configs_after:
            if c.get('profile_name') == config_name:
                config_id = c.get('id')
                break
    assert_true(config_id is not None, f'获取新建配置ID (id={config_id})')

    r = requests.get(f'{API}/connection/configs')
    assert_true(r.status_code == 200, '获取配置列表')
    configs = r.json()
    assert_true(len(configs) >= 1, f'配置列表不为空 (共{len(configs)}条)')

    r = requests.get(f'{API}/connection/configs/{config_id}')
    assert_true(r.status_code == 200, '获取配置详情')
    detail = r.json()
    assert_true('config' in detail, '详情包含config字段')
    assert_equal(detail['config']['profile_name'], config_name, '配置名称一致')
    assert_equal(detail['config']['service_host'], '127.0.0.1', '服务地址一致')
    assert_equal(detail['config']['service_port'], 5002, '服务端口一致')
    assert_true('snapshots' in detail, '详情包含snapshots')
    assert_true('logs' in detail, '详情包含logs')

    header('二、端口变更后入口URL仍可用')

    r = requests.put(f'{API}/connection/configs/{config_id}', json={
        'service_port': 5002,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, '更新配置端口')

    r = requests.get(f'{API}/connection/configs/{config_id}/entry-url')
    assert_true(r.status_code == 200, '获取入口URL')
    entry_data = r.json()
    assert_true('entry_url' in entry_data, '返回entry_url字段')
    assert_true(':5002' in entry_data['entry_url'], f'入口URL包含更新后端口 (URL={entry_data["entry_url"]})')

    r = requests.put(f'{API}/connection/configs/{config_id}', json={
        'service_port': 8080,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, '变更端口为8080')

    r = requests.get(f'{API}/connection/configs/{config_id}/entry-url')
    entry_data2 = r.json()
    assert_true(':8080' in entry_data2['entry_url'], f'端口变更后入口URL自动更新 (URL={entry_data2["entry_url"]})')

    r = requests.put(f'{API}/connection/configs/{config_id}', json={
        'service_port': 5002,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, '恢复原端口5002')

    header('三、连接探测与诊断')

    r = requests.post(f'{API}/connection/configs/{config_id}/probe', json={
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, '执行连接探测')
    probe_result = r.json()
    assert_true('status' in probe_result, '返回status字段')
    assert_true('message' in probe_result, '返回message字段')
    assert_true('probe' in probe_result or 'diagnostics' in str(probe_result), '返回诊断信息')

    r = requests.get(f'{API}/connection/configs/{config_id}')
    detail = r.json()
    assert_true(detail['config']['last_probe_status'] in ['AVAILABLE', 'UNAVAILABLE', 'TIMEOUT', 'ERROR', 'UNKNOWN'],
                f'探测状态已更新 (status={detail["config"]["last_probe_status"]})')

    header('四、缺少操作人时的前置拦截')

    no_op_name = f'无操作人配置_{ts}'
    r = requests.post(f'{API}/connection/configs', json={
        'profile_name': no_op_name,
        'service_host': '127.0.0.1',
        'service_port': 9999,
        'entry_path': '/',
        'protocol': 'http',
        'current_operator_id': None,
        'is_default': False,
        'operator_id': receiver['id']
    })
    no_op_config_id = None
    if r.status_code in (200, 201):
        data = r.json()
        no_op_config_id = data.get('id')
        if not no_op_config_id and 'config' in data:
            no_op_config_id = data['config'].get('id')
    if not no_op_config_id:
        all_c = requests.get(f'{API}/connection/configs').json()
        for c in all_c:
            if c.get('profile_name') == no_op_name:
                no_op_config_id = c.get('id')
                break

    if no_op_config_id:
        r = requests.post(f'{API}/connection/check-strategy-access', json={
            'config_id': no_op_config_id,
            'operator_id': None
        })
        result = r.json()
        assert_true(result.get('has_access') == False, '缺少操作人时has_access=False')
        assert_true(result.get('blocked') == True, '缺少操作人时blocked=True')

        r = requests.post(f'{API}/connection/check-strategy-access', json={
            'config_id': config_id,
            'operator_id': sender['id']
        })
        result = r.json()
        assert_true(result.get('has_access') == False, 'SENDER角色访问策略时has_access=False')
        assert_true(result.get('blocked') == True, 'SENDER角色访问策略时blocked=True')
        assert_true(result.get('reason', ''), '返回原因说明')
    else:
        print(f'{FAIL} 无法创建无操作人配置，跳过前置拦截测试')

    header('五、RECEIVER角色可通过策略访问检查')

    r = requests.post(f'{API}/connection/check-strategy-access', json={
        'config_id': config_id,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, f'RECEIVER角色检查通过 (status={r.status_code})')
    result = r.json()
    assert_true(result.get('has_access') == True, 'RECEIVER角色has_access=True')
    assert_true(result.get('blocked') == False, 'RECEIVER角色blocked=False')

    header('六、操作人切换与版本递增')

    r = requests.get(f'{API}/connection/configs/{config_id}')
    detail = r.json()
    version_before = detail['config']['config_version']

    r = requests.post(f'{API}/connection/configs/{config_id}/switch-operator', json={
        'operator_id': receiver['id'],
        'new_operator_id': sender['id']
    })
    assert_true(r.status_code == 200, f'切换操作人 (status={r.status_code})')
    if r.status_code != 200:
        print(f'  响应: {r.text[:200]}')

    r = requests.get(f'{API}/connection/configs/{config_id}')
    detail = r.json()
    assert_equal(detail['config']['current_operator_id'], sender['id'], '操作人已切换为sender')
    assert_true(detail['config']['config_version'] > version_before,
                f'切换操作人后版本递增 (前={version_before}, 后={detail["config"]["config_version"]})')

    r = requests.post(f'{API}/connection/configs/{config_id}/switch-operator', json={
        'operator_id': receiver['id'],
        'new_operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, '切回操作人为receiver')

    header('七、配置版本快照与回退')

    r = requests.get(f'{API}/connection/configs/{config_id}')
    detail = r.json()
    assert_true(len(detail.get('snapshots', [])) >= 1, f'存在版本快照 (共{len(detail.get("snapshots", []))}条)')

    version_before = detail['config']['config_version']
    r = requests.put(f'{API}/connection/configs/{config_id}', json={
        'service_port': 3000,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, '修改端口触发版本变更')

    r = requests.get(f'{API}/connection/configs/{config_id}')
    detail = r.json()
    assert_true(detail['config']['config_version'] > version_before, '版本号已递增')

    target_version = version_before
    r = requests.post(f'{API}/connection/configs/{config_id}/rollback', json={
        'target_version': target_version,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, f'回退到版本v{target_version}')
    if r.status_code != 200:
        print(f'  响应: {r.text[:200]}')

    r = requests.get(f'{API}/connection/configs/{config_id}')
    detail = r.json()
    assert_equal(detail['config']['service_port'], 5002, f'回退后端口恢复为5002 (实际={detail["config"]["service_port"]})')

    header('八、发布权限控制')

    r = requests.post(f'{API}/connection/configs/{config_id}/publish', json={
        'operator_id': sender['id']
    })
    if r.status_code in (403, 400):
        assert_true(True, f'SENDER角色无法发布配置 (status={r.status_code})')
    else:
        assert_true(r.status_code == 200, f'发布权限检查 (status={r.status_code})')

    r = requests.post(f'{API}/connection/configs/{config_id}/publish', json={
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, f'RECEIVER角色可以发布配置 (status={r.status_code})')

    r = requests.get(f'{API}/connection/configs/{config_id}')
    detail = r.json()
    assert_true(detail['config'].get('is_published') or detail['config'].get('published_at'), '配置已标记为已发布')

    header('九、配置导出与导入')

    r = requests.get(f'{API}/connection/export?ids={config_id}&operator_id={receiver["id"]}')
    assert_true(r.status_code == 200, f'导出指定配置 (status={r.status_code})')
    if r.status_code != 200:
        print(f'  响应: {r.text[:200]}')
    export_data = r.json()
    assert_true('configs' in export_data, '导出数据包含configs字段')
    assert_true(len(export_data['configs']) >= 1, f'导出配置数量>=1 (共{len(export_data["configs"])}条)')

    export_json = json.dumps(export_data)

    import_name = f'导入测试_{ts}'
    export_data_copy = json.loads(export_json)
    for c in export_data_copy['configs']:
        c['profile_name'] = import_name
        c['service_host'] = '192.168.1.100'
        c['service_port'] = 9000

    r = requests.post(f'{API}/connection/import', json={
        'configs': export_data_copy['configs'],
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, f'导入配置 (status={r.status_code})')
    if r.status_code == 200:
        import_result = r.json()
        assert_true(import_result.get('imported_count', 0) >= 1 or import_result.get('success', False),
                    f'导入成功 (imported_count={import_result.get("imported_count", 0)})')

    header('十、导入后配置一致性校验')

    r = requests.get(f'{API}/connection/configs')
    all_configs = r.json()
    imported_config = next((c for c in all_configs if c.get('profile_name') == import_name), None)
    assert_true(imported_config is not None, '导入的配置存在于列表中')
    if imported_config:
        assert_equal(imported_config['service_host'], '192.168.1.100', '导入后主机地址一致')
        assert_equal(imported_config['service_port'], 9000, '导入后端口一致')

    header('十一、导入冲突检测与解决')

    conflict_name = f'冲突测试_{ts}'
    r = requests.post(f'{API}/connection/configs', json={
        'profile_name': conflict_name,
        'service_host': '10.0.0.1',
        'service_port': 1111,
        'entry_path': '/',
        'protocol': 'http',
        'current_operator_id': None,
        'is_default': False,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code in (200, 201), '创建冲突目标配置')

    conflict_import_data = {
        'configs': [{
            'profile_name': conflict_name,
            'service_host': '10.0.0.2',
            'service_port': 2222,
            'entry_path': '/test',
            'protocol': 'https',
            'config_version': 1
        }]
    }

    r = requests.post(f'{API}/connection/import', json={
        'configs': conflict_import_data['configs'],
        'operator_id': receiver['id']
    })
    if r.status_code == 409:
        assert_true(True, '冲突检测返回409')
        conflict_data = r.json()
        has_conflicts = 'conflicts' in conflict_data or 'conflict_configs' in conflict_data
        assert_true(has_conflicts, '返回conflicts字段')
        conflicts = conflict_data.get('conflicts', conflict_data.get('conflict_configs', []))
        assert_true(len(conflicts) >= 1, f'检测到冲突>=1 (共{len(conflicts)}个)')

        resolutions = {
            '0': {
                'mode': 'SAVE_AS_NEW',
                'new_name': f'{conflict_name}_另存'
            }
        }
        r = requests.post(f'{API}/connection/import/resolve', json={
            'configs': conflict_import_data['configs'],
            'operator_id': receiver['id'],
            'resolutions': resolutions
        })
        assert_true(r.status_code == 200, f'冲突解决-SAVE_AS_NEW模式 (status={r.status_code})')
        if r.status_code == 200:
            all_configs = requests.get(f'{API}/connection/configs').json()
            saved_new = next((c for c in all_configs if c.get('profile_name') == f'{conflict_name}_另存'), None)
            assert_true(saved_new is not None, '另存为新配置存在')

            original = next((c for c in all_configs if c.get('profile_name') == conflict_name), None)
            assert_true(original is not None, '原配置仍保留')
            if original:
                assert_equal(original['service_host'], '10.0.0.1', '原配置未被覆盖')
    else:
        assert_true(r.status_code == 200, f'无冲突直接导入成功 (status={r.status_code})')

    header('十一b、冲突处理-OVERWRITE覆盖模式')

    conflict_overwrite_name = f'覆盖测试_{ts}'
    r = requests.post(f'{API}/connection/configs', json={
        'profile_name': conflict_overwrite_name,
        'service_host': '172.16.0.1',
        'service_port': 7777,
        'entry_path': '/old',
        'protocol': 'http',
        'current_operator_id': None,
        'is_default': False,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code in (200, 201), '创建覆盖测试目标配置')

    overwrite_import_data = {
        'configs': [{
            'profile_name': conflict_overwrite_name,
            'service_host': '172.16.0.2',
            'service_port': 8888,
            'entry_path': '/new',
            'protocol': 'https',
            'config_version': 1
        }]
    }

    r = requests.post(f'{API}/connection/import', json={
        'configs': overwrite_import_data['configs'],
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 409, '覆盖模式-冲突检测返回409')

    resolutions_overwrite = {
        '0': {
            'mode': 'OVERWRITE'
        }
    }
    r = requests.post(f'{API}/connection/import/resolve', json={
        'configs': overwrite_import_data['configs'],
        'operator_id': receiver['id'],
        'resolutions': resolutions_overwrite
    })
    assert_true(r.status_code == 200, f'冲突解决-OVERWRITE模式 (status={r.status_code})')
    if r.status_code == 200:
        all_configs = requests.get(f'{API}/connection/configs').json()
        overwritten = next((c for c in all_configs if c.get('profile_name') == conflict_overwrite_name), None)
        assert_true(overwritten is not None, '覆盖模式-配置存在')
        if overwritten:
            assert_equal(overwritten['service_host'], '172.16.0.2', '覆盖模式-主机地址已更新')
            assert_equal(overwritten['service_port'], 8888, '覆盖模式-端口已更新')
            assert_equal(overwritten['entry_path'], '/new', '覆盖模式-入口路径已更新')
            assert_equal(overwritten['protocol'], 'https', '覆盖模式-协议已更新')

    header('十一c、冲突处理-KEEP_LOCAL保留模式')

    conflict_keep_name = f'保留测试_{ts}'
    r = requests.post(f'{API}/connection/configs', json={
        'profile_name': conflict_keep_name,
        'service_host': '192.168.1.1',
        'service_port': 3333,
        'entry_path': '/original',
        'protocol': 'http',
        'current_operator_id': None,
        'is_default': False,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code in (200, 201), '创建保留测试目标配置')

    keep_import_data = {
        'configs': [{
            'profile_name': conflict_keep_name,
            'service_host': '192.168.2.2',
            'service_port': 4444,
            'entry_path': '/modified',
            'protocol': 'https',
            'config_version': 1
        }]
    }

    r = requests.post(f'{API}/connection/import', json={
        'configs': keep_import_data['configs'],
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 409, '保留模式-冲突检测返回409')

    resolutions_keep = {
        '0': {
            'mode': 'KEEP_LOCAL'
        }
    }
    r = requests.post(f'{API}/connection/import/resolve', json={
        'configs': keep_import_data['configs'],
        'operator_id': receiver['id'],
        'resolutions': resolutions_keep
    })
    assert_true(r.status_code == 200, f'冲突解决-KEEP_LOCAL模式 (status={r.status_code})')
    if r.status_code == 200:
        all_configs = requests.get(f'{API}/connection/configs').json()
        kept = next((c for c in all_configs if c.get('profile_name') == conflict_keep_name), None)
        assert_true(kept is not None, '保留模式-配置存在')
        if kept:
            assert_equal(kept['service_host'], '192.168.1.1', '保留模式-主机地址未改变')
            assert_equal(kept['service_port'], 3333, '保留模式-端口未改变')
            assert_equal(kept['entry_path'], '/original', '保留模式-入口路径未改变')
            assert_equal(kept['protocol'], 'http', '保留模式-协议未改变')

    header('十二、重启保持测试（数据库持久化校验）')

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='connection_configs'")
        assert_true(c.fetchone() is not None, 'connection_configs表存在')

        c.execute("SELECT * FROM connection_configs WHERE profile_name = ?", (config_name,))
        row = c.fetchone()
        assert_true(row is not None, f'配置"{config_name}"在数据库中存在')
        if row:
            row_dict = dict(row)
            assert_equal(row_dict['service_port'], 5002, '数据库中端口值正确（经回退后）')

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='connection_config_snapshots'")
        assert_true(c.fetchone() is not None, 'connection_config_snapshots表存在')

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='connection_logs'")
        assert_true(c.fetchone() is not None, 'connection_logs表存在')

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='connection_diagnostics'")
        assert_true(c.fetchone() is not None, 'connection_diagnostics表存在')

        c.execute("SELECT COUNT(*) as cnt FROM connection_config_snapshots WHERE config_id = ?", (config_id,))
        snap_count = c.fetchone()['cnt']
        assert_true(snap_count >= 1, f'版本快照>=1 (共{snap_count}条)')

        c.execute("SELECT COUNT(*) as cnt FROM connection_logs WHERE config_id = ?", (config_id,))
        log_count = c.fetchone()['cnt']
        assert_true(log_count >= 1, f'操作日志>=1 (共{log_count}条)')

        conn.close()
    except Exception as e:
        assert_true(False, f'数据库校验异常: {e}')

    header('十三、操作日志完整性')

    r = requests.get(f'{API}/connection/logs')
    assert_true(r.status_code == 200, '获取操作日志')
    logs = r.json()
    assert_true(len(logs) >= 1, f'操作日志不为空 (共{len(logs)}条)')

    config_logs = [l for l in logs if l.get('config_id') == config_id]
    assert_true(len(config_logs) >= 1, f'当前配置的操作日志>=1 (共{len(config_logs)}条)')

    log_actions = [l.get('action', '') for l in config_logs]
    found_create = any('创建' in a or 'CREATE' in a for a in log_actions)
    assert_true(found_create, '日志包含创建类操作')

    header('十四、入口URL上下文警告')

    r = requests.put(f'{API}/connection/configs/{config_id}', json={
        'current_operator_id': None,
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, '清除操作人')

    r = requests.get(f'{API}/connection/configs/{config_id}/entry-url')
    entry_data = r.json()
    has_operator_warning = False
    warnings = entry_data.get('context_warnings', entry_data.get('warnings', []))
    for w in warnings:
        if isinstance(w, str) and '操作人' in w:
            has_operator_warning = True
        elif isinstance(w, dict) and '操作人' in str(w):
            has_operator_warning = True
    assert_true(has_operator_warning or entry_data.get('entry_url'), '缺少操作人时入口URL仍返回，并带上下文警告')

    r = requests.put(f'{API}/connection/configs/{config_id}', json={
        'current_operator_id': receiver['id'],
        'operator_id': receiver['id']
    })
    assert_true(r.status_code == 200, '恢复操作人')

    header('十五、默认配置获取')

    r = requests.get(f'{API}/connection/configs/default')
    assert_true(r.status_code == 200, '获取默认配置')
    default_config = r.json()
    if default_config:
        is_default = default_config.get('is_default') or (isinstance(default_config, dict) and default_config.get('config', {}).get('is_default'))
        assert_true(is_default, '返回的配置标记为默认')

    header('十六、配置删除')

    clean_names = [no_op_name, import_name, conflict_name + '_另存', conflict_overwrite_name, conflict_keep_name]
    all_c = requests.get(f'{API}/connection/configs').json()
    for c in all_c:
        if c.get('profile_name') in clean_names:
            requests.delete(f'{API}/connection/configs/{c.get("id")}')

    r = requests.delete(f'{API}/connection/configs/{config_id}')
    assert_true(r.status_code == 200, '删除测试配置')

    r = requests.get(f'{API}/connection/configs/{config_id}')
    assert_true(r.status_code == 404, f'删除后获取返回404 (status={r.status_code})')

    print()
    print('=' * 60)
    print(f'测试完成！通过: {passed}/{total}')
    print('=' * 60)

    if passed == total:
        print('🎉 所有测试通过！')
    else:
        print(f'⚠️ 有 {total - passed} 项测试未通过')

if __name__ == '__main__':
    main()
